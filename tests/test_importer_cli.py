from __future__ import annotations

import json
from pathlib import Path

from eve_client.cli import app
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
    assert payload["candidates"][0]["session_id"] == "codex-session-1"
    assert payload["job"]["candidate_count"] == 1


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
