from __future__ import annotations

from eve_client.integrations.provider import ToolProvider
from eve_client.models import DetectedTool, ToolPlan


class ClaudeDesktopProvider(ToolProvider):
    def __init__(self) -> None:
        super().__init__(
            tool="claude-desktop",
            auth_mode="oauth",
            supported=False,
            support_reason=(
                "Hosted Claude Desktop onboarding uses Settings > Connectors; "
                "local installer support is deferred pending a separate DXT/connector slice"
            ),
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
        return ToolPlan(
            tool=self.tool,
            auth_mode=self.auth_mode,
            supported_auth_modes=(self.auth_mode,),
            supported=False,
            reason=self.support_reason,
            actions=[],
        )
