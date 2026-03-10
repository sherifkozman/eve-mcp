"""Manifest handling for Eve-managed changes."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from eve_client.integrity import (
    HMAC_ALGORITHM,
    IntegrityKeyError,
    compute_payload_digest,
    get_or_create_integrity_key,
    sign_payload,
    verify_signature,
)
from eve_client.state_binding import (
    StateBindingError,
    get_or_create_installation_id,
    store_sequence_watermark,
    verify_sequence_watermark,
)

from .models import ManifestRecord
from .safe_fs import SafeFS
from .state_dir import ensure_private_state_dir

MANIFEST_VERSION = 2


class ManifestIntegrityError(RuntimeError):
    """Raised when the installer manifest fails integrity checks."""


def manifest_path(state_dir: Path) -> Path:
    return state_dir / "manifest.json"


def _empty_envelope() -> dict[str, object]:
    payload: dict[str, object] = {
        "version": MANIFEST_VERSION,
        "hmac_algorithm": HMAC_ALGORITHM,
        "installation_id": None,
        "sequence": 0,
        "prev_digest": None,
        "records": [],
    }
    return {
        "payload": payload,
        "signature": "",
    }


def load_manifest_envelope(
    state_dir: Path, *, allow_file_fallback: bool = False
) -> dict[str, object]:
    ensure_private_state_dir(state_dir)
    path = manifest_path(state_dir)
    try:
        installation_id = get_or_create_installation_id(
            state_dir, allow_file_fallback=allow_file_fallback
        )
    except StateBindingError as exc:
        raise ManifestIntegrityError(str(exc)) from exc
    if not path.exists():
        try:
            verify_sequence_watermark(
                state_dir,
                manifest_exists=False,
                sequence=0,
                allow_file_fallback=allow_file_fallback,
            )
        except StateBindingError as exc:
            raise ManifestIntegrityError(str(exc)) from exc
        return _empty_envelope()
    envelope = json.loads(SafeFS.from_roots([state_dir]).read_text(path, encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ManifestIntegrityError("Manifest envelope is not an object")
    payload = envelope.get("payload")
    signature = envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise ManifestIntegrityError("Manifest envelope is malformed")
    if payload.get("version") != MANIFEST_VERSION:
        raise ManifestIntegrityError("Manifest version mismatch")
    if payload.get("hmac_algorithm") != HMAC_ALGORITHM:
        raise ManifestIntegrityError("Manifest HMAC algorithm mismatch")
    if payload.get("installation_id") != installation_id:
        raise ManifestIntegrityError("Manifest installation identity mismatch")
    try:
        verify_sequence_watermark(
            state_dir,
            manifest_exists=True,
            sequence=int(payload.get("sequence", 0)),
            allow_file_fallback=allow_file_fallback,
        )
    except (ValueError, StateBindingError) as exc:
        raise ManifestIntegrityError(str(exc)) from exc
    try:
        secret = get_or_create_integrity_key(state_dir, allow_file_fallback=allow_file_fallback)
    except IntegrityKeyError as exc:
        raise ManifestIntegrityError(str(exc)) from exc
    if not verify_signature(payload, secret, signature):
        raise ManifestIntegrityError("Manifest signature verification failed")
    return envelope


def load_manifest(state_dir: Path, *, allow_file_fallback: bool = False) -> list[ManifestRecord]:
    envelope = load_manifest_envelope(state_dir, allow_file_fallback=allow_file_fallback)
    payload = envelope["payload"]
    return [ManifestRecord(**item) for item in payload.get("records", [])]


def write_manifest(
    state_dir: Path, records: list[ManifestRecord], *, allow_file_fallback: bool = False
) -> None:
    ensure_private_state_dir(state_dir)
    try:
        installation_id = get_or_create_installation_id(
            state_dir, allow_file_fallback=allow_file_fallback
        )
    except StateBindingError as exc:
        raise ManifestIntegrityError(str(exc)) from exc
    previous = load_manifest_envelope(state_dir, allow_file_fallback=allow_file_fallback)
    previous_payload = previous["payload"]
    prev_digest = compute_payload_digest(previous_payload)
    payload = {
        "version": MANIFEST_VERSION,
        "hmac_algorithm": HMAC_ALGORITHM,
        "installation_id": installation_id,
        "sequence": int(previous_payload.get("sequence", 0)) + 1,
        "prev_digest": prev_digest
        if previous_payload.get("sequence", 0) > 0 or previous_payload.get("records")
        else None,
        "records": [asdict(record) for record in records],
    }
    try:
        secret = get_or_create_integrity_key(state_dir, allow_file_fallback=allow_file_fallback)
    except IntegrityKeyError as exc:
        raise ManifestIntegrityError(str(exc)) from exc
    envelope = {"payload": payload, "signature": sign_payload(payload, secret)}
    SafeFS.from_roots([state_dir]).write_text_atomic(
        manifest_path(state_dir),
        json.dumps(envelope, indent=2) + "\n",
    )
    try:
        store_sequence_watermark(
            state_dir,
            payload["sequence"],
            allow_file_fallback=allow_file_fallback,
        )
    except StateBindingError as exc:
        raise ManifestIntegrityError(str(exc)) from exc
