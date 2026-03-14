"""State binding and replay watermark helpers."""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path

from keyring.errors import KeyringError

from eve_client.auth.file_store import FileCredentialStore
from eve_client.auth.keyring_store import KeyringCredentialStore
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
    if sync_back:
        ensure_private_state_dir(state_dir)
    key_name = _installation_key_name(state_dir)
    keyring_store = KeyringCredentialStore()
    try:
        value = keyring_store.get(key_name)
    except KeyringError:
        if not allow_file_fallback:
            raise StateBindingError(
                "Unable to load installation identity without explicit file fallback."
            ) from None
        value = None
    if value and allow_file_fallback and sync_back:
        payload = _file_store(state_dir).load()
        if payload.get(key_name) != value:
            payload[key_name] = value
            _file_store(state_dir).write(payload)
        return value
    if value:
        return value
    if not allow_file_fallback:
        return None
    payload = _load_file_payload(state_dir, read_only=not sync_back)
    existing = payload.get(key_name)
    return existing or None


def get_or_create_installation_id(state_dir: Path, *, allow_file_fallback: bool) -> str:
    ensure_private_state_dir(state_dir)
    key_name = _installation_key_name(state_dir)
    existing = load_existing_installation_id(state_dir, allow_file_fallback=allow_file_fallback)
    if existing:
        return existing
    installation_id = hashlib.sha256(f"{state_dir.resolve(strict=False)}".encode()).hexdigest()[:24]
    keyring_store = KeyringCredentialStore()
    try:
        keyring_store.set(key_name, installation_id)
    except KeyringError:
        if not allow_file_fallback:
            raise StateBindingError(
                "Unable to persist installation identity without explicit file fallback."
            ) from None
    if allow_file_fallback:
        payload = _file_store(state_dir).load()
        payload[key_name] = installation_id
        _file_store(state_dir).write(payload)
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
    if sync_back:
        ensure_private_state_dir(state_dir)
    installation_id = load_existing_installation_id(
        state_dir, allow_file_fallback=allow_file_fallback, sync_back=sync_back
    )
    if installation_id is None:
        return 0
    key_name = _sequence_key_name_for_installation_id(installation_id)
    keyring_store = KeyringCredentialStore()
    try:
        value = keyring_store.get(key_name)
    except KeyringError:
        if not allow_file_fallback:
            raise StateBindingError(
                "Unable to load state binding watermark without explicit file fallback."
            ) from None
        value = None
    if value is not None:
        try:
            watermark = int(value)
        except ValueError:
            raise StateBindingError("State binding watermark is malformed.") from None
        if allow_file_fallback and sync_back:
            payload = _file_store(state_dir).load()
            if payload.get(key_name) != value:
                payload[key_name] = value
                _file_store(state_dir).write(payload)
        return watermark
    if not allow_file_fallback:
        return 0
    payload = _load_file_payload(state_dir, read_only=not sync_back)
    raw = payload.get(key_name)
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        raise StateBindingError("State binding watermark is malformed.") from None


def load_sequence_watermark(state_dir: Path, *, allow_file_fallback: bool) -> int:
    return load_existing_sequence_watermark(
        state_dir, allow_file_fallback=allow_file_fallback
    )


def store_sequence_watermark(state_dir: Path, sequence: int, *, allow_file_fallback: bool) -> None:
    ensure_private_state_dir(state_dir)
    key_name = _sequence_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    keyring_store = KeyringCredentialStore()
    persist_fallback = False
    try:
        keyring_store.set(key_name, str(sequence))
        persist_fallback = allow_file_fallback and keyring_store.backend_is_low_assurance()
        if not persist_fallback:
            return
    except KeyringError:
        if not allow_file_fallback:
            raise StateBindingError(
                "Unable to persist state binding watermark without explicit file fallback."
            ) from None
        persist_fallback = True
    if persist_fallback:
        store = _file_store(state_dir)
        payload = store.load()
        payload[key_name] = str(sequence)
        store.write(payload)


def clear_sequence_watermark(state_dir: Path, *, allow_file_fallback: bool) -> None:
    ensure_private_state_dir(state_dir)
    key_name = _sequence_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    keyring_store = KeyringCredentialStore()
    with contextlib.suppress(KeyringError):
        keyring_store.delete(key_name)
    if not allow_file_fallback:
        return
    store = _file_store(state_dir)
    payload = store.load()
    if key_name in payload:
        del payload[key_name]
        store.write(payload)


def clear_installation_id(state_dir: Path, *, allow_file_fallback: bool) -> None:
    ensure_private_state_dir(state_dir)
    key_name = _installation_key_name(state_dir)
    keyring_store = KeyringCredentialStore()
    with contextlib.suppress(KeyringError):
        keyring_store.delete(key_name)
    if not allow_file_fallback:
        return
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
            f"Installer manifest sequence replay detected; current sequence {sequence} is older than watermark {watermark}."
        )
