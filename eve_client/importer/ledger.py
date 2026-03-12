"""SQLite-backed local importer ledger."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from eve_client.importer.models import ImportCandidate, ImportJob, ImportSourceType


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
