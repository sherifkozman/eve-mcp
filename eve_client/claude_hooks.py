"""Claude Code hook handlers for Eve client.

These hooks are intentionally stdlib-only and fail closed:
- never raise to Claude
- always exit 0
- only emit hook JSON when there is useful Eve context to inject
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from eve_client.auth.base import CredentialStoreUnavailableError
from eve_client.auth.local_store import LocalCredentialStore
from eve_client.config import resolve_api_base_url, resolve_config
from eve_client.merge import source_agent_header

_MAX_TRANSCRIPT_BYTES = 800_000


def _log(message: str) -> None:
    print(f"[eve-client:claude-hooks] {message}", file=sys.stderr)


def _safe_exit() -> None:
    raise SystemExit(0)


class _MemoryClient:
    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = _api_base_url(base_url)
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "X-Source-Agent": source_agent_header("claude-code"),
        }

    def _post(self, path: str, payload: dict[str, Any], *, timeout: float) -> tuple[bool, dict[str, Any] | None]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return True, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            _log(f"HTTP {exc.code} from {path}: {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            _log(f"Request to {path} failed: {exc}")
        return False, None

    def session_start(
        self,
        *,
        session_id: str | None,
        summary: str,
        recent_topics: list[str] | None,
    ) -> tuple[bool, dict[str, Any] | None]:
        payload: dict[str, Any] = {"summary": summary, "tool_name": "claude-code"}
        if session_id:
            payload["session_id"] = session_id
        if recent_topics:
            payload["recent_topics"] = recent_topics
        return self._post("/memory/session/start", payload, timeout=5.0)

    def search(self, *, query: str) -> tuple[bool, dict[str, Any] | None]:
        return self._post(
            "/memory/search",
            {"query": query[:500], "limit": 3, "store": "all", "min_similarity": 0.75},
            timeout=5.0,
        )

    def pre_compaction(
        self,
        *,
        session_id: str,
        critical_facts: list[str],
    ) -> tuple[bool, dict[str, Any] | None]:
        return self._post(
            "/memory/session/pre_compaction",
            {"session_id": session_id, "critical_facts": critical_facts, "tool_name": "claude-code"},
            timeout=15.0,
        )

    def extract(self, *, transcript: str, session_id: str | None) -> tuple[bool, dict[str, Any] | None]:
        payload: dict[str, Any] = {
            "transcript": transcript,
            "source": "claude_code",
            "auto_store": True,
            "min_importance": 4,
        }
        if session_id:
            payload["session_id"] = session_id
        return self._post("/memory/extract", payload, timeout=15.0)

    def session_end(
        self,
        *,
        summary: str,
        session_id: str | None,
        status: str | None,
    ) -> tuple[bool, dict[str, Any] | None]:
        payload: dict[str, Any] = {"summary": summary}
        if session_id:
            payload["session_id"] = session_id
        if status:
            payload["status"] = status
        return self._post("/memory/session/end", payload, timeout=5.0)


def _load_hook_input() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
        return value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _api_base_url(value: str) -> str:
    parsed = urlparse(value.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[:-4]
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


def _load_api_key() -> str | None:
    config = resolve_config()
    try:
        key, _source = LocalCredentialStore(
            config.state_dir,
            allow_file_fallback=config.allow_file_secret_fallback,
        ).get_api_key("claude-code")
        return key
    except CredentialStoreUnavailableError as exc:
        _log(str(exc))
        return None


def _read_transcript(transcript_path: str) -> str:
    if not transcript_path:
        return ""
    path = Path(transcript_path)
    if not path.is_file():
        return ""

    lines: list[str] = []
    total_bytes = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                if total_bytes >= _MAX_TRANSCRIPT_BYTES:
                    break
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                role = entry.get("role", "")
                if role not in {"human", "assistant"}:
                    continue
                content = entry.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    content = "\n".join(part for part in text_parts if part)
                if not isinstance(content, str) or not content.strip():
                    continue
                label = "User" if role == "human" else "Assistant"
                line = f"{label}: {content.strip()}"
                lines.append(line)
                total_bytes += len(line.encode("utf-8"))
    except OSError:
        return ""
    return "\n\n".join(lines)


def _extract_recent_topics(transcript_path: str, *, max_messages: int = 5) -> list[str]:
    path = Path(transcript_path)
    if not path.is_file():
        return []
    user_messages: list[str] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if entry.get("role") != "human":
                    continue
                content = entry.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                if isinstance(content, str) and content.strip():
                    user_messages.append(content.strip()[:200])
    except OSError:
        return []
    topics = []
    for message in user_messages[-max_messages:]:
        topics.append(message.split(".")[0][:100])
    return topics


def _emit_context(event_name: str, additional_context: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": additional_context,
        }
    }
    json.dump(payload, sys.stdout)


def _build_session_context(data: dict[str, Any]) -> str:
    injected = data.get("injected_context", {})
    parts: list[str] = []

    prefs = injected.get("preferences", [])
    if isinstance(prefs, list) and prefs:
        lines = []
        for item in prefs[:10]:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('category', 'general')}/{item.get('key', 'unknown')}: {item.get('value')} (confidence: {item.get('confidence', 0)})"
                )
        if lines:
            parts.append("## User Preferences\n" + "\n".join(lines))

    rules = injected.get("learned_rules", [])
    if isinstance(rules, list) and rules:
        lines = []
        for item in rules[:10]:
            if isinstance(item, dict):
                lines.append(f"- [{item.get('domain', 'general')}] {item.get('content', '')}")
        if lines:
            parts.append("## Learned Rules\n" + "\n".join(lines))

    episodic = injected.get("recent_episodic", [])
    if isinstance(episodic, list) and episodic:
        lines = []
        for item in episodic[:5]:
            if isinstance(item, dict):
                lines.append(f"- [{item.get('event_type', 'event')}] {item.get('summary', '')}")
        if lines:
            parts.append("## Recent Session History\n" + "\n".join(lines))

    if not parts:
        return ""

    return (
        "# Eve Memory Context (auto-loaded)\n"
        "The following context was recalled from long-term memory. Use it when relevant.\n\n"
        + "\n\n".join(parts)
    )


def _build_search_context(data: dict[str, Any]) -> str:
    results = data.get("results", [])
    if not isinstance(results, list) or not results:
        return ""
    lines: list[str] = []
    for result in results[:3]:
        if not isinstance(result, dict):
            continue
        chunk = result.get("chunk", {})
        text = ""
        source = result.get("store", "")
        if isinstance(chunk, dict):
            text = chunk.get("text", "")
            source = source or chunk.get("source", "")
        if not text:
            text = result.get("text", "")
        if text:
            similarity = result.get("similarity", 0)
            lines.append(f"- [{source or 'memory'}] {text[:300]} (relevance: {similarity:.0%})")
    if not lines:
        return ""
    return "## Relevant Memories\n" + "\n".join(lines)


def _persist_session_id(session_id: str | None) -> None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file or not session_id:
        return
    try:
        with open(env_file, "a", encoding="utf-8") as handle:
            handle.write(f'export EVE_MEMORY_SESSION_ID="{session_id}"\n')
    except OSError:
        return


def _session_tail_summary(transcript_text: str, *, max_chars: int = 3000) -> str:
    tail = transcript_text[-max_chars:] if len(transcript_text) > max_chars else transcript_text
    boundary = tail.find("\n\n")
    if 0 < boundary < len(tail) // 2:
        tail = tail[boundary + 2 :]
    return tail.strip()


def session_start_main() -> None:
    hook_input = _load_hook_input()
    api_key = _load_api_key()
    if not api_key:
        _safe_exit()
    transcript_path = str(hook_input.get("transcript_path", ""))
    recent_topics = _extract_recent_topics(transcript_path)
    client = _MemoryClient(base_url=resolve_api_base_url(resolve_config().mcp_base_url), api_key=api_key)
    ok, data = client.session_start(
        session_id=hook_input.get("session_id"),
        summary=f"Claude Code session ({hook_input.get('source', 'startup')})",
        recent_topics=recent_topics or None,
    )
    if not ok or not data:
        _safe_exit()
    _persist_session_id(str(data.get("session_id", "")) or str(hook_input.get("session_id", "")) or None)
    context = _build_session_context(data)
    if context:
        _emit_context("SessionStart", context)
    _safe_exit()


def prompt_enrich_main() -> None:
    hook_input = _load_hook_input()
    prompt = hook_input.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) < 20:
        _safe_exit()
    api_key = _load_api_key()
    if not api_key:
        _safe_exit()
    client = _MemoryClient(base_url=resolve_api_base_url(resolve_config().mcp_base_url), api_key=api_key)
    ok, data = client.search(query=prompt)
    if not ok or not data:
        _safe_exit()
    context = _build_search_context(data)
    if context:
        _emit_context("UserPromptSubmit", context[:2000])
    _safe_exit()


def pre_compact_main() -> None:
    hook_input = _load_hook_input()
    session_id = os.environ.get("EVE_MEMORY_SESSION_ID") or hook_input.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        _safe_exit()
    transcript = _read_transcript(str(hook_input.get("transcript_path", "")))
    if not transcript:
        _safe_exit()
    candidate_facts = [part.strip() for part in transcript.split("\n\n")[-6:] if part.strip()]
    critical_facts = candidate_facts[:5]
    if not critical_facts:
        _safe_exit()
    api_key = _load_api_key()
    if not api_key:
        _safe_exit()
    client = _MemoryClient(base_url=resolve_api_base_url(resolve_config().mcp_base_url), api_key=api_key)
    client.pre_compaction(session_id=session_id, critical_facts=critical_facts)
    _safe_exit()


def session_end_main() -> None:
    hook_input = _load_hook_input()
    transcript = _read_transcript(str(hook_input.get("transcript_path", "")))
    if not transcript or len(transcript) < 50:
        _safe_exit()
    api_key = _load_api_key()
    if not api_key:
        _safe_exit()
    session_id = os.environ.get("EVE_MEMORY_SESSION_ID") or hook_input.get("session_id")
    client = _MemoryClient(base_url=resolve_api_base_url(resolve_config().mcp_base_url), api_key=api_key)
    client.extract(transcript=transcript, session_id=session_id if isinstance(session_id, str) else None)
    summary = (
        hook_input.get("summary")
        if isinstance(hook_input.get("summary"), str)
        else _session_tail_summary(transcript)
    )
    client.session_end(
        summary=summary or "Claude Code session ended",
        session_id=session_id if isinstance(session_id, str) else None,
        status=hook_input.get("status") if isinstance(hook_input.get("status"), str) else None,
    )
    _safe_exit()


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        _safe_exit()
    event = args[0]
    handlers = {
        "session_start": session_start_main,
        "prompt_enrich": prompt_enrich_main,
        "pre_compact": pre_compact_main,
        "session_end": session_end_main,
    }
    handler = handlers.get(event)
    if handler is None:
        _log(f"Unknown Claude hook event: {event}")
        _safe_exit()
    handler()


if __name__ == "__main__":
    main()
