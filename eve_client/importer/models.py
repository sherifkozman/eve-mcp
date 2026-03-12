"""Shared importer models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

ImportSourceType = Literal["codex-cli", "gemini-cli"]


@dataclass(slots=True)
class ImportCandidate:
    source_type: ImportSourceType
    path: Path
    session_id: str
    modified_at: datetime
    size_bytes: int
    turn_count_hint: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "path": str(self.path),
            "session_id": self.session_id,
            "modified_at": self.modified_at.isoformat(),
            "size_bytes": self.size_bytes,
            "turn_count_hint": self.turn_count_hint,
        }


@dataclass(slots=True)
class ImportTurn:
    role: str
    content: str
    timestamp: datetime | None
    source_system: str
    source_id: str
    session_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "source_system": self.source_system,
            "source_id": self.source_id,
            "session_id": self.session_id,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ImportJob:
    job_id: str
    status: Literal["scanned", "running", "completed", "failed"]
    created_at: datetime
    updated_at: datetime
    source_type: ImportSourceType | None
    root_path: Path | None
    candidate_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "source_type": self.source_type,
            "root_path": str(self.root_path) if self.root_path else None,
            "candidate_count": self.candidate_count,
        }
