"""Tests for eve memory CLI sub-commands (NW-017)."""

from __future__ import annotations

import json
from http.client import HTTPResponse
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from eve_client import memory_cli
from eve_client.cli import app
from eve_client.config import ResolvedConfig
from typer.testing import CliRunner

runner = CliRunner()


def _config(tmp_path: Path) -> ResolvedConfig:
    return ResolvedConfig(
        config_dir=tmp_path / ".config" / "eve",
        config_path=tmp_path / ".config" / "eve" / "config.json",
        state_dir=tmp_path / ".state",
        project_root=tmp_path,
        mcp_base_url="https://mcp.evemem.com/mcp",
        mcp_server_name="eve-memory",
        environment="production",
        feature_claude_desktop=False,
        codex_enabled=False,
        codex_source="default",
        allow_file_secret_fallback=True,
    )


def _mock_urlopen(status: int, body: dict | list | None):
    """Create a mock for urllib.request.urlopen."""

    def _urlopen(request, timeout=None):  # noqa: ANN001, ARG001
        response = MagicMock()
        response.status = status
        response.__enter__ = lambda s: s
        response.__exit__ = lambda s, *a: None
        encoded = json.dumps(body).encode("utf-8") if body is not None else b""
        response.read.return_value = encoded
        return response

    return _urlopen


def _mock_urlopen_error(status: int, body: dict | None = None):
    """Create a mock that raises HTTPError."""
    import urllib.error

    def _urlopen(request, timeout=None):  # noqa: ANN001, ARG001
        encoded = json.dumps(body).encode("utf-8") if body else b"{}"
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=status,
            msg="Error",
            hdrs={},  # type: ignore[arg-type]
            fp=BytesIO(encoded),
        )

    return _urlopen


_SEARCH_RESULTS = {
    "results": [
        {
            "chunk": {
                "chunk_id": "abc-123",
                "text": "Auth uses OAuth 2.1 with PKCE",
                "source": "semantic:claude_code",
                "metadata": None,
                "entity_refs": ["OAuth"],
                "created_at": "2026-03-10T12:00:00Z",
                "importance": 7,
                "category": "architecture",
            },
            "similarity": 0.92,
            "score": 0.88,
        },
        {
            "chunk": {
                "chunk_id": "def-456",
                "text": "Redis used for session caching",
                "source": "semantic:gemini_cli",
                "metadata": None,
                "entity_refs": ["Redis"],
                "created_at": "2026-03-09T08:30:00Z",
                "importance": 5,
                "category": "infrastructure",
            },
            "similarity": 0.85,
            "score": 0.80,
        },
    ]
}

_HEALTH_OK = {
    "status": "ok",
    "database": "ok",
    "pgvector": "ok",
    "embedding_provider": "gemini",
    "embedding_model": "gemini-embedding-001",
    "memory_counts": {
        "naya": {"episodic": 120, "semantic": 5000, "learned_rules": 15},
        "personal": {"episodic": 30, "semantic": 800, "learned_rules": 3},
    },
}

_HEALTH_DEGRADED = {
    "status": "degraded",
    "database": "ok",
    "pgvector": "error",
    "embedding_provider": "gemini",
    "embedding_model": "gemini-embedding-001",
    "memory_counts": {},
}


def _patch_auth_with_api_key():
    """Patch credential store to return an API key."""
    return patch(
        "eve_client.memory_cli._get_auth_headers",
        return_value={"X-API-Key": "test-key-123"},
    )


def _patch_auth_empty():
    """Patch credential store to return no credentials."""
    return patch(
        "eve_client.memory_cli._get_auth_headers",
        return_value={},
    )


def _patch_config(tmp_path: Path):
    """Patch resolve_config to return a test config."""
    return patch(
        "eve_client.memory_cli.resolve_config",
        return_value=_config(tmp_path),
    )


