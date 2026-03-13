from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
from eve_client.config import (
    DEFAULT_API_BASE_URL,
    DEFAULT_MCP_BASE_URL,
    DEFAULT_UI_BASE_URL,
    get_config_path,
    get_state_dir,
    resolve_api_base_url,
    resolve_config,
    update_local_config,
)


def test_resolve_config_uses_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("EVE_MCP_BASE_URL", raising=False)
    config = resolve_config()
    assert config.mcp_base_url == DEFAULT_MCP_BASE_URL
    assert config.ui_base_url == DEFAULT_UI_BASE_URL
    assert config.environment == "production"
    assert config.mcp_server_name == "eve-memory"
    assert config.allow_file_secret_fallback is False


def test_resolve_api_base_url_maps_official_mcp_host() -> None:
    assert resolve_api_base_url("https://mcp.evemem.com/mcp") == DEFAULT_API_BASE_URL
    assert resolve_api_base_url("https://mcp.evemem.com") == DEFAULT_API_BASE_URL


def test_resolve_config_honors_local_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("EVE_MCP_BASE_URL", raising=False)
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "mcp_base_url": "https://staging.example.com",
                "ui_base_url": "https://preview.evemem.com",
                "feature_claude_desktop": True,
                "codex_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVE_ALLOW_CUSTOM_UI_BASE_URL", "1")
    config = resolve_config()
    assert config.mcp_base_url == "https://staging.example.com"
    assert config.ui_base_url == "https://preview.evemem.com"
    assert config.environment == "custom"
    assert config.feature_claude_desktop is True
    assert config.codex_enabled is False
    assert config.codex_source == "config"
    assert config.allow_file_secret_fallback is False
    assert config.project_root == Path.cwd().resolve()


def test_resolve_config_honors_legacy_feature_codex_cli_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"feature_codex_cli": False}), encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config = resolve_config()
    assert config.codex_enabled is False
    assert config.codex_source == "legacy"
    assert any("feature_codex_cli is deprecated" in str(item.message) for item in caught)


def test_resolve_config_codex_disable_env_overrides_local_config(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_DISABLE_CODEX", "1")
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"codex_enabled": True}),
        encoding="utf-8",
    )
    config = resolve_config()
    assert config.codex_enabled is False
    assert config.codex_source == "env"


def test_resolve_config_defaults_codex_to_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = resolve_config()
    assert config.codex_enabled is False
    assert config.codex_source == "default"


def test_resolve_config_codex_disable_env_false_does_not_disable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_DISABLE_CODEX", "false")
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"codex_enabled": True}), encoding="utf-8")
    config = resolve_config()
    assert config.codex_enabled is True
    assert config.codex_source == "config"


def test_resolve_config_codex_disable_env_zero_does_not_disable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_DISABLE_CODEX", "0")
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"codex_enabled": True}), encoding="utf-8")
    config = resolve_config()
    assert config.codex_enabled is True
    assert config.codex_source == "config"


@pytest.mark.parametrize("env_value", ["", " ", "false", "False", "garbage"])
def test_resolve_config_non_truthy_disable_values_do_not_disable_codex(
    monkeypatch, tmp_path: Path, env_value: str
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_DISABLE_CODEX", env_value)
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"codex_enabled": True}), encoding="utf-8")
    config = resolve_config()
    assert config.codex_enabled is True
    assert config.codex_source == "config"


@pytest.mark.parametrize("env_value", ["1", "true", "True", "yes", "on"])
def test_resolve_config_truthy_disable_values_disable_codex(
    monkeypatch, tmp_path: Path, env_value: str
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_DISABLE_CODEX", env_value)
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"codex_enabled": True}), encoding="utf-8")
    config = resolve_config()
    assert config.codex_enabled is False
    assert config.codex_source == "env"


def test_resolve_config_codex_new_flag_overrides_legacy_true(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"codex_enabled": False, "feature_codex_cli": True}),
        encoding="utf-8",
    )
    config = resolve_config()
    assert config.codex_enabled is False
    assert config.codex_source == "config"


def test_resolve_config_codex_new_flag_overrides_legacy_false(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"codex_enabled": True, "feature_codex_cli": False}),
        encoding="utf-8",
    )
    config = resolve_config()
    assert config.codex_enabled is True
    assert config.codex_source == "config"


