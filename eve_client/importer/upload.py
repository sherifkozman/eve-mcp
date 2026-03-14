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
from eve_client.importer.adapters import get_adapter
from eve_client.importer.ledger import ImportLedger
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
_MAX_BATCH_REQUEST_BYTES = 64 * 1024
_MAX_REMOTE_RESULT_SUMMARY_BYTES = 8 * 1024
_MAX_UPLOAD_TIMEOUT_SECONDS = 180.0
_TRANSPORT_RETRY_ATTEMPTS = 1
_SOURCE_BATCH_TURN_CAPS: dict[str, int] = {
    "claude-code": 8,
    "codex-cli": 25,
    "gemini-cli": 25,
}
_SOURCE_TARGET_REQUEST_BYTES: dict[str, int] = {
    "claude-code": 24 * 1024,
    "codex-cli": 48 * 1024,
    "gemini-cli": 48 * 1024,
}


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
    except TimeoutError as exc:
        raise ImportUploadError(f"Connection failed: {_sanitize_error(exc)}") from exc
    except urllib.error.URLError as exc:
        raise ImportUploadError(f"Connection failed: {_sanitize_error(exc.reason)}") from exc


def _estimate_request_bytes(
    *,
    run_id: str,
    source_type: str,
    session_id: str,
    context_mode: str,
    source_priority: int,
    min_importance: int,
    candidate: ImportCandidate,
    turns: list[ImportTurn],
) -> int:
    payload = {
        "import_job_id": run_id,
        "source_system": source_type,
        "session_id": session_id,
        "turns": [turn.to_dict() for turn in turns],
        "context_mode": context_mode,
        "source_priority": source_priority,
        "min_importance": min_importance,
        "idempotency_key": "batch_preview",
    }
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


_SENSITIVE_METADATA_KEYS = {"path", "file_path", "source_path", "cwd"}


def _sanitize_metadata_value(value: object) -> object:
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, nested_value in value.items():
            if key in _SENSITIVE_METADATA_KEYS:
                continue
            sanitized[key] = _sanitize_metadata_value(nested_value)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_metadata_value(item) for item in value]
    return value


def _sanitize_turn_dicts_for_transport(turn_dicts: list[dict[str, object]]) -> list[dict[str, object]]:
    sanitized_turns: list[dict[str, object]] = []
    for turn in turn_dicts:
        sanitized_turn: dict[str, object] = {}
        for key, value in turn.items():
            if key == "metadata" and isinstance(value, dict):
                sanitized_turn[key] = _sanitize_metadata_value(value)
            else:
                sanitized_turn[key] = value
        sanitized_turns.append(sanitized_turn)
    return sanitized_turns


def _slice_turn_groups(
    *,
    run_id: str,
    candidate: ImportCandidate,
    turns: list[ImportTurn],
    batch_size: int,
    context_mode: str,
    source_priority: int,
    min_importance: int,
) -> list[tuple[int, list[ImportTurn]]]:
    turn_groups: list[tuple[int, list[ImportTurn]]] = []
    source_turn_cap = _SOURCE_BATCH_TURN_CAPS.get(candidate.source_type, batch_size)
    max_turns_per_batch = min(batch_size, source_turn_cap)
    target_request_bytes = min(
        _MAX_BATCH_REQUEST_BYTES,
        _SOURCE_TARGET_REQUEST_BYTES.get(candidate.source_type, _MAX_BATCH_REQUEST_BYTES),
    )
    current_offset = 0
    current_chunk: list[ImportTurn] = []
    current_chunk_offset = 0
    for index, turn in enumerate(turns):
        next_chunk = [*current_chunk, turn]
        estimated_bytes = _estimate_request_bytes(
            run_id=run_id,
            source_type=candidate.source_type,
            session_id=candidate.session_id,
            context_mode=context_mode,
            source_priority=source_priority,
            min_importance=min_importance,
            candidate=candidate,
            turns=next_chunk,
        )
        if not current_chunk:
            if estimated_bytes > _MAX_BATCH_REQUEST_BYTES:
                raise ImportUploadError(
                    "Importer source contains a single turn that exceeds the maximum upload batch size."
                )
            current_chunk = next_chunk
            current_chunk_offset = current_offset
            current_offset = index + 1
            continue
        if len(next_chunk) > max_turns_per_batch or estimated_bytes > target_request_bytes:
            turn_groups.append((current_chunk_offset, current_chunk))
            current_chunk = [turn]
            current_chunk_offset = index
            current_offset = index + 1
            single_bytes = _estimate_request_bytes(
                run_id=run_id,
                source_type=candidate.source_type,
                session_id=candidate.session_id,
                context_mode=context_mode,
                source_priority=source_priority,
                min_importance=min_importance,
                candidate=candidate,
                turns=current_chunk,
            )
            if single_bytes > _MAX_BATCH_REQUEST_BYTES:
                raise ImportUploadError(
                    "Importer source contains a single turn that exceeds the maximum upload batch size."
                )
            continue
        current_chunk = next_chunk
        current_offset = index + 1
    if current_chunk:
        turn_groups.append((current_chunk_offset, current_chunk))
    return turn_groups


