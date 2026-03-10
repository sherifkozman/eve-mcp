from __future__ import annotations

import sys
from pathlib import Path

from eve_client.integrations.provider import ToolProvider, planned_action
from eve_client.merge import is_eve_companion_file
from eve_client.models import DetectedTool, PromptScope, ToolPlan


class GeminiCliProvider(ToolProvider):
    def __init__(self) -> None:
        super().__init__(
            tool="gemini-cli",
            auth_mode="api-key",
            supported_auth_modes=("api-key", "oauth"),
        )

    def build_plan(
        self,
        detected: DetectedTool,
        mcp_base_url: str,
        *,
        auth_mode=None,
        prompt_scope: PromptScope | None = None,
        hooks_enabled: bool | None = None,
    ) -> ToolPlan:
        selected_auth_mode = auth_mode or self.auth_mode
        hook_command = (
            str(Path(sys.argv[0]).resolve().with_name("eve-gemini-hook"))
            if Path(sys.argv[0]).name.startswith("eve")
            else f"{sys.executable} -m eve_client.gemini_hooks"
        )
        project_companion = Path.cwd() / "GEMINI.md"
        if prompt_scope:
            selected_prompt_scope: PromptScope = prompt_scope
        elif is_eve_companion_file(project_companion, self.tool):
            selected_prompt_scope = "project"
        else:
            selected_prompt_scope = "global"
        gemini_root = detected.config_path.parent
        companion_path = (
            Path.cwd() / "GEMINI.md"
            if selected_prompt_scope == "project"
            else gemini_root / "GEMINI.md"
        )
        action_scope = "project" if selected_prompt_scope == "project" else "global-config"
        install_hooks = True if hooks_enabled is None else hooks_enabled
        actions = [
            planned_action(
                tool=self.tool,
                action_type="write_config",
                path=detected.config_path,
                summary="Add Eve MCP server entry to Gemini CLI settings",
                scope="global-config",
                requires_backup=True,
                requires_confirmation=True,
                idempotent=True,
                details={"config_format": "json", "mcp_base_url": mcp_base_url},
            ),
        ]
        if install_hooks:
            actions.append(
                planned_action(
                    tool=self.tool,
                    action_type="write_hooks_config",
                    path=detected.config_path,
                    summary="Add Eve hooks to Gemini CLI settings",
                    scope="global-config",
                    requires_backup=True,
                    requires_confirmation=True,
                    idempotent=True,
                    details={
                        "config_format": "json",
                        "mcp_base_url": mcp_base_url,
                        "hook_command": hook_command,
                    },
                )
            )
        actions.extend(
            [
                planned_action(
                    tool=self.tool,
                    action_type="create_companion_file",
                    path=companion_path,
                    summary=(
                        "Create or update Eve-managed Gemini instructions in project GEMINI.md"
                        if selected_prompt_scope == "project"
                        else "Create or update Eve-managed Gemini instructions in global GEMINI.md"
                    ),
                    scope=action_scope,
                    requires_backup=True,
                    requires_confirmation=True,
                    idempotent=True,
                    details={
                        "mcp_base_url": mcp_base_url,
                        "prompt_scope": selected_prompt_scope,
                    },
                ),
                planned_action(
                    tool=self.tool,
                    action_type="auth_setup",
                    path=None,
                    summary=(
                        "Prepare Gemini CLI for Eve OAuth; Gemini completes OAuth natively on first use"
                        if selected_auth_mode == "oauth"
                        else "Store Eve-issued API key for Gemini CLI integration"
                    ),
                    scope="state",
                    requires_backup=False,
                    requires_confirmation=True,
                    idempotent=True,
                    details={"auth_mode": selected_auth_mode},
                ),
            ]
        )
        return ToolPlan(
            tool=self.tool,
            auth_mode=selected_auth_mode,
            supported_auth_modes=self.supported_auth_modes,
            supported=True,
            actions=actions,
        )
