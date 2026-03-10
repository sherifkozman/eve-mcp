"""Eve client CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from urllib.parse import urlencode, urljoin, urlparse
from typing import Optional
import webbrowser

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from eve_client._version import __version__
from eve_client.apply import apply_install_plan, rollback_transaction
from eve_client.auth import CredentialStoreUnavailableError, LocalCredentialStore, OAuthSession
from eve_client.config import DEFAULT_UI_BASE_URL, resolve_config, update_local_config
from eve_client.detect import ALL_TOOLS, detect_tools
from eve_client.integrations import get_adapter
from eve_client.lock import (
    InstallerLockUnsupportedPlatformError,
    installer_lock_is_held,
    read_lock_metadata,
)
from eve_client.manifest import ManifestIntegrityError, load_manifest
from eve_client.oauth_device import (
    OAuthDeviceFlowError,
    poll_auth0_device_token,
    refresh_auth0_token,
    start_auth0_device_authorization,
)
from eve_client.plan import build_install_plan, feature_enabled
from eve_client.recovery import reinitialize_trust_state
from eve_client.tool_state import classify_codex_disabled_state, classify_codex_local_state
from eve_client.transaction_state import load_transaction_state
from eve_client.uninstall import UninstallError, uninstall_tools
from eve_client.verify import verify_tools

app = typer.Typer(name="eve", help="Eve client installer and tool integration manager.")
console = Console()
CODEX_BEARER_TOKEN_ENV_VAR = "EVE_CODEX_BEARER_TOKEN"
MCP_OAUTH_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "memory.read",
    "memory.write",
)


def _credential_store(config):
    return LocalCredentialStore(
        config.state_dir, allow_file_fallback=config.allow_file_secret_fallback
    )


def _keyring_health(config) -> dict[str, object]:
    store = _credential_store(config)
    return {
        "backend": store.keyring_store.backend_name(),
        "low_assurance": store.keyring_store.backend_is_low_assurance(),
        "file_fallback_enabled": config.allow_file_secret_fallback,
    }


def _parse_tools(raw_tools: Optional[list[str]]) -> list[str] | None:
    if not raw_tools:
        return None
    parsed: list[str] = []
    for value in raw_tools:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    valid = [tool for tool in parsed if tool in ALL_TOOLS]
    return valid or None


def _normalize_prompt_scope(prompt_scope: str | None) -> str | None:
    if prompt_scope is None:
        return None
    value = prompt_scope.strip().lower()
    if value not in {"global", "project"}:
        raise typer.BadParameter("--prompt-scope must be 'global' or 'project'")
    return value


def _prompt_scope_overrides_for_tools(
    selected_tools: list[str] | None, prompt_scope: str | None
) -> dict[str, str]:
    normalized = _normalize_prompt_scope(prompt_scope)
    if not normalized or not selected_tools:
        return {}
    return {
        tool_name: normalized
        for tool_name in selected_tools
        if tool_name in {"gemini-cli", "claude-code"}
    }


def _hook_overrides_for_tools(
    selected_tools: list[str] | None, hooks_enabled: bool | None
) -> dict[str, bool]:
    if hooks_enabled is None or not selected_tools:
        return {}
    return {
        tool_name: hooks_enabled
        for tool_name in selected_tools
        if tool_name in {"gemini-cli", "claude-code"}
    }


def _resolve_gemini_install_options(
    detected_tool_name: str,
    *,
    prompt_scope: str | None,
    hooks_enabled: bool | None,
) -> tuple[str | None, bool | None]:
    normalized_scope = _normalize_prompt_scope(prompt_scope)
    if detected_tool_name != "gemini-cli":
        return normalized_scope, hooks_enabled
    selected_scope = normalized_scope
    selected_hooks = hooks_enabled
    if _stdin_is_tty() and selected_scope is None:
        selected_scope = (
            typer.prompt(
                "Choose Gemini prompt scope (global, project)",
                default="global",
                show_default=True,
            )
            .strip()
            .lower()
        )
        selected_scope = _normalize_prompt_scope(selected_scope)
    if _stdin_is_tty() and selected_hooks is None:
        selected_hooks = typer.confirm("Install Gemini Eve hooks?", default=True)
    return selected_scope, selected_hooks


def _preferred_tool_order(tool_name: str) -> int:
    order = {
        "claude-code": 0,
        "gemini-cli": 1,
        "codex-cli": 2,
        "claude-desktop": 3,
    }
    return order.get(tool_name, 99)


def _resolve_detected_tools(
    config,
    *,
    raw_tools: Optional[list[str]] = None,
    project: bool = False,
):
    selected_tools = _parse_tools(raw_tools)
    detected = detect_tools(
        only=selected_tools,
        project_scoped=project,
        enable_claude_desktop=config.feature_claude_desktop,
    )
    return selected_tools, detected


def _quickstart_state(tool_plan, detected_tool) -> str:
    if not tool_plan.supported:
        return "disabled"
    if not detected_tool.binary_found:
        return "not_detected"
    if detected_tool.config_exists:
        return "detected_with_config"
    return "detected"


def _connect_url(config, tool_name: str) -> str:
    query = urlencode({"tool": tool_name})
    return f"{urljoin(f'{config.ui_base_url}/', 'app/connect')}?{query}"


def _resource_metadata_url(config) -> str:
    parsed = urlparse(config.mcp_base_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[: -len("/mcp")]
    resource_root = parsed._replace(path=path or "", params="", query="", fragment="")
    return urljoin(resource_root.geturl().rstrip("/") + "/", ".well-known/oauth-protected-resource")


def _oauth_tool_next_steps(tool_name: str) -> list[str]:
    if tool_name == "claude-code":
        return [
            "Open Claude Code and use `/mcp` to inspect the Eve connection.",
            "When Claude prompts to authenticate the Eve MCP server, "
            "complete the OAuth flow in your browser.",
            "Run a small Eve memory store/search round trip to confirm "
            "the OAuth session is active.",
        ]
    if tool_name == "gemini-cli":
        return [
            "Open Gemini CLI and inspect the Eve MCP connection with `/mcp`.",
            "When Gemini prompts to authenticate the Eve MCP server, "
            "complete the OAuth flow in your browser.",
            "Run a small Eve memory store/search round trip to confirm "
            "the OAuth session is active.",
        ]
    if tool_name == "codex-cli":
        return [
            "Open Codex CLI and inspect the Eve MCP connection.",
            "If Codex offers OAuth authentication for the Eve MCP server, "
            "complete it in the browser.",
            "Re-run a small Eve memory store/search round trip to confirm "
            "the OAuth session is active.",
        ]
    return [
        "Open the tool and inspect the Eve MCP connection.",
        "Complete the browser-based OAuth flow when prompted.",
        "Run a small Eve memory round trip to confirm the OAuth session is active.",
    ]


def _print_oauth_guidance(config, tool_name: str, *, open_browser: bool) -> None:
    connect_url = _connect_url(config, tool_name)
    _print_hosted_endpoint_context(config)
    console.print(f"Connect in browser: [bold]{connect_url}[/bold]")
    console.print(f"Protected resource metadata: [bold]{_resource_metadata_url(config)}[/bold]")
    console.print("\n[bold]Next step in the client[/bold]")
    for step in _oauth_tool_next_steps(tool_name):
        console.print(f"- {step}")
    if open_browser:
        opened = _open_browser(connect_url)
        console.print(
            "[green]Opened browser.[/green]"
            if opened
            else "[yellow]Could not open browser automatically.[/yellow]"
        )


def _supports_device_flow(tool_name: str) -> bool:
    return tool_name == "codex-cli"


def _store_oauth_session(config, tool_name: str, token_result) -> tuple[OAuthSession, str]:
    expires_at = int(time.time()) + token_result.expires_in if token_result.expires_in else None
    session = OAuthSession(
        tool=tool_name,  # type: ignore[arg-type]
        access_token=token_result.access_token,
        refresh_token=token_result.refresh_token,
        expires_at=expires_at,
        scope=token_result.scope,
        token_type=token_result.token_type,
    )
    record = _credential_store(config).set_oauth_session(session)
    return session, record.source


def _login_via_device_flow(
    config, tool_name: str, *, open_browser: bool
) -> tuple[OAuthSession, str]:
    try:
        device = start_auth0_device_authorization(
            domain=config.oauth_domain,
            client_id=config.oauth_client_id,
            audience=config.mcp_base_url,
            scopes=MCP_OAUTH_SCOPES,
        )
    except OAuthDeviceFlowError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc

    console.print(Panel("[bold]Eve OAuth Device Flow[/bold]", style="green"))
    console.print(f"Tool: [bold]{tool_name}[/bold]")
    if device.verification_uri_complete:
        console.print(f"Open: [bold]{device.verification_uri_complete}[/bold]")
        if open_browser:
            opened = _open_browser(device.verification_uri_complete)
            console.print(
                "[green]Opened browser.[/green]"
                if opened
                else "[yellow]Could not open browser automatically.[/yellow]"
            )
    else:
        console.print(f"Open: [bold]{device.verification_uri}[/bold]")
        console.print(f"Code: [bold]{device.user_code}[/bold]")
        if open_browser:
            opened = _open_browser(device.verification_uri)
            console.print(
                "[green]Opened browser.[/green]"
                if opened
                else "[yellow]Could not open browser automatically.[/yellow]"
            )
    console.print("[dim]Waiting for OAuth approval...[/dim]")
    try:
        token_result = poll_auth0_device_token(
            domain=config.oauth_domain,
            client_id=config.oauth_client_id,
            device_code=device.device_code,
            expires_in=device.expires_in,
            interval=device.interval,
        )
    except OAuthDeviceFlowError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc
    return _store_oauth_session(config, tool_name, token_result)


def _load_active_oauth_session(config, tool_name: str) -> tuple[OAuthSession | None, str | None]:
    session, source = _credential_store(config).get_oauth_session(tool_name)  # type: ignore[arg-type]
    if session and session.expires_at and session.expires_at <= int(time.time()) + 30:
        if session.refresh_token:
            token_result = refresh_auth0_token(
                domain=config.oauth_domain,
                client_id=config.oauth_client_id,
                refresh_token=session.refresh_token,
            )
            return _store_oauth_session(config, tool_name, token_result)
    return session, source


def _build_quickstart_payload(config, detected, plan):
    items: list[dict[str, object]] = []
    for detected_tool, tool_plan in zip(detected, plan.tool_plans, strict=False):
        state = _quickstart_state(tool_plan, detected_tool)
        item = {
            "tool": detected_tool.name,
            "state": state,
            "binary_found": detected_tool.binary_found,
            "config_exists": detected_tool.config_exists,
            "config_path": str(detected_tool.config_path),
            "supported": tool_plan.supported,
            "auth_mode": tool_plan.auth_mode,
            "reason": tool_plan.reason,
        }
        items.append(item)

    items.sort(key=lambda item: (_preferred_tool_order(str(item["tool"])), str(item["tool"])))
    recommended = next(
        (item["tool"] for item in items if item["supported"] and item["binary_found"]),
        None,
    )

    next_steps: list[str] = []
    if recommended:
        recommended_plan = next(item for item in items if item["tool"] == recommended)
        next_steps.extend(
            [
                f"eve connect --tool {recommended}"
                if recommended_plan["auth_mode"] == "api-key"
                else f"eve connect --tool {recommended} --auth-mode oauth",
                f"eve verify --tool {recommended}",
            ]
        )
    else:
        next_steps.extend(
            [
                "Install one supported tool: Claude Code, Gemini CLI, or Codex CLI.",
                "Run eve quickstart again after the tool is installed.",
            ]
        )

    return {
        "mcp_base_url": config.mcp_base_url,
        "ui_base_url": config.ui_base_url,
        "environment": config.environment,
        "recommended_tool": recommended,
        "tools": items,
        "next_steps": next_steps,
    }


def _selected_detected_tool(config, *, raw_tool: Optional[list[str]] = None, project: bool = False):
    _, detected = _resolve_detected_tools(config, raw_tools=raw_tool, project=project)
    plan = build_install_plan(detected, config)
    by_tool = {tool_plan.tool: tool_plan for tool_plan in plan.tool_plans}
    candidates = []
    for detected_tool in detected:
        tool_plan = by_tool[detected_tool.name]
        if tool_plan.supported and detected_tool.binary_found:
            candidates.append((detected_tool, tool_plan))
    candidates.sort(key=lambda item: _preferred_tool_order(item[0].name))
    return detected, plan, candidates


def _select_auth_candidate(
    config, *, requested_tool: str | None, auth_mode: str | None, project: bool = False
):
    detected, plan, candidates = _selected_detected_tool(
        config,
        raw_tool=[requested_tool] if requested_tool else None,
        project=project,
    )
    by_tool = {tool_plan.tool: tool_plan for tool_plan in plan.tool_plans}
    selected_auth_mode = auth_mode

    if requested_tool:
        requested_detected = next((tool for tool in detected if tool.name == requested_tool), None)
        if requested_detected is None:
            raise typer.BadParameter(f"Unsupported tool: {requested_tool}")
        requested_plan = by_tool[requested_tool]
        selected_auth_mode = selected_auth_mode or requested_plan.auth_mode
        if selected_auth_mode == "oauth":
            if "oauth" not in requested_plan.supported_auth_modes:
                raise typer.BadParameter(
                    f"{requested_tool} does not support OAuth in the current client rollout."
                )
            return requested_detected, requested_plan, selected_auth_mode
        if selected_auth_mode != "api-key":
            raise typer.BadParameter("auth mode must be 'api-key' or 'oauth'")
        if not requested_plan.supported or not requested_detected.binary_found:
            reason = (
                requested_plan.reason or "Tool is not ready for credential setup on this machine."
            )
            raise typer.BadParameter(reason)
        return requested_detected, requested_plan, selected_auth_mode

    oauth_candidates: list[tuple[object, object]] = []
    api_candidates: list[tuple[object, object]] = []
    for detected_tool in detected:
        tool_plan = by_tool[detected_tool.name]
        if "oauth" in tool_plan.supported_auth_modes and (
            detected_tool.binary_found or detected_tool.name == "claude-desktop"
        ):
            oauth_candidates.append((detected_tool, tool_plan))
        if tool_plan.supported and detected_tool.binary_found:
            api_candidates.append((detected_tool, tool_plan))

    oauth_candidates.sort(key=lambda item: _preferred_tool_order(item[0].name))
    api_candidates.sort(key=lambda item: _preferred_tool_order(item[0].name))

    if selected_auth_mode == "oauth":
        if not oauth_candidates:
            console.print("[yellow]No OAuth-capable tool is ready on this machine.[/yellow]")
            raise typer.Exit(1)
        if len(oauth_candidates) == 1:
            detected_tool, tool_plan = oauth_candidates[0]
        else:
            detected_tool, tool_plan = _prompt_tool_choice(oauth_candidates)
        return detected_tool, tool_plan, "oauth"

    if selected_auth_mode and selected_auth_mode != "api-key":
        raise typer.BadParameter("auth mode must be 'api-key' or 'oauth'")
    if not api_candidates:
        console.print(
            "[yellow]No ready supported tool detected. Run `eve quickstart` for guidance.[/yellow]"
        )
        raise typer.Exit(1)
    if len(api_candidates) == 1:
        detected_tool, tool_plan = api_candidates[0]
    else:
        detected_tool, tool_plan = _prompt_tool_choice(api_candidates)
    return detected_tool, tool_plan, "api-key"


def _prompt_tool_choice(candidates) -> tuple[object, object]:
    if not _stdin_is_tty():
        raise typer.BadParameter("--tool is required in non-interactive mode.")
    options = [detected_tool.name for detected_tool, _ in candidates]
    default = options[0]
    selected = typer.prompt(
        f"Choose a tool to connect ({', '.join(options)})",
        default=default,
        show_default=True,
    ).strip()
    for detected_tool, tool_plan in candidates:
        if detected_tool.name == selected:
            return detected_tool, tool_plan
    raise typer.BadParameter(f"Unsupported tool choice: {selected}")


def _open_browser(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def _legacy_codex_warning(config) -> str | None:
    if config.codex_source == "legacy" and config.codex_enabled:
        return (
            "Codex enabled via legacy feature_codex_cli flag; prefer codex_enabled=true "
            "and remove the legacy flag."
        )
    return None


def _print_tool_actions(tool_plan) -> None:
    if not tool_plan.actions:
        return
    console.print("[bold]Planned changes[/bold]")
    for action in tool_plan.actions:
        location = f" -> {action.path}" if action.path else ""
        console.print(f"- {action.summary}{location}")


from eve_client.tty import stdin_is_tty as _stdin_is_tty  # noqa: E402


def _print_hosted_endpoint_context(config) -> None:
    if config.blocked_ui_base_url:
        raise typer.BadParameter(
            "Custom hosted UI base URL requires EVE_ALLOW_CUSTOM_UI_BASE_URL=1: "
            f"{config.blocked_ui_base_url}"
        )
    console.print(f"Hosted UI: [bold]{config.ui_base_url}[/bold]")
    if config.ui_base_url != DEFAULT_UI_BASE_URL:
        console.print("[yellow]Warning:[/yellow] using a custom hosted UI endpoint.")


def _enable_file_fallback(config) -> object:
    config_path = update_local_config({"allow_file_secret_fallback": True})
    console.print(
        "[yellow]Enabled file-based Eve credential fallback for this machine.[/yellow] "
        f"Config: [bold]{config_path}[/bold]"
    )
    return resolve_config()


def _apply_requested_file_fallback(config, allow_file_fallback: bool):
    if allow_file_fallback and not config.allow_file_secret_fallback:
        return _enable_file_fallback(config)
    return config


def _recover_from_unavailable_credential_store(
    config, tool_name: str, exc: CredentialStoreUnavailableError
):
    message = (
        f"{exc} Use a desktop keyring when available, or enable Eve's file fallback for "
        "headless Linux machines."
    )
    if not _stdin_is_tty():
        raise typer.BadParameter(message) from exc
    console.print(f"[yellow]{message}[/yellow]")
    if not typer.confirm(
        f"Enable file-based Eve credential fallback for {tool_name} on this machine?"
    ):
        raise typer.Exit(1) from exc
    return _enable_file_fallback(config)


def _tool_status_payload(detected, config, credential_store):
    eve_configured = False
    credential = None
    source = None
    disabled_state = classify_codex_disabled_state(config) if detected.name == "codex-cli" else None
    if detected.name != "codex-cli" and detected.config_format == "json":
        from eve_client.merge import has_eve_json_entry

        eve_configured = has_eve_json_entry(detected.config_path)
    elif detected.name != "codex-cli" and detected.config_format == "toml":
        from eve_client.merge import has_eve_toml_entry

        eve_configured = has_eve_toml_entry(detected.config_path)
    item = {
        "name": detected.name,
        "binary_found": detected.binary_found,
        "config_exists": detected.config_exists,
        "config_path": str(detected.config_path),
    }
    if detected.name == "codex-cli":
        if disabled_state is None:
            from eve_client.merge import has_eve_toml_entry

            eve_configured = has_eve_toml_entry(detected.config_path)
            try:
                credential, source = credential_store.get_api_key(detected.name)
            except CredentialStoreUnavailableError:
                credential, source = None, "unavailable"
        item["codex"] = {
            "enabled": config.codex_enabled,
            "source": config.codex_source,
            "credential_source": source,
            "eve_configured": eve_configured,
            "state": disabled_state
            or classify_codex_local_state(
                config,
                detected,
                auth_mode=get_adapter(detected.name).auth_mode,
                credential_present=bool(credential),
                eve_configured=eve_configured,
            ),
        }
    return item


@app.command()
def version() -> None:
    """Show Eve client version."""
    console.print(__version__)


@app.command()
def quickstart(
    tool: Optional[list[str]] = typer.Option(None, "--tool", "-t"),
    json_output: bool = typer.Option(False, "--json"),
    project: bool = typer.Option(False, "--project"),
) -> None:
    """Show the fastest safe path to connect the current machine to Eve."""
    config = resolve_config()
    _, detected = _resolve_detected_tools(config, raw_tools=tool, project=project)
    plan = build_install_plan(detected, config)
    payload = _build_quickstart_payload(config, detected, plan)

    if json_output:
        console.print_json(json.dumps(payload))
        return

    console.print(Panel("[bold]Eve Quickstart[/bold]", style="green"))
    console.print(f"MCP endpoint: [bold]{payload['mcp_base_url']}[/bold]")
    if payload["recommended_tool"]:
        console.print(
            f"Best first tool on this machine: [bold]{payload['recommended_tool']}[/bold]"
        )
    else:
        console.print("[yellow]No ready supported tool detected yet.[/yellow]")

    table = Table(title="Tool readiness")
    table.add_column("Tool")
    table.add_column("State")
    table.add_column("Auth")
    table.add_column("Config path")
    table.add_column("Notes")
    for item in payload["tools"]:
        note = item["reason"] or ""
        table.add_row(
            str(item["tool"]),
            str(item["state"]),
            str(item["auth_mode"]),
            str(item["config_path"]),
            str(note),
        )
    console.print(table)
    console.print("\n[bold]Suggested next steps[/bold]")
    for step in payload["next_steps"]:
        console.print(f"- {step}")


@app.command()
def connect(
    tool: Optional[str] = typer.Option(None, "--tool", "-t"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    bearer_token: Optional[str] = typer.Option(None, "--bearer-token"),
    auth_mode: Optional[str] = typer.Option(None, "--auth-mode"),
    prompt_scope: Optional[str] = typer.Option(None, "--prompt-scope"),
    hooks_enabled: Optional[bool] = typer.Option(None, "--with-hooks/--without-hooks"),
    project: bool = typer.Option(False, "--project"),
    open_browser: bool = typer.Option(True, "--open-browser/--no-browser"),
    allow_file_fallback: bool = typer.Option(False, "--allow-file-fallback"),
    yes: bool = typer.Option(False, "--yes", help="Confirm apply without additional prompt"),
) -> None:
    """Guided connection flow for the best supported tool on this machine."""
    config = resolve_config()
    detected_tool, tool_plan, selected_auth_mode = _select_auth_candidate(
        config,
        requested_tool=tool,
        auth_mode=auth_mode,
        project=project,
    )
    detected, plan, _ = _selected_detected_tool(
        config,
        raw_tool=[detected_tool.name],
        project=project,
    )
    prompt_scope, hooks_enabled = _resolve_gemini_install_options(
        detected_tool.name,
        prompt_scope=prompt_scope,
        hooks_enabled=hooks_enabled,
    )
    selected_plan = build_install_plan(
        detected,
        config,
        auth_overrides={detected_tool.name: selected_auth_mode},
        prompt_scope_overrides=_prompt_scope_overrides_for_tools(
            [detected_tool.name], prompt_scope
        ),
        hook_overrides=_hook_overrides_for_tools([detected_tool.name], hooks_enabled),
    )
    selected_tool_plan = next(
        tool for tool in selected_plan.tool_plans if tool.tool == detected_tool.name
    )

    console.print(Panel("[bold]Eve Connect[/bold]", style="green"))
    console.print(f"Tool: [bold]{detected_tool.name}[/bold]")
    console.print(f"Auth mode: [bold]{selected_auth_mode}[/bold]")
    _print_tool_actions(selected_tool_plan)

    if selected_auth_mode == "oauth":
        if _supports_device_flow(detected_tool.name):
            config = _apply_requested_file_fallback(config, allow_file_fallback)
        if not selected_tool_plan.supported or not selected_tool_plan.actions:
            console.print(Panel("[bold]Eve Connect[/bold]", style="green"))
            console.print(f"Tool: [bold]{detected_tool.name}[/bold]")
            console.print(f"Auth mode: [bold]{selected_auth_mode}[/bold]")
            _print_oauth_guidance(config, detected_tool.name, open_browser=open_browser)
            return
        if "oauth" not in selected_tool_plan.supported_auth_modes:
            raise typer.BadParameter(
                f"{detected_tool.name} does not support OAuth in the current client rollout."
            )
        if not yes and not _stdin_is_tty():
            raise typer.BadParameter("--yes is required in non-interactive mode.")
        if not yes and not typer.confirm(
            f"Apply Eve OAuth configuration for {detected_tool.name}?"
        ):
            raise typer.Exit(1)
        credential_store = _credential_store(config)
        result = apply_install_plan(
            selected_plan,
            config,
            credential_store,
            provided_secrets={detected_tool.name: bearer_token},
            auth_overrides={detected_tool.name: selected_auth_mode},
            allowed_tools=[detected_tool.name],
        )
        verify_result = verify_tools(
            [detected_tool],
            config,
            credential_store,
            auth_overrides={detected_tool.name: selected_auth_mode},
        )[0]
        console.print(
            f"[green]Connected.[/green] Transaction: [bold]{result.transaction_id}[/bold]"
        )
        if verify_result["connectivity"]["success"]:
            console.print("[green]Verification succeeded.[/green]")
        else:
            err = verify_result["connectivity"]["error"]
            console.print(f"[yellow]Verification requires follow-up:[/yellow] {err}")
        if not bearer_token and _supports_device_flow(detected_tool.name):
            try:
                _login_via_device_flow(config, detected_tool.name, open_browser=open_browser)
            except CredentialStoreUnavailableError as exc:
                config = _recover_from_unavailable_credential_store(config, detected_tool.name, exc)
                credential_store = _credential_store(config)
                _login_via_device_flow(config, detected_tool.name, open_browser=open_browser)
            credential_store = _credential_store(config)
            verify_result = verify_tools(
                [detected_tool],
                config,
                credential_store,
                auth_overrides={detected_tool.name: selected_auth_mode},
            )[0]
            if verify_result["connectivity"]["success"]:
                console.print("[green]OAuth verification succeeded.[/green]")
            else:
                oauth_err = verify_result["connectivity"]["error"]
                console.print(
                    f"[yellow]OAuth verification requires follow-up:[/yellow] {oauth_err}"
                )
        elif not bearer_token:
            _print_oauth_guidance(config, detected_tool.name, open_browser=open_browser)
        return

    config = _apply_requested_file_fallback(config, allow_file_fallback)
    if not api_key:
        if not _stdin_is_tty():
            raise typer.BadParameter("--api-key is required in non-interactive mode.")
        api_key = typer.prompt("Eve API key", hide_input=True)
    if not yes and not _stdin_is_tty():
        raise typer.BadParameter("--yes is required in non-interactive mode.")
    if not yes and not typer.confirm(f"Apply Eve configuration for {detected_tool.name}?"):
        raise typer.Exit(1)

    credential_store = _credential_store(config)
    try:
        result = apply_install_plan(
            selected_plan,
            config,
            credential_store,
            provided_api_keys={detected_tool.name: api_key},
            allowed_tools=[detected_tool.name],
        )
    except CredentialStoreUnavailableError as exc:
        config = _recover_from_unavailable_credential_store(config, detected_tool.name, exc)
        credential_store = _credential_store(config)
        selected_plan = build_install_plan(
            detected,
            config,
            auth_overrides={detected_tool.name: selected_auth_mode},
            prompt_scope_overrides=_prompt_scope_overrides_for_tools(
                [detected_tool.name], prompt_scope
            ),
            hook_overrides=_hook_overrides_for_tools([detected_tool.name], hooks_enabled),
        )
        result = apply_install_plan(
            selected_plan,
            config,
            credential_store,
            provided_api_keys={detected_tool.name: api_key},
            allowed_tools=[detected_tool.name],
        )
    verify_result = verify_tools([detected_tool], config, credential_store)[0]
    console.print(f"[green]Connected.[/green] Transaction: [bold]{result.transaction_id}[/bold]")
    if verify_result["connectivity"]["success"]:
        console.print("[green]Verification succeeded.[/green]")
    else:
        conn_err = verify_result["connectivity"]["error"]
        console.print(f"[yellow]Verification requires follow-up:[/yellow] {conn_err}")


@app.command()
def install(
    tool: Optional[list[str]] = typer.Option(None, "--tool", "-t"),
    all_tools: bool = typer.Option(False, "--all", help="Apply to every detected supported tool."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply"),
    json_output: bool = typer.Option(False, "--json"),
    non_interactive: bool = typer.Option(False, "--non-interactive"),
    mcp_base_url: Optional[str] = typer.Option(None, "--mcp-base-url"),
    project: bool = typer.Option(False, "--project"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    bearer_token: Optional[str] = typer.Option(None, "--bearer-token"),
    auth_mode: Optional[str] = typer.Option(None, "--auth-mode"),
    prompt_scope: Optional[str] = typer.Option(None, "--prompt-scope"),
    hooks_enabled: Optional[bool] = typer.Option(None, "--with-hooks/--without-hooks"),
    allow_file_fallback: bool = typer.Option(False, "--allow-file-fallback"),
    yes: bool = typer.Option(False, "--yes", help="Confirm apply without additional prompt"),
) -> None:
    """Generate or apply an Eve installation plan."""
    if all_tools and tool:
        raise typer.BadParameter("Use either --tool or --all, not both.")
    if non_interactive and not dry_run and not yes:
        raise typer.BadParameter("Non-interactive apply requires --yes.")

    # Interactive flow: guided prompts when TTY, no --tool, no --all
    from eve_client.interactive import (  # noqa: PLC0415
        is_keyring_available,
        preview_and_confirm,
        prompt_file_fallback,
        run_interactive_install,
        should_use_interactive,
    )

    if should_use_interactive(tool_flag=tool, all_flag=all_tools, non_interactive=non_interactive):
        config = resolve_config(override_mcp_base_url=mcp_base_url)
        _, detected = _resolve_detected_tools(config, raw_tools=None, project=project)
        interactive_result = run_interactive_install(detected)
        if interactive_result is None:
            raise typer.Exit(1)

        if interactive_result.uninstall_tools:
            credential_store = _credential_store(config)
            uninstall_tools(
                config=config,
                credential_store=credential_store,
                tools=interactive_result.uninstall_tools,
            )
            console.print(
                f"[green]Uninstalled:[/green] {', '.join(interactive_result.uninstall_tools)}"
            )
            if not interactive_result.selected_tools:
                return

        plan = build_install_plan(
            detected,
            config,
            auth_overrides=interactive_result.auth_overrides,
            prompt_scope_overrides=interactive_result.prompt_scope_overrides,
            hook_overrides=interactive_result.hook_overrides,
        )
        if json_output:
            console.print_json(json.dumps(plan.to_dict()))
            return
        if not preview_and_confirm(plan):
            raise typer.Exit(1)
        # Proactively handle missing keyring before attempting to store credentials.
        # This surfaces the headless-Linux footgun as a friendly prompt rather than an
        # error mid-apply.  The explicit --allow-file-fallback flag still works for
        # scripted / non-interactive use.
        config = _apply_requested_file_fallback(config, allow_file_fallback)
        if not config.allow_file_secret_fallback and not is_keyring_available():
            if not prompt_file_fallback():
                raise typer.Exit(1)
            config = _enable_file_fallback(config)
        plan = build_install_plan(
            detected,
            config,
            auth_overrides=interactive_result.auth_overrides,
            prompt_scope_overrides=interactive_result.prompt_scope_overrides,
            hook_overrides=interactive_result.hook_overrides,
        )
        credential_store = _credential_store(config)
        try:
            result = apply_install_plan(
                plan,
                config,
                credential_store,
                provided_secrets=interactive_result.provided_secrets,
                auth_overrides=interactive_result.auth_overrides,
                allowed_tools=interactive_result.selected_tools,
            )
        except CredentialStoreUnavailableError as exc:
            # Guard: selected_tools is non-empty here (empty case returns on line 854)
            first_tool = interactive_result.selected_tools[0]
            config = _recover_from_unavailable_credential_store(config, first_tool, exc)
            credential_store = _credential_store(config)
            plan = build_install_plan(
                detected,
                config,
                auth_overrides=interactive_result.auth_overrides,
                prompt_scope_overrides=interactive_result.prompt_scope_overrides,
                hook_overrides=interactive_result.hook_overrides,
            )
            result = apply_install_plan(
                plan,
                config,
                credential_store,
                provided_secrets=interactive_result.provided_secrets,
                auth_overrides=interactive_result.auth_overrides,
                allowed_tools=interactive_result.selected_tools,
            )
        console.print(
            f"\n[green]Applied.[/green] Transaction: [bold]{result.transaction_id}[/bold]"
        )
        verify_results = verify_tools(
            detected,
            config,
            credential_store,
            auth_overrides=interactive_result.auth_overrides,
        )
        all_ok = all(v.get("connectivity", {}).get("success") for v in verify_results)
        if all_ok:
            console.print("[green]Verification passed.[/green]")
        else:
            console.print("[yellow]Verification issues:[/yellow]")
            for v in verify_results:
                if not v.get("connectivity", {}).get("success"):
                    reason = v.get("connectivity", {}).get("error", "unknown")
                    console.print(f"  - {v['tool']}: {reason}")
        return

    config = resolve_config(override_mcp_base_url=mcp_base_url)
    selected_tools, detected = _resolve_detected_tools(
        config, raw_tools=None if all_tools else tool, project=project
    )
    if selected_tools == ["gemini-cli"]:
        prompt_scope, hooks_enabled = _resolve_gemini_install_options(
            "gemini-cli",
            prompt_scope=prompt_scope,
            hooks_enabled=hooks_enabled,
        )
    auth_overrides = (
        {tool_name: auth_mode for tool_name in selected_tools or []}
        if auth_mode in {"api-key", "oauth"} and selected_tools
        else {}
    )
    plan = build_install_plan(
        detected,
        config,
        auth_overrides=auth_overrides,
        prompt_scope_overrides=_prompt_scope_overrides_for_tools(selected_tools, prompt_scope),
        hook_overrides=_hook_overrides_for_tools(selected_tools, hooks_enabled),
    )

    if json_output:
        console.print_json(json.dumps(plan.to_dict()))
        return

    console.print(Panel("[bold]Eve Install Plan[/bold]", style="green"))
    console.print(f"MCP endpoint: [bold]{plan.mcp_base_url}[/bold]")
    console.print(f"Environment: [bold]{plan.environment}[/bold]")
    table = Table(title="Detected tools")
    table.add_column("Tool")
    table.add_column("Supported")
    table.add_column("Auth")
    table.add_column("Notes")
    for tool_plan in plan.tool_plans:
        table.add_row(
            tool_plan.tool,
            "Yes" if tool_plan.supported else "No",
            tool_plan.auth_mode,
            tool_plan.reason or "",
        )
    console.print(table)
    for tool_plan in plan.tool_plans:
        if not tool_plan.actions:
            continue
        console.print(f"\n[bold]{tool_plan.tool}[/bold]")
        for action in tool_plan.actions:
            location = f" -> {action.path}" if action.path else ""
            console.print(f"- {action.summary}{location}")
    if dry_run:
        console.print("\n[dim]Dry run only. Re-run with --apply to execute after review.[/dim]")
        if not selected_tools and not all_tools:
            console.print(
                "[dim]Tip: use eve quickstart for a faster first-run recommendation.[/dim]"
            )
        return

    if not yes and not typer.confirm("Apply this plan?"):
        raise typer.Exit(1)

    config = _apply_requested_file_fallback(config, allow_file_fallback)
    plan = build_install_plan(
        detected,
        config,
        auth_overrides=auth_overrides,
        prompt_scope_overrides=_prompt_scope_overrides_for_tools(selected_tools, prompt_scope),
        hook_overrides=_hook_overrides_for_tools(selected_tools, hooks_enabled),
    )
    credential_store = _credential_store(config)
    provided_secrets = {
        tool_name: (bearer_token if auth_mode == "oauth" else api_key)
        for tool_name in selected_tools or ALL_TOOLS
        if (bearer_token if auth_mode == "oauth" else api_key)
    }
    try:
        result = apply_install_plan(
            plan,
            config,
            credential_store,
            provided_secrets=provided_secrets,
            auth_overrides=auth_overrides,
            allowed_tools=selected_tools,
        )
    except CredentialStoreUnavailableError as exc:
        first_tool = (selected_tools or ALL_TOOLS)[0]
        config = _recover_from_unavailable_credential_store(config, first_tool, exc)
        credential_store = _credential_store(config)
        plan = build_install_plan(
            detected,
            config,
            auth_overrides=auth_overrides,
            prompt_scope_overrides=_prompt_scope_overrides_for_tools(selected_tools, prompt_scope),
            hook_overrides=_hook_overrides_for_tools(selected_tools, hooks_enabled),
        )
        result = apply_install_plan(
            plan,
            config,
            credential_store,
            provided_secrets=provided_secrets,
            auth_overrides=auth_overrides,
            allowed_tools=selected_tools,
        )
    console.print(f"\n[green]Applied.[/green] Transaction: [bold]{result.transaction_id}[/bold]")


@app.command()
def status(
    tool: Optional[list[str]] = typer.Option(None, "--tool", "-t"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show detected tool and environment status."""
    config = resolve_config()
    _, detected = _resolve_detected_tools(config, raw_tools=tool)
    credential_store = _credential_store(config)
    manifest_status = "ok"
    try:
        load_manifest(config.state_dir, allow_file_fallback=config.allow_file_secret_fallback)
    except ManifestIntegrityError as exc:
        manifest_status = str(exc)
    pending_transaction = load_transaction_state(config.state_dir)
    lock_error: str | None = None
    try:
        lock_held = installer_lock_is_held(config.state_dir)
    except InstallerLockUnsupportedPlatformError as exc:
        lock_held = False
        lock_error = str(exc)
    payload = {
        "mcp_base_url": config.mcp_base_url,
        "environment": config.environment,
        "feature_claude_desktop": config.feature_claude_desktop,
        "codex_enabled": config.codex_enabled,
        "codex_source": config.codex_source,
        "pending_transaction": pending_transaction,
        "manifest_status": manifest_status,
        "lock": {
            "held": lock_held,
            "error": lock_error,
            "metadata": read_lock_metadata(config.state_dir),
        },
        "keyring": _keyring_health(config),
        "tools": [_tool_status_payload(tool, config, credential_store) for tool in detected],
    }
    codex_warning = _legacy_codex_warning(config)
    if codex_warning:
        payload["warnings"] = [codex_warning]
    if json_output:
        console.print_json(json.dumps(payload))
        return
    console.print(Panel("[bold]Eve Status[/bold]", style="blue"))
    console.print(f"MCP endpoint: [bold]{config.mcp_base_url}[/bold]")
    console.print(f"Config: [bold]{config.config_path}[/bold]")
    console.print(f"State: [bold]{config.state_dir}[/bold]")
    pending = pending_transaction
    if pending:
        console.print(f"[yellow]Pending transaction:[/yellow] {pending}")
        if not lock_held:
            console.print(
                "[yellow]Recovery hint:[/yellow] no active installer lock is held; "
                "the previous run likely terminated unexpectedly."
            )
    if lock_error:
        console.print(f"[yellow]Locking:[/yellow] {lock_error}")
    if manifest_status != "ok":
        console.print(f"[yellow]Trust state:[/yellow] {manifest_status}")
    if payload["keyring"]["low_assurance"]:
        console.print(
            f"[yellow]Keyring backend:[/yellow] {payload['keyring']['backend']} (low assurance)"
        )
        if not config.allow_file_secret_fallback:
            console.print(
                "[yellow]Headless setup hint:[/yellow] Eve can enable encrypted file "
                "fallback during `eve connect` or `eve auth login` if no system keyring "
                "is available."
            )
    elif config.allow_file_secret_fallback:
        console.print("[yellow]File secret fallback:[/yellow] enabled")
    if codex_warning:
        console.print(f"[yellow]Codex:[/yellow] {codex_warning}")
    table = Table(title="Tool status")
    table.add_column("Tool")
    table.add_column("Binary")
    table.add_column("Config")
    table.add_column("Config path")
    for item in payload["tools"]:
        config_state = "Exists" if item["config_exists"] else "Missing"
        if item["name"] == "codex-cli" and "codex" in item:
            config_state = item["codex"]["state"]
        table.add_row(
            item["name"],
            "Found" if item["binary_found"] else "Missing",
            config_state,
            item["config_path"],
        )
    console.print(table)


