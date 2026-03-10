"""Per-IDE integration tests for config placement, companion files, and hook emission.

Tests the exact file paths, merge semantics, and format correctness for each
supported IDE: Claude Code, Gemini CLI, and Codex CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import tomllib
import pytest

from eve_client.config import ResolvedConfig
from eve_client.detect.base import detect_tools
from eve_client.integrations.claude_code import ClaudeCodeProvider
from eve_client.integrations.codex_cli import CodexCliProvider
from eve_client.integrations.gemini_cli import GeminiCliProvider
from eve_client.merge import (
    build_mcp_json_entry,
    companion_content,
    has_eve_claude_hooks,
    has_eve_gemini_hooks,
    has_eve_json_entry,
    has_eve_toml_entry,
    is_eve_companion_file,
    merge_companion_file,
    merge_json_config,
    merge_toml_config,
)
from eve_client.models import DetectedTool


def _resolved_config(tmp_path: Path, **overrides) -> ResolvedConfig:
    defaults = dict(
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
    defaults.update(overrides)
    return ResolvedConfig(**defaults)


# ---------------------------------------------------------------------------
# Chunk 1: Config File Placement and Merge Tests
# ---------------------------------------------------------------------------


class TestConfigPlacement:
    """Exact config file placement and merge tests per IDE."""

    # --- Claude Code ---

    def test_claude_code_global_config_path(self, tmp_path: Path) -> None:
        with patch("eve_client.detect.base._home", return_value=tmp_path):
            tools = detect_tools(only=["claude-code"])
        assert tools[0].config_path == tmp_path / ".claude.json"
        assert tools[0].hooks_path == tmp_path / ".claude" / "settings.json"

    def test_claude_code_project_scoped_config_path(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        with patch("eve_client.detect.base._home", return_value=tmp_path):
            tools = detect_tools(only=["claude-code"], project_scoped=True)
        assert tools[0].config_path == tmp_path / ".mcp.json"
        assert tools[0].hooks_path is None
        assert tools[0].project_scoped is True

    def test_claude_code_json_entry_uses_type_http(self) -> None:
        entry = build_mcp_json_entry("claude-code", "https://mcp.evemem.com", "test-key")
        assert entry["eve-memory"]["type"] == "http"
        assert entry["eve-memory"]["url"] == "https://mcp.evemem.com"
        assert entry["eve-memory"]["headers"]["X-API-Key"] == "test-key"
        assert entry["eve-memory"]["headers"]["X-Source-Agent"] == "claude_code"

    def test_claude_code_merge_preserves_existing_servers(self, tmp_path: Path) -> None:
        config = tmp_path / ".claude.json"
        config.write_text(
            '{"mcpServers": {"other-tool": {"type": "stdio", "command": "foo"}}}',
            encoding="utf-8",
        )
        merged = merge_json_config(config, "claude-code", "https://mcp.evemem.com", "key")
        parsed = json.loads(merged)
        assert "other-tool" in parsed["mcpServers"]
        assert "eve-memory" in parsed["mcpServers"]
        assert parsed["mcpServers"]["eve-memory"]["type"] == "http"

    def test_claude_code_merge_creates_file_when_absent(self, tmp_path: Path) -> None:
        config = tmp_path / ".claude.json"
        merged = merge_json_config(config, "claude-code", "https://mcp.evemem.com", "key")
        parsed = json.loads(merged)
        assert parsed["mcpServers"]["eve-memory"]["url"] == "https://mcp.evemem.com"

    def test_claude_code_oauth_omits_api_key(self, tmp_path: Path) -> None:
        config = tmp_path / ".claude.json"
        merged = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            None,
            auth_mode="oauth",
        )
        parsed = json.loads(merged)
        headers = parsed["mcpServers"]["eve-memory"]["headers"]
        assert "X-API-Key" not in headers
        assert "Authorization" not in headers
        assert headers["X-Source-Agent"] == "claude_code"

    # --- Gemini CLI ---

    def test_gemini_cli_config_path(self, tmp_path: Path) -> None:
        with patch("eve_client.detect.base._home", return_value=tmp_path):
            tools = detect_tools(only=["gemini-cli"])
        assert tools[0].config_path == tmp_path / ".gemini" / "settings.json"
        assert tools[0].supports_hooks is True

    def test_gemini_cli_json_entry_uses_http_url_key(self) -> None:
        entry = build_mcp_json_entry("gemini-cli", "https://mcp.evemem.com", "test-key")
        assert "httpUrl" in entry["eve-memory"]
        assert "type" not in entry["eve-memory"]
        assert "url" not in entry["eve-memory"]
        assert entry["eve-memory"]["httpUrl"] == "https://mcp.evemem.com"
        assert entry["eve-memory"]["headers"]["X-Source-Agent"] == "gemini_cli"

    def test_gemini_cli_merge_preserves_existing_servers(self, tmp_path: Path) -> None:
        config = tmp_path / ".gemini" / "settings.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            '{"mcpServers": {"other": {"httpUrl": "https://example.com"}}}',
            encoding="utf-8",
        )
        merged = merge_json_config(config, "gemini-cli", "https://mcp.evemem.com", "key")
        parsed = json.loads(merged)
        assert "other" in parsed["mcpServers"]
        assert parsed["mcpServers"]["eve-memory"]["httpUrl"] == "https://mcp.evemem.com"

    def test_gemini_cli_oauth_sets_source_agent_only(self, tmp_path: Path) -> None:
        config = tmp_path / ".gemini" / "settings.json"
        config.parent.mkdir(parents=True)
        merged = merge_json_config(
            config,
            "gemini-cli",
            "https://mcp.evemem.com",
            None,
            auth_mode="oauth",
        )
        parsed = json.loads(merged)
        headers = parsed["mcpServers"]["eve-memory"]["headers"]
        assert "X-API-Key" not in headers
        assert "Authorization" not in headers
        assert headers["X-Source-Agent"] == "gemini_cli"

    # --- Codex CLI ---

    def test_codex_cli_config_path(self, tmp_path: Path) -> None:
        with patch("eve_client.detect.base._home", return_value=tmp_path):
            tools = detect_tools(only=["codex-cli"])
        assert tools[0].config_path == tmp_path / ".codex" / "config.toml"
        assert tools[0].config_format == "toml"
        assert tools[0].supports_hooks is False

    def test_codex_cli_toml_entry_has_startup_timeout(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        merged = merge_toml_config(config, "codex-cli", "https://mcp.evemem.com", "key")
        parsed = tomllib.loads(merged)
        eve = parsed["mcp_servers"]["eve-memory"]
        assert eve["url"] == "https://mcp.evemem.com"
        assert eve["startup_timeout_sec"] == 60
        assert eve["http_headers"]["X-API-Key"] == "key"
        assert eve["http_headers"]["X-Source-Agent"] == "codex_cli"

    def test_codex_cli_toml_preserves_existing_servers(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text(
            '[mcp_servers.other]\nurl = "https://example.com"\n',
            encoding="utf-8",
        )
        merged = merge_toml_config(config, "codex-cli", "https://mcp.evemem.com", "key")
        parsed = tomllib.loads(merged)
        assert "other" in parsed["mcp_servers"]
        assert "eve-memory" in parsed["mcp_servers"]

    def test_codex_cli_oauth_uses_bearer_token_env_var(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        merged = merge_toml_config(
            config,
            "codex-cli",
            "https://mcp.evemem.com",
            None,
            auth_mode="oauth",
        )
        parsed = tomllib.loads(merged)
        eve = parsed["mcp_servers"]["eve-memory"]
        assert eve["bearer_token_env_var"] == "EVE_CODEX_BEARER_TOKEN"
        assert "X-API-Key" not in eve.get("http_headers", {})
        assert eve["http_headers"]["X-Source-Agent"] == "codex_cli"

    def test_codex_cli_toml_idempotent_merge(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        first = merge_toml_config(config, "codex-cli", "https://mcp.evemem.com", "key1")
        config.write_text(first, encoding="utf-8")
        second = merge_toml_config(config, "codex-cli", "https://mcp.evemem.com", "key2")
        parsed = tomllib.loads(second)
        assert parsed["mcp_servers"]["eve-memory"]["http_headers"]["X-API-Key"] == "key2"
        assert second.count('[mcp_servers."eve-memory"]') == 1


# ---------------------------------------------------------------------------
# Chunk 2: Prompt/Companion File Placement Tests
# ---------------------------------------------------------------------------


class TestCompanionPlacement:
    """Prompt/companion file placement tests per IDE."""

    # --- Claude Code ---

    def test_claude_code_global_companion_path(self, tmp_path: Path) -> None:
        detected = DetectedTool(
            name="claude-code",
            config_path=tmp_path / ".claude.json",
            config_format="json",
            supports_hooks=True,
            binary_found=True,
            config_exists=False,
            hooks_path=tmp_path / ".claude" / "settings.json",
        )
        plan = ClaudeCodeProvider().build_plan(detected, "https://mcp.evemem.com")
        companion_action = [a for a in plan.actions if a.action_type == "create_companion_file"][0]
        assert companion_action.path == tmp_path / ".claude" / "CLAUDE.md"
        assert companion_action.scope == "global-config"

    def test_claude_code_project_scoped_companion_path(self, tmp_path: Path) -> None:
        detected = DetectedTool(
            name="claude-code",
            config_path=tmp_path / ".mcp.json",
            config_format="json",
            supports_hooks=True,
            binary_found=True,
            config_exists=False,
            project_scoped=True,
        )
        plan = ClaudeCodeProvider().build_plan(detected, "https://mcp.evemem.com")
        companion_action = [a for a in plan.actions if a.action_type == "create_companion_file"][0]
        assert companion_action.path == tmp_path / "CLAUDE.md"
        assert companion_action.scope == "project"

    def test_claude_code_companion_content_has_markers(self) -> None:
        content = companion_content("claude-code", "https://mcp.evemem.com")
        assert "<!-- EVE-BEGIN:claude-code:v1 -->" in content
        assert "<!-- EVE-END:claude-code:v1 -->" in content
        assert "## Eve Memory Protocol" in content
        assert "### What Eve already does automatically" in content
        assert "### Use Eve tools explicitly when" in content
        assert "### Read discipline" in content
        assert "### Write discipline" in content
        assert "### Session behavior" in content
        assert "MCP endpoint: `https://mcp.evemem.com`" in content

    def test_claude_code_companion_appends_to_existing(self, tmp_path: Path) -> None:
        companion = tmp_path / "CLAUDE.md"
        companion.write_text("# My Project\n\nCustom instructions here.\n", encoding="utf-8")
        merged = merge_companion_file(companion, "claude-code", "https://mcp.evemem.com")
        assert merged.startswith("# My Project")
        assert "Custom instructions here." in merged
        assert "EVE-BEGIN:claude-code:v1" in merged

    def test_claude_code_companion_replaces_existing_block(self, tmp_path: Path) -> None:
        companion = tmp_path / "CLAUDE.md"
        initial = merge_companion_file(companion, "claude-code", "https://mcp.evemem.com/v1")
        companion.write_text(initial, encoding="utf-8")
        updated = merge_companion_file(companion, "claude-code", "https://mcp.evemem.com/v2")
        assert updated.count("EVE-BEGIN:claude-code:v1") == 1
        assert "mcp.evemem.com/v2" in updated
        assert "mcp.evemem.com/v1" not in updated

    def test_claude_code_companion_detected(self, tmp_path: Path) -> None:
        companion = tmp_path / "CLAUDE.md"
        companion.write_text(
            companion_content("claude-code", "https://mcp.evemem.com"),
            encoding="utf-8",
        )
        assert is_eve_companion_file(companion, "claude-code") is True
        assert is_eve_companion_file(companion, "gemini-cli") is False

    # --- Gemini CLI ---

    def test_gemini_cli_global_companion_path(self, tmp_path: Path) -> None:
        detected = DetectedTool(
            name="gemini-cli",
            config_path=tmp_path / ".gemini" / "settings.json",
            config_format="json",
            supports_hooks=True,
            binary_found=True,
            config_exists=False,
        )
        plan = GeminiCliProvider().build_plan(detected, "https://mcp.evemem.com")
        companion_action = [a for a in plan.actions if a.action_type == "create_companion_file"][0]
        assert companion_action.path == tmp_path / ".gemini" / "GEMINI.md"

    def test_gemini_cli_project_companion_when_scope_override(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        detected = DetectedTool(
            name="gemini-cli",
            config_path=tmp_path / ".gemini" / "settings.json",
            config_format="json",
            supports_hooks=True,
            binary_found=True,
            config_exists=False,
        )
        plan = GeminiCliProvider().build_plan(
            detected,
            "https://mcp.evemem.com",
            prompt_scope="project",
        )
        companion_action = [a for a in plan.actions if a.action_type == "create_companion_file"][0]
        assert companion_action.path == tmp_path / "GEMINI.md"
        assert companion_action.scope == "project"

    def test_gemini_cli_auto_detects_project_scope_from_existing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        project_gemini = tmp_path / "GEMINI.md"
        project_gemini.write_text(
            companion_content("gemini-cli", "https://mcp.evemem.com"),
            encoding="utf-8",
        )
        detected = DetectedTool(
            name="gemini-cli",
            config_path=tmp_path / ".gemini" / "settings.json",
            config_format="json",
            supports_hooks=True,
            binary_found=True,
            config_exists=False,
        )
        plan = GeminiCliProvider().build_plan(detected, "https://mcp.evemem.com")
        companion_action = [a for a in plan.actions if a.action_type == "create_companion_file"][0]
        assert companion_action.path == tmp_path / "GEMINI.md"

    def test_gemini_cli_companion_content_has_markers(self) -> None:
        content = companion_content("gemini-cli", "https://mcp.evemem.com")
        assert "<!-- EVE-BEGIN:gemini-cli:v1 -->" in content
        assert "<!-- EVE-END:gemini-cli:v1 -->" in content
        assert "## Eve Memory Protocol" in content
        assert "### Use Eve when" in content
        assert "### Read discipline" in content
        assert "### Write discipline" in content

    def test_gemini_cli_companion_appends_to_existing(self, tmp_path: Path) -> None:
        companion = tmp_path / "GEMINI.md"
        companion.write_text("# My Gemini Config\n", encoding="utf-8")
        merged = merge_companion_file(companion, "gemini-cli", "https://mcp.evemem.com")
        assert "# My Gemini Config" in merged
        assert "EVE-BEGIN:gemini-cli:v1" in merged

    # --- Codex CLI ---

    def test_codex_cli_companion_is_always_project_scoped(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        detected = DetectedTool(
            name="codex-cli",
            config_path=tmp_path / ".codex" / "config.toml",
            config_format="toml",
            supports_hooks=False,
            binary_found=True,
            config_exists=False,
        )
        plan = CodexCliProvider().build_plan(detected, "https://mcp.evemem.com")
        companion_action = [a for a in plan.actions if a.action_type == "create_companion_file"][0]
        assert companion_action.path == tmp_path / "AGENTS.md"
        assert companion_action.scope == "project"

    def test_codex_cli_companion_content_has_markers(self) -> None:
        content = companion_content("codex-cli", "https://mcp.evemem.com")
        assert "<!-- EVE-BEGIN:codex-cli:v1 -->" in content
        assert "<!-- EVE-END:codex-cli:v1 -->" in content
        assert "## Eve Memory Protocol" in content
        assert "### Use Eve when" in content

    def test_codex_cli_companion_appends_to_existing_agents_md(self, tmp_path: Path) -> None:
        companion = tmp_path / "AGENTS.md"
        companion.write_text("# Team Agents\n\nPrefer concise diffs.\n", encoding="utf-8")
        merged = merge_companion_file(companion, "codex-cli", "https://mcp.evemem.com/mcp")
        assert "# Team Agents" in merged
        assert "Prefer concise diffs." in merged
        assert "EVE-BEGIN:codex-cli:v1" in merged

    def test_codex_cli_no_hooks_in_plan(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        detected = DetectedTool(
            name="codex-cli",
            config_path=tmp_path / ".codex" / "config.toml",
            config_format="toml",
            supports_hooks=False,
            binary_found=True,
            config_exists=False,
        )
        plan = CodexCliProvider().build_plan(detected, "https://mcp.evemem.com")
        hook_actions = [a for a in plan.actions if a.action_type == "write_hooks_config"]
        assert hook_actions == []


# ---------------------------------------------------------------------------
# Chunk 3: Hook Config Emission Tests
# ---------------------------------------------------------------------------


class TestHookEmission:
    """Hook config emission tests per IDE."""

    # --- Claude Code ---

    def test_claude_code_emits_all_four_hook_events(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-claude-hook",
        )
        parsed = json.loads(merged)
        hooks = parsed["hooks"]
        assert "SessionStart" in hooks
        assert "UserPromptSubmit" in hooks
        assert "PreCompact" in hooks
        assert "SessionEnd" in hooks

    def test_claude_code_session_start_has_matcher(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-claude-hook",
        )
        parsed = json.loads(merged)
        session_start = parsed["hooks"]["SessionStart"][0]
        assert session_start["matcher"] == "startup|resume"
        hook = session_start["hooks"][0]
        assert hook["type"] == "command"
        assert hook["command"] == "/usr/local/bin/eve-claude-hook session_start"
        assert hook["timeout"] == 5

    def test_claude_code_prompt_enrich_timeout(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-claude-hook",
        )
        parsed = json.loads(merged)
        hook = parsed["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        assert hook["command"].endswith("prompt_enrich")
        assert hook["timeout"] == 5

    def test_claude_code_pre_compact_timeout(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-claude-hook",
        )
        parsed = json.loads(merged)
        hook = parsed["hooks"]["PreCompact"][0]["hooks"][0]
        assert hook["command"].endswith("pre_compact")
        assert hook["timeout"] == 15

    def test_claude_code_session_end_is_async(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-claude-hook",
        )
        parsed = json.loads(merged)
        hook = parsed["hooks"]["SessionEnd"][0]["hooks"][0]
        assert hook["command"].endswith("session_end")
        assert hook["timeout"] == 15
        assert hook["async"] is True

    def test_claude_code_hooks_detected(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-claude-hook",
        )
        config.write_text(merged, encoding="utf-8")
        assert has_eve_claude_hooks(config) is True
        assert has_eve_gemini_hooks(config) is False

    def test_claude_code_hooks_preserve_user_hooks(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        config.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "always",
                                "hooks": [{"type": "command", "command": "user-hook start"}],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        merged = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-claude-hook",
        )
        parsed = json.loads(merged)
        session_start_entries = parsed["hooks"]["SessionStart"]
        assert len(session_start_entries) == 2
        commands = [e["hooks"][0]["command"] for e in session_start_entries]
        assert "user-hook start" in commands
        assert "/usr/local/bin/eve-claude-hook session_start" in commands

    def test_claude_code_hooks_idempotent(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        first = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-claude-hook",
        )
        config.write_text(first, encoding="utf-8")
        second = merge_json_config(
            config,
            "claude-code",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-claude-hook",
        )
        parsed = json.loads(second)
        for event_entries in parsed["hooks"].values():
            eve_entries = [
                e
                for e in event_entries
                if any("eve-claude-hook" in h.get("command", "") for h in e.get("hooks", []))
            ]
            assert len(eve_entries) == 1

    # --- Gemini CLI ---

    def test_gemini_cli_emits_all_four_hook_events(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "gemini-cli",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-gemini-hook",
            hooks_only=True,
        )
        parsed = json.loads(merged)
        hooks = parsed["hooks"]
        assert "SessionStart" in hooks
        assert "BeforeAgent" in hooks
        assert "PreCompress" in hooks
        assert "SessionEnd" in hooks

    def test_gemini_cli_hook_names_are_prefixed(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "gemini-cli",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-gemini-hook",
            hooks_only=True,
        )
        parsed = json.loads(merged)
        hook = parsed["hooks"]["SessionStart"][0]["hooks"][0]
        assert hook["name"] == "eve-memory-session-start"
        assert hook["type"] == "command"
        assert hook["command"] == "/usr/local/bin/eve-gemini-hook session_start"

    def test_gemini_cli_hook_timeouts_are_in_milliseconds(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "gemini-cli",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-gemini-hook",
            hooks_only=True,
        )
        parsed = json.loads(merged)
        assert parsed["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] == 8000
        assert parsed["hooks"]["BeforeAgent"][0]["hooks"][0]["timeout"] == 8000
        assert parsed["hooks"]["PreCompress"][0]["hooks"][0]["timeout"] == 20000
        assert parsed["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 35000

    def test_gemini_cli_hook_event_names_differ_from_claude(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "gemini-cli",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-gemini-hook",
            hooks_only=True,
        )
        parsed = json.loads(merged)
        assert "BeforeAgent" in parsed["hooks"]
        assert "UserPromptSubmit" not in parsed["hooks"]
        assert "PreCompress" in parsed["hooks"]
        assert "PreCompact" not in parsed["hooks"]

    def test_gemini_cli_hooks_detected(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        merged = merge_json_config(
            config,
            "gemini-cli",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-gemini-hook",
            hooks_only=True,
        )
        config.write_text(merged, encoding="utf-8")
        assert has_eve_gemini_hooks(config) is True
        assert has_eve_claude_hooks(config) is False

    def test_gemini_cli_hooks_idempotent(self, tmp_path: Path) -> None:
        config = tmp_path / "settings.json"
        first = merge_json_config(
            config,
            "gemini-cli",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-gemini-hook",
            hooks_only=True,
        )
        config.write_text(first, encoding="utf-8")
        second = merge_json_config(
            config,
            "gemini-cli",
            "https://mcp.evemem.com",
            "key",
            hook_command="/usr/local/bin/eve-gemini-hook",
            hooks_only=True,
        )
        parsed = json.loads(second)
        for event_entries in parsed["hooks"].values():
            eve_entries = [
                e
                for e in event_entries
                if any("eve-gemini-hook" in h.get("command", "") for h in e.get("hooks", []))
            ]
            assert len(eve_entries) == 1

    def test_gemini_cli_hooks_disabled_omits_hook_action(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        detected = DetectedTool(
            name="gemini-cli",
            config_path=tmp_path / ".gemini" / "settings.json",
            config_format="json",
            supports_hooks=True,
            binary_found=True,
            config_exists=False,
        )
        plan = GeminiCliProvider().build_plan(
            detected,
            "https://mcp.evemem.com",
            hooks_enabled=False,
        )
        hook_actions = [a for a in plan.actions if a.action_type == "write_hooks_config"]
        assert hook_actions == []
        assert len(plan.actions) == 3

    # --- Codex CLI ---

    def test_codex_cli_has_no_hooks_support(self, tmp_path: Path) -> None:
        with patch("eve_client.detect.base._home", return_value=tmp_path):
            tools = detect_tools(only=["codex-cli"])
        assert tools[0].supports_hooks is False

    def test_codex_cli_plan_has_no_hook_actions(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        detected = DetectedTool(
            name="codex-cli",
            config_path=tmp_path / ".codex" / "config.toml",
            config_format="toml",
            supports_hooks=False,
            binary_found=True,
            config_exists=False,
        )
        plan = CodexCliProvider().build_plan(detected, "https://mcp.evemem.com")
        action_types = [a.action_type for a in plan.actions]
        assert "write_hooks_config" not in action_types
        assert "write_config" in action_types
        assert "create_companion_file" in action_types

    def test_codex_cli_oauth_plan_has_no_auth_setup(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        detected = DetectedTool(
            name="codex-cli",
            config_path=tmp_path / ".codex" / "config.toml",
            config_format="toml",
            supports_hooks=False,
            binary_found=True,
            config_exists=False,
        )
        plan = CodexCliProvider().build_plan(detected, "https://mcp.evemem.com", auth_mode="oauth")
        action_types = [a.action_type for a in plan.actions]
        assert "auth_setup" not in action_types
        assert len(plan.actions) == 2

    def test_codex_cli_api_key_plan_has_auth_setup(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        detected = DetectedTool(
            name="codex-cli",
            config_path=tmp_path / ".codex" / "config.toml",
            config_format="toml",
            supports_hooks=False,
            binary_found=True,
            config_exists=False,
        )
        plan = CodexCliProvider().build_plan(
            detected, "https://mcp.evemem.com", auth_mode="api-key"
        )
        action_types = [a.action_type for a in plan.actions]
        assert "auth_setup" in action_types
        assert len(plan.actions) == 3
