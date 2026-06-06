"""Eve memory CLI sub-commands (NW-017)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from eve_client.auth import CredentialStoreUnavailableError, LocalCredentialStore
from eve_client.config import resolve_api_base_url, resolve_config
from eve_client.oauth_device import refresh_auth0_token

memory_app = typer.Typer(name="memory", help="Search and inspect Eve memories.")
console = Console()

_SEARCH_PATH = "/memory/search"
_HEALTH_PATH = "/health"
_REQUEST_TIMEOUT = 30.0
_SUPPORTED_TOOLS = ("claude-code", "gemini-cli", "codex-cli")


class AmbiguousMemoryAuthSelectionError(RuntimeError):
    def __init__(self, tools: tuple[str, ...]) -> None:
        self.tools = tools
        super().__init__("multiple stored Eve credentials are available")


def _tool_auth_precedence(tool_name: str) -> tuple[str, str]:
    if tool_name in {"claude-code", "gemini-cli"}:
        return ("api-key", "oauth")
    return ("oauth", "api-key")


def _auth_headers_for_tool(config, tool_name: str) -> dict[str, str]:  # noqa: ANN001
    """Build auth headers for a single tool from stored credentials.

    Uses tool-aware credential precedence and falls back to the other supported
    credential type if the preferred one is unavailable.
    """
    import time

    store = LocalCredentialStore(
        config.state_dir, allow_file_fallback=config.allow_file_secret_fallback
    )

    def _oauth_headers() -> dict[str, str]:
        try:
            session, _source = store.get_oauth_session(tool_name)  # type: ignore[arg-type]
            if session and session.access_token:
                if session.expires_at and session.expires_at < time.time():
                    if session.refresh_token:
                        try:
                            refreshed = refresh_auth0_token(
                                session.refresh_token,
                                session.client_id,
                            )
                            store.save_oauth_session(tool_name, refreshed)  # type: ignore[arg-type]
                            return {"Authorization": f"Bearer {refreshed.access_token}"}
                        except Exception:
                            return {}
                    return {}
                return {"Authorization": f"Bearer {session.access_token}"}
        except CredentialStoreUnavailableError:
            return {}
        return {}

    def _api_key_headers() -> dict[str, str]:
        try:
            api_key, _source = store.get_api_key(tool_name)  # type: ignore[arg-type]
            if api_key:
                return {"X-API-Key": api_key}
        except CredentialStoreUnavailableError:
            return {}
        return {}

    resolvers = {
        "oauth": _oauth_headers,
        "api-key": _api_key_headers,
    }

    for credential_kind in _tool_auth_precedence(tool_name):
        headers = resolvers[credential_kind]()
        if headers:
            return headers
    return {}


def _get_auth_headers(config, tool_name: str | None = None) -> dict[str, str]:  # noqa: ANN001
    """Build auth headers from stored credentials.

    If a tool is provided, use only that tool's credentials.
    Otherwise, require the credential set to be unambiguous.
    """
    if tool_name:
        return _auth_headers_for_tool(config, tool_name)

    matches: list[dict[str, str]] = []
    matched_tools: list[str] = []
    for candidate in _SUPPORTED_TOOLS:
        headers = _auth_headers_for_tool(config, candidate)
        if headers:
            matches.append(headers)
            matched_tools.append(candidate)

    if len(matches) > 1:
        raise AmbiguousMemoryAuthSelectionError(tuple(matched_tools))

    if matches:
        return matches[0]

    return {}


def _api_base_url(config) -> str:  # noqa: ANN001
    return resolve_api_base_url(config.mcp_base_url).rstrip("/")


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = _REQUEST_TIMEOUT,
) -> tuple[int, dict[str, Any] | None]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310
            return response.status, _parse_json(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, _parse_json(exc.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"error": str(exc)}


def _parse_json(body: str) -> dict[str, Any] | None:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


@memory_app.command()
def search(
    query: str = typer.Argument(..., help="Search query text"),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=100, help="Max results (1-100)"),
    context: str = typer.Option(
        "naya", "--context", "-c", help="Context scope (naya, personal, es)"
    ),
    store: str = typer.Option(
        "semantic",
        "--store",
        "-s",
        help="Store to search (semantic, episodic, preference, all)",
    ),
    tool: str | None = typer.Option(
        None,
        "--tool",
        help="Credential source to use when multiple Eve client logins are configured (claude-code, gemini-cli, codex-cli)",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Search memories via the Eve REST API."""
    _VALID_CONTEXTS = {"naya", "personal", "es"}
    _VALID_STORES = {"semantic", "episodic", "preference", "all"}
    _VALID_TOOLS = set(_SUPPORTED_TOOLS)
    if context.lower() not in _VALID_CONTEXTS:
        raise typer.BadParameter(f"context must be one of {_VALID_CONTEXTS}, got '{context}'")
    if store.lower() not in _VALID_STORES:
        raise typer.BadParameter(f"store must be one of {_VALID_STORES}, got '{store}'")
    if tool and tool.lower() not in _VALID_TOOLS:
        raise typer.BadParameter(f"tool must be one of {_VALID_TOOLS}, got '{tool}'")
    context = context.upper()
    store = store.lower()
    tool = tool.lower() if tool else None
    config = resolve_config()
    try:
        auth_headers = _get_auth_headers(config, tool)
    except AmbiguousMemoryAuthSelectionError as exc:
        joined_tools = ", ".join(exc.tools)
        console.print(
            "[yellow]Multiple stored credentials found for "
            f"{joined_tools}. Re-run with `--tool` to choose one.[/yellow]"
        )
        raise typer.Exit(1)
    if not auth_headers:
        if tool:
            console.print(
                f"[yellow]No stored credentials found for `{tool}`. Run `eve auth login --tool {tool}` first.[/yellow]"
            )
        else:
            console.print("[yellow]No stored credentials found. Run `eve auth login` first.[/yellow]")
        raise typer.Exit(1)

    url = _api_base_url(config) + _SEARCH_PATH
    payload = {
        "query": query,
        "limit": limit,
        "context": context,
        "store": store,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **auth_headers,
    }
    body = json.dumps(payload).encode("utf-8")
    status, data = _http_request(url, method="POST", headers=headers, body=body)

    if status == 0:
        error_msg = data.get("error", "Unknown error") if data else "Connection failed"
        console.print(f"[red]Connection error:[/red] {error_msg}")
        raise typer.Exit(1)

    if status == 401:
        console.print(
            "[yellow]Authentication failed. Run `eve auth login` to refresh credentials.[/yellow]"
        )
        raise typer.Exit(1)

    if status != 200:
        detail = ""
        if data and "detail" in data:
            detail = f": {data['detail']}"
        console.print(f"[red]API error (HTTP {status}){detail}[/red]")
        raise typer.Exit(1)

    results = (data or {}).get("results", [])

    if json_output:
        console.print(json.dumps(results, indent=2, default=str))
        return

    if not results:
        console.print("[dim]No memories found.[/dim]")
        return

    table = Table(title=f"Memories matching: {query}", show_lines=True)
    table.add_column("Score", style="cyan", width=6)
    table.add_column("Text", style="white", ratio=3)
    table.add_column("Source", style="green", width=20)
    table.add_column("Created", style="dim", width=12)

    for item in results:
        chunk = item.get("chunk", {})
        similarity = item.get("score") or item.get("similarity", 0)
        text = chunk.get("text", "")
        if len(text) > 200:
            text = text[:197] + "..."
        source = chunk.get("source", "")
        created = chunk.get("created_at", "")[:10]  # date portion only
        table.add_row(f"{similarity:.2f}", text, source, created)

    console.print(table)
    console.print(f"[dim]{len(results)} result(s)[/dim]")


