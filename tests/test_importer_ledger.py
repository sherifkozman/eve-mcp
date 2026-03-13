from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
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
