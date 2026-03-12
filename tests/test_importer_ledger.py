from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from eve_client.importer.ledger import ImportLedger
from eve_client.importer.models import ImportCandidate


def _candidate(path: Path, *, source_type: str, session_id: str) -> ImportCandidate:
    return ImportCandidate(
        source_type=source_type,  # type: ignore[arg-type]
        path=path,
        session_id=session_id,
        modified_at=datetime(2026, 3, 10, 12, 0, tzinfo=UTC),
        size_bytes=42,
        turn_count_hint=2,
    )


def test_import_ledger_persists_scan_jobs(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    candidates = [
        _candidate(tmp_path / "a.jsonl", source_type="codex-cli", session_id="s1"),
        _candidate(tmp_path / "b.json", source_type="gemini-cli", session_id="s2"),
    ]
    job = ledger.create_scan_job(source_type=None, root_path=None, candidates=candidates)

    jobs = ledger.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == job.job_id
    assert jobs[0].candidate_count == 2

    stored = ledger.get_job_candidates(job.job_id)
    assert [candidate.session_id for candidate in stored] == ["s1", "s2"]


def test_import_ledger_returns_empty_candidate_list_for_unknown_job(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    assert ledger.get_job_candidates("missing") == []
