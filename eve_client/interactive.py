"""Interactive installer prompting for Eve client."""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from eve_client.models import DetectedTool, InstallPlan, ToolName
from eve_client.tty import stdin_is_tty as _stdin_is_tty

console = Console()


# ---------------------------------------------------------------------------
# Task 1: Tool selection prompt
# ---------------------------------------------------------------------------


def prompt_tool_selection(detected_tools: list[DetectedTool]) -> list[str]:
    """Prompt the user to select which detected tools to configure.

    Returns a list of tool names (e.g. ["claude-code", "gemini-cli"]).
    Auto-selects if only one tool has a binary. Returns empty if none detected.
    """
    available = [t for t in detected_tools if t.binary_found]
    if not available:
        return []
    if len(available) == 1:
        return [available[0].name]

    table = Table(title="Detected Tools")
    table.add_column("#", style="bold")
    table.add_column("Tool")
    table.add_column("Hooks")
    table.add_column("Config Format")
    for i, tool in enumerate(available, 1):
        table.add_row(
            str(i),
            tool.name,
            "Yes" if tool.supports_hooks else "No",
            tool.config_format.upper(),
        )
    console.print(table)

    raw = Prompt.ask(
        "Select tools to configure (comma-separated numbers, or 'all')",
        default="all",
    )
    if raw.strip().lower() == "all":
        return [t.name for t in available]

    indices: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part)
            if 1 <= idx <= len(available):
                indices.append(idx - 1)
    if not indices:
        return [t.name for t in available]
    return [available[i].name for i in indices]


# ---------------------------------------------------------------------------
# Task 2: Per-tool option prompts
# ---------------------------------------------------------------------------

# Capabilities per tool
_TOOL_CAPS: dict[str, dict[str, bool]] = {
    "claude-code": {"has_hooks": True, "has_prompt_scope": False, "has_oauth": True},
    "gemini-cli": {"has_hooks": True, "has_prompt_scope": True, "has_oauth": True},
    "codex-cli": {"has_hooks": False, "has_prompt_scope": False, "has_oauth": False},
    "claude-desktop": {"has_hooks": False, "has_prompt_scope": False, "has_oauth": False},
}


def prompt_tool_options(tool_name: str) -> dict[str, object]:
    """Prompt for per-tool install options. Returns dict with keys based on tool capabilities."""
    caps = _TOOL_CAPS.get(tool_name, {})
    opts: dict[str, object] = {}

    # Auth mode
    if caps.get("has_oauth"):
        auth = Prompt.ask(
            f"[{tool_name}] Auth mode",
            choices=["api-key", "oauth"],
            default="api-key",
        )
    else:
        auth = "api-key"
    opts["auth_mode"] = auth

    # Prompt scope (Gemini only currently)
    if caps.get("has_prompt_scope"):
        scope = Prompt.ask(
            f"[{tool_name}] Prompt scope",
            choices=["global", "project"],
            default="global",
        )
        opts["prompt_scope"] = scope

    # Hooks
    if caps.get("has_hooks"):
        hooks = Confirm.ask(f"[{tool_name}] Enable Eve hooks?", default=True)
        opts["hooks_enabled"] = hooks

    return opts


# ---------------------------------------------------------------------------
# Task 3: Plan preview and apply confirmation
# ---------------------------------------------------------------------------


def preview_and_confirm(plan: InstallPlan) -> bool:
    """Display the install plan and ask for confirmation. Returns True if user approves."""
    all_actions = [a for tp in plan.tool_plans for a in tp.actions]
    if not all_actions:
        console.print("[yellow]No actions to apply — nothing to do.[/yellow]")
        return False

    console.print(Panel("[bold]Eve Install Plan Preview[/bold]", style="green"))
    console.print(f"MCP endpoint: [bold]{plan.mcp_base_url}[/bold]")
    console.print(f"Environment: [bold]{plan.environment}[/bold]\n")

    for tool_plan in plan.tool_plans:
        if not tool_plan.actions:
            continue
        console.print(f"[bold]{tool_plan.tool}[/bold] (auth: {tool_plan.auth_mode})")
        for action in tool_plan.actions:
            path_str = f" -> {action.path}" if action.path else ""
            console.print(f"  - {action.summary}{path_str}")
        console.print()

    return Confirm.ask("Apply this plan?", default=False)


