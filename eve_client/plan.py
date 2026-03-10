"""Install plan generation."""

from __future__ import annotations

from eve_client.config import ResolvedConfig
from eve_client.integrations import get_adapter
from eve_client.models import AuthMode, DetectedTool, InstallPlan, PromptScope, ToolName


def feature_enabled_for_gate(feature_gate: str | None, config: ResolvedConfig) -> bool:
    if feature_gate == "claude-desktop":
        return config.feature_claude_desktop
    return True


def feature_enabled_for_tool(tool: ToolName, config: ResolvedConfig) -> bool:
    if tool == "claude-desktop":
        return config.feature_claude_desktop
    if tool == "codex-cli":
        return config.codex_enabled
    return True


def feature_enabled(detected: DetectedTool, config: ResolvedConfig) -> bool:
    if not detected.feature_flag_required:
        return feature_enabled_for_tool(detected.name, config)
    return feature_enabled_for_gate(detected.feature_gate, config)


def build_install_plan(
    detected_tools: list[DetectedTool],
    config: ResolvedConfig,
    *,
    auth_overrides: dict[ToolName, AuthMode] | None = None,
    prompt_scope_overrides: dict[ToolName, PromptScope] | None = None,
    hook_overrides: dict[ToolName, bool] | None = None,
) -> InstallPlan:
    tool_plans = []
    auth_overrides = auth_overrides or {}
    prompt_scope_overrides = prompt_scope_overrides or {}
    hook_overrides = hook_overrides or {}
    for detected in detected_tools:
        adapter = get_adapter(detected.name)
        tool_plan = adapter.build_plan(
            detected,
            config.mcp_base_url,
            auth_mode=auth_overrides.get(detected.name),
            prompt_scope=prompt_scope_overrides.get(detected.name),
            hooks_enabled=hook_overrides.get(detected.name),
        )
        if not feature_enabled(detected, config):
            tool_plan.supported = False
            if detected.name == "codex-cli":
                tool_plan.reason = "Built-in but disabled by default until explicitly enabled"
            else:
                tool_plan.reason = "Feature-flagged and disabled by default"
            tool_plan.actions = []
        tool_plans.append(tool_plan)
    return InstallPlan(
        mcp_base_url=config.mcp_base_url,
        environment=config.environment,
        transaction_scope="per-tool-with-session-grouping",
        tool_plans=tool_plans,
    )
