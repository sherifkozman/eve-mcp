from __future__ import annotations

import json
from pathlib import Path

from eve_client.scope import ResolvedScope, resolve_scope


def test_resolve_scope_returns_none_without_file_or_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    monkeypatch.delenv("EVE_DEFAULT_CONTEXT", raising=False)
    monkeypatch.delenv("EVE_TENANT_SLUG", raising=False)

    assert resolve_scope(tmp_path) is None


def test_resolve_scope_uses_nearest_eve_json(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    monkeypatch.delenv("EVE_DEFAULT_CONTEXT", raising=False)
    monkeypatch.delenv("EVE_TENANT_SLUG", raising=False)
    monkeypatch.setattr("eve_client.scope.Path.home", lambda: tmp_path)
    outer = tmp_path / "eve.json"
    inner = tmp_path / "workspace" / "project" / "eve.json"
    inner.parent.mkdir(parents=True)
    outer.write_text(json.dumps({"scope_version": 1, "visibility": "PERSONAL"}))
    inner.write_text(
        json.dumps(
            {
                "scope_version": 1,
                "visibility": "shared",
                "context": "Team_Project",
                "tenant_slug": "acme-team",
            }
        ),
        encoding="utf-8",
    )

    scope = resolve_scope(inner.parent / "src")

    assert scope == ResolvedScope(
        visibility="SHARED",
        context="Team_Project",
        tenant_slug="acme-team",
    )
    assert scope.trust_confirmed is False
    assert scope.requires_trust_confirmation() is True
    assert not hasattr(scope, "as_payload")
    assert not hasattr(scope, "as_headers")
    assert not hasattr(scope, "as_env")


def test_resolve_scope_env_overrides_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "eve.json").write_text(
        json.dumps(
            {
                "scope_version": 1,
                "visibility": "SHARED",
                "context": "file_context",
                "tenant_slug": "file-tenant",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVE_DEFAULT_VISIBILITY", "personal")
    monkeypatch.setenv("EVE_DEFAULT_CONTEXT", "env_context")
    monkeypatch.setenv("EVE_TENANT_SLUG", "env-tenant")

    assert resolve_scope(tmp_path) == ResolvedScope(
        visibility="PERSONAL",
        context="env_context",
        tenant_slug="env-tenant",
    )


def test_resolve_scope_tenant_slug_only_does_not_resolve(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "eve.json").write_text(
        json.dumps({"scope_version": 1, "tenant_slug": "acme"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVE_TENANT_SLUG", "env-acme")
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    monkeypatch.delenv("EVE_DEFAULT_CONTEXT", raising=False)

    assert resolve_scope(tmp_path) is None


def test_resolve_scope_env_tenant_slug_can_join_file_scope(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "eve.json").write_text(
        json.dumps({"scope_version": 1, "visibility": "SHARED"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVE_TENANT_SLUG", "env-acme")
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    monkeypatch.delenv("EVE_DEFAULT_CONTEXT", raising=False)

    assert resolve_scope(tmp_path) == ResolvedScope(
        visibility="SHARED",
        tenant_slug="env-acme",
    )


def test_resolve_scope_invalid_file_visibility_fails_open_to_none(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    monkeypatch.delenv("EVE_DEFAULT_CONTEXT", raising=False)
    monkeypatch.delenv("EVE_TENANT_SLUG", raising=False)
    (tmp_path / "eve.json").write_text(
        json.dumps({"scope_version": 1, "visibility": "GLOBAL", "context": "team"}),
        encoding="utf-8",
    )

    assert resolve_scope(tmp_path) is None
    assert "Invalid" in capsys.readouterr().err


def test_resolve_scope_invalid_env_value_is_absent_and_file_can_apply(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    (tmp_path / "eve.json").write_text(
        json.dumps({"scope_version": 1, "visibility": "SHARED"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVE_DEFAULT_VISIBILITY", "GLOBAL")

    assert resolve_scope(tmp_path) == ResolvedScope(visibility="SHARED")
    assert "Invalid EVE_DEFAULT_VISIBILITY" in capsys.readouterr().err


def test_resolve_scope_malformed_eve_json_warns_and_fails_open(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    (tmp_path / "eve.json").write_text("{not-json", encoding="utf-8")

    assert resolve_scope(tmp_path) is None
    assert "Ignoring malformed eve.json" in capsys.readouterr().err


def test_resolve_scope_symlinked_eve_json_warns_and_fails_open(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    monkeypatch.delenv("EVE_DEFAULT_CONTEXT", raising=False)
    monkeypatch.delenv("EVE_TENANT_SLUG", raising=False)
    target = tmp_path / "actual-eve.json"
    target.write_text(json.dumps({"scope_version": 1, "visibility": "SHARED"}), encoding="utf-8")
    (tmp_path / "eve.json").symlink_to(target)

    assert resolve_scope(tmp_path) is None
    assert "symlinked scope config" in capsys.readouterr().err


def test_resolve_scope_oversized_eve_json_warns_and_fails_open(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    (tmp_path / "eve.json").write_text(" " * (64 * 1024 + 1), encoding="utf-8")

    assert resolve_scope(tmp_path) is None
    assert "too large" in capsys.readouterr().err


def test_resolve_scope_accepts_numeric_scope_version_one(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    monkeypatch.delenv("EVE_DEFAULT_CONTEXT", raising=False)
    monkeypatch.delenv("EVE_TENANT_SLUG", raising=False)
    (tmp_path / "eve.json").write_text(
        json.dumps({"scope_version": 1.0, "visibility": "PERSONAL"}),
        encoding="utf-8",
    )

    assert resolve_scope(tmp_path) == ResolvedScope(visibility="PERSONAL")


def test_resolve_scope_rejects_unsupported_scope_version(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    monkeypatch.delenv("EVE_DEFAULT_CONTEXT", raising=False)
    monkeypatch.delenv("EVE_TENANT_SLUG", raising=False)
    (tmp_path / "eve.json").write_text(
        json.dumps({"scope_version": "1.0", "visibility": "PERSONAL"}),
        encoding="utf-8",
    )

    assert resolve_scope(tmp_path) is None
    assert "unsupported scope_version" in capsys.readouterr().err


def test_resolve_scope_stops_at_home_boundary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = home / "project"
    outside_home = tmp_path / "eve.json"
    project.mkdir(parents=True)
    outside_home.write_text(json.dumps({"visibility": "SHARED"}), encoding="utf-8")
    monkeypatch.setattr("eve_client.scope.Path.home", lambda: home)
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)

    assert resolve_scope(project) is None


def test_resolve_scope_outside_home_does_not_scan_parent_dirs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_home = tmp_path / "home"
    outside_project = tmp_path / "outside" / "project"
    outside_project.mkdir(parents=True)
    (tmp_path / "outside" / "eve.json").write_text(
        json.dumps({"scope_version": 1, "visibility": "SHARED"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("eve_client.scope.Path.home", lambda: fake_home)
    monkeypatch.delenv("EVE_DEFAULT_VISIBILITY", raising=False)
    monkeypatch.delenv("EVE_DEFAULT_CONTEXT", raising=False)
    monkeypatch.delenv("EVE_TENANT_SLUG", raising=False)

    assert resolve_scope(outside_project) is None
