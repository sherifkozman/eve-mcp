from __future__ import annotations

import os
import time
from multiprocessing import Event, Process, Queue
from pathlib import Path

import pytest
from eve_client.lock import (
    InstallerLockError,
    InstallerLockUnsupportedPlatformError,
    installer_lock,
    installer_lock_is_held,
    read_lock_metadata,
)


def _hold_lock(state_dir: str, ready: Queue, release: Event) -> None:
    path = Path(state_dir)
    with installer_lock(path):
        ready.put("locked")
        release.wait(5)


def _crash_after_lock(state_dir: str) -> None:
    path = Path(state_dir)
    with installer_lock(path):
        (path / "crash-sentinel").write_text("locked", encoding="utf-8")
        os._exit(0)


def test_installer_lock_rejects_concurrent_writer(tmp_path: Path) -> None:
    ready: Queue[str] = Queue()
    release = Event()
    process = Process(target=_hold_lock, args=(str(tmp_path / ".eve-state"), ready, release))
    process.start()
    try:
        assert ready.get(timeout=5) == "locked"
        time.sleep(0.1)
        assert installer_lock_is_held(tmp_path / ".eve-state") is True
        with pytest.raises(InstallerLockError):
            with installer_lock(tmp_path / ".eve-state"):
                pass
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def test_installer_lock_recovers_after_process_exit(tmp_path: Path) -> None:
    state_dir = tmp_path / ".eve-state"
    process = Process(target=_crash_after_lock, args=(str(state_dir),))
    process.start()
    deadline = time.time() + 5
    while not (state_dir / "crash-sentinel").exists():
        if time.time() > deadline:
            raise AssertionError("Timed out waiting for crash sentinel")
        time.sleep(0.05)
    process.join(timeout=5)
    assert process.exitcode == 0
    assert installer_lock_is_held(state_dir) is False
    with installer_lock(state_dir):
        pass


def test_installer_lock_writes_metadata(tmp_path: Path) -> None:
    state_dir = tmp_path / ".eve-state"
    with installer_lock(state_dir):
        payload = read_lock_metadata(state_dir)
        assert payload is not None
        assert isinstance(payload.get("pid"), int)
        assert isinstance(payload.get("hostname"), str)
        assert isinstance(payload.get("started_at"), int)


def test_installer_lock_fails_closed_without_fcntl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("eve_client.lock.fcntl", None)
    with pytest.raises(InstallerLockUnsupportedPlatformError):
        with installer_lock(tmp_path / ".eve-state"):
            pass
    with pytest.raises(InstallerLockUnsupportedPlatformError):
        installer_lock_is_held(tmp_path / ".eve-state")
