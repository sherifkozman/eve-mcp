from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from eve_client.config import ResolvedConfig
from eve_client.importer.ledger import ImportLedger
from eve_client.importer.models import ImportCandidate
from eve_client.importer.upload import (
    ImportUploadError,
    build_batches_for_job,
    upload_run,
)


class _FakeCredentialStore:
    def __init__(self, *, api_key: str | None = None, bearer_token: str | None = None) -> None:
        self.api_key = api_key
        self.bearer_token = bearer_token

    def get_api_key(self, tool):  # noqa: ANN001
        return self.api_key, "test"

    def get_bearer_token(self, tool):  # noqa: ANN001
        return self.bearer_token, "test"


def _config(tmp_path: Path) -> ResolvedConfig:
    return ResolvedConfig(
        config_dir=tmp_path / ".config" / "eve",
        mcp_base_url="https://mcp.evemem.com/mcp",
        mcp_server_name="eve-memory",
        environment="production",
        codex_enabled=False,
        codex_source="feature_flag",
        ui_base_url="https://evemem.com",
        state_dir=tmp_path / ".state",
        config_path=tmp_path / ".config" / "eve" / "config.json",
        project_root=tmp_path,
        allow_file_secret_fallback=True,
        feature_claude_desktop=False,
        oauth_domain="evemem.us.auth0.com",
        oauth_client_id="client-id",
    )


def _seed_job(tmp_path: Path) -> tuple[ImportLedger, object]:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    fixture = Path(__file__).parent / "fixtures" / "importer_codex_sample.jsonl"
    root = tmp_path / ".codex" / "sessions" / "2026" / "03" / "10"
    root.mkdir(parents=True)
    target = root / fixture.name
    target.write_text(fixture.read_text(encoding="utf-8"))
    stat_result = target.stat()
    job = ledger.create_scan_job(
        source_type="codex-cli",
        root_path=root.parent.parent.parent.parent,
        candidates=[
            ImportCandidate(
                source_type="codex-cli",
                path=target,
                session_id="codex-session-1",
                modified_at=datetime.fromtimestamp(stat_result.st_mtime, tz=UTC),
                size_bytes=stat_result.st_size,
                turn_count_hint=2,
            )
        ],
    )
    return ledger, job