@app.command()
def doctor(
    tool: Optional[list[str]] = typer.Option(None, "--tool", "-t"),
) -> None:
    """Check local state without mutating configs."""
    config = resolve_config()
    _, detected = _resolve_detected_tools(config, raw_tools=tool)
    credential_store = _credential_store(config)
    problems: list[str] = []
    keyring_health = _keyring_health(config)
    pending_transaction = load_transaction_state(config.state_dir)
    try:
        lock_held = installer_lock_is_held(config.state_dir)
    except InstallerLockUnsupportedPlatformError as exc:
        lock_held = False
        problems.append(f"locking: {exc}")
    try:
        load_manifest(config.state_dir, allow_file_fallback=config.allow_file_secret_fallback)
    except ManifestIntegrityError as exc:
        problems.append(
            f"trust-state: {exc}. Run 'eve trust reinit --yes' after reviewing local state."
        )
    if pending_transaction and not lock_held:
        problems.append(
            "transaction-state: interrupted installer run detected with no active lock; "
            "recover or reinitialize trust state before applying again"
        )
    if keyring_health["low_assurance"]:
        problems.append(
            f"keyring: low-assurance backend detected ({keyring_health['backend']}); "
            "use a desktop keyring or enable Eve file fallback during auth/connect"
        )
    if config.allow_file_secret_fallback:
        problems.append(
            "credentials: file fallback is enabled; this weakens local secret "
            "and trust-anchor protection"
        )
    codex_warning = _legacy_codex_warning(config)
    for detected_tool in detected:
        if detected_tool.name == "codex-cli":
            codex_state = classify_codex_disabled_state(config)
            if codex_state is None:
                from eve_client.merge import has_eve_toml_entry

                try:
                    credential, _ = credential_store.get_api_key(detected_tool.name)
                    credential_present = bool(credential)
                except CredentialStoreUnavailableError:
                    credential_present = False
                codex_state = classify_codex_local_state(
                    config,
                    detected_tool,
                    auth_mode=get_adapter(detected_tool.name).auth_mode,
                    credential_present=credential_present,
                    eve_configured=has_eve_toml_entry(detected_tool.config_path),
                )
            if codex_state in {
                "disabled_by_env",
                "disabled_by_config",
                "disabled_by_default",
                "disabled_by_legacy",
            }:
                continue
            if codex_state == "enabled_binary_missing":
                problems.append("codex-cli: enabled but binary not found")
                continue
            if codex_state == "enabled_unconfigured":
                problems.append("codex-cli: enabled but local Eve config or credential is missing")
                continue
        if detected_tool.feature_flag_required and not feature_enabled(detected_tool, config):
            problems.append(f"{detected_tool.name}: feature-flagged and disabled")
            continue
        if not detected_tool.binary_found and detected_tool.name != "claude-desktop":
            problems.append(f"{detected_tool.name}: binary not found")
    if problems:
        console.print(Panel("[bold]Eve Doctor[/bold]", style="yellow"))
        if codex_warning:
            console.print(f"- {codex_warning}")
        for problem in problems:
            console.print(f"- {problem}")
        raise typer.Exit(1)
    if codex_warning:
        console.print(f"[yellow]{codex_warning}[/yellow]")
    console.print("[green]No core engine issues detected.[/green]")


