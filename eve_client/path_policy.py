"""Filesystem policy helpers for client-owned writes."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PathPolicy:
    allowed_roots: tuple[Path, ...]

    @classmethod
    def from_roots(cls, roots: list[Path] | tuple[Path, ...]) -> PathPolicy:
        return cls(allowed_roots=tuple(Path(root).expanduser() for root in roots))


def safe_real_path(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _existing_path_chain(path: Path) -> list[Path]:
    absolute = path.expanduser()
    if not absolute.is_absolute():
        absolute = absolute.resolve(strict=False)
    existing: list[Path] = []
    current = absolute
    while True:
        if current.exists():
            existing.append(current)
        if current.parent == current:
            break
        current = current.parent
    return existing


def _normalize_path(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve(strict=False)
    return candidate


def ensure_no_symlink_components(path: Path) -> None:
    for candidate in _existing_path_chain(path):
        if candidate.is_symlink():
            raise OSError(f"Refusing to operate through symlinked path component: {candidate}")


def ensure_existing_file_is_safe(path: Path) -> None:
    if not path.exists():
        return
    stats = path.lstat()
    if not stat.S_ISREG(stats.st_mode):
        raise OSError(f"Refusing to operate on non-regular file: {path}")
    if stats.st_nlink > 1:
        raise OSError(f"Refusing to operate on multiply-linked file: {path}")


def ensure_path_is_safe(path: Path, policy: PathPolicy) -> Path:
    target = _normalize_path(path)

    ensure_no_symlink_components(target)
    resolved_roots = tuple(_normalize_path(root) for root in policy.allowed_roots)
    for root in resolved_roots:
        ensure_no_symlink_components(root)
    real_path = safe_real_path(target)
    if not any(
        real_path == safe_real_path(root) or safe_real_path(root) in real_path.parents
        for root in resolved_roots
    ):
        raise OSError(f"Refusing to write outside allowed roots: {target}")
    ensure_existing_file_is_safe(target)
    return target
