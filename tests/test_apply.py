from __future__ import annotations

from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
from unittest.mock import patch

import tomllib
import pytest

from eve_client.apply import ApplyPlanError, RollbackConflictError, apply_install_plan, rollback_transaction
from eve_client.auth.local_store import LocalCredentialStore
from eve_client.config import ResolvedConfig
from eve_client.detect.base import detect_tools
from eve_client.plan import build_install_plan
from eve_client.transaction_state import load_transaction_state


@contextmanager
def patched_keyring(state: dict[str, str] | None = None):
    if state is None:
        state = {}

    def get_password(_service: str, key_name: str) -> str | None:
        if key_name.endswith(":api-key"):
            return "eve-secret"
        return state.get(key_name)

    def set_password(_service: str, key_name: str, secret: str) -> None:
        state[key_name] = secret

    with ExitStack() as stack:
        stack.enter_context(patch("eve_client.auth.keyring_store.keyring.get_password", side_effect=get_password))
        stack.enter_context(patch("eve_client.auth.keyring_store.keyring.set_password", side_effect=set_password))
        yield


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


def test_apply_install_plan_writes_claude_code_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(),
    ):
        detected = detect_tools(only=["claude-code"])
        plan = build_install_plan(detected, _config(tmp_path))
        result = apply_install_plan(
            plan,
            _config(tmp_path),
            LocalCredentialStore(_config(tmp_path).state_dir),
            provided_api_keys={"claude-code": "eve-secret"},
        )
    mcp_config_path = tmp_path / ".claude.json"
    hooks_config_path = tmp_path / ".claude" / "settings.json"
    companion = tmp_path / ".claude" / "CLAUDE.md"
    assert result.transaction_id
    assert result.applied_actions == 3
    assert mcp_config_path.exists()
    mcp_payload = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    assert mcp_payload["mcpServers"]["eve-memory"]["headers"]["X-API-Key"] == "eve-secret"
    hooks_payload = json.loads(hooks_config_path.read_text(encoding="utf-8"))
    assert hooks_payload["hooks"]["SessionStart"][0]["hooks"][0]["command"].endswith("session_start")
    assert companion.exists()
    manifest = json.loads((_config(tmp_path).state_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["payload"]["version"] == 2
    assert manifest["payload"]["sequence"] == 1
    assert len(manifest["payload"]["records"]) == 3
    assert manifest["signature"]
    assert all(
        str(_config(tmp_path).state_dir / "backups") in record["backup_path"]
        for record in manifest["payload"]["records"]
        if record["backup_path"]
    )
    assert load_transaction_state(_config(tmp_path).state_dir) is None


def test_apply_install_plan_writes_gemini_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/gemini"),
        patched_keyring(),
    ):
        detected = detect_tools(only=["gemini-cli"])
        plan = build_install_plan(detected, _config(tmp_path))
        result = apply_install_plan(
            plan,
            _config(tmp_path),
            LocalCredentialStore(_config(tmp_path).state_dir),
            provided_api_keys={"gemini-cli": "eve-secret"},
        )
    config_path = tmp_path / ".gemini" / "settings.json"
    companion = tmp_path / ".gemini" / "GEMINI.md"
    assert result.transaction_id
    assert result.applied_actions == 3
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["eve-memory"]["headers"]["X-API-Key"] == "eve-secret"
    assert payload["hooks"]["SessionStart"][0]["hooks"][0]["command"].endswith("session_start")
    assert companion.exists()


def test_apply_install_plan_writes_project_scoped_gemini_companion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/gemini"),
        patched_keyring(),
    ):
        detected = detect_tools(only=["gemini-cli"])
        plan = build_install_plan(
            detected,
            _config(tmp_path),
            prompt_scope_overrides={"gemini-cli": "project"},
        )
        result = apply_install_plan(
            plan,
            _config(tmp_path),
            LocalCredentialStore(_config(tmp_path).state_dir),
            provided_api_keys={"gemini-cli": "eve-secret"},
        )
    assert result.applied_actions == 3
    assert (tmp_path / "GEMINI.md").exists()
    assert not (tmp_path / ".gemini" / "GEMINI.md").exists()


def test_apply_preserves_existing_active_claude_md_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    existing_mcp_config = tmp_path / ".claude.json"
    existing_mcp_config.write_text('{"mcpServers": {}}', encoding="utf-8")
    existing_hooks_config = tmp_path / ".claude" / "settings.json"
    existing_hooks_config.parent.mkdir(parents=True)
    existing_hooks_config.write_text("{}", encoding="utf-8")
    (tmp_path / ".claude" / "CLAUDE.md").write_text("user-owned file", encoding="utf-8")
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(),
    ):
        detected = detect_tools(only=["claude-code"])
        plan = build_install_plan(detected, _config(tmp_path))
    apply_install_plan(
        plan,
        _config(tmp_path),
        LocalCredentialStore(_config(tmp_path).state_dir),
        provided_api_keys={"claude-code": "eve-secret"},
    )
    mcp_payload = json.loads(existing_mcp_config.read_text(encoding="utf-8"))
    assert mcp_payload["mcpServers"]["eve-memory"]["headers"]["X-API-Key"] == "eve-secret"
    hooks_payload = json.loads(existing_hooks_config.read_text(encoding="utf-8"))
    assert hooks_payload["hooks"]["SessionStart"][0]["hooks"][0]["command"].endswith("session_start")
    assert "user-owned file" in (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert load_transaction_state(_config(tmp_path).state_dir) is None


def test_apply_and_rollback_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    keyring_state: dict[str, str] = {}
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/codex"),
        patched_keyring(keyring_state),
    ):
        detected = detect_tools(only=["codex-cli"])
        plan = build_install_plan(
            detected,
            _config(tmp_path),
            auth_overrides={"codex-cli": "api-key"},
        )
        result = apply_install_plan(
            plan,
            _config(tmp_path),
            LocalCredentialStore(_config(tmp_path).state_dir),
            provided_api_keys={"codex-cli": "eve-secret"},
        )
    config_path = tmp_path / ".codex" / "config.toml"
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert result.applied_actions == 2
    assert parsed["mcp_servers"]["eve-memory"]["http_headers"]["X-API-Key"] == "eve-secret"
    with patched_keyring(keyring_state):
        restored = rollback_transaction(_config(tmp_path), result.transaction_id)
    assert restored.restored_actions == 2
    assert not config_path.exists()
    assert load_transaction_state(_config(tmp_path).state_dir) is None