def test_build_batches_for_job_uses_run_id_as_import_job_id(tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None

    run, batches = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=1,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    assert len(batches) == 2
    assert all(batch.request_payload["import_job_id"] == run.run_id for batch in batches)
    stored_batches = ledger.get_run_batches(run.run_id)
    assert len(stored_batches) == 2
    stored_payload = stored_batches[0].request_payload
    assert stored_payload["batch_id"] == stored_batches[0].batch_id
    assert isinstance(stored_payload["batch_hash"], str)
    assert "turns" not in stored_payload
    with sqlite3.connect(ledger.path) as conn:
        raw_payload = conn.execute(
            "SELECT request_payload FROM import_run_batches WHERE run_id = ? ORDER BY batch_index ASC",
            (run.run_id,),
        ).fetchone()[0]
    assert "Testing codex upload path." not in raw_payload


def test_upload_run_marks_batches_uploaded(monkeypatch, tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    def _request_batch(**kwargs):  # noqa: ANN003
        return 200, {
            "status": "completed",
            "idempotency_key": "idem-1",
            "extracted_count": 2,
            "stored_count": 2,
            "error_count": 0,
            "duplicate": False,
            "result_summary": {"chunk_ids": ["c1", "c2"]},
        }

    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    result = upload_run(
        config=_config(tmp_path),
        ledger=ledger,
        credential_store=_FakeCredentialStore(bearer_token="bearer-token"),
        run=run,
    )

    assert result.run.status == "completed"
    assert result.batches[0].status == "uploaded"
    assert result.batches[0].remote_idempotency_key == "idem-1"


def test_upload_run_locks_on_ledger_directory_not_config_state_dir(
    monkeypatch, tmp_path: Path
) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )
    captured: list[Path] = []

    class _Lock:
        def __init__(self, path: Path) -> None:
            self.path = path

        def __enter__(self) -> None:
            captured.append(self.path)
            return None

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

    def _installer_lock(path: Path) -> _Lock:
        return _Lock(path)

    def _request_batch(**kwargs):  # noqa: ANN003
        return 200, {
            "status": "completed",
            "idempotency_key": "idem-locked",
            "extracted_count": 2,
            "stored_count": 2,
            "error_count": 0,
            "duplicate": False,
            "result_summary": {"chunk_ids": ["c1", "c2"]},
        }

    monkeypatch.setattr("eve_client.importer.upload.installer_lock", _installer_lock)
    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    config = _config(tmp_path)
    result = upload_run(
        config=config,
        ledger=ledger,
        credential_store=_FakeCredentialStore(bearer_token="bearer-token"),
        run=run,
    )

    assert result.run.status == "completed"
    assert captured == [ledger.path.parent]
    assert config.state_dir != ledger.path.parent


def test_upload_run_marks_conflict_and_fails_run(monkeypatch, tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    def _request_batch(**kwargs):  # noqa: ANN003
        return 409, {"detail": "idempotency key reused with different payload"}

    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    result = upload_run(
        config=_config(tmp_path),
        ledger=ledger,
        credential_store=_FakeCredentialStore(api_key="eve_test_key"),
        run=run,
    )

    assert result.run.status == "failed"
    assert result.batches[0].status == "conflict"
    assert "idempotency key reused" in (result.batches[0].last_error or "")


def test_upload_run_skips_batches_already_marked_conflict(monkeypatch, tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )
    batch = ledger.get_run_batches(run.run_id)[0]
    ledger.fail_batch(batch_id=batch.batch_id, status="conflict", error="conflict")

    def _request_batch(**kwargs):  # noqa: ANN003
        raise AssertionError("conflict batches should not be retried")

    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    result = upload_run(
        config=_config(tmp_path),
        ledger=ledger,
        credential_store=_FakeCredentialStore(api_key="eve_test_key"),
        run=run,
    )

    assert result.batches[0].status == "conflict"
    assert result.run.status == "failed"


def test_upload_run_marks_transport_failures_failed(monkeypatch, tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    def _request_batch(**kwargs):  # noqa: ANN003
        raise ImportUploadError("network down")

    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    with pytest.raises(ImportUploadError, match="network down"):
        upload_run(
            config=_config(tmp_path),
            ledger=ledger,
            credential_store=_FakeCredentialStore(bearer_token="bearer-token"),
            run=run,
        )

    stored_run = ledger.get_run(run.run_id)
    assert stored_run is not None
    assert stored_run.status == "failed"
    assert stored_run.last_error == "network down"


def test_upload_run_requires_stored_secret(tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="gemini-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    with pytest.raises(ImportUploadError, match="No Eve OAuth bearer token stored"):
        upload_run(
            config=_config(tmp_path),
            ledger=ledger,
            credential_store=_FakeCredentialStore(),
            run=run,
        )


def test_build_batches_for_job_rejects_empty_scan_job(tmp_path: Path) -> None:
    ledger = ImportLedger(tmp_path / "state" / "importer.sqlite3")
    job = ledger.create_scan_job(source_type="codex-cli", root_path=tmp_path, candidates=[])

    with pytest.raises(ImportUploadError, match="has no candidates"):
        build_batches_for_job(
            job=job,
            ledger=ledger,
            batch_size=10,
            auth_source_tool="codex-cli",
            auth_mode="api-key",
            context_mode="PERSONAL",
            source_priority=1,
            min_importance=4,
        )


def test_build_batches_for_job_wraps_parse_failures(monkeypatch, tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None

    class _BrokenAdapter:
        def parse(self, candidate):  # noqa: ANN001
            raise json.JSONDecodeError("bad json", "{}", 0)

    monkeypatch.setattr("eve_client.importer.upload.get_adapter", lambda source_type: _BrokenAdapter())

    with pytest.raises(ImportUploadError, match="Failed to parse importer source"):
        build_batches_for_job(
            job=job,
            ledger=ledger,
            batch_size=10,
            auth_source_tool="codex-cli",
            auth_mode="oauth",
            context_mode="PERSONAL",
            source_priority=1,
            min_importance=4,
        )


def test_build_batches_for_job_wraps_missing_adapter(monkeypatch, tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None

    def _missing_adapter(source_type):  # noqa: ANN001
        raise KeyError(source_type)

    monkeypatch.setattr("eve_client.importer.upload.get_adapter", _missing_adapter)

    with pytest.raises(ImportUploadError, match="No importer adapter is available"):
        build_batches_for_job(
            job=job,
            ledger=ledger,
            batch_size=10,
            auth_source_tool="codex-cli",
            auth_mode="oauth",
            context_mode="PERSONAL",
            source_priority=1,
            min_importance=4,
        )


def test_upload_run_rejects_changed_source_after_scan(tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    candidate_path = ledger.get_run_batches(run.run_id)[0].candidate_path
    candidate_path.write_text(
        '{"type":"response.item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"mutated"}]}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ImportUploadError, match="changed after scan"):
        upload_run(
            config=_config(tmp_path),
            ledger=ledger,
            credential_store=_FakeCredentialStore(bearer_token="bearer-token"),
            run=run,
        )


def test_upload_run_recovers_stale_submitting_batches(monkeypatch, tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )
    batch = ledger.get_run_batches(run.run_id)[0]
    assert ledger.mark_batch_submitting(batch_id=batch.batch_id) is True

    def _request_batch(**kwargs):  # noqa: ANN003
        return 200, {
            "status": "completed",
            "idempotency_key": "idem-recovered",
            "extracted_count": 2,
            "stored_count": 2,
            "error_count": 0,
            "duplicate": False,
            "result_summary": {"chunk_ids": ["c1", "c2"]},
        }

    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    result = upload_run(
        config=_config(tmp_path),
        ledger=ledger,
        credential_store=_FakeCredentialStore(bearer_token="bearer-token"),
        run=run,
    )

    assert result.run.status == "completed"
    assert result.batches[0].status == "uploaded"


def test_upload_run_rejects_content_change_with_preserved_stat_fields(tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    candidate_path = ledger.get_run_batches(run.run_id)[0].candidate_path
    stat_result = candidate_path.stat()
    original = candidate_path.read_text(encoding="utf-8")
    mutated = original.replace(
        "Remember that I prefer concise release notes.",
        "Remember that I prefer concise summary notes.",
    )
    assert len(mutated) == len(original)
    candidate_path.write_text(mutated, encoding="utf-8")
    candidate_path.chmod(0o644)
    os.utime(candidate_path, (stat_result.st_atime, stat_result.st_mtime))

    with pytest.raises(ImportUploadError, match="content changed after scan"):
        upload_run(
            config=_config(tmp_path),
            ledger=ledger,
            credential_store=_FakeCredentialStore(bearer_token="bearer-token"),
            run=run,
        )


def test_upload_run_wraps_missing_adapter_during_materialization(
    monkeypatch, tmp_path: Path
) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    def _missing_adapter(source_type):  # noqa: ANN001
        raise KeyError(source_type)

    monkeypatch.setattr("eve_client.importer.upload.get_adapter", _missing_adapter)

    with pytest.raises(ImportUploadError, match="No importer adapter is available"):
        upload_run(
            config=_config(tmp_path),
            ledger=ledger,
            credential_store=_FakeCredentialStore(bearer_token="bearer-token"),
            run=run,
        )

    stored_run = ledger.get_run(run.run_id)
    assert stored_run is not None
    assert stored_run.status == "failed"


def test_upload_run_fails_when_remote_status_is_processing(monkeypatch, tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    def _request_batch(**kwargs):  # noqa: ANN003
        return 200, {"status": "processing", "result_summary": {}}

    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    result = upload_run(
        config=_config(tmp_path),
        ledger=ledger,
        credential_store=_FakeCredentialStore(bearer_token="bearer-token"),
        run=run,
    )

    assert result.run.status == "failed"
    assert result.batches[0].status == "failed"
    assert result.batches[0].last_error == (
        "Managed importer batch is still processing remotely; retry upload later."
    )


def test_upload_run_fails_when_remote_status_is_failed(monkeypatch, tmp_path: Path) -> None:
    ledger, job = _seed_job(tmp_path)
    assert job is not None
    run, _ = build_batches_for_job(
        job=job,
        ledger=ledger,
        batch_size=10,
        auth_source_tool="codex-cli",
        auth_mode="oauth",
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
    )

    def _request_batch(**kwargs):  # noqa: ANN003
        return 200, {"status": "failed", "result_summary": {"detail": "remote parse failure"}}

    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    result = upload_run(
        config=_config(tmp_path),
        ledger=ledger,
        credential_store=_FakeCredentialStore(bearer_token="bearer-token"),
        run=run,
    )

    assert result.run.status == "failed"
    assert result.batches[0].status == "failed"
    assert result.batches[0].last_error == "remote parse failure"
