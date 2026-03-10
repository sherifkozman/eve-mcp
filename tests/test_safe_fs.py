from __future__ import annotations

import os
from pathlib import Path

import pytest

from eve_client.safe_fs import SafeFS


def test_safe_fs_read_text_reads_regular_file(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"ok": true}\n', encoding="utf-8")
    fs = SafeFS.from_roots([tmp_path])
    assert fs.read_text(target) == '{"ok": true}\n'


def test_safe_fs_read_text_rejects_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    target = tmp_path / "config.json"
    target.symlink_to(outside)
    fs = SafeFS.from_roots([tmp_path])
    with pytest.raises(OSError):
        fs.read_text(target)


def test_safe_fs_delete_file_removes_regular_file(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("{}", encoding="utf-8")
    fs = SafeFS.from_roots([tmp_path])
    fs.delete_file(target)
    assert not target.exists()


def test_safe_fs_delete_file_rejects_fifo(tmp_path: Path) -> None:
    target = tmp_path / "config.pipe"
    os.mkfifo(target)
    fs = SafeFS.from_roots([tmp_path])
    with pytest.raises(OSError):
        fs.delete_file(target)
