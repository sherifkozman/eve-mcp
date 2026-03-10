"""Tool provider registry."""

from __future__ import annotations

from eve_client.integrations.claude_code import ClaudeCodeProvider
from eve_client.integrations.claude_desktop import ClaudeDesktopProvider
from eve_client.integrations.codex_cli import CodexCliProvider
from eve_client.integrations.gemini_cli import GeminiCliProvider
from eve_client.integrations.provider import ToolProvider
from eve_client.models import ToolName

_PROVIDERS: dict[ToolName, type[ToolProvider]] = {
    "claude-code": ClaudeCodeProvider,
    "claude-desktop": ClaudeDesktopProvider,
    "codex-cli": CodexCliProvider,
    "gemini-cli": GeminiCliProvider,
}


def get_adapter(tool: ToolName) -> ToolProvider:
    return _PROVIDERS[tool]()
