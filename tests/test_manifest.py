from __future__ import annotations

import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from eve_client.integrity import HMAC_ALGORITHM
from eve_client.manifest import ManifestIntegrityError, load_manifest, manifest_path, write_manifest
from eve_client.models import ManifestRecord
from eve_client.state_binding import get_or_create_installation_id, store_sequence_watermark
from keyring.errors import KeyringError


def _record() -> ManifestRecord:
    return ManifestRecord(
        transaction_id="txn-1",
        tool="claude-code",
        action_id="action-1",
        action_type="write_config",
        path="/tmp/.claude/settings.json",
        backup_path="/tmp/.eve-state/backups/txn-1/action-1.bak",
        sha256="abc",
        backup_sha256="def",
        scope="global-config",
        environment="production",
    )


@contextmanager
def patched_keyring():
    state: dict[str, str] = {}

    def get_password(_service: str, key_name: str) -> str | None:
        return state.get(key_name)

    def set_password(_service: str, key_name: str, secret: str) -> None:
        state[key_name] = secret

    with ExitStack() as stack:
        stack.enter_context(
            patch("eve_client.auth.keyring_store.keyring.get_password", side_effect=get_password)
        )
        stack.enter_context(
            patch("eve_client.auth.keyring_store.keyring.set_password", side_effect=set_password)
        )
        yield


def test_manifest_write_creates_signed_envelope(tmp_path: Path) -> None:
    write_manifest(tmp_path, [_record()])
    payload = json.loads(manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["payload"]["version"] == 2
    assert payload["payload"]["hmac_algorithm"] == HMAC_ALGORITHM
    assert payload["payload"]["installation_id"]
    assert payload["payload"]["sequence"] == 1
    assert payload["signature"]
    records = load_manifest(tmp_path)
    assert len(records) == 1
    assert records[0].transaction_id == "txn-1"


def test_manifest_tamper_is_detected(tmp_path: Path) -> None:
    write_manifest(tmp_path, [_record()])
    path = manifest_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["payload"]["records"][0]["sha256"] = "tampered"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ManifestIntegrityError):
        load_manifest(tmp_path)


def test_manifest_rejects_installation_identity_mismatch(tmp_path: Path) -> None:
    write_manifest(tmp_path, [_record()])
    path = manifest_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["payload"]["installation_id"] = "other-installation"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ManifestIntegrityError):
        load_manifest(tmp_path)


def test_manifest_sequence_increments(tmp_path: Path) -> None:
    write_manifest(tmp_path, [_record()])
    write_manifest(tmp_path, [_record(), _record()])
    payload = json.loads(manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["payload"]["sequence"] == 2
    assert payload["payload"]["prev_digest"]


def test_manifest_uses_keyring_for_integrity_key(tmp_path: Path) -> None:
    with patched_keyring():
        write_manifest(tmp_path, [_record()])
        records = load_manifest(tmp_path)
    assert len(records) == 1
    assert not (tmp_path / "integrity.key").exists()


def test_installation_identity_is_stable(tmp_path: Path) -> None:
    first = get_or_create_installation_id(tmp_path, allow_file_fallback=True)
    second = get_or_create_installation_id(tmp_path, allow_file_fallback=True)
    assert first == second


def test_manifest_requires_explicit_file_fallback_when_no_keyring(tmp_path: Path) -> None:
    with (
        patch(
            "eve_client.auth.keyring_store.keyring.get_password",
            side_effect=KeyringError("no keyring"),
        ),
        patch(
            "eve_client.auth.keyring_store.keyring.set_password",
            side_effect=KeyringError("no keyring"),
        ),
    ):
        with pytest.raises(ManifestIntegrityError):
            write_manifest(tmp_path, [_record()])


def test_manifest_file_fallback_respects_private_permissions(tmp_path: Path) -> None:
    with (
        patch(
            "eve_client.auth.keyring_store.keyring.get_password",
            side_effect=KeyringError("no keyring"),
        ),
        patch(
            "eve_client.auth.keyring_store.keyring.set_password",
            side_effect=KeyringError("no keyring"),
        ),
    ):
        write_manifest(tmp_path, [_record()], allow_file_fallback=True)
    key_path = tmp_path / "integrity.key"
    assert key_path.exists()
    assert oct(os.stat(key_path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(tmp_path).st_mode & 0o777) == "0o700"


def test_manifest_detects_missing_manifest_when_sequence_watermark_exists(tmp_path: Path) -> None:
    store_sequence_watermark(tmp_path, 3, allow_file_fallback=True)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(tmp_path, allow_file_fallback=True)


def test_manifest_detects_sequence_replay_against_watermark(tmp_path: Path) -> None:
    write_manifest(tmp_path, [_record()], allow_file_fallback=True)
    store_sequence_watermark(tmp_path, 5, allow_file_fallback=True)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(tmp_path, allow_file_fallback=True)


def test_manifest_load_fails_closed_when_keyring_watermark_cannot_be_loaded(tmp_path: Path) -> None:
    write_manifest(tmp_path, [_record()], allow_file_fallback=True)
    with patch(
        "eve_client.auth.keyring_store.keyring.get_password", side_effect=KeyringError("no keyring")
    ):
        with pytest.raises(ManifestIntegrityError):
            load_manifest(tmp_path)
