"""Shared Eve client installer models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

ToolName = Literal["claude-code", "claude-desktop", "gemini-cli", "codex-cli"]
FeatureGateName = Literal["claude-desktop"]
ConfigFormat = Literal["json", "toml", "markdown", "text"]
PromptScope = Literal["global", "project"]
ActionType = Literal[
    "write_config",
    "write_hooks_config",
    "create_companion_file",
    "auth_setup",
]
AuthMode = Literal["api-key", "oauth"]
ActionScope = Literal["global-config", "project", "state"]


@dataclass(slots=True)
class DetectedTool:
    name: ToolName
    config_path: Path
    config_format: Literal["json", "toml"]
    supports_hooks: bool
    binary_found: bool
    config_exists: bool
    hooks_path: Path | None = None
    project_scoped: bool = False
    feature_flag_required: bool = False
    feature_gate: FeatureGateName | None = None
    minimum_supported_version: str | None = None


@dataclass(slots=True)
class PlannedAction:
    action_id: str
    tool: ToolName
    action_type: ActionType
    path: Path | None
    summary: str
    scope: ActionScope
    requires_backup: bool
    requires_confirmation: bool
    idempotent: bool
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["path"] = str(self.path) if self.path else None
        return data


@dataclass(slots=True)
class ToolPlan:
    tool: ToolName
    auth_mode: AuthMode
    supported: bool
    supported_auth_modes: tuple[AuthMode, ...] = ("api-key",)
    reason: str | None = None
    actions: list[PlannedAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "auth_mode": self.auth_mode,
            "supported_auth_modes": list(self.supported_auth_modes),
            "supported": self.supported,
            "reason": self.reason,
            "actions": [action.to_dict() for action in self.actions],
        }


@dataclass(slots=True)
class InstallPlan:
    mcp_base_url: str
    environment: str
    transaction_scope: str
    tool_plans: list[ToolPlan]

    def to_dict(self) -> dict[str, object]:
        return {
            "mcp_base_url": self.mcp_base_url,
            "environment": self.environment,
            "transaction_scope": self.transaction_scope,
            "tool_plans": [tool_plan.to_dict() for tool_plan in self.tool_plans],
        }


@dataclass(slots=True)
class ApplyResult:
    transaction_id: str
    applied_actions: int
    applied_tools: list[ToolName]

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "applied_actions": self.applied_actions,
            "applied_tools": self.applied_tools,
        }


@dataclass(slots=True)
class RollbackResult:
    transaction_id: str
    restored_actions: int

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "restored_actions": self.restored_actions,
        }


@dataclass(slots=True)
class UninstallResult:
    transaction_id: str
    removed_actions: int
    removed_tools: list[ToolName]

    def to_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "removed_actions": self.removed_actions,
            "removed_tools": self.removed_tools,
        }


@dataclass(slots=True)
class ManifestRecord:
    transaction_id: str
    tool: ToolName
    action_id: str
    action_type: ActionType
    path: str
    backup_path: str | None
    sha256: str | None
    backup_sha256: str | None
    scope: ActionScope
    environment: str
