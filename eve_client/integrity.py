"""Manifest integrity helpers."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import secrets
from pathlib import Path

from keyring.errors import KeyringError

from eve_client.auth.keyring_store import KeyringCredentialStore
from eve_client.safe_fs import SafeFS
from eve_client.state_binding import get_or_create_installation_id, load_existing_installation_id
from eve_client.state_dir import ensure_private_state_dir

INTEGRITY_KEY_FILE = "integrity.key"
INTEGRITY_KEY_NAME_PREFIX = "installer-integrity-key"
HMAC_ALGORITHM = "HMAC-SHA256"


class IntegrityKeyError(RuntimeError):
    """Raised when the manifest integrity key cannot be loaded safely."""


def integrity_key_path(state_dir: Path) -> Path:
    return state_dir / INTEGRITY_KEY_FILE


def integrity_key_name(state_dir: Path, *, allow_file_fallback: bool) -> str:
    installation_id = get_or_create_installation_id(
        state_dir, allow_file_fallback=allow_file_fallback
    )
    return f"{INTEGRITY_KEY_NAME_PREFIX}:{installation_id}"


def load_existing_integrity_key(
    state_dir: Path, *, allow_file_fallback: bool, sync_back: bool = True
) -> str | None:
    if sync_back:
        ensure_private_state_dir(state_dir)
    installation_id = load_existing_installation_id(
        state_dir, allow_file_fallback=allow_file_fallback, sync_back=sync_back
    )
    if installation_id is None:
        return None
    key_name = f"{INTEGRITY_KEY_NAME_PREFIX}:{installation_id}"
    path = integrity_key_path(state_dir)
    keyring_store = KeyringCredentialStore()
    try:
        value = keyring_store.get(key_name)
    except KeyringError:
        if not allow_file_fallback:
            raise IntegrityKeyError(
                "No keyring available for manifest integrity key; explicit file fallback is required."
            ) from None
        value = None
    if value and allow_file_fallback and sync_back and keyring_store.backend_is_low_assurance():
        current_file_value = None
        if path.exists():
            current_file_value = SafeFS.from_roots([state_dir]).read_text(path).strip() or None
        if current_file_value != value:
            SafeFS.from_roots([state_dir]).write_text_atomic(path, f"{value}\n", permissions=0o600)
        return value
    if value:
        return value
    if allow_file_fallback and path.exists():
        value = SafeFS.from_roots([state_dir]).read_text(path).strip()
        if value:
            return value
    return None


def get_or_create_integrity_key(state_dir: Path, *, allow_file_fallback: bool = False) -> str:
    path = integrity_key_path(state_dir)
    existing = load_existing_integrity_key(state_dir, allow_file_fallback=allow_file_fallback)
    if existing:
        return existing
    key_name = integrity_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    keyring_store = KeyringCredentialStore()
    key = secrets.token_hex(32)
    persist_fallback = False
    try:
        keyring_store.set(key_name, key)
        persist_fallback = allow_file_fallback and keyring_store.backend_is_low_assurance()
        if not persist_fallback:
            return key
    except KeyringError:
        if not allow_file_fallback:
            raise IntegrityKeyError(
                "No keyring available for manifest integrity key; explicit file fallback is required."
            ) from None
        persist_fallback = True
    if persist_fallback:
        ensure_private_state_dir(state_dir)
        SafeFS.from_roots([state_dir]).write_text_atomic(path, f"{key}\n", permissions=0o600)
    return key


def clear_integrity_key(state_dir: Path, *, allow_file_fallback: bool = False) -> None:
    path = integrity_key_path(state_dir)
    key_name = integrity_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    keyring_store = KeyringCredentialStore()
    with contextlib.suppress(KeyringError):
        keyring_store.delete(key_name)
    if not allow_file_fallback:
        return
    fs = SafeFS.from_roots([state_dir])
    if path.exists():
        fs.delete_file(path)


def canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_payload_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def sign_payload(payload: dict[str, object], secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), canonical_json(payload), hashlib.sha256).hexdigest()


def verify_signature(payload: dict[str, object], secret: str, signature: str) -> bool:
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(expected, signature)
