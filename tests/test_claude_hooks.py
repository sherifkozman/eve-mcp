from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from eve_client import claude_hooks


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
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


def test_session_start_emits_additional_context_and_persists_session_id(tmp_path: Path, monkeypatch, capsys) -> None:
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
                    "preferences": [{"category": "code", "key": "style", "value": "concise", "confidence": 0.9}],
                    "learned_rules": [{"domain": "python", "content": "Prefer explicit tests", "confidence": 0.8, "source_episodes": []}],
                    "recent_episodic": [{"summary": "Refactored apply path", "event_type": "session_end", "stored_at": "now"}],
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
        io.StringIO(json.dumps({"prompt": "Find the prior decision about the installer rollback integrity model"})),
    )
    with patch(
        "eve_client.claude_hooks.urllib.request.urlopen",
        return_value=_Response(
            {
                "results": [
                    {
                        "store": "semantic",
                        "similarity": 0.91,
                        "chunk": {"text": "Rollback must fail closed when backups or hashes diverge."},
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


def test_session_end_reads_transcript_and_calls_extract_and_end(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path)
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        json.dumps({"role": "human", "content": [{"type": "text", "text": "Remember my preference for terse code review findings."}]})
        + "\n"
        + json.dumps({"role": "assistant", "content": [{"type": "text", "text": "Stored that preference and updated the review output."}]})
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