@memory_app.command()
def status(
    tool: str | None = typer.Option(
        None,
        "--tool",
        help="Credential source to use when multiple Eve client logins are configured (claude-code, gemini-cli, codex-cli)",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Show memory service health and status."""
    config = resolve_config()
    url = _api_base_url(config) + _HEALTH_PATH
    headers = {"Accept": "application/json"}

    # Health endpoint is typically unauthenticated, but include auth if available
    tool = tool.lower() if tool else None
    if tool and tool not in _SUPPORTED_TOOLS:
        raise typer.BadParameter(f"tool must be one of {set(_SUPPORTED_TOOLS)}, got '{tool}'")
    try:
        auth_headers = _get_auth_headers(config, tool)
    except AmbiguousMemoryAuthSelectionError as exc:
        joined_tools = ", ".join(exc.tools)
        console.print(
            "[yellow]Multiple stored credentials found for "
            f"{joined_tools}. Continuing with an unauthenticated health request. "
            "Re-run with `--tool` if you need a specific credential.[/yellow]"
        )
        auth_headers = {}
    headers.update(auth_headers)

    status_code, data = _http_request(url, headers=headers)

    if status_code == 0:
        error_msg = data.get("error", "Unknown error") if data else "Connection failed"
        console.print(f"[red]Connection error:[/red] {error_msg}")
        raise typer.Exit(1)

    if status_code != 200:
        console.print(f"[red]Health check failed (HTTP {status_code})[/red]")
        raise typer.Exit(1)

    if not data:
        console.print("[red]Empty health response[/red]")
        raise typer.Exit(1)

    if json_output:
        console.print(json.dumps(data, indent=2, default=str))
        return

    overall = data.get("status", "unknown")
    style = "green" if overall == "ok" else "yellow" if overall == "degraded" else "red"

    lines = [f"[bold]Status:[/bold] [{style}]{overall}[/{style}]"]

    for key in ("database", "pgvector", "embedding_provider", "embedding_model"):
        if key in data:
            lines.append(f"[bold]{key}:[/bold] {data[key]}")

    memory_counts = data.get("memory_counts", {})
    if memory_counts:
        lines.append("")
        lines.append("[bold]Memory counts:[/bold]")
        for ctx_name, counts in memory_counts.items():
            if isinstance(counts, dict):
                parts = [f"{k}={v}" for k, v in counts.items() if v]
                if parts:
                    lines.append(f"  {ctx_name}: {', '.join(parts)}")
                else:
                    lines.append(f"  {ctx_name}: (empty)")

    panel = Panel("\n".join(lines), title="Eve Memory Service", border_style=style)
    console.print(panel)
