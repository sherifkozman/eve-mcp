"""Uninstall Eve-managed tool integrations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from eve_client.auth import CredentialStore
from eve_client.backup import create_backup, restore_backup, sha256_file, validate_config
from eve_client.config import ResolvedConfig
from eve_client.lock import installer_lock
from eve_client.manifest import load_manifest, write_manifest
from eve_client.merge import (
    eve_json_entry_has_unknown_fields,
    eve_toml_entry_has_unknown_fields,
    is_eve_companion_file,
    remove_companion_file,
    remove_claude_hooks_json_config,
    remove_gemini_hooks_json_config,
    remove_json_config,
    remove_toml_config,
)
from eve_client.models import ManifestRecord, ToolName, UninstallResult
from eve_client.safe_fs import SafeFS
from eve_client.transaction_state import clear_transaction_state, write_transaction_state


class UninstallError(RuntimeError):
    """Raised when uninstall cannot complete safely."""

    def __init__(self, message: str, *, remaining_paths: list[Path] | None = None) -> None:
        super().__init__(message)
        self.remaining_paths = remaining_paths or []


@dataclass(slots=True)
class _ModifiedFile:
    path: Path
    backup_path: Path | None
    backup_sha256: str | None
    scope: str


def _allowed_roots(record: ManifestRecord, config: ResolvedConfig) -> list[Path]:
    path = Path(record.path)
    if record.scope == "state":
        return [config.state_dir]
    if record.scope == "project":
        return [config.project_root]
    return [path.parent]


def _blocked_uninstall(message: str, path: Path) -> UninstallError:
    return UninstallError(message, remaining_paths=[path])


def _render_uninstall_content(record: ManifestRecord) -> str | None:
    path = Path(record.path)
    if record.action_type == "write_config":
        if not path.exists():
            return None
        if path.suffix == ".toml":
            if eve_toml_entry_has_unknown_fields(path):
                raise _blocked_uninstall(f"Refusing to remove user-modified Eve TOML block: {path}", path)
            return remove_toml_config(path)
        if eve_json_entry_has_unknown_fields(path, record.tool):
            raise _blocked_uninstall(f"Refusing to remove user-modified Eve config entry: {path}", path)
        return remove_json_config(path)
    if record.action_type == "write_hooks_config":
        if not path.exists():
            return None
        if eve_json_entry_has_unknown_fields(path, record.tool):
            raise _blocked_uninstall(f"Refusing to remove user-modified Eve hook entry: {path}", path)
        if record.tool == "claude-code":
            return remove_claude_hooks_json_config(path)
        if record.tool == "gemini-cli":
            return remove_gemini_hooks_json_config(path)
        raise UninstallError(f"Unsupported hook uninstall for {record.tool}")
    if record.action_type == "create_companion_file":
        if not path.exists():
            return None
        if not is_eve_companion_file(path, record.tool):
            raise _blocked_uninstall(f"Refusing to remove non-Eve companion file: {path}", path)
        return remove_companion_file(path, record.tool)
    return None


def uninstall_tools(
    *,
    config: ResolvedConfig,
    credential_store: CredentialStore,
    tools: list[ToolName],
) -> UninstallResult:
    transaction_id = str(uuid4())
    with installer_lock(config.state_dir):
        write_transaction_state(
            config.state_dir,
            {"transaction_id": transaction_id, "phase": "uninstall", "tools": tools},
        )
        records = load_manifest(config.state_dir, allow_file_fallback=config.allow_file_secret_fallback)
        target = [record for record in records if record.tool in tools]
        modified: list[_ModifiedFile] = []
        try:
            for record in target:
                path = Path(record.path)
                content = _render_uninstall_content(record)
                if record.action_type in {"write_config", "write_hooks_config"}:
                    if not path.exists():
                        continue
                    backup_path, backup_sha = create_backup(
                        path,
                        state_dir=config.state_dir,
                        transaction_id=transaction_id,
                        action_id=record.action_id,
                    )
                    modified.append(
                        _ModifiedFile(
                            path=path,
                            backup_path=backup_path,
                            backup_sha256=backup_sha,
                            scope=record.scope,
                        )
                    )
                    SafeFS.from_roots(_allowed_roots(record, config)).write_text_atomic(path, content or "", permissions=0o600)
                    fmt = "toml" if path.suffix == ".toml" else "json"
                    if not validate_config(path, fmt):
                        raise UninstallError(f"Rendered uninstall config failed validation: {path}")
                elif record.action_type == "create_companion_file" and path.exists():
                    backup_path, backup_sha = create_backup(
                        path,
                        state_dir=config.state_dir,
                        transaction_id=transaction_id,
                        action_id=record.action_id,
                    )
                    modified.append(
                        _ModifiedFile(
                            path=path,
                            backup_path=backup_path,
                            backup_sha256=backup_sha,
                            scope=record.scope,
                        )
                    )
                    updated = _render_uninstall_content(record)
                    if updated:
                        SafeFS.from_roots(_allowed_roots(record, config)).write_text_atomic(path, updated, permissions=0o600)
                    else:
                        SafeFS.from_roots(_allowed_roots(record, config)).delete_file(path)
            for tool in tools:
                credential_store.delete_api_key(tool)
            remaining = [record for record in records if record.tool not in tools]
            write_manifest(config.state_dir, remaining, allow_file_fallback=config.allow_file_secret_fallback)
            clear_transaction_state(config.state_dir)
            return UninstallResult(
                transaction_id=transaction_id,
                removed_actions=len(target),
                removed_tools=tools,
            )
        except Exception:
            for tool in tools:
                credential_store.delete_api_key(tool)
            for item in reversed(modified):
                if item.backup_path and item.backup_path.exists():
                    restore_backup(item.backup_path, item.path, allowed_roots=[config.project_root] if item.scope == "project" else [item.path.parent])
            raise
