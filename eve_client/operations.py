"""Operation executors for install plan actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from eve_client.auth.base import CredentialStore
from eve_client.config import ResolvedConfig
from eve_client.merge import merge_companion_file, merge_json_config, merge_toml_config
from eve_client.models import PlannedAction


class OperationError(RuntimeError):
    """Raised when a planned operation cannot be executed safely."""


@dataclass(slots=True)
class OperationContext:
    config: ResolvedConfig
    credentials: CredentialStore
    action: PlannedAction
    secret: str | None = None
    auth_mode: str | None = None
    api_key: str | None = None


@dataclass(slots=True)
class RenderedOperation:
    content: str | None
    permissions: int | None = None


def _require_path(action: PlannedAction) -> Path:
    if action.path is None:
        raise OperationError(f"{action.action_id} has no path")
    return action.path


def execute_auth_setup(context: OperationContext) -> RenderedOperation:
    auth_mode = context.auth_mode or context.action.details.get("auth_mode", "api-key")
    secret = context.secret or context.api_key
    if auth_mode == "oauth" and not secret:
        return RenderedOperation(content=None)
    if not secret:
        if auth_mode == "oauth":
            secret, _ = context.credentials.get_bearer_token(context.action.tool)
        else:
            secret, _ = context.credentials.get_api_key(context.action.tool)
    if not secret:
        required = "OAuth bearer token" if auth_mode == "oauth" else "API key"
        raise OperationError(f"{context.action.tool} requires an {required} before apply")
    if auth_mode == "oauth":
        context.credentials.set_bearer_token(context.action.tool, secret)
    else:
        context.credentials.set_api_key(context.action.tool, secret)
    return RenderedOperation(content=None)


def execute_write_config(context: OperationContext) -> RenderedOperation:
    action = context.action
    path = _require_path(action)
    auth_mode = context.auth_mode or action.details.get("auth_mode", "api-key")
    if auth_mode == "oauth":
        resolved_secret = None
        if context.secret or context.api_key:
            resolved_secret = context.secret or context.api_key
        else:
            try:
                resolved_secret, _ = context.credentials.get_bearer_token(action.tool)
            except Exception:
                resolved_secret = None
    else:
        resolved_secret, _ = context.credentials.get_api_key(action.tool)
    credential_to_use = context.secret or context.api_key or resolved_secret
    if auth_mode != "oauth" and not credential_to_use:
        required = "OAuth bearer token" if auth_mode == "oauth" else "API key"
        raise OperationError(f"No {required} available for {action.tool}")
    if action.details["config_format"] == "json":
        content = merge_json_config(
            path,
            action.tool,
            action.details["mcp_base_url"],
            credential_to_use,
            auth_mode=auth_mode,  # type: ignore[arg-type]
            hook_command=action.details.get("hook_command"),
            hooks_only=action.action_type == "write_hooks_config",
        )
    else:
        content = merge_toml_config(
            path,
            action.tool,
            action.details["mcp_base_url"],
            credential_to_use,
            auth_mode=auth_mode,  # type: ignore[arg-type]
        )
    permissions = path.stat().st_mode & 0o777 if path.exists() else 0o600
    return RenderedOperation(content=content, permissions=permissions)


def execute_create_companion_file(context: OperationContext) -> RenderedOperation:
    action = context.action
    path = _require_path(action)
    permissions = path.stat().st_mode & 0o777 if path.exists() else 0o600
    return RenderedOperation(
        content=merge_companion_file(path, action.tool, action.details["mcp_base_url"]),
        permissions=permissions,
    )


OPERATION_EXECUTORS: dict[str, Callable[[OperationContext], RenderedOperation]] = {
    "auth_setup": execute_auth_setup,
    "write_config": execute_write_config,
    "write_hooks_config": execute_write_config,
    "create_companion_file": execute_create_companion_file,
}


def execute_operation(context: OperationContext) -> RenderedOperation:
    try:
        executor = OPERATION_EXECUTORS[context.action.action_type]
    except KeyError as exc:
        raise OperationError(f"Unsupported action type: {context.action.action_type}") from exc
    return executor(context)
