"""Project scope resolution for Eve client installs and hooks."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCOPE_CONFIG_FILENAME = "eve.json"
MAX_SCOPE_CONFIG_BYTES = 64 * 1024
VISIBILITY_ENV_VAR = "EVE_DEFAULT_VISIBILITY"
CONTEXT_ENV_VAR = "EVE_DEFAULT_CONTEXT"
TENANT_SLUG_ENV_VAR = "EVE_TENANT_SLUG"

_CONTEXT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
_TENANT_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_VALID_VISIBILITY = {"PERSONAL", "SHARED"}


@dataclass(slots=True, frozen=True)
class ResolvedScope:
    """Advisory scope discovery result.

    This type is not authorization. PACK-07 owns trust confirmation before
    SHARED/team routing is allowed to affect write behavior.
    """

    visibility: str | None = None
    context: str | None = None
    tenant_slug: str | None = None
    trust_confirmed: bool = False

    def has_scope_default(self) -> bool:
        return bool(self.visibility or self.context)

    def has_any_value(self) -> bool:
        return bool(self.visibility or self.context or self.tenant_slug)

    def requires_trust_confirmation(self) -> bool:
        return self.visibility == "SHARED" or bool(self.tenant_slug)


def _warn(message: str) -> None:
    print(f"[eve-client:scope] {message}", file=sys.stderr)


def _normalize_visibility(value: object, *, source: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _warn(f"Invalid {source}: visibility must be a string")
        return None
    normalized = value.strip().upper()
    if not normalized:
        return None
    if normalized not in _VALID_VISIBILITY:
        _warn(f"Invalid {source}: visibility must be PERSONAL or SHARED")
        return None
    return normalized


def _normalize_context(value: object, *, source: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _warn(f"Invalid {source}: context must be a string")
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.lower() == "ephemeral" or not _CONTEXT_RE.fullmatch(normalized):
        _warn(
            f"Invalid {source}: context must start with a letter and contain only "
            "letters, numbers, or underscores"
        )
        return None
    return normalized


def _normalize_tenant_slug(value: object, *, source: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _warn(f"Invalid {source}: tenant_slug must be a string")
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if not _TENANT_SLUG_RE.fullmatch(normalized):
        _warn(
            f"Invalid {source}: tenant_slug must contain only letters, numbers, "
            "hyphen, or underscore"
        )
        return None
    return normalized


def _find_scope_config(cwd: Path) -> Path | None:
    current = cwd.expanduser().resolve()
    if current.is_file():
        current = current.parent
    home = Path.home().expanduser().resolve()
    try:
        current.relative_to(home)
        stop_at = home
    except ValueError:
        # Outside $HOME, do not walk arbitrary parent directories. Still allow an
        # explicit eve.json in the current working directory for test fixtures
        # and sandboxed project roots.
        stop_at = current
    while True:
        candidate = current / SCOPE_CONFIG_FILENAME
        if candidate.is_file():
            if candidate.is_symlink():
                _warn(f"Ignoring {candidate}: symlinked scope config is not supported")
                return None
            return candidate
        if current == stop_at or current.parent == current:
            return None
        current = current.parent


def _payload_field_is_invalid(raw_value: object, normalized_value: str | None) -> bool:
    if raw_value is None:
        return False
    if isinstance(raw_value, str) and not raw_value.strip():
        return False
    return normalized_value is None


def _scope_from_payload(
    payload: dict[str, Any], *, source: str, fail_entire_payload_on_invalid: bool = False
) -> ResolvedScope | None:
    raw_visibility = payload.get("visibility")
    raw_context = payload.get("context")
    raw_tenant_slug = payload.get("tenant_slug")
    visibility = _normalize_visibility(raw_visibility, source=source)
    context = _normalize_context(raw_context, source=source)
    tenant_slug = _normalize_tenant_slug(raw_tenant_slug, source=source)
    if fail_entire_payload_on_invalid and (
        _payload_field_is_invalid(raw_visibility, visibility)
        or _payload_field_is_invalid(raw_context, context)
        or _payload_field_is_invalid(raw_tenant_slug, tenant_slug)
    ):
        return None
    scope = ResolvedScope(
        visibility=visibility,
        context=context,
        tenant_slug=tenant_slug,
    )
    if not scope.has_any_value():
        return None
    return scope


def _load_file_scope(cwd: Path) -> ResolvedScope | None:
    path = _find_scope_config(cwd)
    if path is None:
        return None
    try:
        if path.stat().st_size > MAX_SCOPE_CONFIG_BYTES:
            _warn(f"Ignoring {path}: file is too large")
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _warn(f"Ignoring malformed {SCOPE_CONFIG_FILENAME} at {path}")
        return None
    if not isinstance(payload, dict):
        _warn(f"Ignoring malformed {SCOPE_CONFIG_FILENAME} at {path}: expected object")
        return None
    raw_version = payload.get("scope_version", 1)
    if (
        isinstance(raw_version, bool)
        or not isinstance(raw_version, (int, float))
        or raw_version != 1
    ):
        _warn(f"Ignoring {path}: unsupported scope_version")
        return None
    return _scope_from_payload(payload, source=str(path), fail_entire_payload_on_invalid=True)


def _env_scope() -> ResolvedScope | None:
    scope = ResolvedScope(
        visibility=_normalize_visibility(
            os.environ.get(VISIBILITY_ENV_VAR), source=VISIBILITY_ENV_VAR
        ),
        context=_normalize_context(os.environ.get(CONTEXT_ENV_VAR), source=CONTEXT_ENV_VAR),
        tenant_slug=_normalize_tenant_slug(
            os.environ.get(TENANT_SLUG_ENV_VAR), source=TENANT_SLUG_ENV_VAR
        ),
    )
    if not (scope.visibility or scope.context or scope.tenant_slug):
        return None
    return scope


def resolve_scope(cwd: Path | str | None = None) -> ResolvedScope | None:
    """Resolve product scope defaults using env-over-project-file precedence."""
    start = Path(cwd) if cwd is not None else Path.cwd()
    file_scope = _load_file_scope(start)
    env_scope = _env_scope()
    if file_scope is None:
        return env_scope if env_scope and env_scope.has_scope_default() else None
    if env_scope is None:
        return file_scope if file_scope.has_scope_default() else None
    merged = ResolvedScope(
        visibility=env_scope.visibility or file_scope.visibility,
        context=env_scope.context or file_scope.context,
        tenant_slug=env_scope.tenant_slug or file_scope.tenant_slug,
    )
    return merged if merged.has_scope_default() else None
