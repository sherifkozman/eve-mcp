from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from eve_client.apply import apply_install_plan
from eve_client.auth.local_store import LocalCredentialStore
from eve_client.config import ResolvedConfig
from eve_client.detect.base import detect_tools
from eve_client.manifest import manifest_path
from eve_client.manifest import load_manifest
from eve_client.plan import build_install_plan
from eve_client.uninstall import UninstallError, uninstall_tools


@contextmanager
def patched_keyring(state: dict[str, str] | None = None):
    if state is None:
        state = {}

    def get_password(_service: str, key_name: str) -> str | None:
        return state.get(key_name)

    def set_password(_service: str, key_name: str, secret: str) -> None:
        state[key_name] = secret

    def delete_password(_service: str, key_name: str) -> None:
        state.pop(key_name, None)

    with ExitStack() as stack:
        stack.enter_context(patch("eve_client.auth.keyring_store.keyring.get_password", side_effect=get_password))
        stack.enter_context(patch("eve_client.auth.keyring_store.keyring.set_password", side_effect=set_password))
        stack.enter_context(patch("eve_client.auth.keyring_store.keyring.delete_password", side_effect=delete_password))
        yield state


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


def test_uninstall_claude_code_removes_eve_owned_files_and_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    keyring_state: dict[str, str] = {}
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(keyring_state),
    ):
        detected = detect_tools(only=["claude-code"])
        plan = build_install_plan(detected, _config(tmp_path))
        credential_store = LocalCredentialStore(_config(tmp_path).state_dir)
        apply_install_plan(
            plan,
            _config(tmp_path),
            credential_store,
            provided_api_keys={"claude-code": "eve-secret"},
        )
        result = uninstall_tools(
            config=_config(tmp_path),
            credential_store=credential_store,
            tools=["claude-code"],
        )
        assert load_manifest(_config(tmp_path).state_dir, allow_file_fallback=True) == []
    assert result.removed_actions == 3
    mcp_payload = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    assert "eve-memory" not in mcp_payload.get("mcpServers", {})
    hooks_payload = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "hooks" not in hooks_payload or not hooks_payload["hooks"]
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()
    assert "claude-code:api-key" not in keyring_state


def test_uninstall_claude_code_preserves_user_content_in_active_claude_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    keyring_state: dict[str, str] = {}
    companion = tmp_path / ".claude" / "CLAUDE.md"
    companion.parent.mkdir(parents=True, exist_ok=True)
    companion.write_text("# Team Rules\n\nStay concise.\n", encoding="utf-8")
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(keyring_state),
    ):
        detected = detect_tools(only=["claude-code"])
        plan = build_install_plan(detected, _config(tmp_path))
        credential_store = LocalCredentialStore(_config(tmp_path).state_dir)
        apply_install_plan(
            plan,
            _config(tmp_path),
            credential_store,
            provided_api_keys={"claude-code": "eve-secret"},
        )
        uninstall_tools(
            config=_config(tmp_path),
            credential_store=credential_store,
            tools=["claude-code"],
        )
    content = companion.read_text(encoding="utf-8")
    assert "# Team Rules" in content
    assert "Stay concise." in content
    assert "EVE-BEGIN:claude-code:v1" not in content


def test_uninstall_preserves_user_content_outside_eve_companion_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    keyring_state: dict[str, str] = {}
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/gemini"),
        patched_keyring(keyring_state),
    ):
        detected = detect_tools(only=["gemini-cli"])
        plan = build_install_plan(detected, _config(tmp_path))
        credential_store = LocalCredentialStore(_config(tmp_path).state_dir)
        apply_install_plan(
            plan,
            _config(tmp_path),
            credential_store,
            provided_api_keys={"gemini-cli": "eve-secret"},
        )
        companion = tmp_path / ".gemini" / "GEMINI.md"
        companion.write_text(companion.read_text(encoding="utf-8") + "\nmanual edit", encoding="utf-8")
        uninstall_tools(
            config=_config(tmp_path),
            credential_store=credential_store,
            tools=["gemini-cli"],
        )
    assert "gemini-cli:api-key" not in keyring_state
    assert companion.exists()
    assert companion.read_text(encoding="utf-8").strip() == "manual edit"
    settings_payload = json.loads((tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert "eve-memory" not in settings_payload.get("mcpServers", {})
    assert "hooks" not in settings_payload or not settings_payload["hooks"]


def test_uninstall_refuses_user_modified_eve_json_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    keyring_state: dict[str, str] = {}
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(keyring_state),
    ):
        detected = detect_tools(only=["claude-code"])
        plan = build_install_plan(detected, _config(tmp_path))
        credential_store = LocalCredentialStore(_config(tmp_path).state_dir)
        apply_install_plan(
            plan,
            _config(tmp_path),
            credential_store,
            provided_api_keys={"claude-code": "eve-secret"},
        )
        config_path = tmp_path / ".claude.json"
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload["mcpServers"]["eve-memory"]["timeout"] = 15
        config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(UninstallError):
            uninstall_tools(
                config=_config(tmp_path),
                credential_store=credential_store,
                tools=["claude-code"],
            )
    assert "claude-code:api-key" not in keyring_state


def test_uninstall_refuses_tampered_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    keyring_state: dict[str, str] = {}
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(keyring_state),
    ):
        detected = detect_tools(only=["claude-code"])
        plan = build_install_plan(detected, _config(tmp_path))
        credential_store = LocalCredentialStore(_config(tmp_path).state_dir)
        apply_install_plan(
            plan,
            _config(tmp_path),
            credential_store,
            provided_api_keys={"claude-code": "eve-secret"},
        )
        manifest_file = manifest_path(_config(tmp_path).state_dir)
        manifest_file.write_text(manifest_file.read_text(encoding="utf-8").replace("sha256", "tampered"), encoding="utf-8")
        with pytest.raises(Exception):
            uninstall_tools(
                config=_config(tmp_path),
                credential_store=credential_store,
                tools=["claude-code"],
            )
