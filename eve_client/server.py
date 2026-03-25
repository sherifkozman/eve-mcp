"""Eve Memory MCP Server — local stdio bridge.

Proxies tool calls to the hosted Eve Memory service at mcp.evemem.com.
Provides local tool discovery for Glama quality inspection and MCP clients
that prefer stdio transport.

Install:  pip install 'eve-client[server]'
Run:      eve-mcp-server
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "eve-memory",
    instructions=(
        "Persistent memory infrastructure for AI agents. "
        "Search, store, and manage durable knowledge "
        "across sessions. Works with Claude Code, "
        "Gemini CLI, Codex CLI, and any MCP client."
    ),
)

EVE_API_URL = os.getenv("EVE_MCP_BASE_URL", "https://mcp.evemem.com/mcp")


def _get_api_key() -> str:
    """Resolve Eve API key from environment or local credential store."""
    key = os.getenv("EVE_API_KEY", "")
    if key:
        return key
    # Try reading from eve-client's local credential store
    try:
        from eve_client.auth import LocalCredentialStore
        from eve_client.config import resolve_config

        cfg = resolve_config()
        store = LocalCredentialStore(cfg.state_dir, allow_file_fallback=True)
        for tool in ("claude-code", "gemini-cli", "codex-cli"):
            try:
                api_key, _source = store.get_api_key(tool)  # type: ignore[arg-type]
                if api_key:
                    return api_key
            except Exception:
                continue
    except Exception:
        pass
    return ""


async def _proxy(tool_name: str, arguments: dict[str, Any]) -> str:
    """Forward a tool call to the remote Eve Memory API."""
    api_key = _get_api_key()
    if not api_key:
        return json.dumps(
            {
                "error": "authentication_required",
                "message": (
                    "Eve Memory API key not configured. "
                    "Run `eve connect` to set up credentials, "
                    "or set EVE_API_KEY environment variable."
                ),
                "docs": "https://evemem.com",
            }
        )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                EVE_API_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if "result" in data:
                content = data["result"].get("content", [])
                if content:
                    return content[0].get("text", json.dumps(content))
            if "error" in data:
                return json.dumps(data["error"])
            return resp.text
    except Exception as exc:
        return json.dumps({"error": "proxy_error", "message": str(exc)})


# ---------------------------------------------------------------------------
# Tools — schemas match the hosted Eve Memory API at mcp.evemem.com
# ---------------------------------------------------------------------------


@mcp.tool()
async def memory_search(
    query: str,
    context: str = "all",
    store: str = "semantic",
    category: str | None = None,
    limit: int = 10,
    min_similarity: float = 0.7,
    source_agent: str = "eve-mcp-local",
    visibility: str = "PERSONAL",
) -> str:
    """Search memory using a natural language query.

    Args:
        query: Natural language question or keywords
        context: Context scope — personal, naya, es, or all
        store: Memory store — semantic, episodic, learned_rules, preference, or all
        category: Filter by memory category (e.g. architecture, security)
        limit: Max number of results (default 10)
        min_similarity: Minimum confidence threshold 0.0–1.0 (default 0.7)
        source_agent: Agent/tool calling this API (audit trail)
        visibility: Visibility scope — PERSONAL or SHARED
    """
    return await _proxy(
        "memory_search",
        {
            k: v
            for k, v in {
                "query": query,
                "context": context,
                "store": store,
                "category": category,
                "limit": limit,
                "min_similarity": min_similarity,
                "source_agent": source_agent,
                "visibility": visibility,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_store(
    text: str = "",
    source: str = "",
    context: str = "personal",
    store: str = "auto",
    category: str | None = None,
    entity_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    key: str | None = None,
    value: str | None = None,
    confidence: float = 1.0,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Store a piece of knowledge into memory.

    Args:
        text: Text content to remember
        source: Source origin of the memory
        context: Context scope — personal, naya, or es
        store: Target store — auto, semantic, episodic, preference, or learned_rules
        category: Category for organization (e.g. architecture)
        entity_refs: List of related entity names
        metadata: Extra context dictionary
        key: Key for preference store
        value: Value for preference store
        confidence: Confidence score 0.0–1.0 (default 1.0)
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_store",
        {
            k: v
            for k, v in {
                "text": text,
                "source": source,
                "context": context,
                "store": store,
                "category": category,
                "entity_refs": entity_refs,
                "metadata": metadata,
                "key": key,
                "value": value,
                "confidence": confidence,
                "source_agent": source_agent,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_extract(
    transcript: str,
    source: str = "mcp_client",
    context: str = "personal",
    auto_store: bool = False,
    min_importance: int = 4,
    use_extraction: bool = False,
    session_id: str | None = None,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Extract memorable facts, preferences, and events from transcript text.

    Batch-processes transcript using AI classification. Optionally auto-stores
    extracted items with dedup checking.

    Args:
        transcript: Raw transcript text to extract memories from
        source: Source identifier (e.g. claude_code, gemini_cli)
        context: Context scope — personal, naya, or es
        auto_store: If true, automatically store extracted items with dedup
        min_importance: Only extract items at or above this importance (1–10)
        use_extraction: If true, use atomic fact extraction (SPO fields)
        session_id: Optional session UUID for provenance tracking
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_extract",
        {
            k: v
            for k, v in {
                "transcript": transcript,
                "source": source,
                "context": context,
                "auto_store": auto_store,
                "min_importance": min_importance,
                "use_extraction": use_extraction,
                "session_id": session_id,
                "source_agent": source_agent,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_forget(
    chunk_id: str,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Soft-delete (retract) a semantic memory chunk by its chunk_id.

    Sets retracted_at so the chunk is excluded from future searches
    but preserved for audit purposes.

    Args:
        chunk_id: UUID of the semantic chunk to forget
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_forget",
        {
            "chunk_id": chunk_id,
            "source_agent": source_agent,
        },
    )


@mcp.tool()
async def memory_update(
    chunk_id: str,
    text: str | None = None,
    metadata: dict[str, Any] | None = None,
    entity_refs: list[str] | None = None,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Update an existing memory. If text changes, facts are re-extracted.

    Only provided fields are updated; omitted fields keep existing values.

    Args:
        chunk_id: UUID of the semantic chunk to update
        text: New text content (triggers re-embedding if changed)
        metadata: New metadata dictionary (replaces existing)
        entity_refs: New entity references list (replaces existing)
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_update",
        {
            k: v
            for k, v in {
                "chunk_id": chunk_id,
                "text": text,
                "metadata": metadata,
                "entity_refs": entity_refs,
                "source_agent": source_agent,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_session_start(
    summary: str,
    context: str = "personal",
    session_id: str | None = None,
    details: dict[str, Any] | None = None,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Log the beginning of a conversation or work session.

    Args:
        summary: Brief description of the session goal
        context: Context scope — personal, naya, or es
        session_id: Optional UUID (auto-generated if omitted)
        details: Optional dictionary of context
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_session_start",
        {
            k: v
            for k, v in {
                "summary": summary,
                "context": context,
                "session_id": session_id,
                "details": details,
                "source_agent": source_agent,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_session_end(
    summary: str,
    context: str = "personal",
    session_id: str | None = None,
    details: dict[str, Any] | None = None,
    status: str | None = None,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Log the end of a session. Triggers the AI learning pipeline.

    Args:
        summary: Brief description of the outcome
        context: Context scope — personal, naya, or es
        session_id: Optional UUID matching the session_start
        details: Optional dictionary of context
        status: Session outcome — success, failure, or unknown
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_session_end",
        {
            k: v
            for k, v in {
                "summary": summary,
                "context": context,
                "session_id": session_id,
                "details": details,
                "status": status,
                "source_agent": source_agent,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_get_preferences(
    context: str = "personal",
    category: str | None = None,
    scope: str = "user",
    source_agent: str = "eve-mcp-local",
) -> str:
    """Get stored preferences for a context.

    Use at session start to load behavioral preferences.

    Args:
        context: Context scope — personal, naya, or es
        category: Optional category filter (e.g. architecture, communication_style)
        scope: Preference scope — user or tenant
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_get_preferences",
        {
            k: v
            for k, v in {
                "context": context,
                "category": category,
                "scope": scope,
                "source_agent": source_agent,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_feedback(
    chunk_id: str,
    outcome: str,
    context: str | None = None,
    correction_text: str | None = None,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Record consumer feedback on a retrieved memory chunk.

    Args:
        chunk_id: UUID of the semantic chunk being rated
        outcome: Feedback outcome — helpful, not_helpful, or correction
        context: Optional query or context text for this retrieval
        correction_text: Corrected text when outcome is correction
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_feedback",
        {
            k: v
            for k, v in {
                "chunk_id": chunk_id,
                "outcome": outcome,
                "context": context,
                "correction_text": correction_text,
                "source_agent": source_agent,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_ingest(
    file_path: str,
    source_type_hint: str | None = None,
    source_priority: int = 1,
    batch_size: int = 100,
    dry_run: bool = False,
    predicate_allowlist: list[str] | None = None,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Start a batch ingestion job from a conversation export file.

    Triggers asynchronous processing of an export file (ChatGPT, Claude,
    Gemini, etc.) through the ingestion pipeline.

    Args:
        file_path: Absolute path to the export file
        source_type_hint: Override format auto-detection
            (chatgpt, claude_code, etc.)
        source_priority: Priority for stored claims (1=bulk, 5=explicit)
        batch_size: Number of turns per extraction batch (default 100)
        dry_run: If true, count facts without writing to memory
        predicate_allowlist: Only store claims with these predicates
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_ingest",
        {
            k: v
            for k, v in {
                "file_path": file_path,
                "source_type_hint": source_type_hint,
                "source_priority": source_priority,
                "batch_size": batch_size,
                "dry_run": dry_run,
                "predicate_allowlist": predicate_allowlist,
                "source_agent": source_agent,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_ingest_url(
    url: str,
    context: str = "personal",
    importance: int | None = None,
    metadata: dict[str, Any] | None = None,
    entity_refs: list[str] | None = None,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Ingest a web URL into memory.

    Fetches the web page, extracts clean content, chunks it, and stores
    each chunk. Full SSRF protection applied.

    Args:
        url: HTTPS URL to ingest
        context: Context scope — personal, naya, or es
        importance: Override importance score (1–10)
        metadata: Extra metadata to attach to each chunk
        entity_refs: List of related entities
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_ingest_url",
        {
            k: v
            for k, v in {
                "url": url,
                "context": context,
                "importance": importance,
                "metadata": metadata,
                "entity_refs": entity_refs,
                "source_agent": source_agent,
            }.items()
            if v is not None
        },
    )


@mcp.tool()
async def memory_ingest_status(
    job_id: str,
    source_agent: str = "eve-mcp-local",
) -> str:
    """Poll the status of a running ingestion job.

    Args:
        job_id: The job ID returned from memory_ingest
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_ingest_status",
        {
            "job_id": job_id,
            "source_agent": source_agent,
        },
    )


@mcp.tool()
async def memory_pre_compact(
    session_id: str,
    messages: list[dict[str, str]],
    context: str = "personal",
    source_agent: str = "eve-mcp-local",
) -> str:
    """Distill raw conversation messages into memories before context compaction.

    Extracts decisions, preferences, and learned patterns via AI.
    Use when you have raw conversation text to process before compaction.

    Args:
        session_id: Current session UUID
        messages: List of {role, content} message dicts
        context: Context scope — personal, naya, or es
        source_agent: Agent/tool calling this API (audit trail)
    """
    return await _proxy(
        "memory_pre_compact",
        {
            "session_id": session_id,
            "messages": messages,
            "context": context,
            "source_agent": source_agent,
        },
    )


def main() -> None:
    """Entry point for the eve-mcp-server command."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
