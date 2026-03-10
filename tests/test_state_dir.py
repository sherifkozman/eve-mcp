from __future__ import annotations

import os
from pathlib import Path

import pytest

from eve_client.state_dir import StateDirSecurityError, ensure_private_state_dir


def test_ensure_private_state_dir_sets_secure_mode(tmp_path: Path) -> None:
    target = tmp_path / ".eve-state"
    ensure_private_state_dir(target)
    assert target.exists()
    assert oct(os.stat(target).st_mode & 0o777) == "0o700"


def test_ensure_private_state_dir_rejects_world_writable_parent(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir()
    os.chmod(parent, 0o777)
    target = parent / ".eve-state"
    with pytest.raises(StateDirSecurityError):
        ensure_private_state_dir(target)


def test_ensure_private_state_dir_accepts_explicit_xdg_anchor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    xdg_state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state_home))
    target = xdg_state_home / "eve"
    ensure_private_state_dir(target)
    assert target.exists()
    assert oct(os.stat(xdg_state_home).st_mode & 0o777) == "0o700"
    assert oct(os.stat(target).st_mode & 0o777) == "0o700"
