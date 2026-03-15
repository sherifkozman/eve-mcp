"""Tests for state_binding.py — file-only (no keyring)."""

from __future__ import annotations

from pathlib import Path

import pytest

from eve_client.state_binding import (
    StateBindingError,
    clear_installation_id,
    clear_sequence_watermark,
    get_or_create_installation_id,
    load_existing_installation_id,
    store_sequence_watermark,
    verify_sequence_watermark,
)


def test_get_or_create_installation_id_creates_on_first_call(tmp_path: Path) -> None:
    installation_id = get_or_create_installation_id(tmp_path, allow_file_fallback=True)
    assert installation_id
    assert len(installation_id) == 24


def test_get_or_create_installation_id_is_idempotent(tmp_path: Path) -> None:
    first = get_or_create_installation_id(tmp_path, allow_file_fallback=True)
    second = get_or_create_installation_id(tmp_path, allow_file_fallback=True)
    assert first == second


def test_load_existing_installation_id_returns_none_when_empty(tmp_path: Path) -> None:
    result = load_existing_installation_id(tmp_path, allow_file_fallback=True)
    assert result is None


def test_store_and_load_sequence_watermark(tmp_path: Path) -> None:
    store_sequence_watermark(tmp_path, 42, allow_file_fallback=True)
    from eve_client.state_binding import load_sequence_watermark

    value = load_sequence_watermark(tmp_path, allow_file_fallback=True)
    assert value == 42


def test_clear_sequence_watermark(tmp_path: Path) -> None:
    store_sequence_watermark(tmp_path, 7, allow_file_fallback=True)
    clear_sequence_watermark(tmp_path, allow_file_fallback=True)
    from eve_client.state_binding import load_sequence_watermark

    value = load_sequence_watermark(tmp_path, allow_file_fallback=True)
    assert value == 0


def test_clear_installation_id(tmp_path: Path) -> None:
    get_or_create_installation_id(tmp_path, allow_file_fallback=True)
    clear_installation_id(tmp_path, allow_file_fallback=True)
    result = load_existing_installation_id(tmp_path, allow_file_fallback=True)
    assert result is None


def test_verify_sequence_watermark_accepts_higher(tmp_path: Path) -> None:
    store_sequence_watermark(tmp_path, 10, allow_file_fallback=True)
    # sequence >= watermark should not raise
    verify_sequence_watermark(
        tmp_path,
        manifest_exists=True,
        sequence=10,
        allow_file_fallback=True,
    )
    verify_sequence_watermark(
        tmp_path,
        manifest_exists=True,
        sequence=20,
        allow_file_fallback=True,
    )


def test_verify_sequence_watermark_rejects_replay(tmp_path: Path) -> None:
    store_sequence_watermark(tmp_path, 10, allow_file_fallback=True)
    with pytest.raises(StateBindingError, match="replay"):
        verify_sequence_watermark(
            tmp_path,
            manifest_exists=True,
            sequence=5,
            allow_file_fallback=True,
        )


def test_no_keyring_import_at_module_level() -> None:
    import importlib
    import sys

    # Remove cached module to force re-import check on source
    mod_name = "eve_client.state_binding"
    sys.modules.pop(mod_name, None)
    import ast
    import inspect

    import eve_client.state_binding as sb

    source = inspect.getsource(sb)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "keyring" and not (node.module or "").startswith("keyring"), (
                    f"keyring import found at line {node.lineno}: {ast.dump(node)}"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "keyring" and not alias.name.startswith("keyring"), (
                        f"keyring import found at line {node.lineno}"
                    )