@app.command()
def verify(
    tool: Optional[list[str]] = typer.Option(None, "--tool", "-t"),
    json_output: bool = typer.Option(False, "--json"),
    auth_mode: Optional[str] = typer.Option(None, "--auth-mode"),
) -> None:
    """Verify local Eve integration state and live MCP connectivity."""
    config = resolve_config()
    _, detected = _resolve_detected_tools(config, raw_tools=tool)
    selected_tools = _parse_tools(tool)
    auth_overrides = (
        {tool_name: auth_mode for tool_name in selected_tools or []}
        if auth_mode in {"api-key", "oauth"} and selected_tools
        else {}
    )
    results = verify_tools(
        detected, config, _credential_store(config), auth_overrides=auth_overrides
    )
    if json_output:
        console.print_json(json.dumps(results))
        return
    console.print(Panel("[bold]Eve Verify[/bold]", style="cyan"))
    table = Table(title="Verification")
    table.add_column("Tool")
    table.add_column("Local")
    table.add_column("Credential")
    table.add_column("Remote")
    table.add_column("Notes")
    has_failures = False
    for item in results:
        local_ok = item["binary_found"] and item["eve_configured"]
        remote = item["connectivity"]
        remote_ok = bool(remote["success"])
        has_failures = has_failures or not remote_ok or not local_ok
        notes: list[str] = []
        if item["companion_path"]:
            notes.append("companion ok" if item["companion_present"] else "companion missing")
        if not item["feature_enabled"]:
            notes.append("feature disabled")
        elif not item["binary_found"]:
            notes.append("binary missing")
        elif not item["eve_configured"]:
            notes.append("Eve config missing")
        elif not remote_ok:
            notes.append(str(remote["error"]))
        table.add_row(
            item["tool"],
            "OK" if local_ok else "Needs repair",
            item["credential_source"] or "missing",
            "OK" if remote_ok else "Failed",
            "; ".join(notes),
        )
    console.print(table)
    if has_failures:
        raise typer.Exit(1)


