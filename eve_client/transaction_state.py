"""Persistent transaction state for crash-aware apply and rollback."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import time

from eve_client.safe_fs import SafeFS
from eve_client.state_dir import ensure_private_state_dir

STATE_FILE = "transaction-state.json"


def transaction_state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILE


def load_transaction_state(state_dir: Path) -> dict[str, object] | None:
    path = transaction_state_path(state_dir)
    if not path.exists():
        return None
    try:
        ensure_private_state_dir(state_dir)
        return json.loads(SafeFS.from_roots([state_dir]).read_text(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _timestamp() -> int:
    return int(time.time())


def write_transaction_state(state_dir: Path, payload: dict[str, object]) -> None:
    ensure_private_state_dir(state_dir)
    existing = load_transaction_state(state_dir) or {}
    started_at = existing.get("started_at") if isinstance(existing.get("started_at"), int) else _timestamp()
    enriched = dict(payload)
    enriched.setdefault("pid", os.getpid())
    enriched.setdefault("hostname", socket.gethostname())
    enriched["started_at"] = started_at
    enriched["updated_at"] = _timestamp()
    SafeFS.from_roots([state_dir]).write_text_atomic(
        transaction_state_path(state_dir),
        json.dumps(enriched, indent=2) + "\n",
        permissions=0o600,
    )


def clear_transaction_state(state_dir: Path) -> None:
    path = transaction_state_path(state_dir)
    if path.exists():
        SafeFS.from_roots([state_dir]).delete_file(path)
