"""Shared importer models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

ImportSourceType = Literal["claude-code", "codex-cli", "gemini-cli"]


@dataclass(slots=True)
class ImportCandidate:
    source_type: ImportSourceType
    path: Path
    session_id: str
    modified_at: datetime
    size_bytes: int
    content_sha256: str
    turn_count_hint: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "path": str(self.path),
            "session_id": self.session_id,
            "modified_at": self.modified_at.isoformat(),
            "size_bytes": self.size_bytes,
            "content_sha256": self.content_sha256,
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
    status: Literal["scanned"]
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


@dataclass(slots=True)
class ImportRun:
    run_id: str
    scan_job_id: str
    status: Literal["planned", "running", "completed", "failed"]
    auth_source_tool: str
    auth_mode: Literal["api-key", "oauth"]
    batch_size: int
    batch_count: int
    created_at: datetime
    updated_at: datetime
    context_mode: str
    source_priority: int
    min_importance: int
    last_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "scan_job_id": self.scan_job_id,
            "status": self.status,
            "auth_source_tool": self.auth_source_tool,
            "auth_mode": self.auth_mode,
            "batch_size": self.batch_size,
            "batch_count": self.batch_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "context_mode": self.context_mode,
            "source_priority": self.source_priority,
            "min_importance": self.min_importance,
            "last_error": self.last_error,
        }


@dataclass(slots=True)
class ImportBatch:
    run_id: str
    batch_id: str
    batch_index: int
    candidate_path: Path
    source_type: ImportSourceType
    session_id: str
    turn_offset: int
    turn_count: int
    status: Literal["pending", "submitting", "uploaded", "failed", "conflict"]
    request_payload: dict[str, object]
    remote_idempotency_key: str | None = None
    extracted_count: int | None = None
    stored_count: int | None = None
    error_count: int | None = None
    duplicate: bool = False
    result_summary: dict[str, object] = field(default_factory=dict)
    last_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "batch_index": self.batch_index,
            "candidate_path": str(self.candidate_path),
            "source_type": self.source_type,
            "session_id": self.session_id,
            "turn_offset": self.turn_offset,
            "turn_count": self.turn_count,
            "status": self.status,
            "request_payload": self.request_payload,
            "remote_idempotency_key": self.remote_idempotency_key,
            "extracted_count": self.extracted_count,
            "stored_count": self.stored_count,
            "error_count": self.error_count,
            "duplicate": self.duplicate,
            "result_summary": self.result_summary,
            "last_error": self.last_error,
        }


@dataclass(slots=True)
class ImportCleanupSummary:
    cutoff_at: datetime
    completed_runs_pruned: int
    batches_pruned: int
    orphaned_jobs_pruned: int
    candidates_pruned: int
    vacuumed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "cutoff_at": self.cutoff_at.isoformat(),
            "completed_runs_pruned": self.completed_runs_pruned,
            "batches_pruned": self.batches_pruned,
            "orphaned_jobs_pruned": self.orphaned_jobs_pruned,
            "candidates_pruned": self.candidates_pruned,
            "vacuumed": self.vacuumed,
        }
