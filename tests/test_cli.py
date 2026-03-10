from __future__ import annotations

from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner
from keyring.errors import KeyringError

from eve_client.auth import OAuthSession
from eve_client.auth.local_store import LocalCredentialStore
from eve_client.cli import app, doctor
from eve_client.config import ResolvedConfig, resolve_config
from eve_client.manifest import write_manifest
from eve_client.models import ManifestRecord
from eve_client._version import __version__
from eve_client.state_binding import store_sequence_watermark
from eve_client.transaction_state import write_transaction_state

runner = CliRunner()


def _resolved_config(
    tmp_path: Path,
    *,
    codex_enabled: bool = False,
    codex_source: str = "default",
    feature_claude_desktop: bool = False,
) -> ResolvedConfig:
    root = tmp_path.resolve()
    return ResolvedConfig(
        config_dir=root / ".cfg" / "eve",
        config_path=root / ".cfg" / "eve" / "config.json",
        state_dir=root / ".cfg" / "eve",
        project_root=root,
        mcp_base_url="https://mcp.evemem.com",
        mcp_server_name="eve-memory",
        environment="production",
        feature_claude_desktop=feature_claude_desktop,
        codex_enabled=codex_enabled,
        codex_source=codex_source,
        allow_file_secret_fallback=False,
    )


@contextmanager
def patched_keyring():
    state: dict[str, str] = {}

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


def test_install_json_output() -> None:
    result = runner.invoke(app, ["install", "--json"])
    assert result.exit_code == 0
    assert '"tool_plans"' in result.stdout


def test_quickstart_json_output() -> None:
    result = runner.invoke(app, ["quickstart", "--json"])
    assert result.exit_code == 0
    assert '"next_steps"' in result.stdout


def test_install_rejects_tool_and_all_combined() -> None:
    result = runner.invoke(app, ["install", "--tool", "claude-code", "--all"])
    assert result.exit_code != 0
    assert "Use either --tool or --all" in result.output


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_quickstart_recommends_first_supported_tool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch(
            "eve_client.detect.base.shutil.which",
            side_effect=lambda name: "/usr/bin/claude" if name == "claude" else None,
        ),
    ):
        result = runner.invoke(app, ["quickstart", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["recommended_tool"] == "claude-code"
    assert payload["next_steps"][0] == "eve connect --tool claude-code"


def test_auth_login_oauth_opens_browser_for_claude_desktop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    monkeypatch.setenv("EVE_UI_BASE_URL", "https://evemem.com")
    with patch("eve_client.cli._open_browser", return_value=True) as open_browser:
        result = runner.invoke(app, ["auth", "login", "--tool", "claude-desktop", "--auth-mode", "oauth"])
    assert result.exit_code == 0
    assert "https://evemem.com/app/connect?tool=claude-desktop" in result.output
    open_browser.assert_called_once()


def test_auth_login_oauth_selects_candidate_when_tool_omitted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    (root / "Library" / "Application Support" / "Claude").mkdir(parents=True)
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root, feature_claude_desktop=True)),
        patch("eve_client.cli._open_browser", return_value=False),
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.platform.system", return_value="Darwin"),
        patch("eve_client.detect.base.shutil.which", return_value=None),
    ):
        result = runner.invoke(app, ["auth", "login", "--auth-mode", "oauth", "--no-browser"])
    assert result.exit_code == 0
    assert "Tool: claude-desktop" in result.output
    assert "https://mcp.evemem.com/.well-known/oauth-protected-resource" in result.output


def test_auth_login_allows_oauth_for_codex(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None),
        patch(
            "eve_client.cli.start_auth0_device_authorization",
            return_value=type(
                "_Device",
                (),
                {
                    "device_code": "dev-code",
                    "user_code": "USER-CODE",
                    "verification_uri": "https://evemem.us.auth0.com/activate",
                    "verification_uri_complete": "https://evemem.us.auth0.com/activate?user_code=USER-CODE",
                    "expires_in": 600,
                    "interval": 1,
                },
            )(),
        ),
        patch(
            "eve_client.cli.poll_auth0_device_token",
            return_value=type(
                "_Token",
                (),
                {
                    "access_token": "oauth-access-token",
                    "refresh_token": "oauth-refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "openid profile email offline_access memory.read memory.write",
                },
            )(),
        ),
        patched_keyring(),
    ):
        result = runner.invoke(app, ["auth", "login", "--tool", "codex-cli", "--auth-mode", "oauth", "--no-browser"])
    assert result.exit_code == 0
    assert "Stored codex-cli OAuth session" in result.output


