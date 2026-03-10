from __future__ import annotations

import tomllib
from pathlib import Path

from eve_client.merge import (
    companion_content,
    has_eve_claude_hooks,
    has_eve_gemini_hooks,
    has_eve_json_entry,
    has_eve_toml_entry,
    is_eve_companion_file,
    merge_companion_file,
    merge_json_config,
    merge_toml_config,
    remove_json_config,
    remove_toml_config,
)


def test_merge_json_config_adds_eve_entry(tmp_path: Path) -> None:
    config = tmp_path / ".claude" / "settings.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"mcpServers": {"other": {"url": "https://example.com"}}}', encoding="utf-8")
    merged = merge_json_config(
        config,
        "claude-code",
        "https://mcp.evemem.com",
        "eve-key",
        hook_command="/tmp/eve-claude-hook",
    )
    assert '"other"' in merged
    assert '"eve-memory"' in merged
    assert '"X-API-Key": "eve-key"' in merged
    assert "/tmp/eve-claude-hook session_start" in merged


def test_merge_json_config_oauth_omits_bearer_when_not_supplied(tmp_path: Path) -> None:
    config = tmp_path / ".gemini" / "settings.json"
    config.parent.mkdir(parents=True)
    merged = merge_json_config(
        config,
        "gemini-cli",
        "https://mcp.evemem.com",
        None,
        auth_mode="oauth",
    )
    assert '"httpUrl": "https://mcp.evemem.com"' in merged
    assert '"Authorization"' not in merged
    assert '"X-Source-Agent": "gemini_cli"' in merged


def test_merge_json_config_adds_gemini_hooks(tmp_path: Path) -> None:
    config = tmp_path / ".gemini" / "settings.json"
    config.parent.mkdir(parents=True)
    merged = merge_json_config(
        config,
        "gemini-cli",
        "https://mcp.evemem.com",
        "eve-key",
        hook_command="/tmp/eve-gemini-hook",
        hooks_only=True,
    )
    assert "/tmp/eve-gemini-hook session_start" in merged
    assert "eve-memory-session-start" in merged
    config.write_text(merged, encoding="utf-8")
    assert has_eve_gemini_hooks(config) is True


def test_merge_toml_config_preserves_other_entries(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.other]\nurl = "https://example.com"\n', encoding="utf-8")
    merged = merge_toml_config(config, "codex-cli", "https://mcp.evemem.com", "eve-key")
    parsed = tomllib.loads(merged)
    assert "other" in parsed["mcp_servers"]
    assert parsed["mcp_servers"]["eve-memory"]["url"] == "https://mcp.evemem.com"
    assert parsed["mcp_servers"]["eve-memory"]["startup_timeout_sec"] == 60
    assert parsed["mcp_servers"]["eve-memory"]["http_headers"]["X-API-Key"] == "eve-key"


def test_merge_toml_config_oauth_omits_bearer_when_not_supplied(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    merged = merge_toml_config(
        config,
        "codex-cli",
        "https://mcp.evemem.com",
        None,
        auth_mode="oauth",
    )
    parsed = tomllib.loads(merged)
    assert parsed["mcp_servers"]["eve-memory"]["url"] == "https://mcp.evemem.com"
    assert parsed["mcp_servers"]["eve-memory"]["startup_timeout_sec"] == 60
    assert parsed["mcp_servers"]["eve-memory"]["bearer_token_env_var"] == "EVE_CODEX_BEARER_TOKEN"
    assert "Authorization" not in parsed["mcp_servers"]["eve-memory"].get("http_headers", {})
    assert parsed["mcp_servers"]["eve-memory"]["http_headers"]["X-Source-Agent"] == "codex_cli"


def test_companion_content_contains_markers() -> None:
    content = companion_content("gemini-cli", "https://mcp.evemem.com")
    assert "EVE-BEGIN:gemini-cli:v1" in content
    assert "MCP endpoint:" in content
    assert "## Eve Memory Protocol" in content
    assert "### Read discipline" in content
    assert "### Write discipline" in content


def test_merge_companion_file_appends_to_existing_agents_md(tmp_path: Path) -> None:
    companion = tmp_path / "AGENTS.md"
    companion.write_text("# Team agents\n\nPrefer concise diffs.\n", encoding="utf-8")
    merged = merge_companion_file(companion, "codex-cli", "https://mcp.evemem.com/mcp")
    assert "# Team agents" in merged
    assert "## Eve Memory Protocol" in merged
    assert "EVE-BEGIN:codex-cli:v1" in merged
    assert "MCP endpoint:" in merged


def test_remove_json_config_only_removes_eve_entry(tmp_path: Path) -> None:
    config = tmp_path / ".claude" / "settings.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        '{"mcpServers": {"eve-memory": {"url": "https://mcp.evemem.com"}, "other": {"url": "https://example.com"}}}',
        encoding="utf-8",
    )
    updated = remove_json_config(config)
    assert '"eve-memory"' not in updated
    assert '"other"' in updated