@app.command()
def repair(
    tool: Optional[list[str]] = typer.Option(None, "--tool", "-t"),
    dry_run: bool = typer.Option(True, "--dry-run/--apply"),
    yes: bool = typer.Option(False, "--yes", help="Confirm apply without additional prompt"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    prompt_scope: Optional[str] = typer.Option(None, "--prompt-scope"),
    hooks_enabled: Optional[bool] = typer.Option(None, "--with-hooks/--without-hooks"),
    project: bool = typer.Option(False, "--project"),
    allow_file_fallback: bool = typer.Option(False, "--allow-file-fallback"),
) -> None:
    """Repair supported Eve-managed tool integrations."""
    config = resolve_config()
    selected_tools, detected = _resolve_detected_tools(config, raw_tools=tool, project=project)
    if selected_tools == ["gemini-cli"]:
        prompt_scope, hooks_enabled = _resolve_gemini_install_options(
            "gemini-cli",
            prompt_scope=prompt_scope,
            hooks_enabled=hooks_enabled,
        )
    plan = build_install_plan(
        detected,
        config,
        prompt_scope_overrides=_prompt_scope_overrides_for_tools(selected_tools, prompt_scope),
        hook_overrides=_hook_overrides_for_tools(selected_tools, hooks_enabled),
    )
    verification = verify_tools(detected, config, _credential_store(config))
    if dry_run:
        console.print(Panel("[bold]Eve Repair Plan[/bold]", style="magenta"))
        for item in verification:
            status = "ok"
            if (
                not item["eve_configured"]
                or not item["companion_present"]
                or not item["connectivity"]["success"]
            ):
                status = "repair suggested"
            console.print(f"- {item['tool']}: {status}")
        console.print("[dim]Dry run only. Re-run with --apply to execute repairs.[/dim]")
        return
    if not yes and not typer.confirm("Apply repair plan?"):
        raise typer.Exit(1)
    config = _apply_requested_file_fallback(config, allow_file_fallback)
    plan = build_install_plan(
        detected,
        config,
        prompt_scope_overrides=_prompt_scope_overrides_for_tools(selected_tools, prompt_scope),
        hook_overrides=_hook_overrides_for_tools(selected_tools, hooks_enabled),
    )
    credential_store = _credential_store(config)
    provided_api_keys: dict[str, str] = {}
    for tool_name in selected_tools or ALL_TOOLS:
        if api_key:
            provided_api_keys[tool_name] = api_key
            continue
        stored, _ = credential_store.get_api_key(tool_name)  # type: ignore[arg-type]
        if stored:
            provided_api_keys[tool_name] = stored
    try:
        result = apply_install_plan(
            plan,
            config,
            credential_store,
            provided_api_keys=provided_api_keys,
            allowed_tools=selected_tools,
        )
    except CredentialStoreUnavailableError as exc:
        first_tool = (selected_tools or ALL_TOOLS)[0]
        config = _recover_from_unavailable_credential_store(config, first_tool, exc)
        credential_store = _credential_store(config)
        plan = build_install_plan(
            detected,
            config,
            prompt_scope_overrides=_prompt_scope_overrides_for_tools(selected_tools, prompt_scope),
            hook_overrides=_hook_overrides_for_tools(selected_tools, hooks_enabled),
        )
        result = apply_install_plan(
            plan,
            config,
            credential_store,
            provided_api_keys=provided_api_keys,
            allowed_tools=selected_tools,
        )
    console.print(f"[green]Repaired.[/green] Transaction: [bold]{result.transaction_id}[/bold]")


@app.command()
def uninstall(
    tool: list[str] = typer.Option(..., "--tool", "-t"),
    yes: bool = typer.Option(False, "--yes", help="Confirm uninstall without additional prompt"),
) -> None:
    """Remove Eve-managed config, companion files, and stored credentials for selected tools."""
    config = resolve_config()
    selected_tools = _parse_tools(tool)
    if not selected_tools:
        raise typer.BadParameter("At least one supported tool must be selected.")
    if not yes and not typer.confirm(f"Uninstall Eve from: {', '.join(selected_tools)}?"):
        raise typer.Exit(1)
    try:
        result = uninstall_tools(
            config=config,
            credential_store=_credential_store(config),
            tools=selected_tools,  # type: ignore[arg-type]
        )
    except UninstallError as exc:
        console.print(f"[yellow]Uninstall incomplete:[/yellow] {exc}")
        if exc.remaining_paths:
            console.print("[yellow]Eve left these files in place for manual review:[/yellow]")
            for path in exc.remaining_paths:
                console.print(f"- {path}")
            console.print(
                "[yellow]These files may still contain Eve configuration or credentials. "
                "Review them before considering Eve fully removed.[/yellow]"
            )
        console.print(
            "[yellow]Stored credentials for the selected tool(s) were still removed.[/yellow]"
        )
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Uninstalled.[/green] Transaction: [bold]{result.transaction_id}[/bold] "
        f"Removed {result.removed_actions} action(s)."
    )


