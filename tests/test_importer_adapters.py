from __future__ import annotations

import os
from pathlib import Path

from eve_client.importer.adapters import CodexCliAdapter, GeminiCliAdapter, scan_candidates

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
    codex_root = tmp_path / ".codex" / "sessions"
    gemini_root = tmp_path / ".gemini" / "tmp" / "h" / "chats"
    codex_root.mkdir(parents=True)
    gemini_root.mkdir(parents=True)
    codex_target = codex_root / "importer_codex_sample.jsonl"
    gemini_target = gemini_root / "session-2026-03-10T11-00-demo.json"
    codex_target.write_text((FIXTURES / "importer_codex_sample.jsonl").read_text(), encoding="utf-8")
    gemini_target.write_text((FIXTURES / "importer_gemini_sample.json").read_text(), encoding="utf-8")
    os.utime(codex_target, (1_700_000_000, 1_700_000_000))

    candidates = scan_candidates(
        roots_by_source={
            "codex-cli": [codex_root],
            "gemini-cli": [tmp_path / ".gemini" / "tmp"],
        }
    )
    assert len(candidates) == 2
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
