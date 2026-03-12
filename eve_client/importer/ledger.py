"""SQLite-backed local importer ledger."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from eve_client.importer.models import (
    ImportBatch,
    ImportCandidate,
    ImportJob,
    ImportRun,
    ImportSourceType,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class ImportLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS import_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    source_type TEXT,
                    root_path TEXT,
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS import_candidates (
                    job_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    modified_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    turn_count_hint INTEGER,
                    PRIMARY KEY (job_id, path),
                    FOREIGN KEY (job_id) REFERENCES import_jobs(job_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS import_runs (
                    run_id TEXT PRIMARY KEY,
                    scan_job_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    auth_source_tool TEXT NOT NULL,
                    auth_mode TEXT NOT NULL,
                    batch_size INTEGER NOT NULL,
                    batch_count INTEGER NOT NULL DEFAULT 0,
                    context_mode TEXT NOT NULL,
                    source_priority INTEGER NOT NULL,
                    min_importance INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    FOREIGN KEY (scan_job_id) REFERENCES import_jobs(job_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS import_run_batches (
                    batch_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    batch_index INTEGER NOT NULL,
                    candidate_path TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    turn_offset INTEGER NOT NULL,
                    turn_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    request_payload TEXT NOT NULL,
                    remote_idempotency_key TEXT,
                    extracted_count INTEGER,
                    stored_count INTEGER,
                    error_count INTEGER,
                    duplicate INTEGER NOT NULL DEFAULT 0,
                    result_summary_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (run_id, batch_index),
                    FOREIGN KEY (run_id) REFERENCES import_runs(run_id) ON DELETE CASCADE
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_scan_job(
        self,
        *,
        source_type: ImportSourceType | None,
        root_path: Path | None,
        candidates: list[ImportCandidate],
    ) -> ImportJob:
        self.initialize()
        now = _utcnow().isoformat()
        job_id = f"scan_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO import_jobs (
                    job_id, status, source_type, root_path, candidate_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    "scanned",
                    source_type,
                    str(root_path) if root_path else None,
                    len(candidates),
                    now,
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO import_candidates (
                    job_id, source_type, path, session_id, modified_at, size_bytes, turn_count_hint
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        candidate.source_type,
                        str(candidate.path),
                        candidate.session_id,
                        candidate.modified_at.isoformat(),
                        candidate.size_bytes,
                        candidate.turn_count_hint,
                    )
                    for candidate in candidates
                ],
            )
        return ImportJob(
            job_id=job_id,
            status="scanned",
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
            source_type=source_type,
            root_path=root_path,
            candidate_count=len(candidates),
        )

    def list_jobs(self) -> list[ImportJob]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, status, source_type, root_path, candidate_count, created_at, updated_at
                FROM import_jobs
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [
            ImportJob(
                job_id=row["job_id"],
                status=row["status"],
                source_type=row["source_type"],
                root_path=Path(row["root_path"]) if row["root_path"] else None,
                candidate_count=row["candidate_count"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def get_job(self, job_id: str) -> ImportJob | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT job_id, status, source_type, root_path, candidate_count, created_at, updated_at
                FROM import_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return ImportJob(
            job_id=row["job_id"],
            status=row["status"],
            source_type=row["source_type"],
            root_path=Path(row["root_path"]) if row["root_path"] else None,
            candidate_count=row["candidate_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_job_candidates(self, job_id: str) -> list[ImportCandidate]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_type, path, session_id, modified_at, size_bytes, turn_count_hint
                FROM import_candidates
                WHERE job_id = ?
                ORDER BY modified_at DESC
                """,
                (job_id,),
            ).fetchall()
        return [
            ImportCandidate(
                source_type=row["source_type"],
                path=Path(row["path"]),
                session_id=row["session_id"],
                modified_at=datetime.fromisoformat(row["modified_at"]),
                size_bytes=row["size_bytes"],
                turn_count_hint=row["turn_count_hint"],
            )
            for row in rows
        ]

    def create_run(
        self,
        *,
        run_id: str | None = None,
        scan_job_id: str,
        auth_source_tool: str,
        auth_mode: str,
        batch_size: int,
        context_mode: str,
        source_priority: int,
        min_importance: int,
        batches: list[ImportBatch],
    ) -> ImportRun:
        self.initialize()
        now = _utcnow().isoformat()
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO import_runs (
                    run_id, scan_job_id, status, auth_source_tool, auth_mode, batch_size, batch_count,
                    context_mode, source_priority, min_importance, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    scan_job_id,
                    "planned",
                    auth_source_tool,
                    auth_mode,
                    batch_size,
                    len(batches),
                    context_mode,
                    source_priority,
                    min_importance,
                    now,
                    now,
                    None,
                ),
            )
            conn.executemany(
                """
                INSERT INTO import_run_batches (
                    batch_id, run_id, batch_index, candidate_path, source_type, session_id, turn_offset,
                    turn_count, status, request_payload, remote_idempotency_key, extracted_count,
                    stored_count, error_count, duplicate, result_summary_json, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        batch.batch_id,
                        run_id,
                        batch.batch_index,
                        str(batch.candidate_path),
                        batch.source_type,
                        batch.session_id,
                        batch.turn_offset,
                        batch.turn_count,
                        batch.status,
                        json.dumps(batch.request_payload, sort_keys=True),
                        batch.remote_idempotency_key,
                        batch.extracted_count,
                        batch.stored_count,
                        batch.error_count,
                        1 if batch.duplicate else 0,
                        json.dumps(batch.result_summary, sort_keys=True),
                        batch.last_error,
                        now,
                        now,
                    )
                    for batch in batches
                ],
            )
        return ImportRun(
            run_id=run_id,
            scan_job_id=scan_job_id,
            status="planned",
            auth_source_tool=auth_source_tool,
            auth_mode=auth_mode,  # type: ignore[arg-type]
            batch_size=batch_size,
            batch_count=len(batches),
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
            context_mode=context_mode,
            source_priority=source_priority,
            min_importance=min_importance,
            last_error=None,
        )

    def update_batch_payload(self, *, batch_id: str, request_payload: dict[str, object]) -> None:
        self.initialize()
        now = _utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE import_run_batches
                SET request_payload = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (json.dumps(request_payload, sort_keys=True), now, batch_id),
            )

    def list_runs(self) -> list[ImportRun]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, scan_job_id, status, auth_source_tool, auth_mode, batch_size, batch_count,
                       context_mode, source_priority, min_importance, created_at, updated_at, last_error
                FROM import_runs
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [
            ImportRun(
                run_id=row["run_id"],
                scan_job_id=row["scan_job_id"],
                status=row["status"],
                auth_source_tool=row["auth_source_tool"],
                auth_mode=row["auth_mode"],
                batch_size=row["batch_size"],
                batch_count=row["batch_count"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                context_mode=row["context_mode"],
                source_priority=row["source_priority"],
                min_importance=row["min_importance"],
                last_error=row["last_error"],
            )
            for row in rows
        ]

    def get_run(self, run_id: str) -> ImportRun | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, scan_job_id, status, auth_source_tool, auth_mode, batch_size, batch_count,
                       context_mode, source_priority, min_importance, created_at, updated_at, last_error
                FROM import_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return ImportRun(
            run_id=row["run_id"],
            scan_job_id=row["scan_job_id"],
            status=row["status"],
            auth_source_tool=row["auth_source_tool"],
            auth_mode=row["auth_mode"],
            batch_size=row["batch_size"],
            batch_count=row["batch_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            context_mode=row["context_mode"],
            source_priority=row["source_priority"],
            min_importance=row["min_importance"],
            last_error=row["last_error"],
        )

    def get_run_batches(self, run_id: str) -> list[ImportBatch]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT batch_id, run_id, batch_index, candidate_path, source_type, session_id, turn_offset,
                       turn_count, status, request_payload, remote_idempotency_key, extracted_count,
                       stored_count, error_count, duplicate, result_summary_json, last_error
                FROM import_run_batches
                WHERE run_id = ?
                ORDER BY batch_index ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            ImportBatch(
                run_id=row["run_id"],
                batch_id=row["batch_id"],
                batch_index=row["batch_index"],
                candidate_path=Path(row["candidate_path"]),
                source_type=row["source_type"],
                session_id=row["session_id"],
                turn_offset=row["turn_offset"],
                turn_count=row["turn_count"],
                status=row["status"],
                request_payload=json.loads(row["request_payload"]),
                remote_idempotency_key=row["remote_idempotency_key"],
                extracted_count=row["extracted_count"],
                stored_count=row["stored_count"],
                error_count=row["error_count"],
                duplicate=bool(row["duplicate"]),
                result_summary=json.loads(row["result_summary_json"]),
                last_error=row["last_error"],
            )
            for row in rows
        ]

    def update_run_status(self, run_id: str, *, status: str, last_error: str | None = None) -> None:
        self.initialize()
        now = _utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE import_runs
                SET status = ?, last_error = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status, last_error, now, run_id),
            )

    def complete_batch(
        self,
        *,
        batch_id: str,
        status: str,
        remote_idempotency_key: str | None,
        extracted_count: int,
        stored_count: int,
        error_count: int,
        duplicate: bool,
        result_summary: dict[str, object],
    ) -> None:
        self.initialize()
        now = _utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE import_run_batches
                SET status = ?, remote_idempotency_key = ?, extracted_count = ?, stored_count = ?,
                    error_count = ?, duplicate = ?, result_summary_json = ?, last_error = NULL,
                    updated_at = ?
                WHERE batch_id = ?
                """,
                (
                    status,
                    remote_idempotency_key,
                    extracted_count,
                    stored_count,
                    error_count,
                    1 if duplicate else 0,
                    json.dumps(result_summary, sort_keys=True),
                    now,
                    batch_id,
                ),
            )

    def fail_batch(self, *, batch_id: str, status: str = "failed", error: str) -> None:
        self.initialize()
        now = _utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE import_run_batches
                SET status = ?, last_error = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (status, error, now, batch_id),
            )
