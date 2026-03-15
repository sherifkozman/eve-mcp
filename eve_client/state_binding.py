"""State binding and replay watermark helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eve_client.auth.file_store import FileCredentialStore
from eve_client.safe_fs import SafeFS
from eve_client.state_dir import ensure_private_state_dir

STATE_BINDING_FILE = "state-binding.json"
INSTALLATION_ID_KEY = "installation-id"


class StateBindingError(RuntimeError):
    """Raised when installer state binding or replay watermark checks fail."""


def _state_scope(state_dir: Path) -> str:
    return hashlib.sha256(str(state_dir.resolve(strict=False)).encode("utf-8")).hexdigest()


def _file_store(state_dir: Path) -> FileCredentialStore:
    return FileCredentialStore(state_dir / STATE_BINDING_FILE, state_dir)


def _load_file_payload(state_dir: Path, *, read_only: bool) -> dict[str, str]:
    if not read_only:
        return _file_store(state_dir).load()
    path = state_dir / STATE_BINDING_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(SafeFS.from_roots([state_dir]).read_text(path, encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise StateBindingError(f"Unable to read state binding file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StateBindingError("State binding file is malformed.") from exc


def _installation_key_name(state_dir: Path) -> str:
    return f"{INSTALLATION_ID_KEY}:{_state_scope(state_dir)}"


def load_existing_installation_id(
    state_dir: Path, *, allow_file_fallback: bool, sync_back: bool = True
) -> str | None:
    # allow_file_fallback is kept for API compatibility; file is always used (keyring removed).
    if sync_back:
        ensure_private_state_dir(state_dir)
    key_name = _installation_key_name(state_dir)
    payload = _load_file_payload(state_dir, read_only=not sync_back)
    return payload.get(key_name) or None


def get_or_create_installation_id(state_dir: Path, *, allow_file_fallback: bool) -> str:
    # allow_file_fallback is kept for API compatibility; file is always used (keyring removed).
    ensure_private_state_dir(state_dir)
    key_name = _installation_key_name(state_dir)
    store = _file_store(state_dir)
    payload = store.load()
    existing = payload.get(key_name)
    if existing:
        return existing
    installation_id = hashlib.sha256(f"{state_dir.resolve(strict=False)}".encode()).hexdigest()[:24]
    payload[key_name] = installation_id
    store.write(payload)
    return installation_id


def _sequence_key_name_for_installation_id(installation_id: str) -> str:
    return f"manifest-sequence:{installation_id}"


def _sequence_key_name(state_dir: Path, *, allow_file_fallback: bool) -> str:
    installation_id = get_or_create_installation_id(
        state_dir, allow_file_fallback=allow_file_fallback
    )
    return _sequence_key_name_for_installation_id(installation_id)


def load_existing_sequence_watermark(
    state_dir: Path, *, allow_file_fallback: bool, sync_back: bool = True
) -> int:
    # allow_file_fallback is kept for API compatibility; file is always used (keyring removed).
    if sync_back:
        ensure_private_state_dir(state_dir)
    installation_id = load_existing_installation_id(
        state_dir, allow_file_fallback=allow_file_fallback, sync_back=sync_back
    )
    if installation_id is None:
        return 0
    key_name = _sequence_key_name_for_installation_id(installation_id)
    payload = _load_file_payload(state_dir, read_only=not sync_back)
    raw = payload.get(key_name)
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        raise StateBindingError("State binding watermark is malformed.") from None


def load_sequence_watermark(state_dir: Path, *, allow_file_fallback: bool) -> int:
    return load_existing_sequence_watermark(state_dir, allow_file_fallback=allow_file_fallback)


def store_sequence_watermark(state_dir: Path, sequence: int, *, allow_file_fallback: bool) -> None:
    # allow_file_fallback is kept for API compatibility; file is always used (keyring removed).
    ensure_private_state_dir(state_dir)
    key_name = _sequence_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    store = _file_store(state_dir)
    payload = store.load()
    payload[key_name] = str(sequence)
    store.write(payload)


def clear_sequence_watermark(state_dir: Path, *, allow_file_fallback: bool) -> None:
    # allow_file_fallback is kept for API compatibility; file is always used (keyring removed).
    ensure_private_state_dir(state_dir)
    key_name = _sequence_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    store = _file_store(state_dir)
    payload = store.load()
    if key_name in payload:
        del payload[key_name]
        store.write(payload)


def clear_installation_id(state_dir: Path, *, allow_file_fallback: bool) -> None:
    # allow_file_fallback is kept for API compatibility; file is always used (keyring removed).
    ensure_private_state_dir(state_dir)
    key_name = _installation_key_name(state_dir)
    store = _file_store(state_dir)
    payload = store.load()
    if key_name in payload:
        del payload[key_name]
        store.write(payload)


def verify_sequence_watermark(
    state_dir: Path,
    *,
    manifest_exists: bool,
    sequence: int,
    allow_file_fallback: bool,
    sync_back: bool = True,
) -> None:
    watermark = load_existing_sequence_watermark(
        state_dir,
        allow_file_fallback=allow_file_fallback,
        sync_back=sync_back,
    )
    if watermark == 0:
        return
    if not manifest_exists:
        raise StateBindingError(
            "Installer state reset detected; manifest is missing but prior state watermark exists."
        )
    if sequence < watermark:
        raise StateBindingError(
            f"Installer manifest sequence replay detected; current sequence {sequence} "
            f"is older than watermark {watermark}."
        )
