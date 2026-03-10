"""Gemini CLI hook handlers for Eve client.

These hooks are stdlib-only, fail closed, and prefer OAuth bearer tokens when
available. They emit Gemini-compatible hook JSON only when there is useful
context to inject.
"""

from __future__ import annotations

import json
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
    print(f"[eve-client:gemini-hooks] {message}", file=sys.stderr)


def _safe_exit(payload: dict[str, Any] | None = None) -> None:
    json.dump(payload or {}, sys.stdout)
    raise SystemExit(0)


def _api_base_url(value: str) -> str:
    parsed = urlparse(value.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[:-4]
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


class _MemoryClient:
    def __init__(self, *, base_url: str, api_key: str | None, bearer_token: str | None) -> None:
        self.base_url = _api_base_url(base_url)
        self.api_key = api_key
        self.bearer_token = bearer_token

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Source-Agent": source_agent_header("gemini-cli"),
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

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

    def session_start(self) -> tuple[bool, dict[str, Any] | None]:
        return self._post(
            "/memory/session/start",
            {"summary": "Gemini CLI session started", "tool_name": "gemini-cli"},
            timeout=5.0,
        )

    def search(self, *, query: str) -> tuple[bool, dict[str, Any] | None]:
        return self._post(
            "/memory/search",
            {"query": query[:500], "limit": 5, "store": "all", "min_similarity": 0.7},
            timeout=5.0,
        )

    def pre_compact(self, *, session_id: str, messages: list[dict[str, str]]) -> tuple[bool, dict[str, Any] | None]:
        return self._post(
            "/memory/hook/compact",
            {"session_id": session_id, "messages": messages},
            timeout=5.0,
        )

    def extract(self, *, transcript: str) -> tuple[bool, dict[str, Any] | None]:
        return self._post(
            "/memory/extract",
            {
                "transcript": transcript[:_MAX_TRANSCRIPT_BYTES],
                "source": "gemini_cli",
                "auto_store": True,
                "min_importance": 5,
                "use_extraction": True,
            },
            timeout=20.0,
        )


def _load_credentials() -> tuple[str | None, str | None]:
    config = resolve_config()
    store = LocalCredentialStore(
        config.state_dir,
        allow_file_fallback=config.allow_file_secret_fallback,
    )
    api_key = None
    bearer = None
    try:
        bearer, _ = store.get_bearer_token("gemini-cli")
    except CredentialStoreUnavailableError as exc:
        _log(str(exc))
    try:
        api_key, _ = store.get_api_key("gemini-cli")
    except CredentialStoreUnavailableError as exc:
        _log(str(exc))
    return api_key, bearer


def _load_hook_input() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def _emit_context(event_name: str, additional_context: str) -> None:
    _safe_exit(
        {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": additional_context,
            }
        }
    )


def _render_injected_context(data: dict[str, Any]) -> str:
    injected = data.get("injected_context", {})
    parts: list[str] = []

    prefs = injected.get("preferences", [])
    if isinstance(prefs, list) and prefs:
        lines = []
        for item in prefs[:10]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('category', 'general')}/{item.get('key', 'unknown')}: {item.get('value')}")
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
            parts.append("## Recent History\n" + "\n".join(lines))

    if not parts:
        return ""
    context_text = "# Eve Memory Context (auto-loaded)\n\n" + "\n\n".join(parts)
    return context_text[:3000] + ("\n...(truncated)" if len(context_text) > 3000 else "")


def _extract_prompt(data: dict[str, Any]) -> str:
    llm_messages = data.get("llm_request", {}).get("messages", [])
    if isinstance(llm_messages, list):
        for item in reversed(llm_messages):
            if not isinstance(item, dict):
                continue
            if item.get("role") not in {"user", "human"}:
                continue
            content = item.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("text")
                )
            if isinstance(content, str) and content.strip():
                return content.strip()
    for field in ("prompt", "input"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _read_transcript_text(path_value: str) -> str:
    path = Path(path_value)
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
                if role not in {"user", "model", "human", "assistant"}:
                    continue
                content = entry.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                if not isinstance(content, str) or not content.strip():
                    continue
                label = "User" if role in {"user", "human"} else "Assistant"
                line = f"{label}: {content.strip()}"
                lines.append(line)
                total_bytes += len(line.encode("utf-8"))
    except OSError:
        return ""
    return "\n\n".join(lines)


def _read_compact_messages(path_value: str) -> list[dict[str, str]]:
    path = Path(path_value)
    if not path.is_file():
        return []
    role_map = {"model": "assistant", "assistant": "assistant", "user": "user", "human": "user", "system": "system"}
    messages: list[dict[str, str]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                role = role_map.get(entry.get("role", ""))
                if not role:
                    continue
                content = entry.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                if isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content.strip()[:5000]})
    except OSError:
        return []
    return messages[-50:]


