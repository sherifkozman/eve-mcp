"""File-based credential storage fallback."""

from __future__ import annotations

import json
from pathlib import Path

from eve_client.safe_fs import SafeFS
from eve_client.state_dir import ensure_private_state_dir


class FileCredentialStore:
    def __init__(self, path: Path, state_dir: Path) -> None:
        self.path = path
        self.state_dir = state_dir

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            ensure_private_state_dir(self.state_dir)
            return json.loads(
                SafeFS.from_roots([self.state_dir]).read_text(self.path, encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {}

    def write(self, payload: dict[str, str]) -> None:
        ensure_private_state_dir(self.state_dir)
        SafeFS.from_roots([self.state_dir]).write_text_atomic(
            self.path,
            json.dumps(payload, indent=2) + "\n",
            permissions=0o600,
        )
