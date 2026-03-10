"""Recovery helpers for installer trust state."""

from __future__ import annotations

import shutil
from pathlib import Path

from eve_client.integrity import clear_integrity_key
from eve_client.lock import installer_lock
from eve_client.manifest import manifest_path
from eve_client.safe_fs import SafeFS
from eve_client.state_binding import clear_installation_id, clear_sequence_watermark
from eve_client.transaction_state import transaction_state_path


def reinitialize_trust_state(state_dir: Path, *, allow_file_fallback: bool) -> None:
    with installer_lock(state_dir):
        fs = SafeFS.from_roots([state_dir])
        for path in (manifest_path(state_dir), transaction_state_path(state_dir)):
            if path.exists():
                fs.delete_file(path)
        backups = state_dir / "backups"
        if backups.exists():
            shutil.rmtree(backups)
        clear_sequence_watermark(state_dir, allow_file_fallback=allow_file_fallback)
        clear_integrity_key(state_dir, allow_file_fallback=allow_file_fallback)
        clear_installation_id(state_dir, allow_file_fallback=allow_file_fallback)