@app.command(
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
    }
)
def run(
    ctx: typer.Context,
    tool: str = typer.Option(..., "--tool", "-t"),
) -> None:
    """Run a supported tool with Eve-managed runtime credentials injected."""
    if tool != "codex-cli":
        raise typer.BadParameter("Only codex-cli is supported by eve run right now.")
    config = resolve_config()
    session, _source = _load_active_oauth_session(config, tool)
    if session is None:
        console.print(
            "[yellow]No stored OAuth session for codex-cli. "
            "Run `eve auth login --tool codex-cli --auth-mode oauth` first.[/yellow]"
        )
        raise typer.Exit(1)
    codex_path = shutil.which("codex")
    if not codex_path:
        console.print("[yellow]codex binary not found on PATH.[/yellow]")
        raise typer.Exit(1)
    env = os.environ.copy()
    env[CODEX_BEARER_TOKEN_ENV_VAR] = session.access_token
    result = subprocess.run([codex_path, *ctx.args], env=env, check=False)
    raise typer.Exit(result.returncode)


trust_app = typer.Typer(name="trust", help="Manage Eve installer trust state.")
app.add_typer(trust_app, name="trust")


@trust_app.command("reinit")
def trust_reinit(
    yes: bool = typer.Option(False, "--yes", help="Confirm trust-state reinitialization"),
) -> None:
    """Reinitialize installer trust state after keyring/disk desync or local corruption."""
    config = resolve_config()
    if not yes and not typer.confirm(
        "Reinitialize Eve trust state? This clears manifests, backups, and transaction state."
    ):
        raise typer.Exit(1)
    # Recovery must clear previously persisted fallback trust state even if fallback is disabled.
    reinitialize_trust_state(config.state_dir, allow_file_fallback=True)
    console.print("[green]Reinitialized[/green] installer trust state.")


