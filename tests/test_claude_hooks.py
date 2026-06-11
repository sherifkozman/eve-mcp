from __future__ import annotations

import io
import json
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from eve_client import claude_hooks
from eve_client.scope import ResolvedScope


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _write_config(tmp_path: Path) -> None:
    cfg = tmp_path / ".cfg" / "eve"
    state = tmp_path / ".state" / "eve"
    cfg.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(
        json.dumps({"config_version": 1, "allow_file_secret_fallback": True}),
        encoding="utf-8",
    )
    (state / "auth-fallback.json").write_text(
        json.dumps({"claude-code:api-key": "eve-secret"}),
        encoding="utf-8",
    )


def test_api_base_url_strips_mcp_suffix() -> None:
    assert claude_hooks._api_base_url("https://mcp.evemem.com/mcp") == "https://mcp.evemem.com"
    assert claude_hooks._api_base_url("https://mcp.evemem.com") == "https://mcp.evemem.com"


def test_session_start_emits_additional_context_and_persists_session_id(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_config(tmp_path)
    env_file = tmp_path / "claude.env"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"session_id": "sess-1", "source": "startup"})),
    )
    with patch(
        "eve_client.claude_hooks.urllib.request.urlopen",
        return_value=_Response(
            {
                "session_id": "sess-1",
                "injected_context": {
                    "preferences": [
                        {"category": "code", "key": "style", "value": "concise", "confidence": 0.9}
                    ],
                    "learned_rules": [
                        {
                            "domain": "python",
                            "content": "Prefer explicit tests",
                            "confidence": 0.8,
                            "source_episodes": [],
                        }
                    ],
                    "recent_episodic": [
                        {
                            "summary": "Refactored apply path",
                            "event_type": "session_end",
                            "stored_at": "now",
                        }
                    ],
                },
            }
        ),
    ):
        try:
            claude_hooks.session_start_main()
        except SystemExit as exc:
            assert exc.code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Eve Memory Context" in payload["hookSpecificOutput"]["additionalContext"]
    assert "style: concise" in payload["hookSpecificOutput"]["additionalContext"]
    assert 'EVE_MEMORY_SESSION_ID="sess-1"' in env_file.read_text(encoding="utf-8")


def test_prompt_enrich_emits_relevant_memories(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {"prompt": "Find the prior decision about the installer rollback integrity model"}
            )
        ),
    )
    with patch(
        "eve_client.claude_hooks.urllib.request.urlopen",
        return_value=_Response(
            {
                "results": [
                    {
                        "store": "semantic",
                        "similarity": 0.91,
                        "chunk": {
                            "text": "Rollback must fail closed when backups or hashes diverge."
                        },
                    }
                ]
            }
        ),
    ):
        try:
            claude_hooks.prompt_enrich_main()
        except SystemExit as exc:
            assert exc.code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "Rollback must fail closed" in payload["hookSpecificOutput"]["additionalContext"]


def test_memory_client_401_prints_key_rotation_help(capsys) -> None:
    client = claude_hooks._MemoryClient(base_url="https://mcp.evemem.com/mcp", api_key="eve-secret")
    error = urllib.error.HTTPError("https://mcp.evemem.com/memory/search", 401, "Unauthorized", {}, BytesIO())

    with patch("eve_client.claude_hooks.urllib.request.urlopen", side_effect=error):
        ok, payload = client.search(query="prior decision")

    assert ok is False
    assert payload is None
    error_output = capsys.readouterr().err
    assert "HTTP 401 from /memory/search: Unauthorized" in error_output
    assert claude_hooks.API_KEY_401_HELP in error_output


def test_memory_client_401_sanitizes_error_reason(capsys) -> None:
    client = claude_hooks._MemoryClient(base_url="https://mcp.evemem.com/mcp", api_key="eve-secret")
    leaked_token = "abcdEFGH1234567890zyxwvu"
    error = urllib.error.HTTPError(
        "https://mcp.evemem.com/memory/search",
        401,
        f"Unauthorized {leaked_token}\nsecond-line",
        {},
        BytesIO(),
    )

    with patch("eve_client.claude_hooks.urllib.request.urlopen", side_effect=error):
        ok, payload = client.search(query="prior decision")

    assert ok is False
    assert payload is None
    error_output = capsys.readouterr().err
    assert leaked_token not in error_output
    assert "Unauthorized **** second-line" in error_output
    assert f"{leaked_token}\nsecond-line" not in error_output


def test_memory_client_generic_exception_sanitizes_error_reason(capsys) -> None:
    client = claude_hooks._MemoryClient(base_url="https://mcp.evemem.com/mcp", api_key="eve-secret")
    leaked_token = "abcdEFGH1234567890zyxwvu"
    error = RuntimeError(f"boom {leaked_token}\nsecond-line")

    with patch("eve_client.claude_hooks.urllib.request.urlopen", side_effect=error):
        ok, payload = client.search(query="prior decision")

    assert ok is False
    assert payload is None
    error_output = capsys.readouterr().err
    assert leaked_token not in error_output
    assert "boom **** second-line" in error_output


