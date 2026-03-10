from __future__ import annotations

from pathlib import Path

import pytest
from eve_client.config import ResolvedConfig
from eve_client.models import PlannedAction
from eve_client.operations import OperationContext, OperationError, execute_operation


class _CredentialStore:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.stored: dict[str, str] = {}

    def set_api_key(self, tool, api_key):
        self.stored[tool] = api_key
        self.api_key = api_key
        return None

    def get_api_key(self, tool):
        return self.api_key, ("memory" if self.api_key else None)

    def delete_api_key(self, tool):
        self.stored.pop(tool, None)


def _config(tmp_path: Path) -> ResolvedConfig:
    return ResolvedConfig(
        config_dir=tmp_path / ".eve-config",
        config_path=tmp_path / ".eve-config" / "config.json",
        state_dir=tmp_path / ".eve-state",
        project_root=tmp_path,
        mcp_base_url="https://mcp.evemem.com",
        mcp_server_name="eve-memory",
        environment="production",
        feature_claude_desktop=False,
        codex_enabled=True,
        codex_source="config",
        allow_file_secret_fallback=True,
    )


def test_execute_auth_setup_requires_api_key(tmp_path: Path) -> None:
    action = PlannedAction(
        action_id="a1",
        tool="claude-code",
        action_type="auth_setup",
        path=None,
        summary="store key",
        scope="state",
        requires_backup=False,
        requires_confirmation=True,
        idempotent=True,
    )
    with pytest.raises(OperationError):
        execute_operation(
            OperationContext(
                config=_config(tmp_path),
                credentials=_CredentialStore(),
                action=action,
                api_key=None,
            )
        )


def test_execute_auth_setup_reuses_stored_key_when_api_key_not_provided(tmp_path: Path) -> None:
    action = PlannedAction(
        action_id="a1b",
        tool="claude-code",
        action_type="auth_setup",
        path=None,
        summary="store key",
        scope="state",
        requires_backup=False,
        requires_confirmation=True,
        idempotent=True,
    )
    store = _CredentialStore(api_key="eve-secret")
    execute_operation(
        OperationContext(config=_config(tmp_path), credentials=store, action=action, api_key=None)
    )
    assert store.stored["claude-code"] == "eve-secret"


def test_execute_write_config_uses_stored_key(tmp_path: Path) -> None:
    config_path = tmp_path / ".claude" / "settings.json"
    action = PlannedAction(
        action_id="a2",
        tool="claude-code",
        action_type="write_config",
        path=config_path,
        summary="write config",
        scope="global-config",
        requires_backup=True,
        requires_confirmation=True,
        idempotent=True,
        details={
            "config_format": "json",
            "mcp_base_url": "https://mcp.evemem.com",
            "hook_command": "/tmp/eve-claude-hook",
        },
    )
    result = execute_operation(
        OperationContext(
            config=_config(tmp_path),
            credentials=_CredentialStore(api_key="eve-secret"),
            action=action,
            api_key=None,
        )
    )
    assert '"eve-memory"' in result.content
    assert "eve-secret" in result.content
    assert "/tmp/eve-claude-hook session_start" in result.content


def test_execute_create_companion_appends_to_existing_active_file(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "CLAUDE.md"
    path.parent.mkdir(parents=True)
    path.write_text("user content", encoding="utf-8")
    action = PlannedAction(
        action_id="a3",
        tool="claude-code",
        action_type="create_companion_file",
        path=path,
        summary="write companion",
        scope="project",
        requires_backup=True,
        requires_confirmation=True,
        idempotent=True,
        details={"mcp_base_url": "https://mcp.evemem.com"},
    )
    result = execute_operation(
        OperationContext(
            config=_config(tmp_path),
            credentials=_CredentialStore(),
            action=action,
            api_key=None,
        )
    )
    assert "user content" in result.content
    assert "EVE-BEGIN:claude-code:v1" in result.content
