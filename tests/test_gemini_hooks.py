from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest
from eve_client import gemini_hooks
from eve_client.scope import ResolvedScope


def _run_hook(func) -> dict[str, object]:
    output = io.StringIO()
    with redirect_stdout(output), pytest.raises(SystemExit) as exc:
        func()
    assert exc.value.code == 0
    return json.loads(output.getvalue())


def test_session_start_emits_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_hooks, "_load_credentials", lambda: ("eve-secret", None))
    monkeypatch.setattr(
        gemini_hooks,
        "resolve_config",
        lambda: SimpleNamespace(mcp_base_url="https://mcp.evemem.com/mcp"),
    )

    def fake_session_start(self) -> tuple[bool, dict[str, object]]:  # noqa: ANN001
        return True, {
            "injected_context": {
                "preferences": [{"category": "editor", "key": "tone", "value": "concise"}],
                "learned_rules": [
                    {"domain": "workflow", "content": "Check Eve before re-deriving context"}
                ],
                "recent_episodic": [
                    {"event_type": "decision", "summary": "Moved search to managed MCP"}
                ],
            }
        }

    monkeypatch.setattr(gemini_hooks._MemoryClient, "session_start", fake_session_start)

    payload = _run_hook(gemini_hooks.session_start)
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "SessionStart"
    assert "User Preferences" in hook_output["additionalContext"]
    assert "concise" in hook_output["additionalContext"]
    assert "Moved search to managed MCP" in hook_output["additionalContext"]


def test_prompt_enrich_emits_relevant_memories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_hooks, "_load_credentials", lambda: (None, "oauth-token"))
    monkeypatch.setattr(
        gemini_hooks,
        "resolve_config",
        lambda: SimpleNamespace(mcp_base_url="https://mcp.evemem.com/mcp"),
    )
    monkeypatch.setattr(
        gemini_hooks,
        "_load_hook_input",
        lambda: {
            "llm_request": {
                "messages": [
                    {
                        "role": "user",
                        "content": "What did we decide about the managed MCP audience and resource URL?",
                    }
                ]
            }
        },
    )

    def fake_search(self, *, query: str) -> tuple[bool, dict[str, object]]:  # noqa: ANN001
        assert "managed MCP audience" in query
        return True, {
            "results": [
                {
                    "store": "semantic",
                    "similarity": 0.92,
                    "chunk": {"text": "Managed MCP audience should be https://mcp.evemem.com/mcp"},
                }
            ]
        }

    monkeypatch.setattr(gemini_hooks._MemoryClient, "search", fake_search)

    payload = _run_hook(gemini_hooks.prompt_enrich)
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "BeforeAgent"
    assert "Relevant Memories" in hook_output["additionalContext"]
    assert "https://mcp.evemem.com/mcp" in hook_output["additionalContext"]


def test_session_end_ignores_short_or_missing_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        json.dumps({"role": "user", "content": "short"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gemini_hooks, "_load_credentials", lambda: ("eve-secret", None))
    monkeypatch.setattr(
        gemini_hooks,
        "resolve_config",
        lambda: SimpleNamespace(mcp_base_url="https://mcp.evemem.com/mcp"),
    )
    monkeypatch.setattr(
        gemini_hooks,
        "_load_hook_input",
        lambda: {"transcript_path": str(transcript_path)},
    )

    def fail_extract(self, *, transcript: str) -> tuple[bool, dict[str, object]]:  # noqa: ANN001
        raise AssertionError(f"extract should not be called for short transcript: {transcript!r}")

    monkeypatch.setattr(gemini_hooks._MemoryClient, "extract", fail_extract)

    payload = _run_hook(gemini_hooks.session_end)
    assert payload == {"ok": True}


def test_pre_compact_reads_transcript_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {"role": "user", "content": "Need to preserve the prior MCP audience decision."}
                ),
                json.dumps(
                    {
                        "role": "model",
                        "content": "We should keep https://mcp.evemem.com/mcp as the resource.",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gemini_hooks, "_load_credentials", lambda: ("eve-secret", None))
    monkeypatch.setattr(
        gemini_hooks,
        "resolve_config",
        lambda: SimpleNamespace(mcp_base_url="https://mcp.evemem.com/mcp"),
    )
    monkeypatch.setattr(
        gemini_hooks,
        "_load_hook_input",
        lambda: {"session_id": "session-123", "transcript_path": str(transcript_path)},
    )

    def fake_pre_compact(
        self, *, session_id: str, messages: list[dict[str, str]]
    ) -> tuple[bool, dict[str, object]]:  # noqa: ANN001
        assert session_id == "session-123"
        assert messages == [
            {"role": "user", "content": "Need to preserve the prior MCP audience decision."},
            {
                "role": "assistant",
                "content": "We should keep https://mcp.evemem.com/mcp as the resource.",
            },
        ]
        return True, {"ok": True}

    monkeypatch.setattr(gemini_hooks._MemoryClient, "pre_compact", fake_pre_compact)

    payload = _run_hook(gemini_hooks.pre_compact)
    assert payload == {"ok": True}


def test_session_end_extracts_long_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "user",
                        "content": "Please remember the production MCP audience and OAuth fix.",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "We fixed the managed MCP audience and tenant setting.",
                    }
                ),
                json.dumps(
                    {
                        "role": "user",
                        "content": "Also remember Gemini hooks are now package-managed.",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gemini_hooks, "_load_credentials", lambda: (None, "oauth-token"))
    monkeypatch.setattr(
        gemini_hooks,
        "resolve_config",
        lambda: SimpleNamespace(mcp_base_url="https://mcp.evemem.com/mcp"),
    )
    monkeypatch.setattr(
        gemini_hooks,
        "_load_hook_input",
        lambda: {"transcript_path": str(transcript_path)},
    )

    def fake_extract(self, *, transcript: str) -> tuple[bool, dict[str, object]]:  # noqa: ANN001
        assert "production MCP audience" in transcript
        assert "Gemini hooks are now package-managed" in transcript
        return True, {"stored": True}

    monkeypatch.setattr(gemini_hooks._MemoryClient, "extract", fake_extract)

    payload = _run_hook(gemini_hooks.session_end)
    assert payload == {"ok": True}


def test_api_base_url_strips_mcp_suffix() -> None:
    assert gemini_hooks._api_base_url("https://mcp.evemem.com/mcp") == "https://mcp.evemem.com"


def test_memory_client_stamps_personal_scope_on_write_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    client = gemini_hooks._MemoryClient(
        base_url="https://mcp.evemem.com/mcp",
        api_key="eve-secret",
        bearer_token=None,
        scope=ResolvedScope(visibility="PERSONAL", context="project_alpha"),
    )
    requests: list[tuple[str, dict[str, object], dict[str, str]]] = []

    class Response:
        def __enter__(self):  # noqa: ANN001
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
            return False

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request, timeout=0):  # noqa: ANN001
        requests.append(
            (
                request.full_url,
                json.loads(request.data.decode("utf-8")),
                dict(request.header_items()),
            )
        )
        return Response()

    monkeypatch.setattr(gemini_hooks.urllib.request, "urlopen", fake_urlopen)

    assert client.extract(transcript="User: remember the alpha project preference.")[0]
    assert client.pre_compact(session_id="sess-1", messages=[{"role": "user", "content": "Alpha"}])[0]

    for _url, payload, headers in requests:
        assert payload["visibility"] == "PERSONAL"
        assert payload["context"] == "project_alpha"
        assert "X-managed-tenant-slug" not in headers


