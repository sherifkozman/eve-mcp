from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from keyring.errors import KeyringError

from eve_client.auth.base import CredentialStoreUnavailableError
from eve_client.auth.local_store import LocalCredentialStore


def test_auth_store_uses_keyring(tmp_path: Path) -> None:
    store = LocalCredentialStore(tmp_path)
    with (
        patch("eve_client.auth.keyring_store.keyring.set_password") as set_password,
        patch("eve_client.auth.keyring_store.keyring.get_password", return_value="eve-secret"),
    ):
        record = store.set_api_key("claude-code", "eve-secret")
        value, source = store.get_api_key("claude-code")
    set_password.assert_called_once()
    assert record.source == "keyring"
    assert value == "eve-secret"
    assert source == "keyring"


def test_auth_store_falls_back_to_file(tmp_path: Path) -> None:
    store = LocalCredentialStore(tmp_path, allow_file_fallback=True)
    with patch("eve_client.auth.keyring_store.keyring.set_password", side_effect=KeyringError("no keyring")):
        record = store.set_api_key("gemini-cli", "eve-secret")
    assert record.source == "file-fallback"
    value, source = store.get_api_key("gemini-cli")
    assert value == "eve-secret"
    assert source == "file-fallback"
    path = tmp_path / "auth-fallback.json"
    assert path.exists()


def test_auth_store_can_disable_file_fallback(tmp_path: Path) -> None:
    store = LocalCredentialStore(tmp_path, allow_file_fallback=False)
    with patch("eve_client.auth.keyring_store.keyring.set_password", side_effect=KeyringError("no keyring")):
        with pytest.raises(CredentialStoreUnavailableError):
            store.set_api_key("gemini-cli", "eve-secret")
    assert not (tmp_path / "auth-fallback.json").exists()


def test_auth_store_without_fallback_raises_on_keyring_error(tmp_path: Path) -> None:
    store = LocalCredentialStore(tmp_path, allow_file_fallback=False)
    with patch("eve_client.auth.keyring_store.keyring.get_password", side_effect=KeyringError("no keyring")):
        with pytest.raises(CredentialStoreUnavailableError):
            store.get_api_key("claude-code")


def test_auth_store_ignores_existing_fallback_when_disabled(tmp_path: Path) -> None:
    store = LocalCredentialStore(tmp_path, allow_file_fallback=True)
    with patch("eve_client.auth.keyring_store.keyring.set_password", side_effect=KeyringError("no keyring")):
        store.set_api_key("claude-code", "eve-secret")

    disabled = LocalCredentialStore(tmp_path, allow_file_fallback=False)
    with patch("eve_client.auth.keyring_store.keyring.get_password", return_value=None):
        value, source = disabled.get_api_key("claude-code")
    assert value is None
    assert source is None


def test_auth_store_deletes_existing_fallback_even_when_disabled(tmp_path: Path) -> None:
    store = LocalCredentialStore(tmp_path, allow_file_fallback=True)
    with patch("eve_client.auth.keyring_store.keyring.set_password", side_effect=KeyringError("no keyring")):
        store.set_api_key("claude-code", "eve-secret")

    disabled = LocalCredentialStore(tmp_path, allow_file_fallback=False)
    with patch("eve_client.auth.keyring_store.keyring.delete_password", side_effect=KeyringError("no keyring")):
        disabled.delete_api_key("claude-code")
    assert not (tmp_path / "auth-fallback.json").exists() or (tmp_path / "auth-fallback.json").read_text().strip() in {"", "{}"}