def test_apply_plan_fails_when_codex_steps_exist_but_codex_is_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/codex"),
        patched_keyring(),
    ):
        detected = detect_tools(only=["codex-cli"])
        enabled_plan = build_install_plan(detected, _config(tmp_path))
        disabled_config = ResolvedConfig(
            config_dir=tmp_path / ".eve-config",
            config_path=tmp_path / ".eve-config" / "config.json",
            state_dir=tmp_path / ".eve-state",
            project_root=tmp_path,
            mcp_base_url="https://mcp.evemem.com",
            mcp_server_name="eve-memory",
            environment="production",
            feature_claude_desktop=False,
            codex_enabled=False,
            codex_source="env",
            allow_file_secret_fallback=True,
        )
        with pytest.raises(ApplyPlanError, match="Codex CLI steps are present in this plan"):
            apply_install_plan(
                enabled_plan,
                disabled_config,
                LocalCredentialStore(disabled_config.state_dir),
                provided_api_keys={"codex-cli": "eve-secret"},
            )
    assert not (tmp_path / ".codex" / "config.toml").exists()


def test_apply_plan_preflight_blocks_other_writes_when_codex_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch(
            "eve_client.detect.base.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}" if name in {"claude", "codex"} else None,
        ),
        patched_keyring(),
    ):
        detected = detect_tools(only=["claude-code", "codex-cli"])
        enabled_plan = build_install_plan(detected, _config(tmp_path))
        disabled_config = ResolvedConfig(
            config_dir=tmp_path / ".eve-config",
            config_path=tmp_path / ".eve-config" / "config.json",
            state_dir=tmp_path / ".eve-state",
            project_root=tmp_path,
            mcp_base_url="https://mcp.evemem.com",
            mcp_server_name="eve-memory",
            environment="production",
            feature_claude_desktop=False,
            codex_enabled=False,
            codex_source="env",
            allow_file_secret_fallback=True,
        )
        with pytest.raises(ApplyPlanError, match="Codex CLI steps are present in this plan"):
            apply_install_plan(
                enabled_plan,
                disabled_config,
                LocalCredentialStore(disabled_config.state_dir),
                provided_api_keys={"claude-code": "eve-secret", "codex-cli": "eve-secret"},
            )
    assert not (tmp_path / ".claude" / "settings.json").exists()
    assert not (tmp_path / ".codex" / "config.toml").exists()


def test_rollback_refuses_to_overwrite_modified_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    keyring_state: dict[str, str] = {}
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/codex"),
        patched_keyring(keyring_state),
    ):
        detected = detect_tools(only=["codex-cli"])
        plan = build_install_plan(detected, _config(tmp_path))
        result = apply_install_plan(
            plan,
            _config(tmp_path),
            LocalCredentialStore(_config(tmp_path).state_dir),
            provided_api_keys={"codex-cli": "eve-secret"},
        )
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.write_text("manually changed", encoding="utf-8")
    with pytest.raises(RollbackConflictError):
        with patched_keyring(keyring_state):
            rollback_transaction(_config(tmp_path), result.transaction_id)


def test_rollback_is_all_or_nothing_when_any_file_conflicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    keyring_state: dict[str, str] = {}
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(keyring_state),
    ):
        detected = detect_tools(only=["claude-code"])
        plan = build_install_plan(detected, _config(tmp_path))
        result = apply_install_plan(
            plan,
            _config(tmp_path),
            LocalCredentialStore(_config(tmp_path).state_dir),
            provided_api_keys={"claude-code": "eve-secret"},
        )
    config_path = tmp_path / ".claude" / "settings.json"
    companion_path = tmp_path / ".claude" / "CLAUDE.md"
    original_companion = companion_path.read_text(encoding="utf-8")
    config_path.write_text("manually changed", encoding="utf-8")
    with pytest.raises(RollbackConflictError) as exc_info:
        with patched_keyring(keyring_state):
            rollback_transaction(_config(tmp_path), result.transaction_id)
    assert "Rollback blocked" in str(exc_info.value)
    assert config_path.read_text(encoding="utf-8") == "manually changed"
    assert companion_path.read_text(encoding="utf-8") == original_companion


def test_apply_rejects_policy_escape_for_companion_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(),
    ):
        detected = detect_tools(only=["claude-code"])
        plan = build_install_plan(detected, _config(tmp_path))
        plan.tool_plans[0].actions[1].path = tmp_path / ".git" / "hooks" / "pre-commit"
        with pytest.raises(ApplyPlanError):
            apply_install_plan(
                plan,
                _config(tmp_path),
                LocalCredentialStore(_config(tmp_path).state_dir),
                provided_api_keys={"claude-code": "eve-secret"},
            )
