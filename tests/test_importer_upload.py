from __future__ import annotations

import json
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
    job = ledger.create_scan_job(
        source_type="codex-cli",
        root_path=root.parent.parent.parent.parent,
        candidates=[
            ImportCandidate(
                source_type="codex-cli",
                path=target,
                session_id="codex-session-1",
                modified_at=datetime(2026, 3, 10, 12, 0, tzinfo=UTC),
                size_bytes=target.stat().st_size,
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
