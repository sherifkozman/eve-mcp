"""Local and remote verification for Eve client tool integrations."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from eve_client.auth.base import CredentialStore, CredentialStoreUnavailableError
from eve_client.config import ResolvedConfig
from eve_client.integrations import get_adapter
from eve_client.merge import (
    has_eve_claude_hooks,
    has_eve_gemini_hooks,
    has_eve_json_entry,
    has_eve_toml_entry,
    is_eve_companion_file,
    source_agent_header,
)
from eve_client.models import DetectedTool, ToolName
from eve_client.plan import feature_enabled
from eve_client.tool_state import classify_codex_disabled_state, classify_codex_verify_state

_SECRET_RE = re.compile(r"[A-Za-z0-9_\-]{12,}")


def _sanitize_error(value: object) -> str:
    return _SECRET_RE.sub("****", str(value))


def _tls_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and not parsed.username and not parsed.password


def _protected_resource_metadata_url(url: str) -> str:
    parsed = urlparse(url.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[: -len("/mcp")]
    root = urlunparse((parsed.scheme, parsed.netloc, path or "", "", "", ""))
    return root.rstrip("/") + "/.well-known/oauth-protected-resource"


def _parse_response(body: str) -> dict[str, Any] | None:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data_str = line[5:].strip()
            if not data_str:
                continue
            try:
                return json.loads(data_str)
            except json.JSONDecodeError:
                continue
    return None


def _request(
    method: str, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - installer validates destination separately
        parsed = _parse_response(response.read().decode("utf-8"))
        if parsed is None:
            raise RuntimeError(f"{method} returned an unreadable response")
        return parsed


def verify_connectivity(
    url: str,
    secret: str | None,
    tool: ToolName,
    *,
    auth_mode: str = "api-key",
    timeout: float = 10.0,
) -> dict[str, Any]:
    if not _tls_allowed(url):
        return {"success": False, "error": "Refusing insecure MCP verification endpoint"}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Source-Agent": source_agent_header(tool),
    }
    if auth_mode == "oauth":
        if not secret:
            metadata_url = _protected_resource_metadata_url(url)
            try:
                request = urllib.request.Request(
                    metadata_url, headers={"Accept": "application/json"}, method="GET"
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                    parsed = json.loads(response.read().decode("utf-8"))
                auth_servers = parsed.get("authorization_servers") or []
                if parsed.get("resource") and auth_servers:
                    return {
                        "success": True,
                        "resource": parsed.get("resource"),
                        "authorization_servers": auth_servers,
                        "mode": "oauth-metadata",
                    }
                return {
                    "success": False,
                    "error": "Protected resource metadata missing OAuth details",
                }
            except urllib.error.HTTPError as exc:
                return {
                    "success": False,
                    "error": f"HTTP {exc.code}: {_sanitize_error(exc.reason)}",
                }
            except urllib.error.URLError as exc:
                return {
                    "success": False,
                    "error": f"Connection failed: {_sanitize_error(exc.reason)}",
                }
            except Exception as exc:  # noqa: BLE001
                return {"success": False, "error": _sanitize_error(exc)}
        headers["Authorization"] = f"Bearer {secret}"
    else:
        headers["X-API-Key"] = secret
    try:
        init = _request(
            "initialize",
            url,
            headers,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "eve-client", "version": "0.1.0"},
                },
            },
            timeout,
        )
        if "error" in init:
            return {"success": False, "error": init["error"].get("message", "initialize failed")}
        tools = _request(
            "tools/list",
            url,
            headers,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout,
        )
        if "error" in tools:
            return {"success": False, "error": tools["error"].get("message", "tools/list failed")}
        return {
            "success": True,
            "server_info": init.get("result", {}).get("serverInfo"),
            "tool_names": [
                item.get("name", "unknown") for item in tools.get("result", {}).get("tools", [])
            ],
        }
    except urllib.error.HTTPError as exc:
        return {"success": False, "error": f"HTTP {exc.code}: {_sanitize_error(exc.reason)}"}
    except urllib.error.URLError as exc:
        return {"success": False, "error": f"Connection failed: {_sanitize_error(exc.reason)}"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": _sanitize_error(exc)}


def _companion_path_for_tool(detected: DetectedTool, config: ResolvedConfig) -> Path | None:
    if detected.name == "gemini-cli":
        project_companion = config.project_root / "GEMINI.md"
        if is_eve_companion_file(project_companion, detected.name):
            return project_companion
        global_companion = detected.config_path.parent / "GEMINI.md"
        if is_eve_companion_file(global_companion, detected.name):
            return global_companion
        return global_companion
    provider = get_adapter(detected.name)
    plan = provider.build_plan(detected, config.mcp_base_url)
    for action in plan.actions:
        if action.action_type == "create_companion_file":
            return action.path
    return None


def _has_eve_config_entry(detected: DetectedTool) -> bool:
    if detected.config_format == "json":
        return has_eve_json_entry(detected.config_path)
    if detected.config_format == "toml":
        return has_eve_toml_entry(detected.config_path)
    return False


def verify_tools(
    detected_tools: list[DetectedTool],
    config: ResolvedConfig,
    credential_store: CredentialStore,
    *,
    auth_overrides: dict[ToolName, str] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    auth_overrides = auth_overrides or {}
    for detected in detected_tools:
        companion_path = _companion_path_for_tool(detected, config)
        eve_entry = False
        credential = None
        source = None
        result: dict[str, Any] = {
            "tool": detected.name,
            "feature_enabled": feature_enabled(detected, config),
            "binary_found": detected.binary_found,
            "config_path": str(detected.config_path),
            "config_exists": detected.config_path.exists(),
            "eve_configured": eve_entry,
            "hooks_present": (
                has_eve_claude_hooks(detected.hooks_path)
                if detected.name == "claude-code" and detected.hooks_path is not None
                else has_eve_gemini_hooks(detected.config_path)
                if detected.name == "gemini-cli"
                else False
            ),
            "companion_path": str(companion_path) if companion_path else None,
            "companion_present": bool(
                companion_path and is_eve_companion_file(companion_path, detected.name)
            ),
            "credential_source": source,
            "connectivity": {"success": False, "error": "not checked"},
        }
        codex_disabled_state = (
            classify_codex_disabled_state(config) if detected.name == "codex-cli" else None
        )
        if codex_disabled_state is not None:
            result["state"] = codex_disabled_state
            result["connectivity"] = {"success": False, "error": "feature disabled"}
            results.append(result)
            continue
        eve_entry = _has_eve_config_entry(detected)
        result["eve_configured"] = eve_entry
        auth_mode = auth_overrides.get(detected.name, "api-key")
        try:
            if auth_mode == "oauth":
                credential, source = credential_store.get_bearer_token(detected.name)
            else:
                credential, source = credential_store.get_api_key(detected.name)
        except CredentialStoreUnavailableError:
            credential, source = None, "unavailable"
        result["credential_source"] = source
        if result["feature_enabled"] and eve_entry:
            if auth_mode == "oauth":
                if credential or detected.name != "codex-cli":
                    result["connectivity"] = verify_connectivity(
                        config.mcp_base_url,
                        credential,
                        detected.name,
                        auth_mode=auth_mode,
                    )
                else:
                    result["connectivity"] = {"success": False, "error": "credential missing"}
            elif credential:
                result["connectivity"] = verify_connectivity(
                    config.mcp_base_url,
                    credential,
                    detected.name,
                    auth_mode=auth_mode,
                )
            else:
                result["connectivity"] = {"success": False, "error": "credential missing"}
        elif not result["feature_enabled"]:
            result["connectivity"] = {"success": False, "error": "feature disabled"}
        elif auth_mode != "oauth" and not credential:
            result["connectivity"] = {"success": False, "error": "credential missing"}
        elif not eve_entry:
            result["connectivity"] = {"success": False, "error": "Eve config entry missing"}
        if detected.name == "codex-cli":
            result["state"] = classify_codex_verify_state(
                config,
                detected,
                auth_mode=auth_mode,
                credential_present=bool(credential),
                eve_configured=eve_entry,
                connectivity_success=result["connectivity"].get("success"),
            )
        results.append(result)
    return results
