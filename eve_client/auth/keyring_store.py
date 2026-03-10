"""Keyring-backed credential storage."""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE_NAME = "eve-client"
WEAK_BACKEND_MARKERS = ("fail", "plaintext", "chainer", "null")


class KeyringCredentialStore:
    def get(self, key_name: str) -> str | None:
        return keyring.get_password(SERVICE_NAME, key_name)

    def set(self, key_name: str, secret: str) -> None:
        keyring.set_password(SERVICE_NAME, key_name, secret)

    def delete(self, key_name: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, key_name)
        except PasswordDeleteError:
            return

    def backend_name(self) -> str:
        backend = keyring.get_keyring()
        return f"{backend.__class__.__module__}.{backend.__class__.__name__}"

    def backend_is_low_assurance(self) -> bool:
        name = self.backend_name().lower()
        return any(marker in name for marker in WEAK_BACKEND_MARKERS)


__all__ = ["KeyringCredentialStore", "KeyringError"]
