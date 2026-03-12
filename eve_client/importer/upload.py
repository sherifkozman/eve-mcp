"""Managed importer upload workflow."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eve_client.auth.base import CredentialStore, CredentialStoreUnavailableError
from eve_client.config import ResolvedConfig, resolve_api_base_url
from eve_client.importer import ImportLedger, get_adapter
from eve_client.importer.models import ImportBatch, ImportJob, ImportRun, ImportTurn
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
    run_id: str,
    source_type: str,
    session_id: str,
    turns: list[ImportTurn],
    context_mode: str,
    source_priority: int,
    min_importance: int,
    candidate_path: Path,
) -> dict[str, object]:
    return {
        "import_job_id": run_id,
        "source_system": source_type,
        "session_id": session_id,
        "turns": [turn.to_dict() for turn in turns],
        "context_mode": context_mode,
        "source_priority": source_priority,
        "min_importance": min_importance,
        "metadata": {"candidate_path": str(candidate_path)},
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
            batches.append(
                ImportBatch(
                    run_id="",
                    batch_id=f"batch_{uuid.uuid4().hex}",
                    batch_index=batch_index,
                    candidate_path=candidate.path,
                    source_type=candidate.source_type,
                    session_id=candidate.session_id,
                    turn_offset=turn_offset,
                    turn_count=len(chunk),
                    status="pending",
                    request_payload=_batch_payload(
                        run_id=run_id,
                        source_type=candidate.source_type,
                        session_id=candidate.session_id,
                        turns=chunk,
                        context_mode=context_mode,
                        source_priority=source_priority,
                        min_importance=min_importance,
                        candidate_path=candidate.path,
                    ),
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
    ledger.update_run_status(run.run_id, status="running", last_error=None)
    final_batches: list[ImportBatch] = []
    for batch in ledger.get_run_batches(run.run_id):
        if batch.status == "uploaded":
            final_batches.append(batch)
            continue
        status_code, response = _request_batch(
            config=config,
            headers=headers,
            payload=batch.request_payload,
            timeout=timeout,
        )
        if status_code == 200 and response is not None:
            ledger.complete_batch(
                batch_id=batch.batch_id,
                status="uploaded",
                remote_idempotency_key=response.get("idempotency_key"),
                extracted_count=int(response.get("extracted_count", 0)),
                stored_count=int(response.get("stored_count", 0)),
                error_count=int(response.get("error_count", 0)),
                duplicate=bool(response.get("duplicate", False)),
                result_summary=response.get("result_summary", {}) or {},
            )
        elif status_code == 409:
            detail = response.get("detail") if isinstance(response, dict) else "idempotency conflict"
            ledger.fail_batch(batch_id=batch.batch_id, status="conflict", error=_sanitize_error(detail))
        else:
            detail = "upload failed"
            if isinstance(response, dict):
                detail = response.get("detail") or response.get("error") or detail
            ledger.fail_batch(batch_id=batch.batch_id, status="failed", error=_sanitize_error(detail))
        final_batches = ledger.get_run_batches(run.run_id)
    run_batches = ledger.get_run_batches(run.run_id)
    failed = [batch for batch in run_batches if batch.status in {"failed", "conflict"}]
    run_status = "failed" if failed else "completed"
    run_error = failed[0].last_error if failed else None
    ledger.update_run_status(run.run_id, status=run_status, last_error=run_error)
    refreshed_run = ledger.get_run(run.run_id)
    if refreshed_run is None:
        raise ImportUploadError(f"Run {run.run_id} disappeared from the local ledger")
    return ImportUploadResult(run=refreshed_run, batches=run_batches)
