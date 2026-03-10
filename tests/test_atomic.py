from __future__ import annotations

import os
from pathlib import Path

import pytest
from eve_client.atomic import atomic_write


def test_atomic_write_writes_content(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    atomic_write(target, '{"ok": true}\n', allowed_roots=[tmp_path])
    assert target.read_text(encoding="utf-8") == '{"ok": true}\n'
    assert oct(os.stat(target).st_mode & 0o777) == "0o600"


def test_atomic_write_refuses_unsafe_symlink(tmp_path: Path) -> None:
    link = tmp_path / "link.txt"
    link.symlink_to(Path("/etc/hosts"))
    with pytest.raises(OSError):
        atomic_write(link, "bad\n", allowed_roots=[tmp_path])


def test_atomic_write_refuses_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "linked-dir"
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        atomic_write(parent / "config.json", '{"ok": true}\n', allowed_roots=[tmp_path])


def test_atomic_write_refuses_multiply_linked_file(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("{}", encoding="utf-8")
    linked = tmp_path / "config-linked.json"
    linked.hardlink_to(target)
    with pytest.raises(OSError):
        atomic_write(target, '{"ok": true}\n', allowed_roots=[tmp_path])


def test_atomic_write_refuses_fifo(tmp_path: Path) -> None:
    target = tmp_path / "config.pipe"
    os.mkfifo(target)
    with pytest.raises(OSError):
        atomic_write(target, '{"ok": true}\n', allowed_roots=[tmp_path])
