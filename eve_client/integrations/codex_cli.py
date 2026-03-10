from __future__ import annotations

from pathlib import Path

from eve_client.integrations.provider import ToolProvider, planned_action
from eve_client.models import DetectedTool, ToolPlan


class CodexCliProvider(ToolProvider):
    def __init__(self) -> None:
        super().__init__(
            tool="codex-cli",
            auth_mode="oauth",
            supported_auth_modes=("oauth", "api-key"),
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
        reason = (
            "Codex CLI integration via remote MCP config and Eve AGENTS companion; "
            "OAuth is the preferred path and API keys remain a compatibility fallback"
        )
        actions = [
            planned_action(
                tool=self.tool,
                action_type="write_config",
                path=detected.config_path,
                summary=(
                    "Add Eve MCP server entry to Codex CLI config for native OAuth discovery"
                    if selected_auth_mode == "oauth"
                    else "Add Eve MCP server entry to Codex CLI config"
                ),
                scope="global-config",
                requires_backup=True,
                requires_confirmation=True,
                idempotent=True,
                details={
                    "config_format": "toml",
                    "mcp_base_url": mcp_base_url,
                    "auth_mode": selected_auth_mode,
                },
            ),
            planned_action(
                tool=self.tool,
                action_type="create_companion_file",
                path=Path.cwd() / "AGENTS.md",
                summary="Create or update Eve-managed instructions in active AGENTS.md (prompt seeding only; no Codex hooks installed)",
                scope="project",
                requires_backup=True,
                requires_confirmation=True,
                idempotent=True,
                details={"mcp_base_url": mcp_base_url},
            ),
        ]
        if selected_auth_mode == "api-key":
            actions.append(
                planned_action(
                    tool=self.tool,
                    action_type="auth_setup",
                    path=None,
                    summary="Store Eve-issued API key for Codex CLI integration",
                    scope="state",
                    requires_backup=False,
                    requires_confirmation=True,
                    idempotent=True,
                    details={"auth_mode": selected_auth_mode},
                )
            )
        return ToolPlan(
            tool=self.tool,
            auth_mode=selected_auth_mode,
            supported_auth_modes=self.supported_auth_modes,
            supported=True,
            reason=reason,
            actions=actions,
        )
