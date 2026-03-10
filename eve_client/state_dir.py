"""Helpers for installer-owned local state directories."""

from __future__ import annotations

import os
import stat
from pathlib import Path


STATE_DIR_MODE = 0o700


class StateDirSecurityError(RuntimeError):
    """Raised when installer-owned state storage is not trusted enough to use."""


def _validate_directory(path: Path, *, require_private: bool) -> None:
    stats = path.lstat()
    if not stat.S_ISDIR(stats.st_mode):
        raise StateDirSecurityError(f"State directory path is not a directory: {path}")
    if path.is_symlink():
        raise StateDirSecurityError(f"Refusing to use symlinked state directory path: {path}")
    if require_private:
        if stats.st_mode & 0o077:
            raise StateDirSecurityError(f"State directory must not be group/world accessible: {path}")
    elif stats.st_mode & 0o022:
        raise StateDirSecurityError(f"State directory parent must not be group/world writable: {path}")


def _validate_state_dir_chain(path: Path) -> None:
    current = path
    anchor, _ = _state_dir_anchor(path)
    while True:
        if current.exists():
            _validate_directory(current, require_private=current == path)
        if current == anchor or current.parent == current:
            break
        current = current.parent


def _state_dir_anchor(path: Path) -> tuple[Path, bool]:
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        candidate = Path(xdg_state_home).expanduser().resolve()
        try:
            path.relative_to(candidate)
        except ValueError:
            pass
        else:
            return candidate, True
    return Path.home().resolve(), False


def ensure_private_state_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    anchor, repair_anchor_permissions = _state_dir_anchor(path)
    if repair_anchor_permissions:
        current = path
        while True:
            try:
                os.chmod(current, STATE_DIR_MODE)
            except OSError:
                pass
            if current == anchor or current.parent == current:
                break
            current = current.parent
    try:
        os.chmod(path, STATE_DIR_MODE)
    except OSError:
        pass
    _validate_state_dir_chain(path)
    return path
