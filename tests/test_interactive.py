"""Tests for the interactive installer flow."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from eve_client.cli import app
from eve_client.config import ResolvedConfig
from eve_client.interactive import (
    InteractiveResult,
    is_keyring_available,
    preview_and_confirm,
    prompt_api_key,
    prompt_file_fallback,
    prompt_repair_or_uninstall,
    prompt_tool_options,
    prompt_tool_selection,
    run_interactive_install,
    should_use_interactive,
)
from eve_client.models import ApplyResult, DetectedTool, InstallPlan, PlannedAction, ToolPlan
from typer.testing import CliRunner


def _make_detected(name, binary_found=True, config_exists=False):
    return DetectedTool(
        name=name,
        config_path=Path(f"/fake/{name}"),
        config_format="json" if name != "codex-cli" else "toml",
        supports_hooks=name in {"claude-code", "gemini-cli"},
        binary_found=binary_found,
        config_exists=config_exists,
    )


# ---------------------------------------------------------------------------
# Task 1: prompt_tool_selection
# ---------------------------------------------------------------------------


class TestPromptToolSelection:
    def test_single_tool_auto_selected(self):
        """When only one tool is detected, it is auto-selected without prompting."""
        tools = [_make_detected("claude-code")]
        result = prompt_tool_selection(tools)
        assert result == ["claude-code"]

    def test_no_tools_returns_empty(self):
        """When no tools are detected, returns empty list."""
        result = prompt_tool_selection([])
        assert result == []

    @patch("eve_client.interactive.Prompt.ask", return_value="1")
    def test_multi_tool_user_picks_one(self, mock_ask):
        tools = [_make_detected("claude-code"), _make_detected("gemini-cli")]
        result = prompt_tool_selection(tools)
        assert result == ["claude-code"]

    @patch("eve_client.interactive.Prompt.ask", return_value="1,2")
    def test_multi_tool_user_picks_multiple(self, mock_ask):
        tools = [_make_detected("claude-code"), _make_detected("gemini-cli")]
        result = prompt_tool_selection(tools)
        assert result == ["claude-code", "gemini-cli"]

    @patch("eve_client.interactive.Prompt.ask", return_value="all")
    def test_multi_tool_user_picks_all(self, mock_ask):
        tools = [
            _make_detected("claude-code"),
            _make_detected("gemini-cli"),
            _make_detected("codex-cli"),
        ]
        result = prompt_tool_selection(tools)
        assert result == ["claude-code", "gemini-cli", "codex-cli"]

    def test_tools_without_binary_excluded(self):
        """Tools without a binary installed are excluded from selection."""
        tools = [_make_detected("claude-code"), _make_detected("gemini-cli", binary_found=False)]
        result = prompt_tool_selection(tools)
        # Only claude-code has binary, auto-selected
        assert result == ["claude-code"]


# ---------------------------------------------------------------------------
# Task 2: prompt_tool_options
# ---------------------------------------------------------------------------


class TestPromptToolOptions:
    @patch("eve_client.interactive.Prompt.ask", return_value="api-key")
    @patch("eve_client.interactive.Confirm.ask", return_value=True)
    def test_claude_code_all_options(self, mock_confirm, mock_prompt):
        opts = prompt_tool_options("claude-code")
        assert opts["auth_mode"] == "api-key"
        assert opts["hooks_enabled"] is True
        # Claude Code doesn't prompt for prompt_scope (always global config)
        assert "prompt_scope" not in opts

    @patch("eve_client.interactive.Prompt.ask", side_effect=["oauth", "global"])
    @patch("eve_client.interactive.Confirm.ask", return_value=False)
    def test_gemini_all_options(self, mock_confirm, mock_prompt):
        opts = prompt_tool_options("gemini-cli")
        assert opts["auth_mode"] == "oauth"
        assert opts["prompt_scope"] == "global"
        assert opts["hooks_enabled"] is False

    @patch("eve_client.interactive.Prompt.ask", return_value="api-key")
    def test_codex_no_hooks_no_scope(self, mock_prompt):
        opts = prompt_tool_options("codex-cli")
        assert opts["auth_mode"] == "api-key"
        assert "hooks_enabled" not in opts
        assert "prompt_scope" not in opts


# ---------------------------------------------------------------------------
# Task 3: preview_and_confirm
# ---------------------------------------------------------------------------


def _make_plan():
    action = PlannedAction(
        action_id="a1",
        tool="claude-code",
        action_type="write_config",
        path=Path("/home/user/.claude.json"),
        summary="Write Eve MCP server config",
        scope="global-config",
        requires_backup=True,
        requires_confirmation=True,
        idempotent=True,
    )
    tool_plan = ToolPlan(
        tool="claude-code",
        auth_mode="api-key",
        supported=True,
        actions=[action],
    )
    return InstallPlan(
        mcp_base_url="https://evemem.com/mcp",
        environment="production",
        transaction_scope="per-tool-with-session-grouping",
        tool_plans=[tool_plan],
    )


class TestPreviewAndConfirm:
    @patch("eve_client.interactive.Confirm.ask", return_value=True)
    def test_user_confirms(self, mock_confirm):
        plan = _make_plan()
        assert preview_and_confirm(plan) is True

    @patch("eve_client.interactive.Confirm.ask", return_value=False)
    def test_user_declines(self, mock_confirm):
        plan = _make_plan()
        assert preview_and_confirm(plan) is False

    def test_empty_plan_returns_false(self):
        plan = InstallPlan(
            mcp_base_url="https://evemem.com/mcp",
            environment="production",
            transaction_scope="per-tool-with-session-grouping",
            tool_plans=[],
        )
        assert preview_and_confirm(plan) is False


# ---------------------------------------------------------------------------
# Task 7: prompt_repair_or_uninstall
# ---------------------------------------------------------------------------


class TestRepairUninstall:
    @patch("eve_client.interactive.Prompt.ask", return_value="repair")
    def test_repair_chosen(self, mock_ask):
        assert prompt_repair_or_uninstall() == "repair"

    @patch("eve_client.interactive.Prompt.ask", return_value="uninstall")
    def test_uninstall_chosen(self, mock_ask):
        assert prompt_repair_or_uninstall() == "uninstall"

    @patch("eve_client.interactive.Prompt.ask", return_value="skip")
    def test_skip_chosen(self, mock_ask):
        assert prompt_repair_or_uninstall() == "skip"


# ---------------------------------------------------------------------------
# Task 4: run_interactive_install + prompt_api_key
# ---------------------------------------------------------------------------


class TestRunInteractiveInstall:
    @patch(
        "eve_client.interactive.prompt_tool_options",
        return_value={"auth_mode": "api-key", "hooks_enabled": True},
    )
    @patch("eve_client.interactive.prompt_api_key", return_value=None)
    @patch("eve_client.interactive.prompt_tool_selection", return_value=["claude-code"])
    def test_returns_result_on_selection(self, mock_select, mock_key, mock_opts):
        tools = [_make_detected("claude-code")]
        result = run_interactive_install(tools)
        assert result is not None
        assert result.selected_tools == ["claude-code"]
        assert result.auth_overrides == {"claude-code": "api-key"}

    def test_no_detected_tools(self):
        result = run_interactive_install([])
        assert result is None

    @patch("eve_client.interactive.prompt_repair_or_uninstall", return_value="skip")
    @patch("eve_client.interactive.prompt_tool_selection", return_value=["claude-code"])
    def test_already_configured_skip_removes_tool(self, mock_select, mock_repair):
        tools = [
            DetectedTool(
                name="claude-code",
                config_path=Path("/fake/claude-code"),
                config_format="json",
                supports_hooks=True,
                binary_found=True,
                config_exists=True,
            )
        ]
        result = run_interactive_install(tools)
        # All tools skipped → None
        assert result is None

    @patch(
        "eve_client.interactive.prompt_tool_options",
        return_value={"auth_mode": "api-key"},
    )
    @patch("eve_client.interactive.prompt_api_key", return_value=None)
    @patch("eve_client.interactive.prompt_repair_or_uninstall", return_value="repair")
    @patch("eve_client.interactive.prompt_tool_selection", return_value=["claude-code"])
    def test_already_configured_repair_proceeds(
        self, mock_select, mock_repair, mock_key, mock_opts
    ):
        tools = [
            DetectedTool(
                name="claude-code",
                config_path=Path("/fake/claude-code"),
                config_format="json",
                supports_hooks=True,
                binary_found=True,
                config_exists=True,
            )
        ]
        result = run_interactive_install(tools)
        assert result is not None
        assert result.selected_tools == ["claude-code"]

    @patch("eve_client.interactive.prompt_repair_or_uninstall", return_value="uninstall")
    @patch("eve_client.interactive.prompt_tool_selection", return_value=["claude-code"])
    def test_already_configured_uninstall_sets_uninstall_list(self, mock_select, mock_repair):
        tools = [
            DetectedTool(
                name="claude-code",
                config_path=Path("/fake/claude-code"),
                config_format="json",
                supports_hooks=True,
                binary_found=True,
                config_exists=True,
            )
        ]
        result = run_interactive_install(tools)
        # Uninstall requested but no install → result still returned with uninstall_tools
        assert result is not None
        assert result.uninstall_tools == ["claude-code"]
        assert result.selected_tools == []


class TestPromptApiKey:
    @patch("eve_client.interactive.Prompt.ask", return_value="sk-test-key-123")
    def test_returns_provided_key(self, mock_ask):
        key = prompt_api_key("claude-code")
        assert key == "sk-test-key-123"

    @patch("eve_client.interactive.Prompt.ask", return_value="")
    def test_empty_returns_none(self, mock_ask):
        key = prompt_api_key("claude-code")
        assert key is None


# ---------------------------------------------------------------------------
# Task 5: should_use_interactive
# ---------------------------------------------------------------------------


class TestInstallCommandInteractiveRouting:
    """Verify that `install` enters interactive mode under the right conditions."""

    @patch("eve_client.interactive._stdin_is_tty", return_value=True)
    def test_tty_no_tool_flag_triggers_interactive(self, mock_tty):
        """When stdin is TTY and no --tool given, interactive flow should be reachable."""
        assert should_use_interactive(tool_flag=None, all_flag=False, non_interactive=False) is True

    @patch("eve_client.interactive._stdin_is_tty", return_value=True)
    def test_explicit_tool_flag_skips_interactive(self, mock_tty):
        assert (
            should_use_interactive(tool_flag=["claude-code"], all_flag=False, non_interactive=False)
            is False
        )

    @patch("eve_client.interactive._stdin_is_tty", return_value=True)
    def test_all_flag_skips_interactive(self, mock_tty):
        assert should_use_interactive(tool_flag=None, all_flag=True, non_interactive=False) is False

    @patch("eve_client.interactive._stdin_is_tty", return_value=False)
    def test_non_tty_skips_interactive(self, mock_tty):
        assert (
            should_use_interactive(tool_flag=None, all_flag=False, non_interactive=False) is False
        )

    @patch("eve_client.interactive._stdin_is_tty", return_value=True)
    def test_non_interactive_flag_skips(self, mock_tty):
        assert should_use_interactive(tool_flag=None, all_flag=False, non_interactive=True) is False


# ---------------------------------------------------------------------------
# CLI integration tests for the interactive installer flow
# ---------------------------------------------------------------------------

_runner = CliRunner()


def _fake_config(tmp_path: Path | None = None) -> ResolvedConfig:
    root = (tmp_path or Path("/tmp/eve-test-fake")).resolve()
    return ResolvedConfig(
        config_dir=root / ".cfg" / "eve",
        config_path=root / ".cfg" / "eve" / "config.json",
        state_dir=root / ".cfg" / "eve",
        project_root=root,
        mcp_base_url="https://mcp.evemem.com",
        mcp_server_name="eve-memory",
        environment="production",
        feature_claude_desktop=False,
        codex_enabled=False,
        codex_source="default",
        allow_file_secret_fallback=False,
    )


def _fake_detected() -> list[DetectedTool]:
    return [_make_detected("claude-code")]


def _fake_plan() -> InstallPlan:
    action = PlannedAction(
        action_id="a1",
        tool="claude-code",
        action_type="write_config",
        path=Path("/home/user/.claude.json"),
        summary="Write Eve MCP server config",
        scope="global-config",
        requires_backup=True,
        requires_confirmation=True,
        idempotent=True,
    )
    tool_plan = ToolPlan(
        tool="claude-code",
        auth_mode="api-key",
        supported=True,
        actions=[action],
    )
    return InstallPlan(
        mcp_base_url="https://mcp.evemem.com",
        environment="production",
        transaction_scope="per-tool-with-session-grouping",
        tool_plans=[tool_plan],
    )


class TestInstallCommandInteractiveIntegration:
    """CliRunner integration tests — verify install command wiring for interactive flow."""

    def test_tty_interactive_path_invoked(self):
        """When interactive mode is active and user declines, exit code is 1."""
        interactive_result = InteractiveResult(selected_tools=["claude-code"])

        with (
            patch("eve_client.interactive.should_use_interactive", return_value=True),
            patch(
                "eve_client.interactive.run_interactive_install", return_value=interactive_result
            ),
            patch("eve_client.interactive.preview_and_confirm", return_value=False),
            patch("eve_client.cli.resolve_config", return_value=_fake_config()),
            patch("eve_client.cli.detect_tools", return_value=_fake_detected()),
            patch("eve_client.cli.build_install_plan", return_value=_fake_plan()),
        ):
            result = _runner.invoke(app, ["install", "--dry-run"])

        from eve_client.interactive import run_interactive_install as _rii

        # Confirm the interactive path was followed and user declining gives exit code 1
        assert result.exit_code == 1

    def test_non_interactive_flag_bypasses(self):
        """--non-interactive skips run_interactive_install and shows plan output."""
        with (
            patch("eve_client.cli.resolve_config", return_value=_fake_config()),
            patch("eve_client.cli.detect_tools", return_value=_fake_detected()),
            patch("eve_client.cli.build_install_plan", return_value=_fake_plan()),
            patch("eve_client.interactive.run_interactive_install") as mock_rii,
        ):
            result = _runner.invoke(app, ["install", "--non-interactive", "--dry-run"])

        mock_rii.assert_not_called()
        assert result.exit_code == 0
        assert (
            "Dry run" in result.output
            or "tool_plans" in result.output
            or "Install Plan" in result.output
        )

    def test_tool_flag_bypasses_interactive(self):
        """--tool bypasses run_interactive_install entirely."""
        with (
            patch("eve_client.cli.resolve_config", return_value=_fake_config()),
            patch("eve_client.cli.detect_tools", return_value=_fake_detected()),
            patch("eve_client.cli.build_install_plan", return_value=_fake_plan()),
            patch("eve_client.interactive.run_interactive_install") as mock_rii,
        ):
            result = _runner.invoke(app, ["install", "--tool", "claude-code", "--dry-run"])

        mock_rii.assert_not_called()
        assert result.exit_code == 0

    def test_interactive_apply_calls_apply_and_verify(self):
        """Full interactive apply chain: interactive → confirm → apply → verify."""
        interactive_result = InteractiveResult(selected_tools=["claude-code"])
        apply_result = ApplyResult(
            transaction_id="txn-abc-123",
            applied_actions=1,
            applied_tools=["claude-code"],
        )
        verify_results = [{"tool": "claude-code", "connectivity": {"success": True}}]

        with (
            patch(
                "eve_client.interactive.should_use_interactive", return_value=True
            ) as mock_interactive,
            patch(
                "eve_client.interactive.run_interactive_install", return_value=interactive_result
            ) as mock_rii,
            patch("eve_client.interactive.preview_and_confirm", return_value=True) as mock_confirm,
            patch("eve_client.cli.resolve_config", return_value=_fake_config()),
            patch("eve_client.cli.detect_tools", return_value=_fake_detected()),
            patch("eve_client.cli.build_install_plan", return_value=_fake_plan()),
            patch("eve_client.cli._credential_store", return_value=MagicMock()),
            patch("eve_client.cli.apply_install_plan", return_value=apply_result) as mock_apply,
            patch("eve_client.cli.verify_tools", return_value=verify_results) as mock_verify,
        ):
            result = _runner.invoke(app, ["install", "--apply"])

        mock_interactive.assert_called_once()
        mock_rii.assert_called_once()
        mock_confirm.assert_called_once()
        mock_apply.assert_called_once()
        mock_verify.assert_called_once()
        assert result.exit_code == 0
        assert "txn-abc-123" in result.output
        assert "Verification passed" in result.output


# ---------------------------------------------------------------------------
# is_keyring_available + prompt_file_fallback
# ---------------------------------------------------------------------------


class TestIsKeyringAvailable:
    def test_returns_true_when_keyring_works(self):
        """is_keyring_available returns True when the keyring probe succeeds."""
        with (
            patch("eve_client.interactive.KeyringCredentialStore") as mock_cls,
        ):
            mock_store = MagicMock()
            mock_cls.return_value = mock_store
            assert is_keyring_available() is True
            mock_store.set.assert_called_once()
            mock_store.delete.assert_called_once()

    def test_returns_false_when_keyring_raises(self):
        """is_keyring_available returns False when the keyring probe raises KeyringError."""
        from keyring.errors import KeyringError

        with patch("eve_client.interactive.KeyringCredentialStore") as mock_cls:
            mock_store = MagicMock()
            mock_store.set.side_effect = KeyringError("no backend")
            mock_cls.return_value = mock_store
            assert is_keyring_available() is False

    def test_returns_false_on_any_exception(self):
        """is_keyring_available returns False for any unexpected error during probe."""
        with patch("eve_client.interactive.KeyringCredentialStore") as mock_cls:
            mock_store = MagicMock()
            mock_store.set.side_effect = RuntimeError("unexpected")
            mock_cls.return_value = mock_store
            assert is_keyring_available() is False


class TestPromptFileFallback:
    @patch("eve_client.interactive.Confirm.ask", return_value=True)
    def test_user_accepts(self, mock_confirm):
        """prompt_file_fallback returns True when user accepts."""
        assert prompt_file_fallback() is True
        mock_confirm.assert_called_once()

    @patch("eve_client.interactive.Confirm.ask", return_value=False)
    def test_user_declines(self, mock_confirm):
        """prompt_file_fallback returns False when user declines."""
        assert prompt_file_fallback() is False

    @patch("eve_client.interactive.Confirm.ask", return_value=True)
    def test_default_is_false(self, mock_confirm):
        """prompt_file_fallback defaults to False (conservative: opt-in required)."""
        prompt_file_fallback()
        _, kwargs = mock_confirm.call_args
        assert kwargs.get("default") is False


class TestInteractiveProactiveKeyringCheck:
    """Verify the proactive keyring check wiring in the interactive install path."""

    def test_no_keyring_user_accepts_file_fallback(self):
        """When keyring is unavailable and user accepts file fallback, install proceeds."""
        interactive_result = InteractiveResult(selected_tools=["claude-code"])
        apply_result = ApplyResult(
            transaction_id="txn-fallback-ok",
            applied_actions=1,
            applied_tools=["claude-code"],
        )
        verify_results = [{"tool": "claude-code", "connectivity": {"success": True}}]
        config_no_fallback = _fake_config()

        import dataclasses

        config_with_fallback = dataclasses.replace(
            config_no_fallback, allow_file_secret_fallback=True
        )

        with (
            patch("eve_client.interactive.should_use_interactive", return_value=True),
            patch(
                "eve_client.interactive.run_interactive_install", return_value=interactive_result
            ),
            patch("eve_client.interactive.preview_and_confirm", return_value=True),
            patch("eve_client.cli.resolve_config", return_value=config_no_fallback),
            patch("eve_client.cli.detect_tools", return_value=_fake_detected()),
            patch("eve_client.cli.build_install_plan", return_value=_fake_plan()),
            patch("eve_client.cli._credential_store", return_value=MagicMock()),
            patch("eve_client.cli.apply_install_plan", return_value=apply_result),
            patch("eve_client.cli.verify_tools", return_value=verify_results),
            patch("eve_client.interactive.is_keyring_available", return_value=False),
            patch("eve_client.interactive.prompt_file_fallback", return_value=True) as mock_pfb,
            patch("eve_client.cli._enable_file_fallback", return_value=config_with_fallback),
        ):
            result = _runner.invoke(app, ["install", "--apply"])

        mock_pfb.assert_called_once()
        assert result.exit_code == 0
        assert "txn-fallback-ok" in result.output

    def test_no_keyring_user_declines_file_fallback_exits(self):
        """When keyring is unavailable and user declines file fallback, exit code is 1."""
        interactive_result = InteractiveResult(selected_tools=["claude-code"])
        config_no_fallback = _fake_config()

        with (
            patch("eve_client.interactive.should_use_interactive", return_value=True),
            patch(
                "eve_client.interactive.run_interactive_install", return_value=interactive_result
            ),
            patch("eve_client.interactive.preview_and_confirm", return_value=True),
            patch("eve_client.cli.resolve_config", return_value=config_no_fallback),
            patch("eve_client.cli.detect_tools", return_value=_fake_detected()),
            patch("eve_client.cli.build_install_plan", return_value=_fake_plan()),
            patch("eve_client.cli._credential_store", return_value=MagicMock()),
            patch("eve_client.interactive.is_keyring_available", return_value=False),
            patch("eve_client.interactive.prompt_file_fallback", return_value=False) as mock_pfb,
        ):
            result = _runner.invoke(app, ["install", "--apply"])

        mock_pfb.assert_called_once()
        assert result.exit_code == 1

    def test_keyring_available_skips_file_fallback_prompt(self):
        """When keyring is available, prompt_file_fallback is never called."""
        interactive_result = InteractiveResult(selected_tools=["claude-code"])
        apply_result = ApplyResult(
            transaction_id="txn-keyring-ok",
            applied_actions=1,
            applied_tools=["claude-code"],
        )
        verify_results = [{"tool": "claude-code", "connectivity": {"success": True}}]

        with (
            patch("eve_client.interactive.should_use_interactive", return_value=True),
            patch(
                "eve_client.interactive.run_interactive_install", return_value=interactive_result
            ),
            patch("eve_client.interactive.preview_and_confirm", return_value=True),
            patch("eve_client.cli.resolve_config", return_value=_fake_config()),
            patch("eve_client.cli.detect_tools", return_value=_fake_detected()),
            patch("eve_client.cli.build_install_plan", return_value=_fake_plan()),
            patch("eve_client.cli._credential_store", return_value=MagicMock()),
            patch("eve_client.cli.apply_install_plan", return_value=apply_result),
            patch("eve_client.cli.verify_tools", return_value=verify_results),
            patch("eve_client.interactive.is_keyring_available", return_value=True),
            patch("eve_client.interactive.prompt_file_fallback") as mock_pfb,
        ):
            result = _runner.invoke(app, ["install", "--apply"])

        mock_pfb.assert_not_called()
        assert result.exit_code == 0
