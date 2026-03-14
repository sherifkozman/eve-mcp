from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from eve_client.importer.ledger import ImportLedger
from eve_client.importer.models import ImportBatch, ImportCandidate


def _candidate(path: Path, *, source_type: str, session_id: str) -> ImportCandidate:
    size_bytes = path.stat().st_size if path.exists() else 42
    content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "sha256-demo"
    return ImportCandidate(
        source_type=source_type,  # type: ignore[arg-type]
        path=path,
        session_id=session_id,
        modified_at=datetime(2026, 3, 10, 12, 0, tzinfo=UTC),
        size_bytes=size_bytes,
        content_sha256=content_sha256,
        turn_count_hint=2,
    )


def test_import_ledger_persists_scan_jobs(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    (tmp_path / "a.jsonl").write_text("a", encoding="utf-8")
    (tmp_path / "b.json").write_text("b", encoding="utf-8")
    candidates = [
        _candidate(tmp_path / "a.jsonl", source_type="codex-cli", session_id="s1"),
        _candidate(tmp_path / "b.json", source_type="gemini-cli", session_id="s2"),
    ]
    candidates[1].modified_at = datetime(2026, 3, 10, 12, 1, tzinfo=UTC)
    job = ledger.create_scan_job(source_type=None, root_path=None, candidates=candidates)

    jobs = ledger.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == job.job_id
    assert jobs[0].candidate_count == 2

    stored = ledger.get_job_candidates(job.job_id)
    assert [candidate.session_id for candidate in stored] == ["s2", "s1"]


def test_import_ledger_returns_empty_candidate_list_for_unknown_job(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    assert ledger.get_job_candidates("missing") == []


def test_import_ledger_persists_runs_and_batches(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    (tmp_path / "a.jsonl").write_text("a", encoding="utf-8")
    job = ledger.create_scan_job(
        source_type="codex-cli",
        root_path=tmp_path,
        candidates=[_candidate(tmp_path / "a.jsonl", source_type="codex-cli", session_id="s1")],
    )
    run = ledger.create_run(
        run_id="run_demo",
        scan_job_id=job.job_id,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        batch_size=10,
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
        batches=[
            ImportBatch(
                run_id="",
                batch_id="batch_1",
                batch_index=0,
                candidate_path=tmp_path / "a.jsonl",
                source_type="codex-cli",
                session_id="s1",
                turn_offset=0,
                turn_count=2,
                status="pending",
                request_payload={"import_job_id": "run_demo", "turns": []},
            )
        ],
    )

    stored_run = ledger.get_run(run.run_id)
    stored_batches = ledger.get_run_batches(run.run_id)
    listed_runs = ledger.list_runs()

    assert stored_run is not None
    assert stored_run.run_id == "run_demo"
    assert stored_run.auth_mode == "oauth"
    assert listed_runs[0].run_id == "run_demo"
    assert len(stored_batches) == 1
    assert stored_batches[0].batch_id == "batch_1"


def test_import_ledger_rejects_orphan_runs(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")

    with pytest.raises(sqlite3.IntegrityError):
        ledger.create_run(
            run_id="run_orphan",
            scan_job_id="missing_job",
            auth_source_tool="codex-cli",
            auth_mode="oauth",
            batch_size=10,
            context_mode="PERSONAL",
            source_priority=1,
            min_importance=4,
            batches=[],
        )


def test_import_ledger_secures_parent_and_db_permissions(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")

    ledger.create_scan_job(source_type=None, root_path=None, candidates=[])

    assert ledger.path.exists()
    assert ledger.path.parent.stat().st_mode & 0o077 == 0
    assert ledger.path.stat().st_mode & 0o077 == 0


def test_import_ledger_recovers_submitting_batches(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    job = ledger.create_scan_job(source_type="codex-cli", root_path=tmp_path, candidates=[])
    run = ledger.create_run(
        scan_job_id=job.job_id,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        batch_size=10,
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
        batches=[
            ImportBatch(
                run_id="",
                batch_id="batch_submitting",
                batch_index=0,
                candidate_path=tmp_path / "a.jsonl",
                source_type="codex-cli",
                session_id="s1",
                turn_offset=0,
                turn_count=1,
                status="submitting",
                request_payload={"import_job_id": "run_demo"},
            )
        ],
    )

    recovered = ledger.recover_submitting_batches(run.run_id)

    assert recovered == 1
    batch = ledger.get_run_batches(run.run_id)[0]
    assert batch.status == "pending"
    assert batch.last_error == "Recovered interrupted upload attempt; retrying batch."


def test_import_ledger_marks_batch_submitting_only_once(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    job = ledger.create_scan_job(source_type="codex-cli", root_path=tmp_path, candidates=[])
    run = ledger.create_run(
        scan_job_id=job.job_id,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        batch_size=10,
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
        batches=[
            ImportBatch(
                run_id="",
                batch_id="batch_claim",
                batch_index=0,
                candidate_path=tmp_path / "a.jsonl",
                source_type="codex-cli",
                session_id="s1",
                turn_offset=0,
                turn_count=1,
                status="pending",
                request_payload={"import_job_id": "run_demo"},
            )
        ],
    )

    assert ledger.mark_batch_submitting(batch_id="batch_claim") is True
    assert ledger.mark_batch_submitting(batch_id="batch_claim") is False
    batch = ledger.get_run_batches(run.run_id)[0]
    assert batch.status == "submitting"

    stored_run = ledger.get_run(run.run_id)
    stored_batches = ledger.get_run_batches(run.run_id)
    assert stored_run is not None
    assert stored_run.batch_count == 1
    assert [batch.batch_id for batch in stored_batches] == ["batch_claim"]
    assert [batch.batch_index for batch in stored_batches] == [0]


def test_import_ledger_cleanup_dry_run_counts_only_old_completed_runs(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    source = tmp_path / "a.jsonl"
    source.write_text("a", encoding="utf-8")
    job = ledger.create_scan_job(
        source_type="codex-cli",
        root_path=tmp_path,
        candidates=[_candidate(source, source_type="codex-cli", session_id="s1")],
    )
    old_run = ledger.create_run(
        run_id="run_old",
        scan_job_id=job.job_id,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        batch_size=10,
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
        batches=[
            ImportBatch(
                run_id="",
                batch_id="batch_old",
                batch_index=0,
                candidate_path=source,
                source_type="codex-cli",
                session_id="s1",
                turn_offset=0,
                turn_count=1,
                status="uploaded",
                request_payload={"import_job_id": "run_old"},
            )
        ],
    )
    recent_run = ledger.create_run(
        run_id="run_recent",
        scan_job_id=job.job_id,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        batch_size=10,
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
        batches=[
            ImportBatch(
                run_id="",
                batch_id="batch_recent",
                batch_index=0,
                candidate_path=source,
                source_type="codex-cli",
                session_id="s1",
                turn_offset=0,
                turn_count=1,
                status="uploaded",
                request_payload={"import_job_id": "run_recent"},
            )
        ],
    )
    ledger.update_run_status(old_run.run_id, status="completed")
    ledger.update_run_status(recent_run.run_id, status="completed")
    cutoff_at = datetime.now(tz=UTC) - timedelta(days=30)
    old_iso = (cutoff_at - timedelta(days=1)).isoformat()
    recent_iso = (cutoff_at + timedelta(days=1)).isoformat()
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("UPDATE import_runs SET completed_at = ? WHERE run_id = ?", (old_iso, old_run.run_id))
        conn.execute(
            "UPDATE import_runs SET completed_at = ? WHERE run_id = ?",
            (recent_iso, recent_run.run_id),
        )
        conn.commit()

    summary = ledger.cleanup(cutoff_at=cutoff_at)

    assert summary.completed_runs_pruned == 1
    assert summary.batches_pruned == 1
    assert summary.orphaned_jobs_pruned == 0
    assert summary.candidates_pruned == 0
    assert {run.run_id for run in ledger.list_runs()} == {"run_old", "run_recent"}


def test_import_ledger_cleanup_apply_prunes_completed_runs_and_optional_scan_jobs(
    tmp_path: Path,
) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    source = tmp_path / "a.jsonl"
    source.write_text("a", encoding="utf-8")
    pruned_job = ledger.create_scan_job(
        source_type="codex-cli",
        root_path=tmp_path,
        candidates=[_candidate(source, source_type="codex-cli", session_id="s1")],
    )
    protected_job = ledger.create_scan_job(
        source_type="codex-cli",
        root_path=tmp_path,
        candidates=[_candidate(source, source_type="codex-cli", session_id="s3")],
    )
    orphan_source = tmp_path / "orphan.jsonl"
    orphan_source.write_text("orphan", encoding="utf-8")
    orphan_job = ledger.create_scan_job(
        source_type="claude-code",
        root_path=tmp_path,
        candidates=[_candidate(orphan_source, source_type="claude-code", session_id="s2")],
    )
    run = ledger.create_run(
        run_id="run_old",
        scan_job_id=pruned_job.job_id,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        batch_size=10,
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
        batches=[
            ImportBatch(
                run_id="",
                batch_id="batch_old",
                batch_index=0,
                candidate_path=source,
                source_type="codex-cli",
                session_id="s1",
                turn_offset=0,
                turn_count=1,
                status="uploaded",
                request_payload={"import_job_id": "run_old"},
            )
        ],
    )
    protected_run = ledger.create_run(
        run_id="run_recent",
        scan_job_id=protected_job.job_id,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        batch_size=10,
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
        batches=[
            ImportBatch(
                run_id="",
                batch_id="batch_recent",
                batch_index=0,
                candidate_path=source,
                source_type="codex-cli",
                session_id="s3",
                turn_offset=0,
                turn_count=1,
                status="uploaded",
                request_payload={"import_job_id": "run_recent"},
            )
        ],
    )
    ledger.update_run_status(run.run_id, status="completed")
    ledger.update_run_status(protected_run.run_id, status="completed")
    cutoff_at = datetime.now(tz=UTC) - timedelta(days=30)
    old_iso = (cutoff_at - timedelta(days=1)).isoformat()
    recent_iso = (cutoff_at + timedelta(days=1)).isoformat()
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("UPDATE import_runs SET completed_at = ? WHERE run_id = ?", (old_iso, run.run_id))
        conn.execute(
            "UPDATE import_runs SET created_at = ?, completed_at = ? WHERE run_id = ?",
            (old_iso, recent_iso, protected_run.run_id),
        )
        conn.execute("UPDATE import_jobs SET created_at = ? WHERE job_id = ?", (old_iso, orphan_job.job_id))
        conn.execute(
            "UPDATE import_jobs SET created_at = ? WHERE job_id = ?",
            (old_iso, pruned_job.job_id),
        )
        conn.execute(
            "UPDATE import_jobs SET created_at = ? WHERE job_id = ?",
            (old_iso, protected_job.job_id),
        )
        conn.commit()

    summary = ledger.cleanup(
        cutoff_at=cutoff_at,
        prune_orphaned_jobs=True,
        apply=True,
    )

    assert summary.completed_runs_pruned == 1
    assert summary.batches_pruned == 1
    assert summary.orphaned_jobs_pruned == 2
    assert summary.candidates_pruned == 2
    assert ledger.get_run(run.run_id) is None
    assert ledger.get_job(orphan_job.job_id) is None
    assert ledger.get_job(pruned_job.job_id) is None
    assert ledger.get_job(protected_job.job_id) is not None


def test_import_ledger_cleanup_vacuum_sets_summary_flag(tmp_path: Path, monkeypatch) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    calls: list[str] = []
    monkeypatch.setattr(ledger, "vacuum", lambda: calls.append("vacuum"))

    summary = ledger.cleanup(
        cutoff_at=datetime.now(tz=UTC) - timedelta(days=30),
        apply=True,
        vacuum=True,
    )

    assert calls == ["vacuum"]
    assert summary.vacuumed is True


def test_import_ledger_cleanup_chunks_large_orphan_deletes(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    cutoff_at = datetime.now(tz=UTC) - timedelta(days=30)
    old_iso = (cutoff_at - timedelta(days=1)).isoformat()

    orphan_ids: list[str] = []
    for index in range(1100):
        source = tmp_path / f"orphan-{index}.jsonl"
        source.write_text("orphan", encoding="utf-8")
        job = ledger.create_scan_job(
            source_type="claude-code",
            root_path=tmp_path,
            candidates=[_candidate(source, source_type="claude-code", session_id=f"s{index}")],
        )
        orphan_ids.append(job.job_id)

    with sqlite3.connect(ledger.path) as conn:
        conn.executemany(
            "UPDATE import_jobs SET created_at = ? WHERE job_id = ?",
            [(old_iso, job_id) for job_id in orphan_ids],
        )
        conn.commit()

    summary = ledger.cleanup(
        cutoff_at=cutoff_at,
        prune_orphaned_jobs=True,
        apply=True,
    )

    assert summary.orphaned_jobs_pruned == 1100
    assert summary.candidates_pruned == 1100
    assert ledger.list_jobs() == []


def test_import_ledger_backfills_completed_at_when_migrating_legacy_schema(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    cutoff_at = datetime.now(tz=UTC) - timedelta(days=30)
    old_iso = (cutoff_at - timedelta(days=1)).isoformat()
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ledger.path) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE import_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                source_type TEXT,
                root_path TEXT,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE import_candidates (
                job_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                path TEXT NOT NULL,
                session_id TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_sha256 TEXT,
                turn_count_hint INTEGER,
                PRIMARY KEY (job_id, path),
                FOREIGN KEY (job_id) REFERENCES import_jobs(job_id) ON DELETE CASCADE
            );
            CREATE TABLE import_runs (
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
            CREATE TABLE import_run_batches (
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
        conn.execute(
            """
            INSERT INTO import_jobs (
                job_id, status, source_type, root_path, candidate_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("scan_legacy", "scanned", "codex-cli", str(tmp_path), 0, old_iso, old_iso),
        )
        conn.execute(
            """
            INSERT INTO import_runs (
                run_id, scan_job_id, status, auth_source_tool, auth_mode, batch_size, batch_count,
                context_mode, source_priority, min_importance, created_at, updated_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run_legacy",
                "scan_legacy",
                "completed",
                "codex-cli",
                "api-key",
                10,
                0,
                "PERSONAL",
                1,
                4,
                old_iso,
                old_iso,
                None,
            ),
        )
        conn.commit()

    ledger.initialize()
    summary = ledger.cleanup(cutoff_at=cutoff_at, apply=True)

    assert summary.completed_runs_pruned == 1
    assert ledger.get_run("run_legacy") is None
