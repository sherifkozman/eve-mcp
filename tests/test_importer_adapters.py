from __future__ import annotations

import os
from pathlib import Path

from eve_client.importer.adapters import (
    ClaudeCodeAdapter,
    CodexCliAdapter,
    GeminiCliAdapter,
    scan_candidates,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_codex_adapter_discovers_and_parses_sample(tmp_path: Path) -> None:
    source = FIXTURES / "importer_codex_sample.jsonl"
    root = tmp_path / ".codex" / "sessions" / "2026" / "03" / "10"
    root.mkdir(parents=True)
    target = root / source.name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    adapter = CodexCliAdapter()
    candidates = adapter.discover([tmp_path / ".codex" / "sessions"])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.session_id == "codex-session-1"
    assert candidate.turn_count_hint == 2

    turns = list(adapter.parse(candidate))
    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[0].content == "Remember that I prefer concise release notes."
    assert turns[1].source_system == "codex-cli"


def test_claude_adapter_discovers_and_parses_sample(tmp_path: Path) -> None:
    source = FIXTURES / "importer_claude_code_sample.jsonl"
    root = tmp_path / ".claude" / "projects" / "project-a"
    root.mkdir(parents=True)
    target = root / source.name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    adapter = ClaudeCodeAdapter()
    candidates = adapter.discover([tmp_path / ".claude" / "projects"])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.session_id == "claude-session-1"
    assert candidate.turn_count_hint == 2

    turns = list(adapter.parse(candidate))
    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[0].content == "Remember that we prefer small importer batches."
    assert turns[1].content == "I will keep the importer batches small and resumable."
    assert turns[1].metadata["cwd"] == "/Users/example/project"
    assert turns[1].source_system == "claude-code"


def test_claude_adapter_skips_malformed_jsonl(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "projects"
    root.mkdir(parents=True)
    (root / "broken.jsonl").write_text("{not-json}\n", encoding="utf-8")

    adapter = ClaudeCodeAdapter()
    assert adapter.discover([root]) == []


def test_claude_adapter_skips_subagent_logs(tmp_path: Path) -> None:
    source = FIXTURES / "importer_claude_code_sample.jsonl"
    root = tmp_path / ".claude" / "projects" / "project-a" / "subagents"
    root.mkdir(parents=True)
    target = root / source.name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    adapter = ClaudeCodeAdapter()
    assert adapter.discover([tmp_path / ".claude" / "projects"]) == []


def test_claude_adapter_ignores_non_text_tool_results(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "projects" / "project-a"
    root.mkdir(parents=True)
    target = root / "tool-result-only.jsonl"
    target.write_text(
        "\n".join(
            [
                '{"sessionId":"claude-session-2","message":{"role":"user","content":[{"type":"tool_result","content":"ignore"}]}}',
                '{"sessionId":"claude-session-2","message":{"role":"assistant","content":[{"type":"thinking","thinking":"ignore"},{"type":"text","text":"Keep only this text."}]}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = ClaudeCodeAdapter()
    candidates = adapter.discover([tmp_path / ".claude" / "projects"])
    assert len(candidates) == 1
    assert candidates[0].turn_count_hint == 1

    turns = list(adapter.parse(candidates[0]))
    assert len(turns) == 1
    assert turns[0].content == "Keep only this text."


def test_gemini_adapter_discovers_and_parses_sample(tmp_path: Path) -> None:
    source = FIXTURES / "importer_gemini_sample.json"
    root = tmp_path / ".gemini" / "tmp" / "hash" / "chats"
    root.mkdir(parents=True)
    target = root / "session-2026-03-10T11-00-demo.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    adapter = GeminiCliAdapter()
    candidates = adapter.discover([tmp_path / ".gemini" / "tmp"])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.session_id == "gemini-session-1"
    assert candidate.turn_count_hint == 2

    turns = list(adapter.parse(candidate))
    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert "OAuth disabled" in turns[0].content
    assert turns[1].source_system == "gemini-cli"


def test_scan_candidates_sorts_newest_first(tmp_path: Path) -> None:
    claude_root = tmp_path / ".claude" / "projects"
    codex_root = tmp_path / ".codex" / "sessions"
    gemini_root = tmp_path / ".gemini" / "tmp" / "h" / "chats"
    claude_root.mkdir(parents=True)
    codex_root.mkdir(parents=True)
    gemini_root.mkdir(parents=True)
    claude_target = claude_root / "importer_claude_code_sample.jsonl"
    codex_target = codex_root / "importer_codex_sample.jsonl"
    gemini_target = gemini_root / "session-2026-03-10T11-00-demo.json"
    claude_target.write_text((FIXTURES / "importer_claude_code_sample.jsonl").read_text(), encoding="utf-8")
    codex_target.write_text((FIXTURES / "importer_codex_sample.jsonl").read_text(), encoding="utf-8")
    gemini_target.write_text((FIXTURES / "importer_gemini_sample.json").read_text(), encoding="utf-8")
    os.utime(claude_target, (1_700_000_050, 1_700_000_050))
    os.utime(codex_target, (1_700_000_000, 1_700_000_000))
    os.utime(gemini_target, (1_700_000_100, 1_700_000_100))

    candidates = scan_candidates(
        roots_by_source={
            "claude-code": [claude_root],
            "codex-cli": [codex_root],
            "gemini-cli": [tmp_path / ".gemini" / "tmp"],
        }
    )
    assert len(candidates) == 3
    assert candidates[0].source_type == "gemini-cli"


def test_codex_adapter_skips_malformed_jsonl(tmp_path: Path) -> None:
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    (root / "broken.jsonl").write_text("{not-json}\n", encoding="utf-8")

    adapter = CodexCliAdapter()
    assert adapter.discover([root]) == []


def test_gemini_adapter_skips_malformed_json(tmp_path: Path) -> None:
    root = tmp_path / ".gemini" / "tmp" / "hash" / "chats"
    root.mkdir(parents=True)
    (root / "session-2026-03-10T11-00-demo.json").write_text("{not-json}", encoding="utf-8")

    adapter = GeminiCliAdapter()
    assert adapter.discover([tmp_path / ".gemini" / "tmp"]) == []
