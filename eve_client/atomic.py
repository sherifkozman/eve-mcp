"""Atomic write helpers for Eve client installer."""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
from pathlib import Path

from eve_client.path_policy import PathPolicy, ensure_path_is_safe


def _validate_existing_target(dir_fd: int, target_name: str, target_path: Path) -> None:
    try:
        stats = os.stat(target_name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(stats.st_mode):
        raise OSError(f"Refusing to operate on non-regular file: {target_path}")
    if stats.st_nlink > 1:
        raise OSError(f"Refusing to operate on multiply-linked file: {target_path}")


def atomic_write(
    path: Path,
    content: str,
    permissions: int = 0o600,
    allowed_roots: list[Path] | None = None,
) -> None:
    policy = PathPolicy.from_roots(allowed_roots or [path.parent])
    target = ensure_path_is_safe(path, policy)
    target.parent.mkdir(parents=True, exist_ok=True)
    dir_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        dir_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        dir_flags |= os.O_NOFOLLOW
    dir_fd = os.open(target.parent, dir_flags)
    _validate_existing_target(dir_fd, target.name, target)
    tmp_name = f".{target.name}.{secrets.token_hex(8)}.tmp"
    file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        file_flags |= os.O_NOFOLLOW
    fd = os.open(tmp_name, file_flags, permissions, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name, dir_fd=dir_fd)
        raise
    finally:
        os.close(dir_fd)
