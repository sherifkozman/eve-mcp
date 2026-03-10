"""Config merge/render helpers for tool configs.

Merge semantics are intentionally narrow and stable:

- JSON config:
  - preserve all existing keys
  - preserve all existing `mcpServers` entries
  - replace only the `mcpServers["eve-memory"]` entry
- TOML config:
  - preserve all existing top-level tables/keys
  - preserve all existing `mcp_servers` entries
  - replace only `mcp_servers["eve-memory"]`

This is a shallow merge around the dedicated Eve namespace, not a general-purpose
deep merge engine.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from eve_client.models import AuthMode, ToolName

MARKER_BEGIN = "<!-- EVE-BEGIN:{tool}:v1 -->"
MARKER_END = "<!-- EVE-END:{tool}:v1 -->"
TOML_SECTION_HEADER = '[mcp_servers."eve-memory"]'
SOURCE_AGENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
CLAUDE_HOOK_MARKERS = ("eve-claude-hook", "eve_client.claude_hooks")
GEMINI_HOOK_MARKERS = ("eve-gemini-hook", "eve_client.gemini_hooks")


def _source_agent(tool: ToolName) -> str:
    return tool.replace("-", "_")


def source_agent_header(tool: ToolName) -> str:
    value = _source_agent(tool)
    if not SOURCE_AGENT_RE.fullmatch(value):
        raise ValueError(f"Unsafe source-agent header value: {value}")
    return value


def _build_headers(tool: ToolName, auth_mode: AuthMode, credential: str | None) -> dict[str, str]:
    if auth_mode == "oauth":
        headers = {"X-Source-Agent": _source_agent(tool)}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return headers
    return {"X-API-Key": credential, "X-Source-Agent": _source_agent(tool)}


def build_mcp_json_entry(
    tool: ToolName,
    mcp_base_url: str,
    credential: str | None,
    *,
    auth_mode: AuthMode = "api-key",
) -> dict[str, Any]:
    key = "eve-memory"
    if tool == "gemini-cli":
        return {
            key: {
                "httpUrl": mcp_base_url,
                "headers": _build_headers(tool, auth_mode, credential),
            }
        }
    return {
        key: {
            "type": "http",
            "url": mcp_base_url,
            "headers": _build_headers(tool, auth_mode, credential),
        }
    }


def _allowed_json_entry_keys(tool: ToolName) -> tuple[set[str], set[str]]:
    if tool == "gemini-cli":
        return {"httpUrl", "headers"}, {"X-API-Key", "X-Source-Agent", "Authorization"}
    return {"type", "url", "headers"}, {"X-API-Key", "X-Source-Agent", "Authorization"}


def merge_json_config(
    config_path: Path,
    tool: ToolName,
    mcp_base_url: str,
    credential: str | None,
    *,
    auth_mode: AuthMode = "api-key",
    hook_command: str | None = None,
    hooks_only: bool = False,
) -> str:
    """Merge Eve config into a JSON tool config without disturbing other entries."""
    existing: dict[str, Any]
    existing = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if not hooks_only:
        existing.setdefault("mcpServers", {})
        existing["mcpServers"].update(
            build_mcp_json_entry(tool, mcp_base_url, credential, auth_mode=auth_mode)
        )
    if hook_command:
        if tool == "claude-code":
            existing["hooks"] = _merge_claude_hooks(existing.get("hooks"), hook_command)
        elif tool == "gemini-cli":
            existing["hooks"] = _merge_gemini_hooks(existing.get("hooks"), hook_command)
    return json.dumps(existing, indent=2) + "\n"


def has_eve_json_entry(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    return "eve-memory" in existing.get("mcpServers", {})


def eve_json_entry_has_unknown_fields(config_path: Path, tool: ToolName) -> bool:
    if not config_path.exists():
        return False
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    entry = existing.get("mcpServers", {}).get("eve-memory")
    if not isinstance(entry, dict):
        return False
    allowed_keys, allowed_header_keys = _allowed_json_entry_keys(tool)
    entry_keys = set(entry.keys())
    if not entry_keys.issubset(allowed_keys):
        return True
    headers = entry.get("headers", {})
    if not isinstance(headers, dict):
        return True
    if not set(headers.keys()).issubset(allowed_header_keys):
        return True
    if tool == "claude-code":
        return claude_hooks_have_unknown_fields(config_path)
    if tool == "gemini-cli":
        return gemini_hooks_have_unknown_fields(config_path)
    return False


def remove_json_config(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    servers = existing.get("mcpServers", {})
    if "eve-memory" not in servers:
        return json.dumps(existing, indent=2) + "\n"
    del servers["eve-memory"]
    if not servers and "mcpServers" in existing:
        del existing["mcpServers"]
    if _has_claude_hook_entries(existing):
        _remove_claude_hooks(existing)
    if _has_gemini_hook_entries(existing):
        _remove_gemini_hooks(existing)
    return json.dumps(existing, indent=2) + "\n"


def remove_claude_hooks_json_config(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    if _has_claude_hook_entries(existing):
        _remove_claude_hooks(existing)
    return json.dumps(existing, indent=2) + "\n"


def remove_gemini_hooks_json_config(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    if _has_gemini_hook_entries(existing):
        _remove_gemini_hooks(existing)
    return json.dumps(existing, indent=2) + "\n"


def merge_toml_config(
    config_path: Path,
    tool: ToolName,
    mcp_base_url: str,
    credential: str | None,
    *,
    auth_mode: AuthMode = "api-key",
) -> str:
    """Merge Eve config into TOML while preserving surrounding formatting/comments."""
    if tool == "codex-cli":
        snippet = _build_codex_toml_snippet(mcp_base_url, credential, auth_mode=auth_mode)
    else:
        if auth_mode == "oauth":
            header_fragment = (
                f'"Authorization" = "Bearer {credential}", "X-Source-Agent" = "{_source_agent(tool)}"'
                if credential
                else f'"X-Source-Agent" = "{_source_agent(tool)}"'
            )
        else:
            header_fragment = (
                f'"X-API-Key" = "{credential}", "X-Source-Agent" = "{_source_agent(tool)}"'
            )
        snippet = (
            f"{TOML_SECTION_HEADER}\n"
            f'url = "{mcp_base_url}"\n'
            f"env_http_headers = {{ {header_fragment} }}\n"
        )
    if not config_path.exists():
        return snippet
    content = config_path.read_text(encoding="utf-8")
    if TOML_SECTION_HEADER not in content:
        suffix = "" if not content or content.endswith("\n") else "\n"
        return f"{content}{suffix}{snippet}"
    return _replace_toml_section(content, snippet)


def has_eve_toml_entry(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    return TOML_SECTION_HEADER in config_path.read_text(encoding="utf-8")


def eve_toml_entry_has_unknown_fields(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    section = _extract_toml_section(config_path.read_text(encoding="utf-8"))
    if not section:
        return False
    allowed_prefixes = {
        "url =",
        "startup_timeout_sec =",
        "env_http_headers = {",
        "bearer_token_env_var =",
        '[mcp_servers."eve-memory".http_headers]',
    }
    for raw_line in section.splitlines()[1:]:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not any(line.startswith(prefix) for prefix in allowed_prefixes):
            return True
        if line.startswith("env_http_headers = {"):
            if '"X-Source-Agent"' not in line:
                return True
            if '"X-API-Key"' not in line and '"Authorization"' not in line:
                return True
        if line.startswith("bearer_token_env_var =") and '"EVE_' not in line:
            return True
    return False


def _build_codex_toml_snippet(
    mcp_base_url: str, credential: str | None, *, auth_mode: AuthMode
) -> str:
    lines = [
        f"{TOML_SECTION_HEADER}",
        f'url = "{mcp_base_url}"',
        "startup_timeout_sec = 60",
    ]
    if auth_mode == "oauth":
        lines.extend(
            [
                'bearer_token_env_var = "EVE_CODEX_BEARER_TOKEN"',
                "",
                '[mcp_servers."eve-memory".http_headers]',
                f'X-Source-Agent = "{_source_agent("codex-cli")}"',
            ]
        )
    else:
        lines.extend(
            [
                "",
                '[mcp_servers."eve-memory".http_headers]',
                f'X-API-Key = "{credential}"',
                f'X-Source-Agent = "{_source_agent("codex-cli")}"',
            ]
        )
    return "\n".join(lines) + "\n"


def remove_toml_config(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    content = config_path.read_text(encoding="utf-8")
    if TOML_SECTION_HEADER not in content:
        return content
    return _remove_toml_section(content)


def companion_content(tool: ToolName, mcp_base_url: str) -> str:
    begin = MARKER_BEGIN.format(tool=tool)
    end = MARKER_END.format(tool=tool)
    guidance = {
        "claude-code": (
            "## Eve Memory Protocol\n"
            "Eve is your persistent memory layer for relevant context, preferences, prior decisions, and durable project knowledge.\n\n"
            "### What Eve already does automatically\n"
            "- SessionStart hooks load relevant context and preferences at the beginning of a Claude session.\n"
            "- UserPromptSubmit hooks enrich the current prompt with relevant memories when useful.\n"
            "- SessionEnd and PreCompact hooks preserve notable session knowledge.\n\n"
            "### Use Eve tools explicitly when\n"
            "- a prompt depends on past decisions, preferences, or context that is not already visible\n"
            "- you make a durable architecture, product, or workflow decision\n"
            "- the user states a stable preference, correction, or constraint\n"
            "- you need to forget or correct stale memory\n\n"
            "### Read discipline\n"
            "- Search Eve before claiming you do not know prior context.\n"
            "- Prefer Eve search over re-deciding something that may already have been decided.\n"
            "- Use the memories already injected by hooks first; search again only when you need more.\n\n"
            "### Write discipline\n"
            "- Store only durable, reusable information.\n"
            "- Do not write every intermediate thought or routine step.\n"
            "- Favor concise memories with enough detail to be useful later.\n\n"
            "### Session behavior\n"
            "- Treat Eve as the long-term memory layer, not as scratchpad storage.\n"
            "- Keep tool use focused: search, store, forget, extract, and session lifecycle actions when needed."
        ),
        "codex-cli": (
            "## Eve Memory Protocol\n"
            "Eve is your durable memory layer across coding sessions.\n\n"
            "### Use Eve when\n"
            "- you need prior decisions, preferences, or project context\n"
            "- you make a reusable decision worth carrying into future sessions\n"
            "- the user corrects or updates a durable fact or preference\n\n"
            "### Read discipline\n"
            "- Search Eve before assuming past context is unavailable.\n"
            "- Reuse durable knowledge instead of re-deriving it.\n\n"
            "### Write discipline\n"
            "- Store durable decisions, preferences, fixes, and notable outcomes.\n"
            "- Skip routine chatter and transient reasoning."
        ),
        "gemini-cli": (
            "## Eve Memory Protocol\n"
            "Eve is your durable memory layer for relevant context, preferences, and prior decisions.\n\n"
            "### Use Eve when\n"
            "- prior context or preferences would improve the next answer\n"
            "- a user gives a stable preference, correction, or decision\n"
            "- you want to preserve a useful outcome for a future session\n\n"
            "### Read discipline\n"
            "- Search Eve before saying prior context is missing.\n"
            "- Prefer relevant existing memories over repeating discovery work.\n\n"
            "### Write discipline\n"
            "- Store concise durable memories, not full transcripts.\n"
            "- Keep entries specific enough to be useful later."
        ),
    }[tool]
    return f"{begin}\n# Eve companion\n\n{guidance}\n\nMCP endpoint: `{mcp_base_url}`\n{end}\n"


def companion_markers(tool: ToolName) -> tuple[str, str]:
    return MARKER_BEGIN.format(tool=tool), MARKER_END.format(tool=tool)


def is_eve_companion_file(path: Path, tool: ToolName) -> bool:
    if not path.exists():
        return False
    begin, end = companion_markers(tool)
    content = path.read_text(encoding="utf-8")
    return begin in content and end in content


def merge_companion_file(path: Path, tool: ToolName, mcp_base_url: str) -> str:
    block = companion_content(tool, mcp_base_url).rstrip() + "\n"
    if not path.exists():
        return block
    content = path.read_text(encoding="utf-8")
    if is_eve_companion_file(path, tool):
        return replace_companion_block(content, tool, block)
    suffix = (
        ""
        if not content or content.endswith("\n\n")
        else ("\n" if content.endswith("\n") else "\n\n")
    )
    return f"{content}{suffix}{block}"


def remove_companion_file(path: Path, tool: ToolName) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    if not is_eve_companion_file(path, tool):
        return content
    return remove_companion_block(content, tool)


def replace_companion_block(content: str, tool: ToolName, block: str) -> str:
    begin, end = companion_markers(tool)
    pattern = re.compile(rf"{re.escape(begin)}.*?{re.escape(end)}\n?", re.S)
    return pattern.sub(block, content, count=1)


def remove_companion_block(content: str, tool: ToolName) -> str:
    begin, end = companion_markers(tool)
    pattern = re.compile(rf"\n?{re.escape(begin)}.*?{re.escape(end)}\n?", re.S)
    updated = pattern.sub("\n", content, count=1)
    updated = re.sub(r"\n{3,}", "\n\n", updated).strip()
    return f"{updated}\n" if updated else ""


def _build_claude_hook_entries(hook_command: str) -> dict[str, Any]:
    def command(event: str) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "type": "command",
            "command": f"{hook_command} {event}",
        }
        if event in {"session_start", "prompt_enrich"}:
            entry["timeout"] = 5
        else:
            entry["timeout"] = 15
        if event == "session_end":
            entry["async"] = True
        return entry

    return {
        "SessionStart": [
            {
                "matcher": "startup|resume",
                "hooks": [command("session_start")],
            }
        ],
        "PreCompact": [{"hooks": [command("pre_compact")]}],
        "SessionEnd": [{"hooks": [command("session_end")]}],
        "UserPromptSubmit": [{"hooks": [command("prompt_enrich")]}],
    }


def _build_gemini_hook_entries(hook_command: str) -> dict[str, Any]:
    def command(name: str, event: str, *, timeout: int) -> dict[str, Any]:
        return {
            "name": name,
            "type": "command",
            "command": f"{hook_command} {event}",
            "description": f"Eve Gemini hook: {event}",
            "timeout": timeout,
        }

    return {
        "SessionStart": [
            {"hooks": [command("eve-memory-session-start", "session_start", timeout=8000)]}
        ],
        "BeforeAgent": [
            {"hooks": [command("eve-memory-prompt-enrich", "prompt_enrich", timeout=8000)]}
        ],
        "PreCompress": [
            {"hooks": [command("eve-memory-pre-compress", "pre_compact", timeout=20000)]}
        ],
        "SessionEnd": [
            {"hooks": [command("eve-memory-session-end", "session_end", timeout=35000)]}
        ],
    }


def _merge_claude_hooks(existing: Any, hook_command: str) -> dict[str, Any]:
    hooks = existing if isinstance(existing, dict) else {}
    eve_hooks = _build_claude_hook_entries(hook_command)
    for event_name, entries in eve_hooks.items():
        current_entries = hooks.get(event_name, [])
        if not isinstance(current_entries, list):
            current_entries = []
        current_entries = [entry for entry in current_entries if not _is_eve_hook_entry(entry)]
        current_entries.extend(entries)
        hooks[event_name] = current_entries
    return hooks


def _merge_gemini_hooks(existing: Any, hook_command: str) -> dict[str, Any]:
    hooks = existing if isinstance(existing, dict) else {}
    eve_hooks = _build_gemini_hook_entries(hook_command)
    for event_name, entries in eve_hooks.items():
        current_entries = hooks.get(event_name, [])
        if not isinstance(current_entries, list):
            current_entries = []
        current_entries = [
            entry for entry in current_entries if not _is_eve_gemini_hook_entry(entry)
        ]
        current_entries.extend(entries)
        hooks[event_name] = current_entries
    return hooks


def _is_eve_hook_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
        if any(marker in command for marker in CLAUDE_HOOK_MARKERS):
            return True
    return False


def _is_eve_gemini_hook_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
        if any(marker in command for marker in GEMINI_HOOK_MARKERS):
            return True
    return False


def _has_claude_hook_entries(existing: dict[str, Any]) -> bool:
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return any(
        _is_eve_hook_entry(entry)
        for entries in hooks.values()
        if isinstance(entries, list)
        for entry in entries
    )


def _has_gemini_hook_entries(existing: dict[str, Any]) -> bool:
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return any(
        _is_eve_gemini_hook_entry(entry)
        for entries in hooks.values()
        if isinstance(entries, list)
        for entry in entries
    )


def _remove_claude_hooks(existing: dict[str, Any]) -> None:
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event_name in list(hooks.keys()):
        entries = hooks.get(event_name)
        if not isinstance(entries, list):
            continue
        filtered = [entry for entry in entries if not _is_eve_hook_entry(entry)]
        if filtered:
            hooks[event_name] = filtered
        else:
            del hooks[event_name]
    if not hooks:
        existing.pop("hooks", None)


def _remove_gemini_hooks(existing: dict[str, Any]) -> None:
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event_name in list(hooks.keys()):
        entries = hooks.get(event_name)
        if not isinstance(entries, list):
            continue
        filtered = [entry for entry in entries if not _is_eve_gemini_hook_entry(entry)]
        if filtered:
            hooks[event_name] = filtered
        else:
            del hooks[event_name]
    if not hooks:
        existing.pop("hooks", None)


def has_eve_claude_hooks(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    return isinstance(existing, dict) and _has_claude_hook_entries(existing)


def has_eve_gemini_hooks(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    return isinstance(existing, dict) and _has_gemini_hook_entries(existing)


def claude_hooks_have_unknown_fields(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event_name, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not _is_eve_hook_entry(entry):
                continue
            if set(entry.keys()) - {"matcher", "hooks"}:
                return True
            hook_entries = entry.get("hooks")
            if not isinstance(hook_entries, list) or len(hook_entries) != 1:
                return True
            hook = hook_entries[0]
            if not isinstance(hook, dict):
                return True
            allowed = {"type", "command", "timeout", "async"}
            if set(hook.keys()) - allowed:
                return True
            command = str(hook.get("command", ""))
            if hook.get("type") != "command" or not any(
                marker in command for marker in CLAUDE_HOOK_MARKERS
            ):
                return True
            if event_name in {"SessionStart", "UserPromptSubmit"} and hook.get("timeout") != 5:
                return True
            if event_name in {"PreCompact", "SessionEnd"} and hook.get("timeout") != 15:
                return True
    return False


def gemini_hooks_have_unknown_fields(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for _event_name, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not _is_eve_gemini_hook_entry(entry):
                continue
            if set(entry.keys()) != {"hooks"}:
                return True
            hook_entries = entry.get("hooks")
            if not isinstance(hook_entries, list) or len(hook_entries) != 1:
                return True
            hook = hook_entries[0]
            if not isinstance(hook, dict):
                return True
            allowed = {"name", "type", "command", "description", "timeout"}
            if set(hook.keys()) - allowed:
                return True
            command = str(hook.get("command", ""))
            if hook.get("type") != "command" or not any(
                marker in command for marker in GEMINI_HOOK_MARKERS
            ):
                return True
    return False


def _find_toml_section_bounds(content: str) -> tuple[int, int] | None:
    lines = content.splitlines(keepends=True)
    start = None
    cursor = 0
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped == TOML_SECTION_HEADER:
            start = cursor
            end = cursor + len(line)
            for remainder in lines[idx + 1 :]:
                stripped_remainder = remainder.strip()
                if (
                    stripped_remainder.startswith("[")
                    and stripped_remainder.endswith("]")
                    and not stripped_remainder.startswith('[mcp_servers."eve-memory".')
                ):
                    break
                end += len(remainder)
            return start, end
        cursor += len(line)
    return None


def _extract_toml_section(content: str) -> str | None:
    bounds = _find_toml_section_bounds(content)
    if bounds is None:
        return None
    start, end = bounds
    return content[start:end]


def _replace_toml_section(content: str, snippet: str) -> str:
    bounds = _find_toml_section_bounds(content)
    if bounds is None:
        return content
    start, end = bounds
    return f"{content[:start]}{snippet}{content[end:]}"


def _remove_toml_section(content: str) -> str:
    bounds = _find_toml_section_bounds(content)
    if bounds is None:
        return content
    start, end = bounds
    updated = f"{content[:start]}{content[end:]}"
    while "\n\n\n" in updated:
        updated = updated.replace("\n\n\n", "\n\n")
    return updated.lstrip("\n")