def _is_retryable_transport_error(exc: ImportUploadError) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in ("timed out", "timeout", "temporarily unavailable", "connection reset", "connection aborted")
    )


def _is_retryable_batch_failure(batch: ImportBatch) -> bool:
    if batch.status != "failed":
        return False
    error = (batch.last_error or "").lower()
    return any(
        token in error
        for token in (
            "transient",
            "temporary",
            "network",
            "connection failed",
            "timeout",
            "timed out",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
        )
    )


def _effective_timeout(*, payload: dict[str, object], base_timeout: float) -> float:
    payload_bytes = len(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    )
    turn_count = len(payload.get("turns", [])) if isinstance(payload.get("turns"), list) else 0
    dynamic_timeout = max(
        base_timeout,
        15.0 + (payload_bytes / 1024.0) * 0.5 + turn_count * 5.0,
    )
    return min(dynamic_timeout, _MAX_UPLOAD_TIMEOUT_SECONDS)


def _normalize_remote_idempotency_key(
    raw_value: object, *, expected_request_key: str
) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ImportUploadError("Managed importer returned an invalid idempotency key.")
    normalized = raw_value.strip()
    if normalized != expected_request_key:
        raise ImportUploadError("Managed importer returned a mismatched idempotency key.")
    return normalized


def _normalize_remote_result_summary(raw_value: object) -> dict[str, object]:
    if not isinstance(raw_value, dict):
        return {}
    encoded = json.dumps(raw_value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    if len(encoded) <= _MAX_REMOTE_RESULT_SUMMARY_BYTES:
        return raw_value
    return {
        "truncated": True,
        "detail": (
            "Managed importer result summary exceeded the local safety limit and was omitted."
        ),
    }


def _coerce_remote_count(raw_value: object, *, field_name: str) -> int:
    if isinstance(raw_value, bool):
        raise ImportUploadError(f"Managed importer returned an invalid {field_name}.")
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, str) and raw_value.isdigit():
        return int(raw_value)
    raise ImportUploadError(f"Managed importer returned an invalid {field_name}.")