def test_auth_login_does_not_persist_fallback_when_validation_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    result = runner.invoke(
        app,
        ["auth", "login", "--tool", "claude-desktop", "--auth-mode", "api-key", "--api-key", "eve-secret", "--allow-file-fallback"],
    )
    assert result.exit_code != 0
    config_path = tmp_path / ".cfg" / "eve" / "config.json"
    assert not config_path.exists()


def test_auth_login_selects_detected_api_key_tool_when_omitted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root)),
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/claude" if name == "claude" else None),
        patched_keyring(),
    ):
        result = runner.invoke(app, ["auth", "login", "--api-key", "eve-secret"])
    assert result.exit_code == 0
    assert "Stored claude-code credential" in result.output


def test_auth_login_requires_tool_in_non_interactive_mode_when_multiple_candidates_exist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root)),
        patch("eve_client.cli._stdin_is_tty", return_value=False),
        patch("eve_client.detect.base._home", return_value=root),
        patch(
            "eve_client.detect.base.shutil.which",
            side_effect=lambda name: "/usr/bin/tool" if name in {"claude", "gemini"} else None,
        ),
    ):
        result = runner.invoke(app, ["auth", "login", "--api-key", "eve-secret"])
    assert result.exit_code != 0
    assert "--tool is required in non-interactive mode." in result.output


def test_connect_oauth_opens_browser_for_claude_desktop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root, feature_claude_desktop=True)),
        patch("eve_client.cli._open_browser", return_value=True) as open_browser,
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.platform.system", return_value="Darwin"),
        patch("eve_client.detect.base.shutil.which", return_value=None),
    ):
        result = runner.invoke(app, ["connect", "--tool", "claude-desktop", "--auth-mode", "oauth"])
    assert result.exit_code == 0
    assert "Protected resource metadata" in result.output
    open_browser.assert_called_once()


def test_run_codex_injects_bearer_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patched_keyring(),
        patch("eve_client.cli.shutil.which", return_value="/usr/bin/codex"),
        patch("eve_client.cli.subprocess.run") as subprocess_run,
    ):
        store = LocalCredentialStore(resolve_config().state_dir)
        store.set_oauth_session(
            OAuthSession(
                tool="codex-cli",
                access_token="oauth-access-token",
                refresh_token="oauth-refresh-token",
                expires_at=None,
                scope="memory.read memory.write",
                token_type="Bearer",
            )
        )
        subprocess_run.return_value.returncode = 0
        result = runner.invoke(app, ["run", "--tool", "codex-cli", "--", "exec", "hello"])
    assert result.exit_code == 0
    args, kwargs = subprocess_run.call_args
    assert args[0] == ["/usr/bin/codex", "exec", "hello"]
    assert kwargs["env"]["EVE_CODEX_BEARER_TOKEN"] == "oauth-access-token"


def test_verify_accepts_auth_mode_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root)),
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/claude" if name == "claude" else None),
        patch("eve_client.cli.verify_tools", return_value=[{"tool": "claude-code", "connectivity": {"success": True}}]) as verify_tools,
    ):
        result = runner.invoke(app, ["verify", "--tool", "claude-code", "--auth-mode", "oauth", "--json"])
    assert result.exit_code == 0
    kwargs = verify_tools.call_args.kwargs
    assert kwargs["auth_overrides"] == {"claude-code": "oauth"}


def test_connect_api_key_tool_applies_and_verifies(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root)),
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/claude" if name == "claude" else None),
        patch("eve_client.cli.apply_install_plan") as apply_install_plan,
        patch("eve_client.cli.verify_tools", return_value=[{"connectivity": {"success": True}}]),
    ):
        apply_install_plan.return_value.transaction_id = "txn-123"
        result = runner.invoke(app, ["connect", "--tool", "claude-code", "--api-key", "eve-secret", "--yes"])
    assert result.exit_code == 0
    assert "Planned changes" in result.output
    assert "eve-secret" not in result.output
    assert "Connected." in result.output
    assert "Verification succeeded." in result.output
    apply_install_plan.assert_called_once()


