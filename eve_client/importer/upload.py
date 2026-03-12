"""Managed importer upload workflow."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eve_client.auth.base import CredentialStore, CredentialStoreUnavailableError
from eve_client.config import ResolvedConfig, resolve_api_base_url
from eve_client.importer import ImportLedger, get_adapter
from eve_client.importer.models import (
    ImportBatch,
    ImportCandidate,
    ImportJob,
    ImportRun,
    ImportTurn,
)
from eve_client.lock import installer_lock
from eve_client.merge import source_agent_header
from eve_client.models import ToolName

_IMPORT_BATCH_PATH = "/memory/import/batch"
_SUPPORTED_AUTH_TOOLS: tuple[ToolName, ...] = ("claude-code", "gemini-cli", "codex-cli")


class ImportUploadError(RuntimeError):
    """Raised when the client importer upload flow cannot continue."""


@dataclass(slots=True)
class ImportUploadResult:
    run: ImportRun
    batches: list[ImportBatch]

    def to_dict(self) -> dict[str, object]:
        return {
            "run": self.run.to_dict(),
            "batches": [batch.to_dict() for batch in self.batches],
        }


def _sanitize_error(value: object) -> str:
    return str(value).replace("\n", " ").strip()


def _parse_response(body: str) -> dict[str, Any] | None:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _validate_auth_source_tool(tool_name: str) -> ToolName:
    if tool_name not in _SUPPORTED_AUTH_TOOLS:
        raise ImportUploadError(
            "--use-auth-from must be one of claude-code, gemini-cli, codex-cli"
        )
    return tool_name  # type: ignore[return-value]


def _load_secret(
    credential_store: CredentialStore,
    *,
    tool_name: ToolName,
    auth_mode: str,
    api_key: str | None = None,
    bearer_token: str | None = None,
) -> tuple[str, str]:
    if auth_mode == "oauth":
        if bearer_token:
            return bearer_token, "cli"
        try:
            secret, source = credential_store.get_bearer_token(tool_name)
        except CredentialStoreUnavailableError as exc:
            raise ImportUploadError(str(exc)) from exc
        if not secret:
            raise ImportUploadError(f"No Eve OAuth bearer token stored for {tool_name}")
        return secret, source or "keyring"
    if api_key:
        return api_key, "cli"
    try:
        secret, source = credential_store.get_api_key(tool_name)
    except CredentialStoreUnavailableError as exc:
        raise ImportUploadError(str(exc)) from exc
    if not secret:
        raise ImportUploadError(f"No Eve API key stored for {tool_name}")
    return secret, source or "keyring"


def _build_headers(*, tool_name: ToolName, auth_mode: str, secret: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Source-Agent": source_agent_header(tool_name),
    }
    if auth_mode == "oauth":
        headers["Authorization"] = f"Bearer {secret}"
    else:
        headers["X-API-Key"] = secret
    return headers


def _request_batch(
    *,
    config: ResolvedConfig,
    headers: dict[str, str],
    payload: dict[str, object],
    timeout: float,
) -> tuple[int, dict[str, Any] | None]:
    url = resolve_api_base_url(config.mcp_base_url).rstrip("/") + _IMPORT_BATCH_PATH
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, _parse_response(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, _parse_response(exc.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ImportUploadError(f"Connection failed: {_sanitize_error(exc.reason)}") from exc


def _batch_payload(
    *,
    batch_id: str,
    run_id: str,
    source_type: str,
    session_id: str,
    turn_offset: int,
    turn_count: int,
    context_mode: str,
    source_priority: int,
    min_importance: int,
    candidate: ImportCandidate,
    turns: list[ImportTurn],
) -> dict[str, object]:
    batch_turns = [turn.to_dict() for turn in turns]
    batch_hash = hashlib.sha256(
        json.dumps(batch_turns, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return {
        "batch_id": batch_id,
        "import_job_id": run_id,
        "source_system": source_type,
        "session_id": session_id,
        "context_mode": context_mode,
        "source_priority": source_priority,
        "min_importance": min_importance,
        "candidate": {
            "source_type": candidate.source_type,
            "path": str(candidate.path),
            "session_id": candidate.session_id,
            "modified_at": candidate.modified_at.isoformat(),
            "size_bytes": candidate.size_bytes,
        },
        "turn_offset": turn_offset,
        "turn_count": turn_count,
        "batch_hash": batch_hash,
    }


def _candidate_from_payload(payload: dict[str, object]) -> ImportCandidate:
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        raise ImportUploadError("Importer run batch is missing its candidate snapshot")
    try:
        path = Path(str(candidate["path"]))
        source_type = str(candidate["source_type"])
        session_id = str(candidate["session_id"])
        modified_at = datetime.fromisoformat(str(candidate["modified_at"]))
        size_bytes = int(candidate["size_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportUploadError("Importer run batch contains invalid candidate snapshot") from exc
    return ImportCandidate(
        source_type=source_type,  # type: ignore[arg-type]
        path=path,
        session_id=session_id,
        modified_at=modified_at if modified_at.tzinfo else modified_at.replace(tzinfo=UTC),
        size_bytes=size_bytes,
    )


def _validate_candidate_snapshot(candidate: ImportCandidate) -> None:
    try:
        stat_result = candidate.path.stat()
    except OSError as exc:
        raise ImportUploadError(
            f"Failed to stat importer source {candidate.path}: {_sanitize_error(exc)}"
        ) from exc
    current_size = int(stat_result.st_size)
    if current_size != candidate.size_bytes:
        raise ImportUploadError(
            "Importer source changed after scan; rerun `eve import scan` before uploading."
        )


def _materialize_request_payload(batch: ImportBatch) -> dict[str, object]:
    payload = batch.request_payload
    candidate = _candidate_from_payload(payload)
    _validate_candidate_snapshot(candidate)
    adapter = get_adapter(candidate.source_type)
    try:
        turns = list(adapter.parse(candidate))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ImportUploadError(
            f"Failed to parse importer source {candidate.path}: {_sanitize_error(exc)}"
        ) from exc
    try:
        turn_offset = int(payload["turn_offset"])
        turn_count = int(payload["turn_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportUploadError("Importer run batch is missing its turn range") from exc
    chunk = turns[turn_offset : turn_offset + turn_count]
    if len(chunk) != turn_count:
        raise ImportUploadError(
            "Importer source no longer matches the scanned turn layout; rerun `eve import scan`."
        )
    batch_turns = [turn.to_dict() for turn in chunk]
    batch_hash = hashlib.sha256(
        json.dumps(batch_turns, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    expected_batch_hash = payload.get("batch_hash")
    if not isinstance(expected_batch_hash, str) or batch_hash != expected_batch_hash:
        raise ImportUploadError(
            "Importer source content changed after scan; rerun `eve import scan` before uploading."
        )
    return {
        "import_job_id": payload["import_job_id"],
        "source_system": payload["source_system"],
        "session_id": payload["session_id"],
        "turns": batch_turns,
        "context_mode": payload["context_mode"],
        "source_priority": payload["source_priority"],
        "min_importance": payload["min_importance"],
        "idempotency_key": payload["batch_id"],
        "metadata": {"candidate_path": str(candidate.path)},
    }


def build_batches_for_job(
    *,
    job: ImportJob,
    ledger: ImportLedger,
    batch_size: int,
    auth_source_tool: str,
    auth_mode: str,
    context_mode: str,
    source_priority: int,
    min_importance: int,
) -> tuple[ImportRun, list[ImportBatch]]:
    if batch_size <= 0:
        raise ImportUploadError("--batch-size must be greater than zero")
    candidates = ledger.get_job_candidates(job.job_id)
    if not candidates:
        raise ImportUploadError(
            "Importer scan job has no candidates. Re-run `eve import scan` before uploading."
        )
    run_id = f"run_{uuid.uuid4().hex}"
    batches: list[ImportBatch] = []
    batch_index = 0
    for candidate in candidates:
        adapter = get_adapter(candidate.source_type)
        try:
            turns = list(adapter.parse(candidate))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ImportUploadError(
                f"Failed to parse importer source {candidate.path}: {_sanitize_error(exc)}"
            ) from exc
        for turn_offset in range(0, len(turns), batch_size):
            chunk = turns[turn_offset : turn_offset + batch_size]
            batch_id = f"batch_{uuid.uuid4().hex}"
            request_payload = _batch_payload(
                batch_id=batch_id,
                run_id=run_id,
                source_type=candidate.source_type,
                session_id=candidate.session_id,
                turn_offset=turn_offset,
                turn_count=len(chunk),
                context_mode=context_mode,
                source_priority=source_priority,
                min_importance=min_importance,
                candidate=candidate,
                turns=chunk,
            )
            batches.append(
                ImportBatch(
                    run_id="",
                    batch_id=batch_id,
                    batch_index=batch_index,
                    candidate_path=candidate.path,
                    source_type=candidate.source_type,
                    session_id=candidate.session_id,
                    turn_offset=turn_offset,
                    turn_count=len(chunk),
                    status="pending",
                    request_payload=request_payload,
                )
            )
            batch_index += 1
    if not batches:
        raise ImportUploadError(
            "Importer scan job produced no uploadable turns. Check the selected source files."
        )
    run = ledger.create_run(
        run_id=run_id,
        scan_job_id=job.job_id,
        auth_source_tool=auth_source_tool,
        auth_mode=auth_mode,
        batch_size=batch_size,
        context_mode=context_mode,
        source_priority=source_priority,
        min_importance=min_importance,
        batches=batches,
    )
    return run, batches


def upload_run(
    *,
    config: ResolvedConfig,
    ledger: ImportLedger,
    credential_store: CredentialStore,
    run: ImportRun,
    api_key: str | None = None,
    bearer_token: str | None = None,
    timeout: float = 30.0,
) -> ImportUploadResult:
    tool_name = _validate_auth_source_tool(run.auth_source_tool)
    secret, _ = _load_secret(
        credential_store,
        tool_name=tool_name,
        auth_mode=run.auth_mode,
        api_key=api_key,
        bearer_token=bearer_token,
    )
    headers = _build_headers(tool_name=tool_name, auth_mode=run.auth_mode, secret=secret)
    # Serialize against the actual ledger location, not the broader config state
    # dir, so concurrent uploaders cannot take different locks while mutating the
    # same SQLite ledger.
    with installer_lock(ledger.path.parent):
        ledger.update_run_status(run.run_id, status="running", last_error=None)
        ledger.recover_submitting_batches(run.run_id)
        try:
            for batch in ledger.get_run_batches(run.run_id):
                if batch.status in {"uploaded", "conflict"}:
                    continue
                if not ledger.mark_batch_submitting(batch_id=batch.batch_id):
                    continue
                payload = _materialize_request_payload(batch)
                status_code, response = _request_batch(
                    config=config,
                    headers=headers,
                    payload=payload,
                    timeout=timeout,
                )
                if status_code == 200 and response is not None:
                    remote_status = str(response.get("status", "")).strip().lower()
                    result_summary = response.get("result_summary", {}) or {}
                    if remote_status == "completed":
                        ledger.complete_batch(
                            batch_id=batch.batch_id,
                            status="uploaded",
                            remote_idempotency_key=response.get("idempotency_key"),
                            extracted_count=int(response.get("extracted_count", 0)),
                            stored_count=int(response.get("stored_count", 0)),
                            error_count=int(response.get("error_count", 0)),
                            duplicate=bool(response.get("duplicate", False)),
                            result_summary=result_summary,
                        )
                    elif remote_status == "processing":
                        detail = (
                            "Managed importer batch is still processing remotely; retry upload later."
                        )
                        ledger.fail_batch(batch_id=batch.batch_id, status="failed", error=detail)
                    elif remote_status == "failed":
                        detail = "Managed importer batch failed remotely."
                        if isinstance(result_summary, dict):
                            summary_detail = result_summary.get("detail") or result_summary.get("error")
                            if summary_detail:
                                detail = _sanitize_error(summary_detail)
                        ledger.fail_batch(batch_id=batch.batch_id, status="failed", error=detail)
                    else:
                        ledger.fail_batch(
                            batch_id=batch.batch_id,
                            status="failed",
                            error="Managed importer returned an unknown batch status.",
                        )
                elif status_code == 409:
                    detail = response.get("detail") if isinstance(response, dict) else "idempotency conflict"
                    ledger.fail_batch(batch_id=batch.batch_id, status="conflict", error=_sanitize_error(detail))
                else:
                    detail = "upload failed"
                    if isinstance(response, dict):
                        detail = response.get("detail") or response.get("error") or detail
                    ledger.fail_batch(batch_id=batch.batch_id, status="failed", error=_sanitize_error(detail))
        except ImportUploadError as exc:
            ledger.update_run_status(run.run_id, status="failed", last_error=_sanitize_error(exc))
            raise
    run_batches = ledger.get_run_batches(run.run_id)
    failed = [batch for batch in run_batches if batch.status in {"failed", "conflict"}]
    run_status = "failed" if failed else "completed"
    run_error = failed[0].last_error if failed else None
    ledger.update_run_status(run.run_id, status=run_status, last_error=run_error)
    refreshed_run = ledger.get_run(run.run_id)
    if refreshed_run is None:
        raise ImportUploadError(f"Run {run.run_id} disappeared from the local ledger")
    return ImportUploadResult(run=refreshed_run, batches=run_batches)
