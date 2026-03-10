from __future__ import annotations

from pathlib import Path

from eve_client.config import ResolvedConfig
from eve_client.integrations.claude_desktop import ClaudeDesktopProvider
from eve_client.models import DetectedTool
from eve_client.plan import build_install_plan


def test_build_install_plan_marks_desktop_disabled() -> None:
    config = ResolvedConfig(
        config_dir=Path("/tmp/eve-config"),
        config_path=Path("/tmp/eve-config/config.json"),
        state_dir=Path("/tmp/eve"),
        project_root=Path("/tmp/project"),
        mcp_base_url="https://mcp.evemem.com",
        mcp_server_name="eve-memory",
        environment="production",
        feature_claude_desktop=False,
        codex_enabled=True,
        codex_source="config",
        allow_file_secret_fallback=True,
    )
    detected = [
        DetectedTool(
            name="claude-desktop",
            config_path=Path("/tmp/claude_desktop_config.json"),
            config_format="json",
            supports_hooks=False,
            binary_found=True,
            config_exists=True,
            feature_flag_required=True,
            feature_gate="claude-desktop",
        )
    ]
    plan = build_install_plan(detected, config)
    assert len(plan.tool_plans) == 1
    assert plan.tool_plans[0].supported is False
    assert "disabled" in (plan.tool_plans[0].reason or "")


def test_build_install_plan_allows_desktop_when_enabled() -> None:
    config = ResolvedConfig(
        config_dir=Path("/tmp/eve-config"),
        config_path=Path("/tmp/eve-config/config.json"),
        state_dir=Path("/tmp/eve"),
        project_root=Path("/tmp/project"),
        mcp_base_url="https://mcp.evemem.com",
        mcp_server_name="eve-memory",
        environment="production",
        feature_claude_desktop=True,
        codex_enabled=True,
        codex_source="config",
        allow_file_secret_fallback=True,
    )
    detected = [
        DetectedTool(
            name="claude-desktop",
            config_path=Path("/tmp/claude_desktop_config.json"),
            config_format="json",
            supports_hooks=False,
            binary_found=True,
            config_exists=True,
            feature_flag_required=True,
            feature_gate="claude-desktop",
        )
    ]
    plan = build_install_plan(detected, config)
    assert plan.tool_plans[0].supported is False
    assert "Settings > Connectors" in (plan.tool_plans[0].reason or "")
    assert plan.tool_plans[0].actions == []


def test_claude_desktop_provider_is_instructional_only() -> None:
    provider = ClaudeDesktopProvider()
    detected = DetectedTool(
        name="claude-desktop",
        config_path=Path("/tmp/claude_desktop_config.json"),
        config_format="json",
        supports_hooks=False,
        binary_found=True,
        config_exists=True,
        feature_flag_required=True,
        feature_gate="claude-desktop",
    )
    plan = provider.build_plan(detected, "https://mcp.evemem.com")
    assert plan.supported is False
    assert plan.actions == []
    assert "Settings > Connectors" in (plan.reason or "")


def test_build_install_plan_for_claude_code() -> None:
    config = ResolvedConfig(
        config_dir=Path("/tmp/eve-config"),
        config_path=Path("/tmp/eve-config/config.json"),
        state_dir=Path("/tmp/eve"),
        project_root=Path("/tmp/project"),
        mcp_base_url="https://mcp.evemem.com",
        mcp_server_name="eve-memory",
        environment="production",
        feature_claude_desktop=False,
        codex_enabled=True,
        codex_source="config",
        allow_file_secret_fallback=True,
    )
    detected = [
        DetectedTool(
            name="claude-code",
            config_path=Path("/tmp/.claude/settings.json"),
            config_format="json",
            supports_hooks=True,
            binary_found=True,
            config_exists=False,
        )
    ]
    plan = build_install_plan(detected, config)
    assert plan.tool_plans[0].tool == "claude-code"
    assert plan.tool_plans[0].auth_mode == "api-key"
    assert len(plan.tool_plans[0].actions) == 4


def test_build_install_plan_supports_codex_by_default() -> None:
    config = ResolvedConfig(
        config_dir=Path("/tmp/eve-config"),
        config_path=Path("/tmp/eve-config/config.json"),
        state_dir=Path("/tmp/eve"),
        project_root=Path("/tmp/project"),
        mcp_base_url="https://mcp.evemem.com",
        mcp_server_name="eve-memory",
        environment="production",
        feature_claude_desktop=False,
        codex_enabled=True,
        codex_source="config",
        allow_file_secret_fallback=True,
    )
    detected = [
        DetectedTool(
            name="codex-cli",
            config_path=Path("/tmp/.codex/config.toml"),
            config_format="toml",
            supports_hooks=False,
            binary_found=True,
            config_exists=False,
        )
    ]
    plan = build_install_plan(detected, config)
    assert plan.tool_plans[0].tool == "codex-cli"
    assert plan.tool_plans[0].supported is True
    assert plan.tool_plans[0].auth_mode == "oauth"
    assert "oauth" in plan.tool_plans[0].supported_auth_modes
    assert len(plan.tool_plans[0].actions) == 2


def test_build_install_plan_disables_codex_when_config_disabled() -> None:
    config = ResolvedConfig(
        config_dir=Path("/tmp/eve-config"),
        config_path=Path("/tmp/eve-config/config.json"),
        state_dir=Path("/tmp/eve"),
        project_root=Path("/tmp/project"),
        mcp_base_url="https://mcp.evemem.com",
        mcp_server_name="eve-memory",
        environment="production",
        feature_claude_desktop=False,
        codex_enabled=False,
        codex_source="default",
        allow_file_secret_fallback=True,
    )
    detected = [
        DetectedTool(
            name="codex-cli",
            config_path=Path("/tmp/.codex/config.toml"),
            config_format="toml",
            supports_hooks=False,
            binary_found=True,
            config_exists=False,
        )
    ]
    plan = build_install_plan(detected, config)
    assert plan.tool_plans[0].supported is False
    assert plan.tool_plans[0].actions == []
    assert "disabled by default" in (plan.tool_plans[0].reason or "")