def test_auth_login_rejects_blocked_custom_ui_base_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    blocked = _resolved_config(root, feature_claude_desktop=True)
    blocked.blocked_ui_base_url = "https://evil.example.com"
    with (
        patch("eve_client.cli.resolve_config", return_value=blocked),
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.platform.system", return_value="Darwin"),
        patch("eve_client.detect.base.shutil.which", return_value=None),
    ):
        result = runner.invoke(app, ["auth", "login", "--tool", "claude-desktop", "--auth-mode", "oauth", "--no-browser"])
    assert result.exit_code != 0
    assert "EVE_ALLOW_CUSTOM_UI_BASE_URL=1" in result.output


def test_connect_requires_api_key_in_non_interactive_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root)),
        patch("eve_client.cli._stdin_is_tty", return_value=False),
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/claude" if name == "claude" else None),
    ):
        result = runner.invoke(app, ["connect", "--tool", "claude-code"])
    assert result.exit_code != 0
    assert "--api-key is required in non-interactive mode." in result.output


def test_install_does_not_persist_fallback_on_dry_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    result = runner.invoke(app, ["install", "--allow-file-fallback"])
    assert result.exit_code == 0
    config_path = tmp_path / ".cfg" / "eve" / "config.json"
    assert not config_path.exists()


def test_non_interactive_apply_is_blocked() -> None:
    result = runner.invoke(app, ["install", "--apply", "--non-interactive"])
    assert result.exit_code != 0
    assert "Non-interactive apply requires --yes" in result.output


def test_auth_login_and_show(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with patched_keyring():
        login = runner.invoke(app, ["auth", "login", "--tool", "claude-code", "--api-key", "eve-secret"])
        show = runner.invoke(app, ["auth", "show", "--tool", "claude-code"])
    assert login.exit_code == 0
    assert "Stored" in login.output
    assert show.exit_code == 0
    assert "ev****et" in show.output


def test_auth_login_enables_file_fallback_when_keyring_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.cli._stdin_is_tty", return_value=True),
        patch(
            "eve_client.auth.local_store.KeyringCredentialStore.set",
            side_effect=KeyringError("No secure keyring backend available"),
        ),
    ):
        result = runner.invoke(
            app,
            ["auth", "login", "--tool", "claude-code", "--api-key", "eve-secret"],
            input="y\n",
        )
    assert result.exit_code == 0
    assert "Enabled file-based Eve credential fallback" in result.output
    config_payload = json.loads((tmp_path / ".cfg" / "eve" / "config.json").read_text(encoding="utf-8"))
    assert config_payload["allow_file_secret_fallback"] is True
    state_payload = json.loads((tmp_path / ".state" / "eve" / "auth-fallback.json").read_text(encoding="utf-8"))
    assert state_payload["claude-code:api-key"] == "eve-secret"


def test_auth_login_allow_file_fallback_supports_headless_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with patch(
        "eve_client.auth.local_store.KeyringCredentialStore.set",
        side_effect=KeyringError("No secure keyring backend available"),
    ):
        result = runner.invoke(
            app,
            [
                "auth",
                "login",
                "--tool",
                "claude-code",
                "--api-key",
                "eve-secret",
                "--allow-file-fallback",
            ],
        )
    assert result.exit_code == 0
    assert "Enabled file-based Eve credential fallback" in result.output
    state_payload = json.loads((tmp_path / ".state" / "eve" / "auth-fallback.json").read_text(encoding="utf-8"))
    assert state_payload["claude-code:api-key"] == "eve-secret"


def test_auth_show_handles_unavailable_store_without_traceback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    config_path = tmp_path / ".cfg" / "eve" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"config_version": 1, "allow_file_secret_fallback": False}), encoding="utf-8")
    with patch(
        "eve_client.auth.keyring_store.keyring.get_password",
        side_effect=KeyringError("no keyring"),
    ):
        result = runner.invoke(app, ["auth", "show", "--tool", "claude-code"])
    assert result.exit_code == 1
    assert "file fallback is disabled" in result.output


def test_install_apply_with_yes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(),
    ):
        result = runner.invoke(
            app,
            ["install", "--tool", "claude-code", "--apply", "--yes", "--api-key", "eve-secret"],
        )
    assert result.exit_code == 0
    assert "Applied." in result.output
    mcp_payload = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    assert mcp_payload["mcpServers"]["eve-memory"]["headers"]["X-API-Key"] == "eve-secret"
    hooks_payload = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert hooks_payload["hooks"]["SessionStart"][0]["hooks"][0]["command"].endswith("session_start")


def test_verify_command_reports_missing_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring(),
    ):
        result = runner.invoke(app, ["verify", "--tool", "claude-code"])
    assert result.exit_code == 1
    assert "Needs repair" in result.output


