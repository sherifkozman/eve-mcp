from __future__ import annotations

from contextlib import ExitStack, contextmanager
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from eve_client.apply import apply_install_plan
from eve_client.auth.local_store import LocalCredentialStore
from eve_client.auth.base import CredentialStoreUnavailableError
from eve_client.config import ResolvedConfig
from eve_client.detect.base import detect_tools
from eve_client.plan import build_install_plan
from eve_client.merge import source_agent_header
from eve_client.verify import verify_connectivity, verify_tools


@contextmanager
def patched_keyring(state: dict[str, str] | None = None):
    if state is None:
        state = {}

    def get_password(_service: str, key_name: str) -> str | None:
        return state.get(key_name)

    def set_password(_service: str, key_name: str, secret: str) -> None:
        state[key_name] = secret

    with ExitStack() as stack:
        stack.enter_context(patch("eve_client.auth.keyring_store.keyring.get_password", side_effect=get_password))
        stack.enter_context(patch("eve_client.auth.keyring_store.keyring.set_password", side_effect=set_password))
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


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _ExplodingCredentialStore:
    def get_api_key(self, _tool):
        raise AssertionError("credential store should not be queried")


def _explode_has_eve_toml_entry(_path):
    raise AssertionError("codex config should not be loaded")


def test_verify_connectivity_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = [
        {"result": {"serverInfo": {"name": "Eve", "version": "1.0"}}},
        {"result": {"tools": [{"name": "memory_store"}, {"name": "memory_search"}]}},
    ]

    def fake_urlopen(_request, timeout=10.0):  # noqa: ARG001
        return _FakeResponse(payloads.pop(0))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = verify_connectivity("https://mcp.evemem.com", "eve-secret", "claude-code")
    assert result["success"] is True
    assert result["tool_names"] == ["memory_store", "memory_search"]


def test_verify_connectivity_oauth_without_secret_uses_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=10.0):  # noqa: ARG001
        assert request.full_url.endswith("/.well-known/oauth-protected-resource")
        return _FakeResponse(
            {
                "resource": "https://mcp.evemem.com",
                "authorization_servers": ["https://evemem.us.auth0.com/"],
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = verify_connectivity("https://mcp.evemem.com", None, "claude-code", auth_mode="oauth")
    assert result["success"] is True
    assert result["mode"] == "oauth-metadata"


def test_verify_connectivity_oauth_without_secret_strips_mcp_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=10.0):  # noqa: ARG001
        assert request.full_url == "https://mcp.evemem.com/.well-known/oauth-protected-resource"
        return _FakeResponse(
            {
                "resource": "https://mcp.evemem.com",
                "authorization_servers": ["https://evemem.us.auth0.com/"],
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = verify_connectivity("https://mcp.evemem.com/mcp", None, "claude-code", auth_mode="oauth")
    assert result["success"] is True


def test_verify_connectivity_rejects_insecure_http() -> None:
    result = verify_connectivity("http://mcp.evemem.com", "eve-secret", "claude-code")
    assert result["success"] is False
    assert "Refusing insecure" in result["error"]


def test_verify_connectivity_sanitizes_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(_request, timeout=10.0):  # noqa: ARG001
        raise RuntimeError("request failed with header X-API-Key eve-secret-123456")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = verify_connectivity("https://mcp.evemem.com", "eve-secret", "claude-code")
    assert result["success"] is False
    assert "eve-secret-123456" not in result["error"]
    assert "****" in result["error"]


def test_source_agent_header_is_strictly_sanitized() -> None:
    assert source_agent_header("claude-code") == "claude_code"
    with pytest.raises(ValueError):
        source_agent_header("bad\r\nagent")  # type: ignore[arg-type]


def test_verify_tools_reports_missing_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring({}),
    ):
        detected = detect_tools(only=["claude-code"])
        results = verify_tools(detected, _config(tmp_path), LocalCredentialStore(_config(tmp_path).state_dir))
    assert results[0]["eve_configured"] is False
    assert results[0]["connectivity"]["error"] == "credential missing"


def test_verify_tools_handles_unavailable_credential_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    class _UnavailableCredentialStore:
        def get_api_key(self, tool):  # noqa: ANN001
            raise CredentialStoreUnavailableError(f"{tool} unavailable")

    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
    ):
        detected = detect_tools(only=["claude-code"])
        results = verify_tools(detected, _config(tmp_path), _UnavailableCredentialStore())
    assert results[0]["credential_source"] == "unavailable"
    assert results[0]["connectivity"]["error"] == "credential missing"


def test_verify_tools_reports_live_connectivity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    keyring_state: dict[str, str] = {}
    payloads = [
        {"result": {"serverInfo": {"name": "Eve", "version": "1.0"}}},
        {"result": {"tools": [{"name": "memory_store"}]}},
    ]

    def fake_urlopen(_request, timeout=10.0):  # noqa: ARG001
        return _FakeResponse(payloads.pop(0))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
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
        results = verify_tools(detected, _config(tmp_path), credential_store)
    assert results[0]["eve_configured"] is True
    assert results[0]["hooks_present"] is True
    assert results[0]["connectivity"]["success"] is True


def test_verify_tools_for_codex_waits_for_local_config_before_connecting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    called = False

    def fake_urlopen(_request, timeout=10.0):  # noqa: ARG001
        nonlocal called
        called = True
        raise AssertionError("verify should not contact gated tool")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/codex"),
        patched_keyring({"codex-cli:api-key": "eve-secret"}),
    ):
        detected = detect_tools(only=["codex-cli"])
        results = verify_tools(detected, _config(tmp_path), LocalCredentialStore(_config(tmp_path).state_dir))
    assert results[0]["feature_enabled"] is True
    assert results[0]["connectivity"]["error"] == "Eve config entry missing"
    assert called is False


def test_verify_tools_for_codex_oauth_does_not_require_local_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = _config(tmp_path)
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/codex"),
        patched_keyring({}),
    ):
        detected = detect_tools(only=["codex-cli"])
        plan = build_install_plan(detected, config)
        credential_store = LocalCredentialStore(config.state_dir)
        apply_install_plan(
            plan,
            config,
            credential_store,
            auth_overrides={"codex-cli": "oauth"},
        )
        results = verify_tools(detected, config, credential_store, auth_overrides={"codex-cli": "oauth"})
    assert results[0]["eve_configured"] is True
    assert results[0]["connectivity"]["error"] == "credential missing"
    assert results[0]["state"] == "enabled_unconfigured"


def test_verify_tools_reports_codex_disabled_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    called = False

    def fake_urlopen(_request, timeout=10.0):  # noqa: ARG001
        nonlocal called
        called = True
        raise AssertionError("verify should not contact disabled tool")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
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
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/codex"),
        patch("eve_client.verify.has_eve_toml_entry", side_effect=_explode_has_eve_toml_entry),
    ):
        detected = detect_tools(only=["codex-cli"])
        results = verify_tools(detected, disabled_config, _ExplodingCredentialStore())
    assert results[0]["feature_enabled"] is False
    assert results[0]["state"] == "disabled_by_env"
    assert results[0]["connectivity"]["error"] == "feature disabled"
    assert called is False
