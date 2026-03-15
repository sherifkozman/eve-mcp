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

memory_app = typer.Typer(name="memory", help="Search and inspect Eve memories.")
console = Console()

_SEARCH_PATH = "/memory/search"
_HEALTH_PATH = "/health"
_REQUEST_TIMEOUT = 30.0


def _get_auth_headers(config) -> dict[str, str]:  # noqa: ANN001
    """Build auth headers from stored credentials.

    Tries OAuth bearer token first, then API key, for each supported tool.
    """
    store = LocalCredentialStore(
        config.state_dir, allow_file_fallback=config.allow_file_secret_fallback
    )
    tools = ("claude-code", "gemini-cli", "codex-cli")
    for tool_name in tools:
        try:
            session, _source = store.get_oauth_session(tool_name)  # type: ignore[arg-type]
            if session and session.access_token:
                return {"Authorization": f"Bearer {session.access_token}"}
        except CredentialStoreUnavailableError:
            pass
        try:
            api_key, _source = store.get_api_key(tool_name)  # type: ignore[arg-type]
            if api_key:
                return {"X-API-Key": api_key}
        except CredentialStoreUnavailableError:
            pass
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
            return response.status, _parse_json(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, _parse_json(exc.read().decode("utf-8"))
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
    limit: int = typer.Option(10, "--limit", "-n", help="Max results to return (1-100)"),
    context: str = typer.Option(
        "naya", "--context", "-c", help="Context scope (naya, personal, es)"
    ),
    store: str = typer.Option(
        "semantic", "--store", "-s", help="Store to search (semantic, episodic, all)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Search memories via the Eve REST API."""
    config = resolve_config()
    auth_headers = _get_auth_headers(config)
    if not auth_headers:
        console.print("[yellow]No stored credentials found. Run `eve auth login` first.[/yellow]")
        raise typer.Exit(1)

    url = _api_base_url(config) + _SEARCH_PATH
    payload = {
        "query": query,
        "limit": limit,
        "context": context.upper(),
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
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Show memory service health and status."""
    config = resolve_config()
    url = _api_base_url(config) + _HEALTH_PATH
    headers = {"Accept": "application/json"}

    # Health endpoint is typically unauthenticated, but include auth if available
    auth_headers = _get_auth_headers(config)
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
