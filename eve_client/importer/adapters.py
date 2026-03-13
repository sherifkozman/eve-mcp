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


class ImportAdapter(Protocol):
    source_type: ImportSourceType

    def discover(self, roots: Iterable[Path] | None = None) -> list[ImportCandidate]:
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


_ADAPTERS: dict[ImportSourceType, ImportAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex-cli": CodexCliAdapter(),
    "gemini-cli": GeminiCliAdapter(),
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
    for adapter in iter_adapters():
        if source_types and adapter.source_type not in set(source_types):
            continue
        roots = roots_by_source.get(adapter.source_type) if roots_by_source else None
        candidates.extend(adapter.discover(roots=roots))
    candidates.sort(key=lambda item: item.modified_at, reverse=True)
    return candidates