def test_uninstall_command_removes_tool_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/claude"),
        patched_keyring() as keyring_state,
    ):
        install = runner.invoke(
            app,
            ["install", "--tool", "claude-code", "--apply", "--yes", "--api-key", "eve-secret"],
        )
        uninstall = runner.invoke(app, ["uninstall", "--tool", "claude-code", "--yes"])
    assert install.exit_code == 0
    assert uninstall.exit_code == 0
    assert "Uninstalled." in uninstall.output
    mcp_payload = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    assert "eve-memory" not in mcp_payload.get("mcpServers", {})
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()
    assert "claude-code:api-key" not in keyring_state


def test_uninstall_command_preserves_user_content_after_removing_eve_block(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/gemini"),
        patched_keyring() as keyring_state,
    ):
        install = runner.invoke(
            app,
            ["install", "--tool", "gemini-cli", "--apply", "--yes", "--api-key", "eve-secret"],
        )
        companion = tmp_path / ".gemini" / "GEMINI.md"
        companion.write_text(companion.read_text(encoding="utf-8") + "\nmanual edit", encoding="utf-8")
        uninstall = runner.invoke(app, ["uninstall", "--tool", "gemini-cli", "--yes"])
    assert install.exit_code == 0
    assert uninstall.exit_code == 0
    assert "Uninstalled." in uninstall.output
    assert companion.exists()
    assert companion.read_text(encoding="utf-8").strip() == "manual edit"
    assert "gemini-cli:api-key" not in keyring_state
    settings_payload = json.loads((tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert "hooks" not in settings_payload or not settings_payload["hooks"]


def test_repair_command_rebuilds_missing_companion_with_stored_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/gemini"),
        patched_keyring(),
    ):
        install = runner.invoke(
            app,
            ["install", "--tool", "gemini-cli", "--apply", "--yes", "--api-key", "eve-secret"],
        )
        (tmp_path / ".gemini" / "GEMINI.md").unlink()
        repair = runner.invoke(
            app,
            ["repair", "--tool", "gemini-cli", "--apply", "--yes"],
        )
    assert install.exit_code == 0
    assert repair.exit_code == 0
    assert "Repaired." in repair.output
    assert (tmp_path / ".gemini" / "GEMINI.md").exists()


def test_install_command_supports_project_scoped_gemini_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/gemini"),
        patched_keyring(),
    ):
        install = runner.invoke(
            app,
            [
                "install",
                "--tool",
                "gemini-cli",
                "--apply",
                "--yes",
                "--api-key",
                "eve-secret",
                "--prompt-scope",
                "project",
            ],
        )
    assert install.exit_code == 0
    assert (tmp_path / "GEMINI.md").exists()
    assert not (tmp_path / ".gemini" / "GEMINI.md").exists()


def test_connect_prompts_for_gemini_install_options_when_interactive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root)),
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/gemini"),
        patch("eve_client.cli._stdin_is_tty", return_value=True),
        patch("eve_client.cli.typer.prompt", return_value="project"),
        patch("eve_client.cli.typer.confirm", side_effect=[True, True, True]),
        patched_keyring(),
    ):
        result = runner.invoke(app, ["connect", "--tool", "gemini-cli", "--api-key", "eve-secret"])
    assert result.exit_code == 0
    assert (tmp_path / "GEMINI.md").exists()


def test_repair_command_applies_codex_when_requested(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    config_dir = tmp_path / ".cfg" / "eve"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps({"codex_enabled": True}), encoding="utf-8")
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", return_value="/usr/bin/codex"),
        patched_keyring(),
    ):
        result = runner.invoke(
            app,
            ["repair", "--tool", "codex-cli", "--apply", "--yes", "--api-key", "eve-secret"],
        )
    assert result.exit_code == 0
    assert "Repaired." in result.output
    assert (tmp_path / ".codex" / "config.toml").exists()
    assert (tmp_path / "AGENTS.md").exists()


