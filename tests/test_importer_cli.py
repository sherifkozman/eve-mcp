from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
import typer
from eve_client.cli import app
from eve_client.importer import ImportUploadError
from eve_client.importer.models import ImportBatch, ImportRun
from typer.testing import CliRunner

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def _write_codex_fixture(tmp_path: Path) -> Path:
    root = tmp_path / ".codex" / "sessions" / "2026" / "03" / "10"
    root.mkdir(parents=True)
    target = root / "importer_codex_sample.jsonl"
    target.write_text((FIXTURES / "importer_codex_sample.jsonl").read_text(encoding="utf-8"))
    return target


def _write_gemini_fixture(tmp_path: Path) -> Path:
    root = tmp_path / ".gemini" / "tmp" / "hash" / "chats"
    root.mkdir(parents=True)
    target = root / "session-2026-03-10T11-00-demo.json"
    target.write_text((FIXTURES / "importer_gemini_sample.json").read_text(encoding="utf-8"))
    return target


def test_import_scan_json_creates_ledger_job(monkeypatch, tmp_path: Path) -> None:
    target = _write_codex_fixture(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    result = runner.invoke(
        app,
        [
            "import",
            "scan",
            "--source",
            "codex-cli",
            "--root",
            str(target.parent.parent.parent.parent),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["candidate_count"] == 1
    assert payload["displayed_candidate_count"] == 1
    assert payload["candidates"][0]["session_id"] == "codex-session-1"
    assert payload["job"]["candidate_count"] == 1


def test_import_scan_json_reports_full_candidate_count_when_truncated(
    monkeypatch, tmp_path: Path
) -> None:
    first = _write_codex_fixture(tmp_path)
    second_root = first.parent
    second = second_root / "importer_codex_sample_2.jsonl"
    second.write_text((FIXTURES / "importer_codex_sample.jsonl").read_text(encoding="utf-8"))
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    result = runner.invoke(
        app,
        [
            "import",
            "scan",
            "--source",
            "codex-cli",
            "--root",
            str(second_root.parent.parent.parent.parent),
            "--limit",
            "1",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["candidate_count"] == 2
    assert payload["displayed_candidate_count"] == 1
    assert len(payload["candidates"]) == 1
    assert payload["job"]["candidate_count"] == 2


def test_import_preview_json_returns_turns(monkeypatch, tmp_path: Path) -> None:
    target = _write_gemini_fixture(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    result = runner.invoke(
        app,
        [
            "import",
            "preview",
            "--source",
            "gemini-cli",
            "--path",
            str(target),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["turn_count"] == 2
    assert payload["turns"][0]["role"] == "user"


def test_import_jobs_lists_created_jobs(monkeypatch, tmp_path: Path) -> None:
    target = _write_codex_fixture(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    scan_result = runner.invoke(
        app,
        [
            "import",
            "scan",
            "--source",
            "codex-cli",
            "--root",
            str(target.parent.parent.parent.parent),
            "--json",
        ],
    )
    assert scan_result.exit_code == 0

    result = runner.invoke(app, ["import", "jobs", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["candidate_count"] == 1


def test_import_runs_lists_created_runs(monkeypatch, tmp_path: Path) -> None:
    target = _write_codex_fixture(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    scan_result = runner.invoke(
        app,
        [
            "import",
            "scan",
            "--source",
            "codex-cli",
            "--root",
            str(target.parent.parent.parent.parent),
            "--json",
        ],
    )
    job = json.loads(scan_result.stdout)["job"]

    def _request_batch(**kwargs):  # noqa: ANN003
        return 200, {
            "status": "completed",
            "idempotency_key": "idem-runs",
            "extracted_count": 2,
            "stored_count": 2,
            "error_count": 0,
            "duplicate": False,
            "result_summary": {"chunk_ids": ["chunk-runs"]},
        }

    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    result = runner.invoke(
        app,
        [
            "import",
            "upload",
            "--job",
            job["job_id"],
            "--use-auth-from",
            "codex-cli",
            "--api-key",
            "eve_test_key",
            "--json",
        ],
    )
    assert result.exit_code == 0

    runs_result = runner.invoke(app, ["import", "runs", "--json"])
    assert runs_result.exit_code == 0
    payload = json.loads(runs_result.stdout)
    assert len(payload) == 1
    assert payload[0]["scan_job_id"] == job["job_id"]


def test_import_upload_json_success(monkeypatch, tmp_path: Path) -> None:
    target = _write_codex_fixture(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    scan_result = runner.invoke(
        app,
        [
            "import",
            "scan",
            "--source",
            "codex-cli",
            "--root",
            str(target.parent.parent.parent.parent),
            "--json",
        ],
    )
    job = json.loads(scan_result.stdout)["job"]

    def _request_batch(**kwargs):  # noqa: ANN003
        return 200, {
            "status": "completed",
            "idempotency_key": "idem-1",
            "extracted_count": 2,
            "stored_count": 2,
            "error_count": 0,
            "duplicate": False,
            "result_summary": {"chunk_ids": ["chunk-1"]},
        }

    monkeypatch.setattr("eve_client.importer.upload._request_batch", _request_batch)

    result = runner.invoke(
        app,
        [
            "import",
            "upload",
            "--job",
            job["job_id"],
            "--use-auth-from",
            "codex-cli",
            "--api-key",
            "eve_test_key",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["run"]["status"] == "completed"
    assert payload["batches"][0]["status"] == "uploaded"


def test_import_resume_json_success(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    from eve_client.cli import _import_ledger  # local import to use configured paths
    from eve_client.config import resolve_config

    config = resolve_config()
    ledger = _import_ledger(config)
    scan_job = ledger.create_scan_job(source_type="codex-cli", root_path=tmp_path, candidates=[])
    run = ledger.create_run(
        run_id="run_resume",
        scan_job_id=scan_job.job_id,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        batch_size=50,
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
        batches=[
            ImportBatch(
                run_id="",
                batch_id="batch_resume",
                batch_index=0,
                candidate_path=tmp_path / "artifact.jsonl",
                source_type="codex-cli",
                session_id="session-1",
                turn_offset=0,
                turn_count=1,
                status="failed",
                request_payload={"import_job_id": "run_resume", "turns": []},
            )
        ],
    )

    def _fake_upload_run(**kwargs):  # noqa: ANN003
        return type(
            "UploadResult",
            (),
            {
                "run": ImportRun(
                    run_id=run.run_id,
                    scan_job_id=run.scan_job_id,
                    status="completed",
                    auth_source_tool=run.auth_source_tool,
                    auth_mode=run.auth_mode,
                    batch_size=run.batch_size,
                    batch_count=1,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                    context_mode=run.context_mode,
                    source_priority=run.source_priority,
                    min_importance=run.min_importance,
                    last_error=None,
                ),
                "batches": [
                    ImportBatch(
                        run_id=run.run_id,
                        batch_id="batch_resume",
                        batch_index=0,
                        candidate_path=tmp_path / "artifact.jsonl",
                        source_type="codex-cli",
                        session_id="session-1",
                        turn_offset=0,
                        turn_count=1,
                        status="uploaded",
                        request_payload={"import_job_id": run.run_id, "turns": []},
                    )
                ],
                "to_dict": lambda self=None: {
                    "run": {"run_id": run.run_id, "status": "completed"},
                    "batches": [{"batch_id": "batch_resume", "status": "uploaded"}],
                },
            },
        )()

    monkeypatch.setattr("eve_client.cli.upload_run", _fake_upload_run)

    result = runner.invoke(
        app,
        [
            "import",
            "resume",
            "--run",
            run.run_id,
            "--api-key",
            "eve_test_key",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["run"]["run_id"] == run.run_id
    assert payload["run"]["status"] == "completed"


def test_import_upload_runtime_error_exits_cleanly(monkeypatch, tmp_path: Path) -> None:
    target = _write_codex_fixture(tmp_path)
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    scan_result = runner.invoke(
        app,
        [
            "import",
            "scan",
            "--source",
            "codex-cli",
            "--root",
            str(target.parent.parent.parent.parent),
            "--json",
        ],
    )
    job = json.loads(scan_result.stdout)["job"]

    def _raise_upload_error(**kwargs):  # noqa: ANN003
        raise ImportUploadError("Connection failed")

    monkeypatch.setattr("eve_client.cli.upload_run", _raise_upload_error)

    result = runner.invoke(
        app,
        [
            "import",
            "upload",
            "--job",
            job["job_id"],
            "--use-auth-from",
            "codex-cli",
            "--api-key",
            "eve_test_key",
        ],
    )
    assert result.exit_code == 1
    assert "Error: Connection failed" in result.stderr


def test_import_resume_runtime_error_exits_cleanly(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    from eve_client.cli import _import_ledger  # local import to use configured paths
    from eve_client.config import resolve_config

    config = resolve_config()
    ledger = _import_ledger(config)
    scan_job = ledger.create_scan_job(source_type="codex-cli", root_path=tmp_path, candidates=[])
    run = ledger.create_run(
        run_id="run_resume",
        scan_job_id=scan_job.job_id,
        auth_source_tool="codex-cli",
        auth_mode="api-key",
        batch_size=50,
        context_mode="PERSONAL",
        source_priority=1,
        min_importance=4,
        batches=[
            ImportBatch(
                run_id="",
                batch_id="batch_resume",
                batch_index=0,
                candidate_path=tmp_path / "artifact.jsonl",
                source_type="codex-cli",
                session_id="session-1",
                turn_offset=0,
                turn_count=1,
                status="failed",
                request_payload={"import_job_id": "run_resume", "turns": []},
            )
        ],
    )

    def _raise_upload_error(**kwargs):  # noqa: ANN003
        raise ImportUploadError("Connection failed")

    monkeypatch.setattr("eve_client.cli.upload_run", _raise_upload_error)

    result = runner.invoke(
        app,
        [
            "import",
            "resume",
            "--run",
            run.run_id,
            "--api-key",
            "eve_test_key",
        ],
    )
    assert result.exit_code == 1
    assert "Error: Connection failed" in result.stderr


def test_normalize_import_context_mode_rejects_invalid_value() -> None:
    from eve_client.cli import _normalize_import_context_mode

    with pytest.raises(typer.BadParameter, match="--context-mode must be 'PERSONAL', 'NAYA', or 'ES'"):
        _normalize_import_context_mode("INVALID")


def test_import_preview_missing_path_returns_nonzero(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    result = runner.invoke(
        app,
        [
            "import",
            "preview",
            "--source",
            "codex-cli",
            "--path",
            str(tmp_path / "missing.jsonl"),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert "No supported codex-cli source found" in result.stderr


def test_import_preview_parse_error_returns_clean_cli_error(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    source_path = tmp_path / "broken.jsonl"
    source_path.write_text("{}", encoding="utf-8")

    @dataclass
    class _Candidate:
        source_type: str
        session_id: str
        path: Path

        def to_dict(self) -> dict[str, object]:
            return {
                "source_type": self.source_type,
                "session_id": self.session_id,
                "path": str(self.path),
            }

    class _Adapter:
        def discover(self, roots):
            return [_Candidate("codex-cli", "broken-session", source_path)]

        def parse(self, candidate):
            raise ValueError("malformed payload")

    monkeypatch.setattr("eve_client.cli.get_import_adapter", lambda _source: _Adapter())

    result = runner.invoke(
        app,
        [
            "import",
            "preview",
            "--source",
            "codex-cli",
            "--path",
            str(source_path),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert "Failed to parse codex-cli source" in result.stderr
    assert "malformed payload" in result.stderr


def test_import_scan_root_requires_source(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("eve_client.config.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    result = runner.invoke(
        app,
        [
            "import",
            "scan",
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert "--root requires --source" in result.stderr