def _coerce_remote_duplicate(raw_value: object) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    raise ImportUploadError("Managed importer returned an invalid duplicate flag.")


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
    batch_turns = _sanitize_turn_dicts_for_transport([turn.to_dict() for turn in turns])
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
            "content_sha256": candidate.content_sha256,
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
        content_sha256 = str(candidate["content_sha256"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportUploadError("Importer run batch contains invalid candidate snapshot") from exc
    return ImportCandidate(
        source_type=source_type,  # type: ignore[arg-type]
        path=path,
        session_id=session_id,
        modified_at=modified_at if modified_at.tzinfo else modified_at.replace(tzinfo=UTC),
        size_bytes=size_bytes,
        content_sha256=content_sha256,
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
    if not candidate.content_sha256:
        return
    current_hash = hashlib.sha256(candidate.path.read_bytes()).hexdigest()
    if current_hash != candidate.content_sha256:
        raise ImportUploadError(
            "Importer source content changed after scan; rerun `eve import scan` before uploading."
        )


def _materialize_request_payload(batch: ImportBatch) -> dict[str, object]:
    payload = batch.request_payload
    candidate = _candidate_from_payload(payload)
    _validate_candidate_snapshot(candidate)
    try:
        adapter = get_adapter(candidate.source_type)
    except KeyError as exc:
        raise ImportUploadError(
            f"No importer adapter is available for source type {candidate.source_type!r}"
        ) from exc
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
    batch_turns = _sanitize_turn_dicts_for_transport([turn.to_dict() for turn in chunk])
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
        _validate_candidate_snapshot(candidate)
        try:
            adapter = get_adapter(candidate.source_type)
        except KeyError as exc:
            raise ImportUploadError(
                f"No importer adapter is available for source type {candidate.source_type!r}"
            ) from exc
        try:
            turns = list(adapter.parse(candidate))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ImportUploadError(
                f"Failed to parse importer source {candidate.path}: {_sanitize_error(exc)}"
            ) from exc
        turn_groups = _slice_turn_groups(
            run_id=run_id,
            candidate=candidate,
            turns=turns,
            batch_size=batch_size,
            context_mode=context_mode,
            source_priority=source_priority,
            min_importance=min_importance,
        )
        for turn_offset, chunk in turn_groups:
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
    return run, ledger.get_run_batches(run.run_id)


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
            while True:
                batch = next(
                    (
                        item
                        for item in ledger.get_run_batches(run.run_id)
                        if item.status == "pending" or _is_retryable_batch_failure(item)
                    ),
                    None,
                )
                if batch is None:
                    break
                if not ledger.mark_batch_submitting(batch_id=batch.batch_id):
                    continue
                payload = _materialize_request_payload(batch)
                expected_request_key = str(payload["idempotency_key"])
                if (
                    batch.status == "remote-processing"
                    and batch.remote_idempotency_key
                    and batch.remote_idempotency_key != expected_request_key
                ):
                    raise ImportUploadError(
                        "Importer remote-processing batch idempotency drifted before resume."
                    )
                effective_timeout = _effective_timeout(payload=payload, base_timeout=timeout)
                attempt = 0
                while True:
                    try:
                        status_code, response = _request_batch(
                            config=config,
                            headers=headers,
                            payload=payload,
                            timeout=effective_timeout,
                        )
                        break
                    except ImportUploadError as exc:
                        # Preserve the same idempotency key when retrying after transport
                        # failures. The request may have reached the server and only the
                        # response path failed, so splitting into fresh child batch IDs here
                        # risks duplicate imports.
                        if attempt >= _TRANSPORT_RETRY_ATTEMPTS or not _is_retryable_transport_error(exc):
                            raise
                        attempt += 1
                        effective_timeout = min(effective_timeout * 2, _MAX_UPLOAD_TIMEOUT_SECONDS)
                if batch.batch_id not in {item.batch_id for item in ledger.get_run_batches(run.run_id)}:
                    continue
                if status_code == 200 and response is not None:
                    remote_status = str(response.get("status", "")).strip().lower()
                    result_summary = _normalize_remote_result_summary(response.get("result_summary", {}))
                    if remote_status == "completed":
                        remote_idempotency_key = _normalize_remote_idempotency_key(
                            response.get("idempotency_key"),
                            expected_request_key=expected_request_key,
                        )
                        ledger.complete_batch(
                            batch_id=batch.batch_id,
                            status="uploaded",
                            remote_idempotency_key=remote_idempotency_key,
                            extracted_count=_coerce_remote_count(
                                response.get("extracted_count", 0),
                                field_name="extracted_count",
                            ),
                            stored_count=_coerce_remote_count(
                                response.get("stored_count", 0),
                                field_name="stored_count",
                            ),
                            error_count=_coerce_remote_count(
                                response.get("error_count", 0),
                                field_name="error_count",
                            ),
                            duplicate=_coerce_remote_duplicate(response.get("duplicate", False)),
                            result_summary=result_summary,
                        )
                    elif remote_status == "processing":
                        remote_idempotency_key = _normalize_remote_idempotency_key(
                            response.get("idempotency_key"),
                            expected_request_key=expected_request_key,
                        )
                        detail = (
                            "Managed importer batch is still processing remotely; retry upload later."
                        )
                        if not ledger.mark_batch_remote_processing(
                            batch_id=batch.batch_id,
                            remote_idempotency_key=remote_idempotency_key,
                            result_summary=result_summary,
                            detail=detail,
                        ):
                            raise ImportUploadError(
                                "Importer run batch changed state unexpectedly during remote-processing handoff."
                            )
                        current_batch_id = None
                        break
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
            current_batch = next(
                (item for item in ledger.get_run_batches(run.run_id) if item.status == "submitting"),
                None,
            )
            if current_batch is not None:
                ledger.fail_batch(
                    batch_id=current_batch.batch_id,
                    status="failed",
                    error=_sanitize_error(exc),
                )
            ledger.update_run_status(run.run_id, status="failed", last_error=_sanitize_error(exc))
            raise
        run_batches = ledger.get_run_batches(run.run_id)
        failed = [batch for batch in run_batches if batch.status in {"failed", "conflict"}]
        in_progress = [
            batch
            for batch in run_batches
            if batch.status in {"pending", "submitting", "remote-processing"}
        ]
        if failed:
            run_status = "failed"
            run_error = failed[0].last_error
        elif in_progress:
            run_status = "running"
            run_error = None
        else:
            run_status = "completed"
            run_error = None
        ledger.update_run_status(run.run_id, status=run_status, last_error=run_error)
        refreshed_run = ledger.get_run(run.run_id)
    if refreshed_run is None:
        raise ImportUploadError(f"Run {run.run_id} disappeared from the local ledger")
    return ImportUploadResult(run=refreshed_run, batches=run_batches)
