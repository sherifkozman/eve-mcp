"""Backup and validation helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timezone
from pathlib import Path

from eve_client.safe_fs import SafeFS
from eve_client.state_dir import ensure_private_state_dir

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(8192):
            digest.update(chunk)
    return digest.hexdigest()


def backup_dir(state_dir: Path, transaction_id: str) -> Path:
    return state_dir / "backups" / transaction_id


def create_backup(
    path: Path,
    *,
    state_dir: Path,
    transaction_id: str,
    action_id: str,
) -> tuple[Path | None, str | None]:
    if not path.exists():
        return None, None
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target_dir = backup_dir(state_dir, transaction_id)
    ensure_private_state_dir(state_dir)
    ensure_private_state_dir(target_dir)
    backup_path = target_dir / f"{action_id}-{stamp}.bak"
    content = SafeFS.from_roots([path.parent]).read_text(path, encoding="utf-8")
    SafeFS.from_roots([state_dir]).write_text_atomic(backup_path, content, permissions=0o600)
    return backup_path, sha256_file(backup_path)


def restore_backup(backup_path: Path, target_path: Path, *, allowed_roots: list[Path]) -> None:
    backup_fs = SafeFS.from_roots([backup_path.parent])
    target_fs = SafeFS.from_roots(allowed_roots)
    content = backup_fs.read_text(backup_path, encoding="utf-8")
    target_fs.write_text_atomic(target_path, content, permissions=0o600)


def validate_config(path: Path, fmt: str) -> bool:
    try:
        if fmt == "json":
            json.loads(path.read_text(encoding="utf-8"))
            return True
        if fmt == "toml":
            with open(path, "rb") as handle:
                tomllib.load(handle)
            return True
        return True
    except Exception:
        return False
