"""Central policy for which local files Eve client may touch."""

from __future__ import annotations

from pathlib import Path

from eve_client.config import ResolvedConfig
from eve_client.models import PlannedAction


class OperationPolicyError(RuntimeError):
    """Raised when an action violates the central installer policy."""


def _matches_path_suffix(path: Path, parts: tuple[str, ...]) -> bool:
    return tuple(path.parts[-len(parts):]) == parts if len(path.parts) >= len(parts) else False


def _is_allowed_config_path(config: ResolvedConfig, tool: str, path: Path) -> bool:
    resolved = path.resolve(strict=False)
    if tool == "claude-code":
        return (
            _matches_path_suffix(resolved, (".claude", "settings.json"))
            or resolved.name == ".claude.json"
            or resolved == (config.project_root / ".mcp.json").resolve(strict=False)
        )
    if tool == "gemini-cli":
        return _matches_path_suffix(resolved, (".gemini", "settings.json"))
    if tool == "codex-cli":
        return _matches_path_suffix(resolved, (".codex", "config.toml"))
    if tool == "claude-desktop":
        return resolved.name == "claude_desktop_config.json"
    return False


def _allowed_companion_path(config: ResolvedConfig, tool: str) -> Path:
    if tool == "claude-code":
        return (config.config_dir.parent / ".claude" / "CLAUDE.md").resolve(strict=False)
    if tool == "codex-cli":
        return (config.project_root / "AGENTS.eve.md").resolve(strict=False)
    if tool == "gemini-cli":
        return (config.project_root / "GEMINI.md").resolve(strict=False)
    raise OperationPolicyError(f"No companion-file policy for tool {tool}")


def validate_action_policy(action: PlannedAction, config: ResolvedConfig) -> None:
    if action.action_type == "auth_setup":
        if action.path is not None or action.scope != "state":
            raise OperationPolicyError(f"{action.action_id} auth_setup must be state-scoped with no path")
        return

    if action.path is None:
        raise OperationPolicyError(f"{action.action_id} has no path")

    resolved_path = action.path.resolve(strict=False)
    if action.action_type in {"write_config", "write_hooks_config"}:
        if action.scope != "global-config" or not _is_allowed_config_path(config, action.tool, resolved_path):
            raise OperationPolicyError(f"{action.tool} config path not allowed by policy: {resolved_path}")
        return

    if action.action_type == "create_companion_file":
        if action.tool == "claude-code":
            is_allowed_global = action.scope == "global-config" and _matches_path_suffix(resolved_path, (".claude", "CLAUDE.md"))
            is_allowed_project = action.scope == "project" and resolved_path == (config.project_root / "CLAUDE.md").resolve(strict=False)
            if not (is_allowed_global or is_allowed_project):
                raise OperationPolicyError(f"{action.tool} companion path not allowed by policy: {resolved_path}")
            return
        if action.tool == "gemini-cli":
            is_allowed_global = action.scope == "global-config" and _matches_path_suffix(resolved_path, (".gemini", "GEMINI.md"))
            is_allowed_project = action.scope == "project" and resolved_path == (config.project_root / "GEMINI.md").resolve(strict=False)
            if not (is_allowed_global or is_allowed_project):
                raise OperationPolicyError(f"{action.tool} companion path not allowed by policy: {resolved_path}")
            return
        allowed = _allowed_companion_path(config, action.tool)
        if action.scope != "project" or resolved_path != allowed:
            raise OperationPolicyError(f"{action.tool} companion path not allowed by policy: {resolved_path}")
        return

    raise OperationPolicyError(f"Unsupported action type for policy: {action.action_type}")