def test_session_end_reads_transcript_and_calls_extract_and_end(
    tmp_path: Path, monkeypatch
) -> None:
    _write_config(tmp_path)
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        json.dumps(
            {
                "role": "human",
                "content": [
                    {
                        "type": "text",
                        "text": "Remember my preference for terse code review findings.",
                    }
                ],
            }
        )
        + "\n"
        + json.dumps(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Stored that preference and updated the review output.",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    monkeypatch.setenv("EVE_MEMORY_SESSION_ID", "sess-2")
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"transcript_path": str(transcript_path), "status": "success"})),
    )

    requests: list[tuple[str, dict[str, object]]] = []

    def _urlopen(request, timeout=0):  # noqa: ANN001
        requests.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _Response({"ok": True})

    with patch("eve_client.claude_hooks.urllib.request.urlopen", side_effect=_urlopen):
        try:
            claude_hooks.session_end_main()
        except SystemExit as exc:
            assert exc.code == 0

    assert requests[0][0].endswith("/memory/extract")
    assert requests[0][1]["session_id"] == "sess-2"
    assert "Remember my preference" in requests[0][1]["transcript"]
    assert requests[1][0].endswith("/memory/session/end")
    assert requests[1][1]["session_id"] == "sess-2"


def test_memory_client_stamps_personal_scope_on_write_payloads() -> None:
    client = claude_hooks._MemoryClient(
        base_url="https://mcp.evemem.com/mcp",
        api_key="eve-secret",
        scope=ResolvedScope(visibility="PERSONAL", context="project_alpha"),
    )
    requests: list[tuple[str, dict[str, object], dict[str, str]]] = []

    def _urlopen(request, timeout=0):  # noqa: ANN001
        requests.append(
            (
                request.full_url,
                json.loads(request.data.decode("utf-8")),
                dict(request.header_items()),
            )
        )
        return _Response({"ok": True})

    with patch("eve_client.claude_hooks.urllib.request.urlopen", side_effect=_urlopen):
        assert client.extract(transcript="User: remember the alpha project preference.", session_id=None)[0]
        assert client.session_end(summary="Finished alpha work", session_id="sess-1", status=None)[0]
        assert client.pre_compaction(session_id="sess-1", critical_facts=["Alpha decision"])[0]

    for _url, payload, headers in requests:
        assert payload["visibility"] == "PERSONAL"
        assert payload["context"] == "project_alpha"
        assert "X-managed-tenant-slug" not in headers


def test_memory_client_stamps_shared_advisory_context_without_tenant_header() -> None:
    client = claude_hooks._MemoryClient(
        base_url="https://mcp.evemem.com/mcp",
        api_key="eve-secret",
        scope=ResolvedScope(visibility="SHARED", context="team_alpha", tenant_slug="acme-team"),
    )
    requests: list[tuple[dict[str, object], dict[str, str]]] = []

    def _urlopen(request, timeout=0):  # noqa: ANN001
        requests.append((json.loads(request.data.decode("utf-8")), dict(request.header_items())))
        return _Response({"ok": True})

    with patch("eve_client.claude_hooks.urllib.request.urlopen", side_effect=_urlopen):
        assert client.extract(transcript="User: remember the team decision.", session_id=None)[0]

    payload, headers = requests[0]
    assert payload["visibility"] == "SHARED"
    assert payload["context"] == "team_alpha"
    assert "X-managed-tenant-slug" not in headers


def test_memory_client_stamps_explicit_env_tenant_slug_header(monkeypatch) -> None:
    monkeypatch.setenv("EVE_TENANT_SLUG", "acme-team")
    client = claude_hooks._MemoryClient(
        base_url="https://mcp.evemem.com/mcp",
        api_key="eve-secret",
        scope=ResolvedScope(visibility="PERSONAL", context="project_alpha", tenant_slug="acme-team"),
    )
    seen_headers: list[dict[str, str]] = []

    def _urlopen(request, timeout=0):  # noqa: ANN001
        seen_headers.append(dict(request.header_items()))
        return _Response({"ok": True})

    with patch("eve_client.claude_hooks.urllib.request.urlopen", side_effect=_urlopen):
        assert client.extract(transcript="User: remember the scoped tenant decision.", session_id=None)[0]

    assert seen_headers[0]["X-managed-tenant-slug"] == "acme-team"


def test_hook_scope_resolution_failure_fails_open(monkeypatch, tmp_path: Path) -> None:
    _write_config(tmp_path)
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        json.dumps({"role": "human", "content": "Remember this implementation detail."}) + "\n"
        + json.dumps({"role": "assistant", "content": "The detail is preserved for later."})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"transcript_path": str(transcript_path)})),
    )
    monkeypatch.setattr(
        "eve_client.claude_hooks.resolve_scope",
        lambda: (_ for _ in ()).throw(RuntimeError("scope unavailable")),
    )
    requests: list[dict[str, object]] = []

    def _urlopen(request, timeout=0):  # noqa: ANN001
        requests.append(json.loads(request.data.decode("utf-8")))
        return _Response({"ok": True})

    with patch("eve_client.claude_hooks.urllib.request.urlopen", side_effect=_urlopen):
        try:
            claude_hooks.session_end_main()
        except SystemExit as exc:
            assert exc.code == 0

    assert requests
    assert all("visibility" not in payload and "context" not in payload for payload in requests)
