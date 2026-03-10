"""Constrained filesystem access for the Eve client installer."""

from __future__ import annotations

import contextlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from eve_client.atomic import atomic_write
from eve_client.path_policy import (
    PathPolicy,
    ensure_existing_file_is_safe,
    ensure_no_symlink_components,
    ensure_path_is_safe,
)


@dataclass(slots=True)
class SafeFS:
    policy: PathPolicy

    @classmethod
    def from_roots(cls, roots: list[Path] | tuple[Path, ...]) -> SafeFS:
        return cls(policy=PathPolicy.from_roots(roots))

    def ensure_safe(self, path: Path) -> Path:
        return ensure_path_is_safe(path, self.policy)

    def _open_parent_dir_fd(self, target: Path) -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(target.parent, flags)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        target = self.ensure_safe(path)
        ensure_no_symlink_components(target)
        ensure_existing_file_is_safe(target)
        dir_fd = self._open_parent_dir_fd(target)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(target.name, flags, dir_fd=dir_fd)
        try:
            stats = os.fstat(fd)
            if not stat.S_ISREG(stats.st_mode):
                raise OSError(f"Refusing to operate on non-regular file: {target}")
            if stats.st_nlink > 1:
                raise OSError(f"Refusing to operate on multiply-linked file: {target}")
            with os.fdopen(fd, "r", encoding=encoding) as handle:
                return handle.read()
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        finally:
            os.close(dir_fd)

    def write_text_atomic(
        self,
        path: Path,
        content: str,
        *,
        permissions: int = 0o600,
    ) -> Path:
        target = self.ensure_safe(path)
        atomic_write(
            target, content, permissions=permissions, allowed_roots=list(self.policy.allowed_roots)
        )
        return target

    def delete_file(self, path: Path) -> None:
        target = self.ensure_safe(path)
        if not target.exists():
            return
        ensure_no_symlink_components(target)
        ensure_existing_file_is_safe(target)
        dir_fd = self._open_parent_dir_fd(target)
        try:
            stats = os.stat(target.name, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_ISREG(stats.st_mode):
                raise OSError(f"Refusing to operate on non-regular file: {target}")
            if stats.st_nlink > 1:
                raise OSError(f"Refusing to operate on multiply-linked file: {target}")
            os.unlink(target.name, dir_fd=dir_fd)
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
