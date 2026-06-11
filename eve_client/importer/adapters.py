"""Local source adapters for importer MVP."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from eve_client.importer.models import ImportCandidate, ImportSourceType, ImportTurn


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _coerce_text_content(parts: object) -> str:
    if isinstance(parts, str):
        return parts.strip()
    if not isinstance(parts, list):
        return ""
    segments: list[str] = []
    for part in parts:
        if isinstance(part, str) and part.strip():
            segments.append(part.strip())
            continue
        if not isinstance(part, dict):
            continue
        for key in ("text", "input", "output", "content"):
            value = part.get(key)
            if isinstance(value, str) and value.strip():
                segments.append(value.strip())
                break
    return "\n".join(segment for segment in segments if segment)


def _coerce_claude_text_content(parts: object) -> str:
    if isinstance(parts, str):
        return parts.strip()
    if not isinstance(parts, list):
        return ""
    segments: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "text":
            continue
        value = part.get("text")
        if isinstance(value, str) and value.strip():
            segments.append(value.strip())
    return "\n".join(segment for segment in segments if segment)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_unix_timestamp(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _is_regular_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


class ImportAdapter(Protocol):
    source_type: ImportSourceType

    def discover(self, roots: Iterable[Path] | None = None) -> list[ImportCandidate]:
        ...

    def candidate_for_path(self, path: Path) -> ImportCandidate | None:
        ...

    def parse(self, candidate: ImportCandidate) -> Iterator[ImportTurn]:
        ...


@dataclass(slots=True)
class ClaudeCodeAdapter:
    source_type: ImportSourceType = "claude-code"

    def default_roots(self) -> tuple[Path, ...]:
        return (Path.home() / ".claude" / "projects",)

    def discover(self, roots: Iterable[Path] | None = None) -> list[ImportCandidate]:
        candidates: list[ImportCandidate] = []
        for root in roots or self.default_roots():
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.jsonl")):
                if "subagents" in path.parts:
                    continue
                candidate = self._candidate_for_path(path)
                if candidate:
                    candidates.append(candidate)
        return candidates

    def candidate_for_path(self, path: Path) -> ImportCandidate | None:
        if not _is_regular_file(path):
            return None
        return self._candidate_for_path(path)

    def _candidate_for_path(self, path: Path) -> ImportCandidate | None:
        session_id = path.stem
        turn_count = 0
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                    if isinstance(record.get("sessionId"), str) and record["sessionId"]:
                        session_id = record["sessionId"]
                    message = record.get("message")
                    role = message.get("role") if isinstance(message, dict) else None
                    if role in {"user", "assistant"} and _coerce_claude_text_content(
                        message.get("content") if isinstance(message, dict) else None
                    ):
                        turn_count += 1
            stat = path.stat()
            modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            content_sha256 = _file_sha256(path)
        except (OSError, json.JSONDecodeError):
            return None
        return ImportCandidate(
            source_type=self.source_type,
            path=path,
            session_id=session_id,
            modified_at=modified_at,
            size_bytes=stat.st_size,
            content_sha256=content_sha256,
            turn_count_hint=turn_count or None,
        )

    def parse(self, candidate: ImportCandidate) -> Iterator[ImportTurn]:
        with candidate.path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in {"user", "assistant", "system"}:
                    continue
                content = _coerce_claude_text_content(message.get("content"))
                if not content:
                    continue
                metadata: dict[str, object] = {"path": str(candidate.path)}
                if isinstance(record.get("cwd"), str) and record["cwd"]:
                    metadata["cwd"] = record["cwd"]
                if isinstance(record.get("version"), str) and record["version"]:
                    metadata["version"] = record["version"]
                yield ImportTurn(
                    role=role,
                    content=content,
                    timestamp=_parse_timestamp(record.get("timestamp")),
                    source_system=self.source_type,
                    source_id=f"{candidate.session_id}:{index}",
                    session_id=candidate.session_id,
                    metadata=metadata,
                )


@dataclass(slots=True)
class CodexCliAdapter:
    source_type: ImportSourceType = "codex-cli"

    def default_roots(self) -> tuple[Path, ...]:
        home = Path.home()
        return (
            home / ".codex" / "sessions",
            home / ".codex" / "archived_sessions",
        )

    def discover(self, roots: Iterable[Path] | None = None) -> list[ImportCandidate]:
        candidates: list[ImportCandidate] = []
        for root in roots or self.default_roots():
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.jsonl")):
                candidate = self._candidate_for_path(path)
                if candidate:
                    candidates.append(candidate)
        return candidates

    def candidate_for_path(self, path: Path) -> ImportCandidate | None:
        if not _is_regular_file(path):
            return None
        return self._candidate_for_path(path)

    def _candidate_for_path(self, path: Path) -> ImportCandidate | None:
        session_id = path.stem
        turn_count = 0
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("type") == "session_meta":
                        payload = record.get("payload", {})
                        if isinstance(payload, dict) and isinstance(payload.get("id"), str):
                            session_id = payload["id"]
                    elif record.get("type") == "response_item":
                        turn_count += 1
        except (OSError, json.JSONDecodeError):
            return None
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        return ImportCandidate(
            source_type=self.source_type,
            path=path,
            session_id=session_id,
            modified_at=modified_at,
            size_bytes=stat.st_size,
            content_sha256=_file_sha256(path),
            turn_count_hint=turn_count or None,
        )

    def parse(self, candidate: ImportCandidate) -> Iterator[ImportTurn]:
        with candidate.path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("type") != "response_item":
                    continue
                payload = record.get("payload", {})
                if not isinstance(payload, dict) or payload.get("type") != "message":
                    continue
                role = payload.get("role")
                if role not in {"user", "assistant", "system"}:
                    continue
                content = _coerce_text_content(payload.get("content"))
                if not content:
                    continue
                yield ImportTurn(
                    role=role,
                    content=content,
                    timestamp=_parse_timestamp(record.get("timestamp")),
                    source_system=self.source_type,
                    source_id=f"{candidate.session_id}:{index}",
                    session_id=candidate.session_id,
                    metadata={"path": str(candidate.path)},
                )


@dataclass(slots=True)
class GeminiCliAdapter:
    source_type: ImportSourceType = "gemini-cli"

    def default_roots(self) -> tuple[Path, ...]:
        return (Path.home() / ".gemini" / "tmp",)

    def discover(self, roots: Iterable[Path] | None = None) -> list[ImportCandidate]:
        candidates: list[ImportCandidate] = []
        for root in roots or self.default_roots():
            if not root.exists():
                continue
            for path in sorted(root.rglob("session-*.json")):
                candidate = self._candidate_for_path(path)
                if candidate:
                    candidates.append(candidate)
        return candidates

    def candidate_for_path(self, path: Path) -> ImportCandidate | None:
        if not _is_regular_file(path):
            return None
        return self._candidate_for_path(path)

    def _candidate_for_path(self, path: Path) -> ImportCandidate | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            session_id = path.stem
        messages = payload.get("messages")
        turn_count = len(messages) if isinstance(messages, list) else None
        modified_at = _parse_timestamp(payload.get("lastUpdated")) or datetime.fromtimestamp(
            path.stat().st_mtime, tz=UTC
        )
        return ImportCandidate(
            source_type=self.source_type,
            path=path,
            session_id=session_id,
            modified_at=modified_at,
            size_bytes=path.stat().st_size,
            content_sha256=_file_sha256(path),
            turn_count_hint=turn_count,
        )

    def parse(self, candidate: ImportCandidate) -> Iterator[ImportTurn]:
        payload = json.loads(candidate.path.read_text(encoding="utf-8"))
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            return
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            raw_type = message.get("type")
            role = "assistant" if raw_type == "gemini" else raw_type
            if role not in {"user", "assistant", "system", "tool"}:
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            metadata: dict[str, object] = {"path": str(candidate.path)}
            if isinstance(message.get("toolCalls"), list):
                metadata["tool_calls"] = len(message["toolCalls"])
            yield ImportTurn(
                role=role,
                content=content.strip(),
                timestamp=_parse_timestamp(message.get("timestamp")),
                source_system=self.source_type,
                source_id=f"{candidate.session_id}:{index}",
                session_id=candidate.session_id,
                metadata=metadata,
            )


@dataclass(slots=True)
class ChatGPTAdapter:
    source_type: ImportSourceType = "chatgpt"

    def default_roots(self) -> tuple[Path, ...]:
        home = Path.home()
        return (home / "Downloads", home / "Desktop")

    def discover(self, roots: Iterable[Path] | None = None) -> list[ImportCandidate]:
        candidates: list[ImportCandidate] = []
        for root in roots or self.default_roots():
            if not root.exists():
                continue
            paths = [root] if root.is_file() else sorted(root.glob("conversations.json"))
            for path in paths:
                candidate = self._candidate_for_path(path)
                if candidate:
                    candidates.append(candidate)
        return candidates

    def candidate_for_path(self, path: Path) -> ImportCandidate | None:
        if not _is_regular_file(path) or path.name != "conversations.json":
            return None
        return self._candidate_for_path(path)

    def _candidate_for_path(self, path: Path) -> ImportCandidate | None:
        try:
            conversations = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not _is_chatgpt_export(conversations):
            return None
        turn_count = 0
        conversation_ids: list[str] = []
        latest_timestamp: datetime | None = None
        for conversation in conversations:
            conversation_id = conversation.get("id")
            if isinstance(conversation_id, str) and conversation_id:
                conversation_ids.append(conversation_id)
            for _node, message in _iter_chatgpt_messages(conversation):
                if _chatgpt_message_content(message):
                    turn_count += 1
                timestamp = _parse_unix_timestamp(message.get("create_time"))
                if timestamp and (latest_timestamp is None or timestamp > latest_timestamp):
                    latest_timestamp = timestamp
        stat = path.stat()
        return ImportCandidate(
            source_type=self.source_type,
            path=path,
            session_id=conversation_ids[0] if len(conversation_ids) == 1 else path.stem,
            modified_at=latest_timestamp or datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            size_bytes=stat.st_size,
            content_sha256=_file_sha256(path),
            turn_count_hint=turn_count or None,
        )

    def parse(self, candidate: ImportCandidate) -> Iterator[ImportTurn]:
        conversations = json.loads(candidate.path.read_text(encoding="utf-8"))
        if not _is_chatgpt_export(conversations):
            return
        for conversation in conversations:
            conversation_id = conversation.get("id")
            if not isinstance(conversation_id, str) or not conversation_id:
                conversation_id = candidate.session_id
            title = conversation.get("title") if isinstance(conversation.get("title"), str) else ""
            for index, (node, message) in enumerate(_iter_chatgpt_messages(conversation)):
                author = message.get("author")
                if not isinstance(author, dict):
                    continue
                role = author.get("role")
                if role not in {"user", "assistant", "system", "tool"}:
                    continue
                content = _chatgpt_message_content(message)
                if not content:
                    continue
                metadata = {"path": str(candidate.path), "title": title}
                author_metadata = author.get("metadata")
                if isinstance(author_metadata, dict) and isinstance(
                    author_metadata.get("model_slug"), str
                ):
                    metadata["model"] = author_metadata["model_slug"]
                source_id = message.get("id") or node.get("id") or f"{conversation_id}:{index}"
                yield ImportTurn(
                    role=role,
                    content=content,
                    timestamp=_parse_unix_timestamp(message.get("create_time")),
                    source_system=self.source_type,
                    source_id=str(source_id),
                    session_id=conversation_id,
                    metadata=metadata,
                )


@dataclass(slots=True)
class ClaudeDesktopAdapter:
    source_type: ImportSourceType = "claude-desktop"

    def default_roots(self) -> tuple[Path, ...]:
        home = Path.home()
        return (home / "Downloads", home / "Desktop")

    def discover(self, roots: Iterable[Path] | None = None) -> list[ImportCandidate]:
        candidates: list[ImportCandidate] = []
        for root in roots or self.default_roots():
            if not root.exists():
                continue
            paths = [root] if root.is_file() else sorted(root.glob("claude_ai_conversations*.json"))
            for path in paths:
                candidate = self._candidate_for_path(path)
                if candidate:
                    candidates.append(candidate)
        return candidates

    def candidate_for_path(self, path: Path) -> ImportCandidate | None:
        if (
            not _is_regular_file(path)
            or not path.name.startswith("claude_ai_conversations")
            or path.suffix != ".json"
        ):
            return None
        return self._candidate_for_path(path)

    def _candidate_for_path(self, path: Path) -> ImportCandidate | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        conversations = _claude_desktop_conversations(payload)
        if conversations is None:
            return None
        turn_count = 0
        conversation_ids: list[str] = []
        latest_timestamp: datetime | None = None
        for conversation in conversations:
            conversation_id = conversation.get("uuid")
            if isinstance(conversation_id, str) and conversation_id:
                conversation_ids.append(conversation_id)
            messages = conversation.get("chat_messages")
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                if _normalize_claude_desktop_sender(message.get("sender")) not in {
                    "user",
                    "assistant",
                    "system",
                }:
                    continue
                text = message.get("text")
                if isinstance(text, str) and text.strip():
                    turn_count += 1
                timestamp = _parse_timestamp(message.get("created_at"))
                if timestamp and (latest_timestamp is None or timestamp > latest_timestamp):
                    latest_timestamp = timestamp
        if turn_count == 0:
            return None
        stat = path.stat()
        return ImportCandidate(
            source_type=self.source_type,
            path=path,
            session_id=conversation_ids[0] if len(conversation_ids) == 1 else path.stem,
            modified_at=latest_timestamp or datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            size_bytes=stat.st_size,
            content_sha256=_file_sha256(path),
            turn_count_hint=turn_count or None,
        )

    def parse(self, candidate: ImportCandidate) -> Iterator[ImportTurn]:
        payload = json.loads(candidate.path.read_text(encoding="utf-8"))
        conversations = _claude_desktop_conversations(payload)
        if conversations is None:
            return
        for conversation in conversations:
            conversation_id = conversation.get("uuid")
            if not isinstance(conversation_id, str) or not conversation_id:
                conversation_id = candidate.session_id
            conversation_name = (
                conversation.get("name") if isinstance(conversation.get("name"), str) else ""
            )
            messages = conversation.get("chat_messages")
            if not isinstance(messages, list):
                continue
            for index, message in enumerate(messages):
                if not isinstance(message, dict):
                    continue
                role = _normalize_claude_desktop_sender(message.get("sender"))
                if role not in {"user", "assistant", "system"}:
                    continue
                text = message.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                source_id = message.get("uuid") or f"{conversation_id}:{index}"
                yield ImportTurn(
                    role=role,
                    content=text.strip(),
                    timestamp=_parse_timestamp(message.get("created_at")),
                    source_system=self.source_type,
                    source_id=str(source_id),
                    session_id=conversation_id,
                    metadata={
                        "path": str(candidate.path),
                        "conversation_name": conversation_name,
                    },
                )


def _is_chatgpt_export(payload: object) -> bool:
    if not isinstance(payload, list):
        return False
    return any(
        isinstance(conversation, dict)
        and isinstance(conversation.get("mapping"), dict)
        and any(_is_chatgpt_importable_message(message) for _node, message in _iter_chatgpt_messages(conversation))
        for conversation in payload
    )


def _iter_chatgpt_messages(conversation: dict[object, object]) -> Iterator[tuple[dict, dict]]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return
    nodes = _chatgpt_active_chain_nodes(conversation, mapping)
    if nodes is None:
        return
    for node in nodes:
        message = node.get("message")
        if isinstance(message, dict):
            yield node, message


def _chatgpt_active_chain_nodes(
    conversation: dict[object, object], mapping: dict[object, object]
) -> list[dict] | None:
    current_node = conversation.get("current_node")
    if not isinstance(current_node, str) or not current_node:
        return None
    nodes_by_id: dict[str, dict] = {
        node_id: node for node_id, node in mapping.items() if isinstance(node_id, str) and isinstance(node, dict)
    }
    chain: list[dict] = []
    visited: set[str] = set()
    node_id: str | None = current_node
    while node_id:
        if node_id in visited:
            return None
        visited.add(node_id)
        node = nodes_by_id.get(node_id)
        if node is None:
            return None
        chain.append(node)
        parent = node.get("parent")
        node_id = parent if isinstance(parent, str) and parent else None
    chain.reverse()
    return chain


def _is_chatgpt_importable_message(message: dict) -> bool:
    author = message.get("author")
    role = author.get("role") if isinstance(author, dict) else None
    return role in {"user", "assistant", "system", "tool"} and bool(_chatgpt_message_content(message))


def _chatgpt_message_content(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return _coerce_text_content(content.get("parts"))
    return _coerce_text_content(content)


def _claude_desktop_conversations(payload: object) -> list[dict] | None:
    if not isinstance(payload, dict):
        return None
    conversations = payload.get("conversations")
    if not isinstance(conversations, list):
        return None
    return [conversation for conversation in conversations if isinstance(conversation, dict)]


def _normalize_claude_desktop_sender(sender: object) -> str:
    if not isinstance(sender, str):
        return ""
    return {
        "human": "user",
        "user": "user",
        "assistant": "assistant",
        "claude": "assistant",
        "system": "system",
    }.get(sender.lower(), sender.lower())


_DEFAULT_SCAN_EXPLICIT_SOURCE_TYPES: frozenset[ImportSourceType] = frozenset(
    {"chatgpt", "claude-desktop"}
)


_ADAPTERS: dict[ImportSourceType, ImportAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex-cli": CodexCliAdapter(),
    "gemini-cli": GeminiCliAdapter(),
    "chatgpt": ChatGPTAdapter(),
    "claude-desktop": ClaudeDesktopAdapter(),
}


def iter_adapters() -> tuple[ImportAdapter, ...]:
    return tuple(_ADAPTERS.values())


def get_adapter(source_type: ImportSourceType) -> ImportAdapter:
    return _ADAPTERS[source_type]


def scan_candidates(
    *,
    source_types: Iterable[ImportSourceType] | None = None,
    roots_by_source: dict[ImportSourceType, list[Path]] | None = None,
) -> list[ImportCandidate]:
    candidates: list[ImportCandidate] = []
    source_type_set = set(source_types) if source_types else None
    root_source_types = set(roots_by_source or {})
    for adapter in iter_adapters():
        if source_type_set and adapter.source_type not in source_type_set:
            continue
        if (
            adapter.source_type in _DEFAULT_SCAN_EXPLICIT_SOURCE_TYPES
            and adapter.source_type not in root_source_types
        ):
            continue
        roots = roots_by_source.get(adapter.source_type) if roots_by_source else None
        candidates.extend(adapter.discover(roots=roots))
    candidates.sort(key=lambda item: item.modified_at, reverse=True)
    return candidates
