"""Detect supported tools using documented locations only."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from eve_client.models import DetectedTool, ToolName

ALL_TOOLS: list[ToolName] = ["claude-code", "claude-desktop", "gemini-cli", "codex-cli"]


def _home() -> Path:
    return Path.home()


def _detect_claude_code(project_scoped: bool = False) -> DetectedTool:
    binary = shutil.which("claude")
    config_path = Path.cwd() / ".mcp.json" if project_scoped else _home() / ".claude.json"
    hooks_path = None if project_scoped else _home() / ".claude" / "settings.json"
    return DetectedTool(
        name="claude-code",
        config_path=config_path,
        config_format="json",
        supports_hooks=True,
        binary_found=binary is not None,
        config_exists=config_path.exists(),
        hooks_path=hooks_path,
        project_scoped=project_scoped,
    )


def _detect_claude_desktop(enabled: bool) -> DetectedTool:
    system = platform.system()
    if system == "Darwin":
        config_path = (
            _home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
    elif system == "Windows":
        config_path = _home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    else:
        config_path = _home() / ".config" / "claude" / "claude_desktop_config.json"
    return DetectedTool(
        name="claude-desktop",
        config_path=config_path,
        config_format="json",
        supports_hooks=False,
        binary_found=config_path.parent.exists(),
        config_exists=config_path.exists(),
        feature_flag_required=True,
        feature_gate="claude-desktop",
    )


def _detect_gemini_cli() -> DetectedTool:
    binary = shutil.which("gemini")
    config_path = _home() / ".gemini" / "settings.json"
    return DetectedTool(
        name="gemini-cli",
        config_path=config_path,
        config_format="json",
        supports_hooks=True,
        binary_found=binary is not None,
        config_exists=config_path.exists(),
    )


def _detect_codex_cli() -> DetectedTool:
    binary = shutil.which("codex")
    config_path = _home() / ".codex" / "config.toml"
    return DetectedTool(
        name="codex-cli",
        config_path=config_path,
        config_format="toml",
        supports_hooks=False,
        binary_found=binary is not None,
        config_exists=config_path.exists(),
    )


def detect_tools(
    only: list[ToolName] | None = None,
    project_scoped: bool = False,
    enable_claude_desktop: bool = False,
) -> list[DetectedTool]:
    targets = only or ALL_TOOLS
    detected: list[DetectedTool] = []
    for tool in targets:
        if tool == "claude-code":
            detected.append(_detect_claude_code(project_scoped=project_scoped))
        elif tool == "claude-desktop":
            detected.append(_detect_claude_desktop(enable_claude_desktop))
        elif tool == "gemini-cli":
            detected.append(_detect_gemini_cli())
        elif tool == "codex-cli":
            detected.append(_detect_codex_cli())
    return detected
