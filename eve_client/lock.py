"""Single-writer locking for installer-owned state."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import time

from eve_client.safe_fs import SafeFS
from eve_client.state_dir import ensure_private_state_dir

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


LOCK_FILE = "installer.lock"


class InstallerLockError(RuntimeError):
    """Raised when another installer transaction is already in progress."""


class InstallerLockUnsupportedPlatformError(InstallerLockError):
    """Raised when installer locking is not implemented on the current platform."""


def lock_path(state_dir: Path) -> Path:
    return state_dir / LOCK_FILE


def read_lock_metadata(state_dir: Path) -> dict[str, object] | None:
    ensure_private_state_dir(state_dir)
    path = lock_path(state_dir)
    if not path.exists():
        return None
    try:
        raw = SafeFS.from_roots([state_dir]).read_text(path, encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def installer_lock_is_held(state_dir: Path) -> bool:
    ensure_private_state_dir(state_dir)
    path = lock_path(state_dir)
    if not path.exists():
        return False
    handle = open(path, "r+", encoding="utf-8")
    try:
        if fcntl is None:  # pragma: no cover
            raise InstallerLockUnsupportedPlatformError(
                "Eve installer locking is not implemented on this platform."
            )
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


@contextmanager
def installer_lock(state_dir: Path):
    ensure_private_state_dir(state_dir)
    path = lock_path(state_dir)
    if not path.exists():
        SafeFS.from_roots([state_dir]).write_text_atomic(path, "", permissions=0o600)
    handle = open(path, "r+", encoding="utf-8")
    try:
        if fcntl is None:  # pragma: no cover
            raise InstallerLockUnsupportedPlatformError(
                "Eve installer locking is not implemented on this platform."
            )
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InstallerLockError("Another Eve installer operation is already running.") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "started_at": int(time.time()),
                }
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        try:
            if fcntl is not None:
                handle.seek(0)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()