def test_remove_toml_config_only_removes_eve_entry(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.other]\nurl = "https://example.com"\n\n[mcp_servers."eve-memory"]\nurl = "https://mcp.evemem.com"\n',
        encoding="utf-8",
    )
    updated = remove_toml_config(config)
    parsed = tomllib.loads(updated)
    assert "eve-memory" not in parsed["mcp_servers"]
    assert "other" in parsed["mcp_servers"]


def test_toml_merge_and_remove_preserve_comments_and_adjacent_formatting(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = '# top comment\n[mcp_servers.other]\nurl = "https://example.com"\n# other comment\n'
    config.write_text(original, encoding="utf-8")
    merged = merge_toml_config(config, "codex-cli", "https://mcp.evemem.com", "eve-key")
    assert (
        '# top comment\n[mcp_servers.other]\nurl = "https://example.com"\n# other comment\n'
        in merged
    )
    config.write_text(merged, encoding="utf-8")
    removed = remove_toml_config(config)
    assert removed == original


def test_has_eve_entries_and_companion_detection(tmp_path: Path) -> None:
    json_config = tmp_path / ".claude" / "settings.json"
    json_config.parent.mkdir(parents=True)
    json_config.write_text(
        merge_json_config(
            json_config,
            "claude-code",
            "https://mcp.evemem.com",
            "eve-key",
            hook_command="/tmp/eve-claude-hook",
        ),
        encoding="utf-8",
    )
    assert has_eve_json_entry(json_config) is True
    assert has_eve_claude_hooks(json_config) is True

    toml_config = tmp_path / "config.toml"
    toml_config.write_text(
        '[mcp_servers."eve-memory"]\nurl = "https://mcp.evemem.com"\n', encoding="utf-8"
    )
    assert has_eve_toml_entry(toml_config) is True

    gemini_config = tmp_path / ".gemini" / "settings.json"
    gemini_config.parent.mkdir(parents=True, exist_ok=True)
    gemini_config.write_text(
        merge_json_config(
            gemini_config,
            "gemini-cli",
            "https://mcp.evemem.com",
            "eve-key",
            hook_command="/tmp/eve-gemini-hook",
            hooks_only=True,
        ),
        encoding="utf-8",
    )
    assert has_eve_gemini_hooks(gemini_config) is True

    companion = tmp_path / ".claude" / "CLAUDE.md"
    companion.write_text(
        companion_content("claude-code", "https://mcp.evemem.com"), encoding="utf-8"
    )
    assert is_eve_companion_file(companion, "claude-code") is True


def test_merge_companion_file_appends_to_existing_active_claude_md(tmp_path: Path) -> None:
    companion = tmp_path / ".claude" / "CLAUDE.md"
    companion.parent.mkdir(parents=True)
    companion.write_text("# Team Instructions\n\nKeep responses concise.\n", encoding="utf-8")
    merged = merge_companion_file(companion, "claude-code", "https://mcp.evemem.com")
    assert "# Team Instructions" in merged
    assert "## Eve Memory Protocol" in merged
    assert "### What Eve already does automatically" in merged
    assert "### Use Eve tools explicitly when" in merged
    assert "EVE-BEGIN:claude-code:v1" in merged
