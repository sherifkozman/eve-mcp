"""Manifest integrity helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from keyring.errors import KeyringError

from eve_client.auth.keyring_store import KeyringCredentialStore
from eve_client.safe_fs import SafeFS
from eve_client.state_binding import get_or_create_installation_id
from eve_client.state_dir import ensure_private_state_dir

INTEGRITY_KEY_FILE = "integrity.key"
INTEGRITY_KEY_NAME_PREFIX = "installer-integrity-key"
HMAC_ALGORITHM = "HMAC-SHA256"


class IntegrityKeyError(RuntimeError):
    """Raised when the manifest integrity key cannot be loaded safely."""


def integrity_key_path(state_dir: Path) -> Path:
    return state_dir / INTEGRITY_KEY_FILE


def integrity_key_name(state_dir: Path, *, allow_file_fallback: bool) -> str:
    installation_id = get_or_create_installation_id(state_dir, allow_file_fallback=allow_file_fallback)
    return f"{INTEGRITY_KEY_NAME_PREFIX}:{installation_id}"


def get_or_create_integrity_key(state_dir: Path, *, allow_file_fallback: bool = False) -> str:
    path = integrity_key_path(state_dir)
    key_name = integrity_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    keyring_store = KeyringCredentialStore()
    try:
        existing = keyring_store.get(key_name)
        if existing:
            return existing
        key = secrets.token_hex(32)
        keyring_store.set(key_name, key)
        return key
    except KeyringError:
        if not allow_file_fallback:
            raise IntegrityKeyError(
                "No keyring available for manifest integrity key; explicit file fallback is required."
            )
        ensure_private_state_dir(state_dir)
        fs = SafeFS.from_roots([state_dir])
        if path.exists():
            return fs.read_text(path).strip()
        key = secrets.token_hex(32)
        fs.write_text_atomic(path, f"{key}\n", permissions=0o600)
        return key


def clear_integrity_key(state_dir: Path, *, allow_file_fallback: bool = False) -> None:
    path = integrity_key_path(state_dir)
    key_name = integrity_key_name(state_dir, allow_file_fallback=allow_file_fallback)
    keyring_store = KeyringCredentialStore()
    try:
        keyring_store.delete(key_name)
    except KeyringError:
        pass
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
