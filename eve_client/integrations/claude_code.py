from __future__ import annotations

import sys
from pathlib import Path

from eve_client.integrations.provider import ToolProvider, planned_action
from eve_client.models import DetectedTool, ToolPlan


class ClaudeCodeProvider(ToolProvider):
    def __init__(self) -> None:
        super().__init__(
            tool="claude-code",
            auth_mode="api-key",
            supported_auth_modes=("api-key", "oauth"),
        )

    def build_plan(
        self,
        detected: DetectedTool,
        mcp_base_url: str,
        *,
        auth_mode=None,
        prompt_scope=None,
        hooks_enabled=None,
    ) -> ToolPlan:
        selected_auth_mode = auth_mode or self.auth_mode
        hook_command = (
            str(Path(sys.argv[0]).resolve().with_name("eve-claude-hook"))
            if Path(sys.argv[0]).name.startswith("eve")
            else f"{sys.executable} -m eve_client.claude_hooks"
        )
        companion_path = (
            detected.config_path.parent / "CLAUDE.md"
            if detected.project_scoped
            else (detected.hooks_path.parent / "CLAUDE.md" if detected.hooks_path else detected.config_path.parent / "CLAUDE.md")
        )
        return ToolPlan(
            tool=self.tool,
            auth_mode=selected_auth_mode,
            supported_auth_modes=self.supported_auth_modes,
            supported=True,
            actions=[
                planned_action(
                    tool=self.tool,
                    action_type="write_config",
                    path=detected.config_path,
                    summary="Add Eve MCP server entry to Claude Code user config",
                    scope="global-config",
                    requires_backup=True,
                    requires_confirmation=True,
                    idempotent=True,
                    details={
                        "config_format": "json",
                        "mcp_base_url": mcp_base_url,
                    },
                ),
                planned_action(
                    tool=self.tool,
                    action_type="write_hooks_config",
                    path=detected.hooks_path,
                    summary="Add Eve hooks to Claude Code settings",
                    scope="global-config",
                    requires_backup=True,
                    requires_confirmation=True,
                    idempotent=True,
                    details={
                        "config_format": "json",
                        "mcp_base_url": mcp_base_url,
                        "hook_command": hook_command,
                    },
                ),
                planned_action(
                    tool=self.tool,
                    action_type="create_companion_file",
                    path=companion_path,
                    summary="Create or update Eve-managed Claude instructions in active CLAUDE.md",
                    scope="project" if detected.project_scoped else "global-config",
                    requires_backup=True,
                    requires_confirmation=True,
                    idempotent=True,
                    details={"mcp_base_url": mcp_base_url},
                ),
                planned_action(
                    tool=self.tool,
                    action_type="auth_setup",
                    path=None,
                    summary=(
                        "Prepare Claude Code for Eve OAuth; Claude Code completes OAuth natively on first use"
                        if selected_auth_mode == "oauth"
                        else "Store Eve-issued API key and wire Claude Code to it"
                    ),
                    scope="state",
                    requires_backup=False,
                    requires_confirmation=True,
                    idempotent=True,
                    details={"auth_mode": selected_auth_mode},
                ),
            ],
        )
