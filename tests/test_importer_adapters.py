from __future__ import annotations

import json
import os
from pathlib import Path

from eve_client.importer.adapters import (
    ChatGPTAdapter,
    ClaudeCodeAdapter,
    ClaudeDesktopAdapter,
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


def test_chatgpt_adapter_discovers_and_parses_sample(tmp_path: Path) -> None:
    source = FIXTURES / "importer_chatgpt_sample_conversations.json"
    root = tmp_path / "Downloads"
    root.mkdir(parents=True)
    target = root / "conversations.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    adapter = ChatGPTAdapter()
    candidates = adapter.discover([root])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_type == "chatgpt"
    assert candidate.session_id == "chatgpt-conversation-1"
    assert candidate.turn_count_hint == 2

    turns = list(adapter.parse(candidate))
    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[0].content == "Remember that I prefer import dry-runs first."
    assert turns[0].source_system == "chatgpt"
    assert turns[0].source_id == "msg-user-1"
    assert turns[0].metadata["title"] == "Launch notes"
    assert turns[1].content == "I will preview importer results before upload."
    assert turns[1].metadata["model"] == "gpt-4o"


def test_chatgpt_adapter_accepts_empty_conversation_without_exception(tmp_path: Path) -> None:
    source = FIXTURES / "importer_chatgpt_empty_conversations.json"
    root = tmp_path / "Downloads"
    root.mkdir(parents=True)
    target = root / "conversations.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    adapter = ChatGPTAdapter()
    assert adapter.discover([root]) == []


def test_chatgpt_adapter_skips_malformed_or_missing_mapping_files(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    root.mkdir(parents=True)
    (root / "conversations.json").write_text("{not-json}", encoding="utf-8")
    (root / "other_conversations.json").write_text('[{"id":"missing-mapping"}]', encoding="utf-8")

    adapter = ChatGPTAdapter()
    assert adapter.discover([root]) == []


def test_chatgpt_adapter_imports_only_active_current_node_branch(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    root.mkdir(parents=True)
    target = root / "conversations.json"
    target.write_text(
        json.dumps(
            [
                {
                    "id": "forked-chat",
                    "title": "Forked conversation",
                    "current_node": "assistant-active",
                    "mapping": {
                        "root": {"id": "root", "parent": None, "message": None},
                        "user-1": {
                            "id": "user-1",
                            "parent": "root",
                            "message": {
                                "id": "msg-user-active",
                                "create_time": 1780000000.0,
                                "author": {"role": "user", "metadata": {}},
                                "content": {"content_type": "text", "parts": ["Which import path is active?"]},
                            },
                        },
                        "assistant-old": {
                            "id": "assistant-old",
                            "parent": "user-1",
                            "message": {
                                "id": "msg-assistant-old",
                                "create_time": 1780000002.0,
                                "author": {"role": "assistant", "metadata": {"model_slug": "gpt-4o"}},
                                "content": {"content_type": "text", "parts": ["Inactive regenerated answer."]},
                            },
                        },
                        "assistant-active": {
                            "id": "assistant-active",
                            "parent": "user-1",
                            "message": {
                                "id": "msg-assistant-active",
                                "create_time": 1780000003.0,
                                "author": {"role": "assistant", "metadata": {"model_slug": "gpt-4o"}},
                                "content": {"content_type": "text", "parts": ["Active answer."]},
                            },
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    adapter = ChatGPTAdapter()
    candidate = adapter.discover([root])[0]
    turns = list(adapter.parse(candidate))

    assert candidate.turn_count_hint == 2
    assert [turn.content for turn in turns] == ["Which import path is active?", "Active answer."]


def test_chatgpt_adapter_rejects_missing_current_node_export(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    root.mkdir(parents=True)
    target = root / "conversations.json"
    payload = json.loads(
        (FIXTURES / "importer_chatgpt_sample_conversations.json").read_text(encoding="utf-8")
    )
    payload[0].pop("current_node", None)
    target.write_text(json.dumps(payload), encoding="utf-8")

    adapter = ChatGPTAdapter()

    assert adapter.discover([root]) == []


def test_chatgpt_adapter_rejects_unexpected_exact_path_name(tmp_path: Path) -> None:
    target = tmp_path / "renamed-chatgpt-export.json"
    target.write_text(
        (FIXTURES / "importer_chatgpt_sample_conversations.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert ChatGPTAdapter().candidate_for_path(target) is None


def test_claude_desktop_adapter_discovers_and_parses_sample(tmp_path: Path) -> None:
    source = FIXTURES / "importer_claude_desktop_sample.json"
    root = tmp_path / "Downloads"
    root.mkdir(parents=True)
    target = root / "claude_ai_conversations.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    adapter = ClaudeDesktopAdapter()
    candidates = adapter.discover([root])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_type == "claude-desktop"
    assert candidate.session_id == "claude-desktop-conversation-1"
    assert candidate.turn_count_hint == 2

    turns = list(adapter.parse(candidate))
    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[0].content == "Remember that Claude Desktop imports should be resumable."
    assert turns[0].source_system == "claude-desktop"
    assert turns[0].source_id == "desktop-user-1"
    assert turns[0].metadata["conversation_name"] == "Desktop import"
    assert turns[1].content == "I will keep the import batches resumable."


def test_claude_desktop_adapter_accepts_empty_export_without_exception(tmp_path: Path) -> None:
    source = FIXTURES / "importer_claude_desktop_empty.json"
    root = tmp_path / "Downloads"
    root.mkdir(parents=True)
    target = root / "claude_ai_conversations_empty.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    adapter = ClaudeDesktopAdapter()
    assert adapter.discover([root]) == []


def test_claude_desktop_adapter_rejects_unexpected_exact_path_name(tmp_path: Path) -> None:
    target = tmp_path / "renamed-claude-export.json"
    target.write_text(
        (FIXTURES / "importer_claude_desktop_sample.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert ClaudeDesktopAdapter().candidate_for_path(target) is None


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


def test_scan_candidates_includes_chatgpt_and_claude_desktop_sources(tmp_path: Path) -> None:
    chatgpt_root = tmp_path / "chatgpt"
    claude_desktop_root = tmp_path / "claude-desktop"
    chatgpt_root.mkdir()
    claude_desktop_root.mkdir()
    (chatgpt_root / "conversations.json").write_text(
        (FIXTURES / "importer_chatgpt_sample_conversations.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (claude_desktop_root / "claude_ai_conversations.json").write_text(
        (FIXTURES / "importer_claude_desktop_sample.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    candidates = scan_candidates(
        source_types=["chatgpt", "claude-desktop"],
        roots_by_source={
            "chatgpt": [chatgpt_root],
            "claude-desktop": [claude_desktop_root],
        },
    )

    assert {candidate.source_type for candidate in candidates} == {
        "chatgpt",
        "claude-desktop",
    }


def test_scan_candidates_omits_broad_export_sources_without_explicit_source(
    monkeypatch, tmp_path: Path
) -> None:
    downloads = tmp_path / "Downloads"
    desktop = tmp_path / "Desktop"
    downloads.mkdir()
    desktop.mkdir()
    (downloads / "conversations.json").write_text(
        (FIXTURES / "importer_chatgpt_sample_conversations.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (desktop / "claude_ai_conversations.json").write_text(
        (FIXTURES / "importer_claude_desktop_sample.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr("eve_client.importer.adapters.Path.home", lambda: tmp_path)

    assert scan_candidates() == []


def test_scan_candidates_requires_explicit_roots_for_broad_export_sources(
    monkeypatch, tmp_path: Path
) -> None:
    downloads = tmp_path / "Downloads"
    desktop = tmp_path / "Desktop"
    downloads.mkdir()
    desktop.mkdir()
    (downloads / "conversations.json").write_text(
        (FIXTURES / "importer_chatgpt_sample_conversations.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (desktop / "claude_ai_conversations.json").write_text(
        (FIXTURES / "importer_claude_desktop_sample.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr("eve_client.importer.adapters.Path.home", lambda: tmp_path)

    assert scan_candidates(source_types=["chatgpt", "claude-desktop"]) == []


def test_broad_export_adapters_do_not_recurse_explicit_roots(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "conversations.json").write_text(
        (FIXTURES / "importer_chatgpt_sample_conversations.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (nested / "claude_ai_conversations.json").write_text(
        (FIXTURES / "importer_claude_desktop_sample.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    candidates = scan_candidates(
        source_types=["chatgpt", "claude-desktop"],
        roots_by_source={
            "chatgpt": [root],
            "claude-desktop": [root],
        },
    )

    assert candidates == []


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