def test_memory_client_stamps_shared_advisory_context_without_tenant_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = gemini_hooks._MemoryClient(
        base_url="https://mcp.evemem.com/mcp",
        api_key="eve-secret",
        bearer_token=None,
        scope=ResolvedScope(visibility="SHARED", context="team_alpha", tenant_slug="acme-team"),
    )
    requests: list[dict[str, object]] = []

    class Response:
        def __enter__(self):  # noqa: ANN001
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
            return False

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request, timeout=0):  # noqa: ANN001
        requests.append(json.loads(request.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr(gemini_hooks.urllib.request, "urlopen", fake_urlopen)

    assert client.extract(transcript="User: remember the shared decision.")[0]

    assert requests[0]["visibility"] == "SHARED"
    assert requests[0]["context"] == "team_alpha"


def test_memory_client_stamps_explicit_env_tenant_slug_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVE_TENANT_SLUG", "acme-team")
    client = gemini_hooks._MemoryClient(
        base_url="https://mcp.evemem.com/mcp",
        api_key="eve-secret",
        bearer_token=None,
        scope=ResolvedScope(visibility="PERSONAL", context="project_alpha", tenant_slug="acme-team"),
    )
    seen_headers: list[dict[str, str]] = []

    class Response:
        def __enter__(self):  # noqa: ANN001
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
            return False

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request, timeout=0):  # noqa: ANN001
        seen_headers.append(dict(request.header_items()))
        return Response()

    monkeypatch.setattr(gemini_hooks.urllib.request, "urlopen", fake_urlopen)

    assert client.extract(transcript="User: remember the tenant-scoped decision.")[0]

    assert seen_headers[0]["X-managed-tenant-slug"] == "acme-team"


def test_session_end_scope_resolution_failure_fails_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "Remember this implementation detail."}),
                json.dumps(
                    {
                        "role": "model",
                        "content": "The detail is preserved for later review.",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gemini_hooks, "_load_credentials", lambda: ("eve-secret", None))
    monkeypatch.setattr(
        gemini_hooks,
        "resolve_config",
        lambda: SimpleNamespace(mcp_base_url="https://mcp.evemem.com/mcp"),
    )
    monkeypatch.setattr(
        gemini_hooks,
        "_load_hook_input",
        lambda: {"transcript_path": str(transcript_path)},
    )
    monkeypatch.setattr(
        gemini_hooks,
        "resolve_scope",
        lambda: (_ for _ in ()).throw(RuntimeError("scope unavailable")),
    )
    requests: list[dict[str, object]] = []

    class Response:
        def __enter__(self):  # noqa: ANN001
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
            return False

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request, timeout=0):  # noqa: ANN001
        requests.append(json.loads(request.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr(gemini_hooks.urllib.request, "urlopen", fake_urlopen)

    payload = _run_hook(gemini_hooks.session_end)

    assert payload == {"ok": True}
    assert requests
    assert all("visibility" not in request and "context" not in request for request in requests)
