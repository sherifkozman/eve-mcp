"""State binding and replay watermark helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from keyring.errors import KeyringError

from eve_client.auth.file_store import FileCredentialStore
from eve_client.auth.keyring_store import KeyringCredentialStore
from eve_client.state_dir import ensure_private_state_dir


STATE_BINDING_FILE = "state-binding.json"
INSTALLATION_ID_KEY = "installation-id"


class StateBindingError(RuntimeError):
    """Raised when installer state binding or replay watermark checks fail."""


def _state_scope(state_dir: Path) -> str:
    return hashlib.sha256(str(state_dir.resolve(strict=False)).encode("utf-8")).hexdigest()


def _file_store(state_dir: Path) -> FileCredentialStore:
    return FileCredentialStore(state_dir / STATE_BINDING_FILE, state_dir)


def _installation_key_name(state_dir: Path) -> str:
    return f"{INSTALLATION_ID_KEY}:{_state_scope(state_dir)}"


def get_or_create_installation_id(state_dir: Path, *, allow_file_fallback: bool) -> str:
    ensure_private_state_dir(state_dir)
    key_name = _installation_key_name(state_dir)
    keyring_store = KeyringCredentialStore()
    try:
        value = keyring_store.get(key_name)
        if value:
            return value
    except KeyringError:
        if not allow_file_fallback:
            raise StateBindingError("Unable to load installation identity without explicit file fallback.") from None
    payload = _file_store(state_dir).load()
    existing = payload.get(key_name)
    if existing:
        return existing
    installation_id = hashlib.sha256(f"{state_dir.resolve(strict=False)}".encode("utf-8")).hexdigest()[:24]
    try:
        keyring_store.set(key_name, installation_id)
    except KeyringError:
        if not allow_file_fallback:
            raise StateBindingError("Unable to persist installation identity without explicit file fallback.") from None
    if allow_file_fallback:
        payload[key_name] = installation_id
        _file_store(state_dir).write(payload)
    return installation_id


def _sequence_key_name(state_dir: Path, *, allow_file_fallback: bool) -> str:
    installation_id = get_or_create_installation_id(state_dir, allow_file_fallback=allow_file_fallback)
    return f"manifest-sequence:{installation_id}"


def load_sequence_watermark(state_dir: Path, *, allow_file_fallback: bool) -> int:
    ensure_private_state_dir(state_dir)
    key_name = _sequence_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    keyring_store = KeyringCredentialStore()
    try:
        value = keyring_store.get(key_name)
        if value is None:
            return 0
        return int(value)
    except ValueError:
        raise StateBindingError("State binding watermark is malformed.") from None
    except KeyringError:
        if not allow_file_fallback:
            raise StateBindingError("Unable to load state binding watermark without explicit file fallback.") from None
    payload = _file_store(state_dir).load()
    raw = payload.get(key_name)
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        raise StateBindingError("State binding watermark is malformed.") from None


def store_sequence_watermark(state_dir: Path, sequence: int, *, allow_file_fallback: bool) -> None:
    ensure_private_state_dir(state_dir)
    key_name = _sequence_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    keyring_store = KeyringCredentialStore()
    try:
        keyring_store.set(key_name, str(sequence))
        return
    except KeyringError:
        if not allow_file_fallback:
            raise StateBindingError("Unable to persist state binding watermark without explicit file fallback.") from None
    store = _file_store(state_dir)
    payload = store.load()
    payload[key_name] = str(sequence)
    store.write(payload)


def clear_sequence_watermark(state_dir: Path, *, allow_file_fallback: bool) -> None:
    ensure_private_state_dir(state_dir)
    key_name = _sequence_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    keyring_store = KeyringCredentialStore()
    try:
        keyring_store.delete(key_name)
    except KeyringError:
        pass
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
    try:
        keyring_store.delete(key_name)
    except KeyringError:
        pass
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
) -> None:
    watermark = load_sequence_watermark(state_dir, allow_file_fallback=allow_file_fallback)
    if watermark == 0:
        return
    if not manifest_exists:
        raise StateBindingError("Installer state reset detected; manifest is missing but prior state watermark exists.")
    if sequence < watermark:
        raise StateBindingError(
            f"Installer manifest sequence replay detected; current sequence {sequence} is older than watermark {watermark}."
        )
