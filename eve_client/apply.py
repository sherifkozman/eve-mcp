"""Install plan execution with transaction-aware rollback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from eve_client.auth import CredentialStore
from eve_client.backup import create_backup, restore_backup, sha256_file, validate_config
from eve_client.config import ResolvedConfig
from eve_client.lock import installer_lock
from eve_client.manifest import load_manifest, write_manifest
from eve_client.models import (
    ApplyResult,
    InstallPlan,
    ManifestRecord,
    PlannedAction,
    RollbackResult,
)
from eve_client.operation_policy import OperationPolicyError, validate_action_policy
from eve_client.operations import OperationContext, OperationError, execute_operation
from eve_client.plan import feature_enabled_for_tool
from eve_client.safe_fs import SafeFS
from eve_client.transaction_state import clear_transaction_state, write_transaction_state


class ApplyPlanError(RuntimeError):
    """Raised when an install plan cannot be applied safely."""


class RollbackConflictError(ApplyPlanError):
    """Raised when rollback would overwrite newer local changes."""

    def __init__(self, message: str, *, conflicted_paths: list[str] | None = None) -> None:
        super().__init__(message)
        self.conflicted_paths = conflicted_paths or []


@dataclass(slots=True)
class AppliedWrite:
    action: PlannedAction
    path: Path
    backup_path: Path | None
    backup_sha256: str | None
    created_new_file: bool


def _allowed_roots_for_action(action: PlannedAction, config: ResolvedConfig) -> list[Path]:
    if action.scope == "state":
        return [config.state_dir]
    if action.scope == "global-config" and action.path:
        return [action.path.parent]
    if action.scope == "project" and action.path:
        return [config.project_root]
    return [config.state_dir]


def _apply_action(
    action: PlannedAction,
    config: ResolvedConfig,
    credential_store: CredentialStore,
    secret: str | None,
    auth_mode: str | None,
    transaction_id: str,
) -> AppliedWrite | None:
    try:
        validate_action_policy(action, config)
    except OperationPolicyError as exc:
        raise ApplyPlanError(str(exc)) from exc
    try:
        rendered = execute_operation(
            OperationContext(
                config=config,
                credentials=credential_store,
                action=action,
                secret=secret,
                auth_mode=auth_mode,
            )
        )
    except OperationError as exc:
        raise ApplyPlanError(str(exc)) from exc
    if rendered.content is None:
        return None

    if action.path is None:
        raise ApplyPlanError(f"{action.action_id} has no path")

    path = action.path
    created_new_file = not path.exists()
    backup_path, backup_sha256 = (
        create_backup(
            path,
            state_dir=config.state_dir,
            transaction_id=transaction_id,
            action_id=action.action_id,
        )
        if action.requires_backup and path.exists()
        else (None, None)
    )

    SafeFS.from_roots(_allowed_roots_for_action(action, config)).write_text_atomic(
        path,
        rendered.content,
        permissions=rendered.permissions or 0o600,
    )
    if action.action_type == "write_config" and not validate_config(
        path, action.details["config_format"]
    ):
        raise ApplyPlanError(f"Rendered config for {action.tool} failed validation")

    return AppliedWrite(
        action=action,
        path=path,
        backup_path=backup_path,
        backup_sha256=backup_sha256,
        created_new_file=created_new_file,
    )


def _rollback_applied_writes(applied_writes: list[AppliedWrite], config: ResolvedConfig) -> None:
    for applied in reversed(applied_writes):
        if applied.backup_path:
            restore_backup(
                applied.backup_path,
                applied.path,
                allowed_roots=_allowed_roots_for_action(applied.action, config),
            )
        elif applied.created_new_file and applied.path.exists():
            SafeFS.from_roots(_allowed_roots_for_action(applied.action, config)).delete_file(
                applied.path
            )


def _ensure_record_matches_current_file(record: ManifestRecord) -> None:
    path = Path(record.path)
    if record.sha256 is None:
        return
    if not path.exists():
        return
    current_sha = sha256_file(path)
    if current_sha != record.sha256:
        raise RollbackConflictError(
            f"Refusing to rollback {path}; file changed since Eve wrote it."
        )


def _ensure_backup_integrity(record: ManifestRecord) -> None:
    if not record.backup_path or not record.backup_sha256:
        return
    backup_path = Path(record.backup_path)
    if not backup_path.exists():
        raise RollbackConflictError(f"Backup missing for rollback: {backup_path}")
    current_sha = sha256_file(backup_path)
    if current_sha != record.backup_sha256:
        raise RollbackConflictError(f"Backup integrity check failed for {backup_path}")


def _verify_restored_target(record: ManifestRecord) -> None:
    if not record.backup_sha256 or not record.path:
        return
    path = Path(record.path)
    if not path.exists():
        raise RollbackConflictError(f"Rollback target missing after restore: {path}")
    restored_sha = sha256_file(path)
    if restored_sha != record.backup_sha256:
        raise RollbackConflictError(f"Rollback restore hash mismatch for {path}")


def _preflight_rollback(records: list[ManifestRecord]) -> None:
    conflicts: list[str] = []
    for record in reversed(records):
        try:
            _ensure_backup_integrity(record)
            _ensure_record_matches_current_file(record)
        except RollbackConflictError as exc:
            conflicts.append(str(exc))
    if conflicts:
        raise RollbackConflictError(
            "Rollback blocked by file conflicts or backup integrity failures.",
            conflicted_paths=conflicts,
        )


def apply_install_plan(
    plan: InstallPlan,
    config: ResolvedConfig,
    credential_store: CredentialStore,
    provided_secrets: dict[str, str] | None = None,
    provided_api_keys: dict[str, str] | None = None,
    auth_overrides: dict[str, str] | None = None,
    allowed_tools: list[str] | None = None,
) -> ApplyResult:
    transaction_id = str(uuid4())
    provided_secrets = provided_secrets or provided_api_keys or {}
    auth_overrides = auth_overrides or {}
    with installer_lock(config.state_dir):
        for tool_plan in plan.tool_plans:
            if allowed_tools and tool_plan.tool not in allowed_tools:
                continue
            if tool_plan.tool == "codex-cli" and not feature_enabled_for_tool(
                tool_plan.tool, config
            ):
                raise ApplyPlanError(
                    "Codex CLI steps are present in this plan, but Codex is disabled at execution time."
                )
        all_records = load_manifest(
            config.state_dir, allow_file_fallback=config.allow_file_secret_fallback
        )
        write_transaction_state(
            config.state_dir,
            {
                "transaction_id": transaction_id,
                "phase": "applying",
                "environment": plan.environment,
                "tools": [tool_plan.tool for tool_plan in plan.tool_plans],
            },
        )

        applied_tools: list[str] = []
        applied_actions = 0
        for tool_plan in plan.tool_plans:
            if allowed_tools and tool_plan.tool not in allowed_tools:
                continue
            if not feature_enabled_for_tool(tool_plan.tool, config):
                continue
            if not tool_plan.supported:
                continue
            applied_writes: list[AppliedWrite] = []
            try:
                for action in tool_plan.actions:
                    write_transaction_state(
                        config.state_dir,
                        {
                            "transaction_id": transaction_id,
                            "phase": "applying",
                            "environment": plan.environment,
                            "tool": tool_plan.tool,
                            "action_id": action.action_id,
                            "action_type": action.action_type,
                        },
                    )
                    applied = _apply_action(
                        action,
                        config,
                        credential_store,
                        provided_secrets.get(tool_plan.tool),
                        auth_overrides.get(tool_plan.tool) or tool_plan.auth_mode,
                        transaction_id,
                    )
                    if applied:
                        applied_writes.append(applied)
                        applied_actions += 1
            except Exception:
                _rollback_applied_writes(applied_writes, config)
                raise

            for applied in applied_writes:
                all_records.append(
                    ManifestRecord(
                        transaction_id=transaction_id,
                        tool=applied.action.tool,
                        action_id=applied.action.action_id,
                        action_type=applied.action.action_type,
                        path=str(applied.path),
                        backup_path=str(applied.backup_path) if applied.backup_path else None,
                        sha256=sha256_file(applied.path),
                        backup_sha256=applied.backup_sha256,
                        scope=applied.action.scope,
                        environment=plan.environment,
                    )
                )
            if tool_plan.actions:
                applied_tools.append(tool_plan.tool)

        write_manifest(
            config.state_dir, all_records, allow_file_fallback=config.allow_file_secret_fallback
        )
        clear_transaction_state(config.state_dir)
        return ApplyResult(
            transaction_id=transaction_id,
            applied_actions=applied_actions,
            applied_tools=applied_tools,
        )


def rollback_transaction(config: ResolvedConfig, transaction_id: str) -> RollbackResult:
    with installer_lock(config.state_dir):
        write_transaction_state(
            config.state_dir,
            {"transaction_id": transaction_id, "phase": "rollback"},
        )
        records = load_manifest(
            config.state_dir, allow_file_fallback=config.allow_file_secret_fallback
        )
        target = [record for record in records if record.transaction_id == transaction_id]
        _preflight_rollback(target)
        restored = 0
        for record in reversed(target):
            path = Path(record.path)
            if record.backup_path:
                if record.scope == "state":
                    allowed_roots = [config.state_dir]
                elif record.scope == "project":
                    allowed_roots = [config.project_root]
                else:
                    allowed_roots = [path.parent]
                restore_backup(Path(record.backup_path), path, allowed_roots=allowed_roots)
                _verify_restored_target(record)
            elif path.exists():
                SafeFS.from_roots(
                    [config.project_root] if record.scope == "project" else [path.parent]
                ).delete_file(path)
            restored += 1
        write_manifest(
            config.state_dir,
            [record for record in records if record.transaction_id != transaction_id],
            allow_file_fallback=config.allow_file_secret_fallback,
        )
        clear_transaction_state(config.state_dir)
        return RollbackResult(transaction_id=transaction_id, restored_actions=restored)
