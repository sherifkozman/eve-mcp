from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from eve_client.detect.base import detect_tools


def test_detect_tools_project_scoped_claude(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    with patch("eve_client.detect.base._home", return_value=tmp_path):
        tools = detect_tools(only=["claude-code"], project_scoped=True)
    assert len(tools) == 1
    assert tools[0].config_path == tmp_path / ".mcp.json"


def test_detect_tools_desktop_feature_flag(tmp_path: Path) -> None:
    with patch("eve_client.detect.base._home", return_value=tmp_path):
        tools = detect_tools(only=["claude-desktop"], enable_claude_desktop=False)
    assert len(tools) == 1
    assert tools[0].feature_flag_required is True
    assert tools[0].feature_gate == "claude-desktop"
    assert tools[0].binary_found is False


def test_detect_tools_codex_is_supported_by_default(tmp_path: Path) -> None:
    with patch("eve_client.detect.base._home", return_value=tmp_path):
        tools = detect_tools(only=["codex-cli"])
    assert len(tools) == 1
    assert tools[0].feature_flag_required is False
    assert tools[0].feature_gate is None