# ---------------------------------------------------------------------------
# Task 7: Repair/uninstall prompt
# ---------------------------------------------------------------------------


def prompt_repair_or_uninstall() -> str:
    """Ask user whether to repair, uninstall, or skip.

    Called by run_interactive_install() when a tool already has Eve configured.
    - repair: proceed with normal install flow (overwrite existing config)
    - uninstall: remove Eve config for this tool
    - skip: leave this tool unchanged
    """
    return Prompt.ask(
        "Eve is already configured for this tool. What would you like to do?",
        choices=["repair", "uninstall", "skip"],
        default="repair",
    )


# ---------------------------------------------------------------------------
# Task 4: Interactive orchestrator + InteractiveResult + prompt_api_key
# ---------------------------------------------------------------------------


@dataclass
class InteractiveResult:
    """Result from the interactive flow: selected tools and their options."""

    selected_tools: list[ToolName]
    auth_overrides: dict[ToolName, str] = field(default_factory=dict)
    prompt_scope_overrides: dict[ToolName, str] = field(default_factory=dict)
    hook_overrides: dict[ToolName, bool] = field(default_factory=dict)
    provided_secrets: dict[ToolName, str] = field(default_factory=dict)
    uninstall_tools: list[ToolName] = field(default_factory=list)


def prompt_api_key(tool_name: str) -> str | None:
    """Prompt for an API key. Returns None if user provides empty input."""
    key = Prompt.ask(
        f"[{tool_name}] Enter your Eve API key (or press Enter to use existing)",
        default="",
        show_default=False,
    )
    return key.strip() or None


def run_interactive_install(
    detected_tools: list[DetectedTool],
) -> InteractiveResult | None:
    """Run the full interactive install flow.

    Returns InteractiveResult with user selections, or None if cancelled/empty.
    Checks if selected tools already have Eve configured and offers repair/uninstall/skip.
    """
    selected = prompt_tool_selection(detected_tools)
    if not selected:
        console.print("[yellow]No tools selected or detected.[/yellow]")
        return None

    # Build lookup for config_exists
    detected_by_name = {t.name: t for t in detected_tools}

    auth_overrides: dict[str, str] = {}
    prompt_scope_overrides: dict[str, str] = {}
    hook_overrides: dict[str, bool] = {}
    provided_secrets: dict[str, str] = {}
    uninstall_list: list[str] = []
    install_list: list[str] = []

    for tool_name in selected:
        det = detected_by_name.get(tool_name)
        # Check if already configured → offer repair/uninstall/skip
        if det and det.config_exists:
            action = prompt_repair_or_uninstall()
            if action == "skip":
                continue
            if action == "uninstall":
                uninstall_list.append(tool_name)
                continue
            # "repair" falls through to normal config flow

        console.print(f"\n[bold]Configuring {tool_name}[/bold]")
        opts = prompt_tool_options(tool_name)
        auth_mode = str(opts.get("auth_mode", "api-key"))
        auth_overrides[tool_name] = auth_mode
        if "prompt_scope" in opts:
            prompt_scope_overrides[tool_name] = str(opts["prompt_scope"])
        if "hooks_enabled" in opts:
            hook_overrides[tool_name] = bool(opts["hooks_enabled"])

        # Prompt for API key when auth_mode is api-key
        if auth_mode == "api-key":
            key = prompt_api_key(tool_name)
            if key:
                provided_secrets[tool_name] = key

        install_list.append(tool_name)

    if not install_list and not uninstall_list:
        console.print("[yellow]No tools to configure.[/yellow]")
        return None

    return InteractiveResult(
        selected_tools=install_list,
        auth_overrides=auth_overrides,
        prompt_scope_overrides=prompt_scope_overrides,
        hook_overrides=hook_overrides,
        provided_secrets=provided_secrets,
        uninstall_tools=uninstall_list,
    )


# ---------------------------------------------------------------------------
# Task 5: should_use_interactive
# ---------------------------------------------------------------------------


def should_use_interactive(
    *,
    tool_flag: list[str] | None,
    all_flag: bool,
    non_interactive: bool,
) -> bool:
    """Return True when the install command should enter interactive mode."""
    if non_interactive:
        return False
    if tool_flag or all_flag:
        return False
    return _stdin_is_tty()