auth_app = typer.Typer(name="auth", help="Manage Eve client credentials.")
app.add_typer(auth_app, name="auth")


@auth_app.command("login")
def auth_login(
    tool: Optional[str] = typer.Option(None, "--tool", "-t"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    bearer_token: Optional[str] = typer.Option(None, "--bearer-token"),
    auth_mode: Optional[str] = typer.Option(None, "--auth-mode"),
    open_browser: bool = typer.Option(True, "--open-browser/--no-browser"),
    allow_file_fallback: bool = typer.Option(False, "--allow-file-fallback"),
) -> None:
    """Store tool auth material or launch the hosted OAuth flow."""
    config = resolve_config()
    detected_tool, _tool_plan, selected_auth_mode = _select_auth_candidate(
        config,
        requested_tool=tool,
        auth_mode=auth_mode,
    )
    tool_name = detected_tool.name
    if selected_auth_mode == "oauth":
        if _supports_device_flow(tool_name):
            config = _apply_requested_file_fallback(config, allow_file_fallback)
        if bearer_token:
            try:
                record = _credential_store(config).set_bearer_token(tool_name, bearer_token)  # type: ignore[arg-type]
            except CredentialStoreUnavailableError as exc:
                config = _recover_from_unavailable_credential_store(config, tool_name, exc)
                record = _credential_store(config).set_bearer_token(tool_name, bearer_token)  # type: ignore[arg-type]
            console.print(
                f"[green]Stored[/green] {tool_name} credential "
                f"via {record.source}: {record.value_masked}"
            )
            return
        if _supports_device_flow(tool_name):
            try:
                _login_via_device_flow(config, tool_name, open_browser=open_browser)
            except CredentialStoreUnavailableError as exc:
                config = _recover_from_unavailable_credential_store(config, tool_name, exc)
                _login_via_device_flow(config, tool_name, open_browser=open_browser)
            console.print(f"[green]Stored[/green] {tool_name} OAuth session")
            return
        console.print(Panel("[bold]Eve Auth[/bold]", style="green"))
        console.print(f"Tool: [bold]{tool_name}[/bold]")
        console.print(f"Auth mode: [bold]{selected_auth_mode}[/bold]")
        _print_oauth_guidance(config, tool_name, open_browser=open_browser)
        return
    config = _apply_requested_file_fallback(config, allow_file_fallback)
    if not api_key:
        if not _stdin_is_tty():
            raise typer.BadParameter("--api-key is required in non-interactive mode.")
        api_key = typer.prompt("Eve API key", hide_input=True)
    try:
        record = _credential_store(config).set_api_key(tool_name, api_key)  # type: ignore[arg-type]
    except CredentialStoreUnavailableError as exc:
        config = _recover_from_unavailable_credential_store(config, tool_name, exc)
        record = _credential_store(config).set_api_key(tool_name, api_key)  # type: ignore[arg-type]
    console.print(
        f"[green]Stored[/green] {tool_name} credential via {record.source}: {record.value_masked}"
    )


@auth_app.command("show")
def auth_show(
    tool: str = typer.Option(..., "--tool", "-t"),
    auth_mode: str = typer.Option("api-key", "--auth-mode"),
) -> None:
    """Show credential status for a tool."""
    if tool not in ALL_TOOLS:
        raise typer.BadParameter("Unsupported tool")
    config = resolve_config()
    try:
        if auth_mode == "oauth":
            value, source = _credential_store(config).get_bearer_token(tool)  # type: ignore[arg-type]
        else:
            value, source = _credential_store(config).get_api_key(tool)  # type: ignore[arg-type]
    except CredentialStoreUnavailableError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc
    if not value:
        console.print(f"[yellow]No stored credential for {tool}[/yellow]")
        raise typer.Exit(1)
    masked = f"{value[:2]}****{value[-2:]}" if len(value) > 4 else "****"
    console.print(f"{tool}: {masked} ({source})")


@auth_app.command("logout")
def auth_logout(
    tool: str = typer.Option(..., "--tool", "-t"),
    auth_mode: str = typer.Option("api-key", "--auth-mode"),
) -> None:
    """Delete a stored credential for a tool."""
    if tool not in ALL_TOOLS:
        raise typer.BadParameter("Unsupported tool")
    config = resolve_config()
    if auth_mode == "oauth":
        _credential_store(config).delete_bearer_token(tool)  # type: ignore[arg-type]
    else:
        _credential_store(config).delete_api_key(tool)  # type: ignore[arg-type]
    console.print(f"[green]Removed[/green] stored credential for {tool}")


@app.command()
def rollback(transaction_id: str = typer.Option(..., "--transaction-id")) -> None:
    """Rollback a previously applied transaction."""
    config = resolve_config()
    result = rollback_transaction(config, transaction_id)
    console.print(
        f"[green]Rolled back[/green] {result.restored_actions} action(s) from {transaction_id}"
    )


def main() -> None:
    app()
