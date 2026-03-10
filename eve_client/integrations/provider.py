"""Base provider contract for Eve client tool integrations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from eve_client.models import AuthMode, DetectedTool, PlannedAction, PromptScope, ToolName, ToolPlan


def planned_action(
    *,
    tool: ToolName,
    action_type: str,
    path: Path | None,
    summary: str,
    scope: str,
    requires_backup: bool,
    requires_confirmation: bool,
    idempotent: bool,
    details: dict[str, str] | None = None,
) -> PlannedAction:
    return PlannedAction(
        action_id=str(uuid4()),
        tool=tool,
        action_type=action_type,  # type: ignore[arg-type]
        path=path,
        summary=summary,
        scope=scope,  # type: ignore[arg-type]
        requires_backup=requires_backup,
        requires_confirmation=requires_confirmation,
        idempotent=idempotent,
        details=details or {},
    )


@dataclass(slots=True)
class ToolProvider:
    tool: ToolName
    auth_mode: AuthMode
    supported_auth_modes: tuple[AuthMode, ...] = ("api-key",)
    supported: bool = True
    support_reason: str | None = None

    def build_plan(
        self,
        detected: DetectedTool,
        mcp_base_url: str,
        *,
        auth_mode: AuthMode | None = None,
        prompt_scope: PromptScope | None = None,
        hooks_enabled: bool | None = None,
    ) -> ToolPlan:
        raise NotImplementedError