def test_doctor_reports_trust_state_recovery(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    state_dir = tmp_path / "eve"
    state_dir.mkdir(parents=True, exist_ok=True)
    store_sequence_watermark(state_dir, 2, allow_file_fallback=True)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "eve trust reinit --yes" in result.output


def test_doctor_reports_interrupted_transaction_without_lock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    state_dir = tmp_path / "eve"
    state_dir.mkdir(parents=True, exist_ok=True)
    write_transaction_state(state_dir, {"transaction_id": "abc", "phase": "applying"})
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "interrupted installer run detected" in result.output


def test_trust_reinit_clears_manifest_and_watermark(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    state_dir = tmp_path / "eve"
    state_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(
        state_dir,
        [
            ManifestRecord(
                transaction_id="txn",
                tool="claude-code",
                action_id="a1",
                action_type="write_config",
                path=str(tmp_path / ".claude" / "settings.json"),
                backup_path=None,
                sha256="abc",
                backup_sha256=None,
                scope="global-config",
                environment="production",
            )
        ],
        allow_file_fallback=True,
    )
    result = runner.invoke(app, ["trust", "reinit", "--yes"])
    assert result.exit_code == 0
    assert "Reinitialized" in result.output
    assert not (state_dir / "manifest.json").exists()


def test_status_reports_low_assurance_keyring(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with patch("eve_client.auth.keyring_store.keyring.get_keyring") as get_keyring:
        class FailKeyring:
            pass
        get_keyring.return_value = FailKeyring()
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "low assurance" in result.output


def test_status_reports_pending_transaction_without_active_lock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    state_dir = tmp_path / "eve"
    state_dir.mkdir(parents=True, exist_ok=True)
    write_transaction_state(state_dir, {"transaction_id": "abc", "phase": "applying"})
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Pending transaction" in result.output
    assert "Recovery hint:" in result.output


def test_doctor_does_not_report_codex_as_disabled(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root)),
        patch("eve_client.cli._keyring_health", return_value={"backend": "SecretService", "low_assurance": False, "file_fallback_enabled": False}),
        patch("eve_client.cli.load_manifest", return_value=[]),
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None),
    ):
        doctor(tool=["codex-cli"])


def test_status_reports_codex_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None),
    ):
        result = runner.invoke(app, ["status", "--tool", "codex-cli", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["codex_enabled"] is False
    assert payload["codex_source"] == "default"
    assert payload["tools"][0]["codex"]["state"] == "disabled_by_default"


def test_status_does_not_read_codex_credentials_when_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None),
        patch("eve_client.merge.has_eve_toml_entry", side_effect=AssertionError("should not load codex config")),
        patch("eve_client.auth.local_store.LocalCredentialStore.get_api_key", side_effect=AssertionError("should not read codex credential")),
    ):
        result = runner.invoke(app, ["status", "--tool", "codex-cli"])
    assert result.exit_code == 0


def test_doctor_does_not_read_codex_credentials_when_disabled(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    with (
        patch("eve_client.cli.resolve_config", return_value=_resolved_config(root)),
        patch("eve_client.cli._keyring_health", return_value={"backend": "SecretService", "low_assurance": False, "file_fallback_enabled": False}),
        patch("eve_client.cli.load_manifest", return_value=[]),
        patch("eve_client.detect.base._home", return_value=root),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None),
        patch("eve_client.auth.local_store.LocalCredentialStore.get_api_key", side_effect=AssertionError("should not read codex credential")),
    ):
        doctor(tool=["codex-cli"])


def test_status_warns_when_codex_enabled_via_legacy_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    config_dir = tmp_path / ".cfg" / "eve"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps({"feature_codex_cli": True}), encoding="utf-8")
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None),
        patch("eve_client.auth.local_store.LocalCredentialStore.get_api_key", return_value=(None, None)),
    ):
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "legacy feature_codex_cli flag" in result.output


def test_status_reports_codex_disabled_by_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    monkeypatch.setenv("EVE_DISABLE_CODEX", "1")
    config_dir = tmp_path / ".cfg" / "eve"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps({"codex_enabled": True}), encoding="utf-8")
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None),
    ):
        result = runner.invoke(app, ["status", "--tool", "codex-cli", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["codex_enabled"] is False
    assert payload["codex_source"] == "env"
    assert payload["tools"][0]["codex"]["state"] == "disabled_by_env"


def test_doctor_reports_codex_enabled_but_unconfigured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    config_dir = tmp_path / ".cfg" / "eve"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps({"codex_enabled": True}), encoding="utf-8")
    with (
        patch("eve_client.detect.base._home", return_value=tmp_path),
        patch("eve_client.detect.base.shutil.which", side_effect=lambda name: "/usr/bin/codex" if name == "codex" else None),
    ):
        result = runner.invoke(app, ["doctor", "--tool", "codex-cli"])
    assert result.exit_code == 1
    assert "enabled but local Eve config or credential is missing" in result.output