@pytest.mark.parametrize(
    ("disable_env", "payload", "expected_enabled", "expected_source"),
    [
        ("1", {"codex_enabled": True}, False, "env"),
        ("1", {"codex_enabled": False}, False, "env"),
        ("true", {"feature_codex_cli": True}, False, "env"),
        (None, {"codex_enabled": False, "feature_codex_cli": True}, False, "config"),
        (None, {"codex_enabled": True, "feature_codex_cli": False}, True, "config"),
        (None, {"feature_codex_cli": True}, True, "legacy"),
        (None, {"feature_codex_cli": False}, False, "legacy"),
        ("0", {"codex_enabled": True}, True, "config"),
        (None, {}, False, "default"),
    ],
)
def test_resolve_config_codex_precedence_matrix(
    monkeypatch,
    tmp_path: Path,
    disable_env: str | None,
    payload: dict[str, object],
    expected_enabled: bool,
    expected_source: str,
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    if disable_env is None:
        monkeypatch.delenv("EVE_DISABLE_CODEX", raising=False)
    else:
        monkeypatch.setenv("EVE_DISABLE_CODEX", disable_env)
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        config = resolve_config()
    assert config.codex_enabled is expected_enabled
    assert config.codex_source == expected_source


def test_resolve_config_enables_file_fallback_from_local_config(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"allow_file_secret_fallback": True}),
        encoding="utf-8",
    )
    config = resolve_config()
    assert config.allow_file_secret_fallback is True


def test_linux_uses_separate_config_and_state_dirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = resolve_config()
    assert config.config_path == tmp_path / "cfg" / "eve" / "config.json"
    assert config.state_dir == tmp_path / "state" / "eve"


def test_darwin_honors_xdg_overrides_for_config_and_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Darwin")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = resolve_config()
    assert config.config_path == tmp_path / "cfg" / "eve" / "config.json"
    assert config.state_dir == tmp_path / "state" / "eve"


def test_update_local_config_writes_to_config_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    written = update_local_config({"allow_file_secret_fallback": True})
    assert written == tmp_path / "cfg" / "eve" / "config.json"
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["allow_file_secret_fallback"] is True


def test_resolve_config_honors_ui_base_url_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_UI_BASE_URL", "https://staging.evemem.com")
    monkeypatch.setenv("EVE_ALLOW_CUSTOM_UI_BASE_URL", "1")
    config = resolve_config()
    assert config.ui_base_url == "https://staging.evemem.com"


def test_resolve_config_rejects_untrusted_ui_base_url_without_override(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_UI_BASE_URL", "https://evil.example.com/custom/path")
    monkeypatch.delenv("EVE_ALLOW_CUSTOM_UI_BASE_URL", raising=False)
    config = resolve_config()
    assert config.ui_base_url == DEFAULT_UI_BASE_URL
    assert config.blocked_ui_base_url == "https://evil.example.com"


def test_resolve_config_allows_custom_local_ui_base_url_with_override(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_UI_BASE_URL", "http://localhost:3300/custom/path")
    monkeypatch.setenv("EVE_ALLOW_CUSTOM_UI_BASE_URL", "1")
    config = resolve_config()
    assert config.ui_base_url == "http://localhost:3300"
    assert config.blocked_ui_base_url is None


def test_resolve_config_rejects_official_suffix_confusion(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_UI_BASE_URL", "https://evemem.com.evil.com")
    config = resolve_config()
    assert config.ui_base_url == DEFAULT_UI_BASE_URL
    assert config.blocked_ui_base_url == "https://evemem.com.evil.com"


def test_resolve_config_accepts_official_origin_with_default_https_port(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_UI_BASE_URL", "https://evemem.com:443")
    config = resolve_config()
    assert config.ui_base_url == "https://evemem.com"
    assert config.blocked_ui_base_url is None


def test_resolve_config_rejects_official_origin_with_non_default_port(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_UI_BASE_URL", "https://evemem.com:8443")
    config = resolve_config()
    assert config.ui_base_url == DEFAULT_UI_BASE_URL
    assert config.blocked_ui_base_url == "https://evemem.com:8443"


def test_resolve_config_rejects_userinfo_trick(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_UI_BASE_URL", "https://evemem.com@evil.com")
    config = resolve_config()
    assert config.ui_base_url == DEFAULT_UI_BASE_URL


def test_resolve_config_rejects_localhost_suffix_trick(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_UI_BASE_URL", "http://localhost.evil.com:3000")
    monkeypatch.setenv("EVE_ALLOW_CUSTOM_UI_BASE_URL", "1")
    config = resolve_config()
    assert config.ui_base_url == DEFAULT_UI_BASE_URL


def test_resolve_config_allows_ipv6_loopback_with_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("EVE_UI_BASE_URL", "http://[::1]:3300")
    monkeypatch.setenv("EVE_ALLOW_CUSTOM_UI_BASE_URL", "1")
    config = resolve_config()
    assert config.ui_base_url == "http://[::1]:3300"