def session_start() -> None:
    api_key, bearer = _load_credentials()
    if not api_key and not bearer:
        _safe_exit()
    client = _MemoryClient(
        base_url=resolve_api_base_url(resolve_config().mcp_base_url),
        api_key=api_key,
        bearer_token=bearer,
    )
    ok, data = client.session_start()
    if not ok or not isinstance(data, dict):
        _safe_exit()
    rendered = _render_injected_context(data)
    if not rendered:
        _safe_exit()
    _emit_context("SessionStart", rendered)


def prompt_enrich() -> None:
    api_key, bearer = _load_credentials()
    if not api_key and not bearer:
        _safe_exit()
    payload = _load_hook_input()
    prompt = _extract_prompt(payload)
    if len(prompt) < 20:
        _safe_exit()
    client = _MemoryClient(
        base_url=resolve_api_base_url(resolve_config().mcp_base_url),
        api_key=api_key,
        bearer_token=bearer,
    )
    ok, data = client.search(query=prompt)
    if not ok or not isinstance(data, dict):
        _safe_exit()
    results = data.get("results", [])
    if not isinstance(results, list) or not results:
        _safe_exit()
    lines: list[str] = []
    for item in results[:5]:
        if not isinstance(item, dict):
            continue
        chunk = item.get("chunk", {})
        text = chunk.get("text", "") if isinstance(chunk, dict) else item.get("text", "")
        store = item.get("store", chunk.get("source", "")) if isinstance(chunk, dict) else item.get("store", "")
        similarity = item.get("similarity", 0)
        if text:
            lines.append(f"- [{store}] {text[:500]} (relevance: {similarity:.0%})")
    if not lines:
        _safe_exit()
    _emit_context("BeforeAgent", ("## Relevant Memories\n" + "\n".join(lines))[:3000])


def pre_compact() -> None:
    api_key, bearer = _load_credentials()
    if not api_key and not bearer:
        _safe_exit({"ok": True})
    payload = _load_hook_input()
    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    if not isinstance(session_id, str) or not session_id.strip() or not isinstance(transcript_path, str):
        _safe_exit({"ok": True})
    messages = _read_compact_messages(transcript_path)
    if not messages:
        _safe_exit({"ok": True})
    client = _MemoryClient(
        base_url=resolve_api_base_url(resolve_config().mcp_base_url),
        api_key=api_key,
        bearer_token=bearer,
    )
    client.pre_compact(session_id=session_id, messages=messages)
    _safe_exit({"ok": True})


def session_end() -> None:
    api_key, bearer = _load_credentials()
    if not api_key and not bearer:
        _safe_exit({"ok": True})
    payload = _load_hook_input()
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str):
        _safe_exit({"ok": True})
    transcript = _read_transcript_text(transcript_path)
    if len(transcript) < 50:
        _safe_exit({"ok": True})
    client = _MemoryClient(
        base_url=resolve_api_base_url(resolve_config().mcp_base_url),
        api_key=api_key,
        bearer_token=bearer,
    )
    client.extract(transcript=transcript)
    _safe_exit({"ok": True})


def main() -> None:
    if len(sys.argv) < 2:
        _log("Usage: python -m eve_client.gemini_hooks <session_start|prompt_enrich|pre_compact|session_end>")
        _safe_exit()
    command = sys.argv[1]
    if command == "session_start":
        session_start()
    if command == "prompt_enrich":
        prompt_enrich()
    if command == "pre_compact":
        pre_compact()
    if command == "session_end":
        session_end()
    _log(f"Unknown command: {command}")
    _safe_exit()


if __name__ == "__main__":
    main()