class TestMemorySearch:
    def test_search_accepts_preference_store(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        def _urlopen(request, timeout=None):  # noqa: ANN001, ARG001
            captured.update(json.loads(request.data.decode("utf-8")))
            response = MagicMock()
            response.status = 200
            response.__enter__ = lambda s: s
            response.__exit__ = lambda s, *a: None
            response.read.return_value = json.dumps({"results": []}).encode("utf-8")
            return response

        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch("eve_client.memory_cli.urllib.request.urlopen", side_effect=_urlopen),
        ):
            result = runner.invoke(
                app,
                [
                    "memory",
                    "search",
                    "which models do I prefer for UI work",
                    "--store",
                    "Preference",
                    "--context",
                    "personal",
                ],
            )
            assert result.exit_code == 0
            assert captured["store"] == "preference"
            assert captured["context"] == "PERSONAL"

    def test_search_returns_results(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen(200, _SEARCH_RESULTS),
            ),
        ):
            result = runner.invoke(app, ["memory", "search", "auth patterns"])
            assert result.exit_code == 0
            assert "auth" in result.stdout.lower() or "Auth" in result.stdout
            assert "2 result(s)" in result.stdout

    def test_search_json_output(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen(200, _SEARCH_RESULTS),
            ),
        ):
            result = runner.invoke(app, ["memory", "search", "auth patterns", "--json"])
            assert result.exit_code == 0
            parsed = json.loads(result.stdout)
            assert len(parsed) == 2
            assert parsed[0]["chunk"]["chunk_id"] == "abc-123"

    def test_search_no_results(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen(200, {"results": []}),
            ),
        ):
            result = runner.invoke(app, ["memory", "search", "nonexistent"])
            assert result.exit_code == 0
            assert "No memories found" in result.stdout

    def test_search_no_auth_prompts_login(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_empty(),
        ):
            result = runner.invoke(app, ["memory", "search", "test"])
            assert result.exit_code == 1
            assert "eve auth login" in result.stdout

    def test_search_auth_failure(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen_error(401, {"detail": "Unauthorized"}),
            ),
        ):
            result = runner.invoke(app, ["memory", "search", "test"])
            assert result.exit_code == 1
            assert "Authentication failed" in result.stdout

    def test_search_connection_error(self, tmp_path: Path) -> None:
        import urllib.error

        def _fail(request, timeout=None):  # noqa: ANN001, ARG001
            raise urllib.error.URLError("Connection refused")

        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch("eve_client.memory_cli.urllib.request.urlopen", side_effect=_fail),
        ):
            result = runner.invoke(app, ["memory", "search", "test"])
            assert result.exit_code == 1
            assert "Connection error" in result.stdout

    def test_search_rejects_invalid_store(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
        ):
            result = runner.invoke(app, ["memory", "search", "test", "--store", "graph"])
            assert result.exit_code == 2
            assert "Usage: eve memory search" in result.output
            assert "Error" in result.output

    def test_search_passes_explicit_tool_to_auth_lookup(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            patch("eve_client.memory_cli._get_auth_headers", return_value={"X-API-Key": "x"}) as auth_mock,
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen(200, {"results": []}),
            ),
        ):
            result = runner.invoke(
                app,
                ["memory", "search", "what tone do I like", "--store", "preference", "--tool", "codex-cli"],
            )
            assert result.exit_code == 0
            auth_mock.assert_called_once()
            args, _kwargs = auth_mock.call_args
            assert args[1] == "codex-cli"

    def test_search_uses_oauth_header_for_codex_tool(self, tmp_path: Path) -> None:
        captured_headers: dict[str, str] = {}

        class _Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_oauth_session(self, tool_name):  # noqa: ANN001
                if tool_name == "codex-cli":
                    return MagicMock(access_token="oauth-token", expires_at=None), "keyring"
                return None, None

            def get_api_key(self, _tool_name):  # noqa: ANN001
                return None, None

        def _urlopen(request, timeout=None):  # noqa: ANN001, ARG001
            captured_headers.update({key.lower(): value for key, value in request.header_items()})
            response = MagicMock()
            response.status = 200
            response.__enter__ = lambda s: s
            response.__exit__ = lambda s, *a: None
            response.read.return_value = json.dumps({"results": []}).encode("utf-8")
            return response

        with (
            _patch_config(tmp_path),
            patch("eve_client.memory_cli.LocalCredentialStore", _Store),
            patch("eve_client.memory_cli.urllib.request.urlopen", side_effect=_urlopen),
        ):
            result = runner.invoke(
                app,
                ["memory", "search", "what do I prefer", "--store", "preference", "--tool", "codex-cli"],
            )
            assert result.exit_code == 0
            assert captured_headers["authorization"] == "Bearer oauth-token"

    def test_search_uses_api_key_header_for_claude_tool(self, tmp_path: Path) -> None:
        captured_headers: dict[str, str] = {}

        class _Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_oauth_session(self, _tool_name):  # noqa: ANN001
                return None, None

            def get_api_key(self, tool_name):  # noqa: ANN001
                if tool_name == "claude-code":
                    return "claude-api-key", "keyring"
                return None, None

        def _urlopen(request, timeout=None):  # noqa: ANN001, ARG001
            captured_headers.update({key.lower(): value for key, value in request.header_items()})
            response = MagicMock()
            response.status = 200
            response.__enter__ = lambda s: s
            response.__exit__ = lambda s, *a: None
            response.read.return_value = json.dumps({"results": []}).encode("utf-8")
            return response

        with (
            _patch_config(tmp_path),
            patch("eve_client.memory_cli.LocalCredentialStore", _Store),
            patch("eve_client.memory_cli.urllib.request.urlopen", side_effect=_urlopen),
        ):
            result = runner.invoke(
                app,
                ["memory", "search", "what tone do I like", "--store", "preference", "--tool", "claude-code"],
            )
            assert result.exit_code == 0
            assert captured_headers["x-api-key"] == "claude-api-key"

    def test_search_exits_when_credentials_are_ambiguous(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            patch(
                "eve_client.memory_cli._get_auth_headers",
                side_effect=memory_cli.AmbiguousMemoryAuthSelectionError(("claude-code", "codex-cli")),
            ),
        ):
            result = runner.invoke(app, ["memory", "search", "what tone do I like"])
            assert result.exit_code == 1
            assert "Multiple stored credentials found" in result.output
            assert "--tool" in result.output

    def test_get_auth_headers_rejects_ambiguous_automatic_selection(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        class _Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_oauth_session(self, tool_name):  # noqa: ANN001
                if tool_name == "claude-code":
                    return MagicMock(access_token="claude-token", expires_at=None), "keyring"
                return None, None

            def get_api_key(self, tool_name):  # noqa: ANN001
                if tool_name == "codex-cli":
                    return "codex-key", "keyring"
                return None, None

        with patch("eve_client.memory_cli.LocalCredentialStore", _Store):
            with pytest.raises(memory_cli.AmbiguousMemoryAuthSelectionError) as exc:
                memory_cli._get_auth_headers(config)
            assert exc.value.tools == ("claude-code", "codex-cli")

    def test_get_auth_headers_uses_requested_tool_only(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        class _Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_oauth_session(self, tool_name):  # noqa: ANN001
                if tool_name == "claude-code":
                    return MagicMock(access_token="claude-token", expires_at=None), "keyring"
                return None, None

            def get_api_key(self, tool_name):  # noqa: ANN001
                if tool_name == "codex-cli":
                    return "codex-key", "keyring"
                return None, None

        with patch("eve_client.memory_cli.LocalCredentialStore", _Store):
            assert memory_cli._get_auth_headers(config, "codex-cli") == {"X-API-Key": "codex-key"}

    def test_get_auth_headers_prefers_api_key_for_claude_code(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        class _Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_oauth_session(self, _tool_name):  # noqa: ANN001
                return MagicMock(access_token="oauth-token", expires_at=None), "keyring"

            def get_api_key(self, _tool_name):  # noqa: ANN001
                return "api-key", "keyring"

        with patch("eve_client.memory_cli.LocalCredentialStore", _Store):
            assert memory_cli._get_auth_headers(config, "claude-code") == {"X-API-Key": "api-key"}

    def test_get_auth_headers_prefers_oauth_for_codex(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        class _Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_oauth_session(self, _tool_name):  # noqa: ANN001
                return MagicMock(access_token="oauth-token", expires_at=None), "keyring"

            def get_api_key(self, _tool_name):  # noqa: ANN001
                return "api-key", "keyring"

        with patch("eve_client.memory_cli.LocalCredentialStore", _Store):
            assert memory_cli._get_auth_headers(config, "codex-cli") == {
                "Authorization": "Bearer oauth-token"
            }

    def test_status_continues_unauthenticated_when_credentials_are_ambiguous(
        self, tmp_path: Path
    ) -> None:
        with (
            _patch_config(tmp_path),
            patch(
                "eve_client.memory_cli._get_auth_headers",
                side_effect=memory_cli.AmbiguousMemoryAuthSelectionError(("claude-code", "codex-cli")),
            ),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen(200, _HEALTH_OK),
            ),
        ):
            result = runner.invoke(app, ["memory", "status"])
            assert result.exit_code == 0
            assert "unauthenticated health request" in result.output


class TestMemoryStatus:
    def test_status_with_explicit_tool_uses_auth_headers(self, tmp_path: Path) -> None:
        captured_headers: dict[str, str] = {}

        class _Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_oauth_session(self, tool_name):  # noqa: ANN001
                if tool_name == "codex-cli":
                    return MagicMock(access_token="oauth-token", expires_at=None), "keyring"
                return None, None

            def get_api_key(self, _tool_name):  # noqa: ANN001
                return None, None

        def _urlopen(request, timeout=None):  # noqa: ANN001, ARG001
            captured_headers.update(dict(request.header_items()))
            response = MagicMock()
            response.status = 200
            response.__enter__ = lambda s: s
            response.__exit__ = lambda s, *a: None
            response.read.return_value = json.dumps(_HEALTH_OK).encode("utf-8")
            return response

        with (
            _patch_config(tmp_path),
            patch("eve_client.memory_cli.LocalCredentialStore", _Store),
            patch("eve_client.memory_cli.urllib.request.urlopen", side_effect=_urlopen),
        ):
            result = runner.invoke(app, ["memory", "status", "--tool", "codex-cli"])
            assert result.exit_code == 0
            assert captured_headers["Authorization"] == "Bearer oauth-token"

    def test_status_with_explicit_tool_and_no_credentials_still_checks_health(
        self, tmp_path: Path
    ) -> None:
        with (
            _patch_config(tmp_path),
            patch("eve_client.memory_cli._get_auth_headers", return_value={}),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen(200, _HEALTH_OK),
            ),
        ):
            result = runner.invoke(app, ["memory", "status", "--tool", "claude-code"])
            assert result.exit_code == 0

    def test_status_healthy(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen(200, _HEALTH_OK),
            ),
        ):
            result = runner.invoke(app, ["memory", "status"])
            assert result.exit_code == 0
            assert "ok" in result.stdout

    def test_status_degraded(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen(200, _HEALTH_DEGRADED),
            ),
        ):
            result = runner.invoke(app, ["memory", "status"])
            assert result.exit_code == 0
            assert "degraded" in result.stdout

    def test_status_json_output(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen(200, _HEALTH_OK),
            ),
        ):
            result = runner.invoke(app, ["memory", "status", "--json"])
            assert result.exit_code == 0
            parsed = json.loads(result.stdout)
            assert parsed["status"] == "ok"
            assert "memory_counts" in parsed

    def test_status_unhealthy(self, tmp_path: Path) -> None:
        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch(
                "eve_client.memory_cli.urllib.request.urlopen",
                side_effect=_mock_urlopen_error(500),
            ),
        ):
            result = runner.invoke(app, ["memory", "status"])
            assert result.exit_code == 1
            assert "500" in result.stdout

    def test_status_connection_error(self, tmp_path: Path) -> None:
        import urllib.error

        def _fail(request, timeout=None):  # noqa: ANN001, ARG001
            raise urllib.error.URLError("Connection refused")

        with (
            _patch_config(tmp_path),
            _patch_auth_with_api_key(),
            patch("eve_client.memory_cli.urllib.request.urlopen", side_effect=_fail),
        ):
            result = runner.invoke(app, ["memory", "status"])
            assert result.exit_code == 1
            assert "Connection error" in result.stdout
