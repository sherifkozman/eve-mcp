"""Central configuration for Eve client installer."""

from __future__ import annotations

import json
import os
import platform
import warnings
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

DEFAULT_MCP_BASE_URL = "https://mcp.evemem.com/mcp"
DEFAULT_API_BASE_URL = "https://api.evemem.com"
DEFAULT_UI_BASE_URL = "https://evemem.com"
DEFAULT_OAUTH_DOMAIN = "evemem.us.auth0.com"
DEFAULT_OAUTH_CLIENT_ID = "65uRnH5NGZxeAbIpzf5Lp6EjDp60sHQy"
OFFICIAL_UI_ORIGINS = {
    "https://evemem.com",
    "https://www.evemem.com",
}
CONFIG_ENV_VAR = "EVE_MCP_BASE_URL"
UI_CONFIG_ENV_VAR = "EVE_UI_BASE_URL"
OAUTH_DOMAIN_ENV_VAR = "EVE_OAUTH_DOMAIN"
OAUTH_CLIENT_ID_ENV_VAR = "EVE_OAUTH_CLIENT_ID"
ALLOW_CUSTOM_UI_CONFIG_ENV_VAR = "EVE_ALLOW_CUSTOM_UI_BASE_URL"
FEATURE_FLAG_ENV_VAR = "EVE_ENABLE_CLAUDE_DESKTOP"
CODEX_DISABLE_ENV_VAR = "EVE_DISABLE_CODEX"
MAX_CONFIG_BYTES = 64 * 1024
MCP_SERVER_NAME = "eve-memory"
CONFIG_VERSION = 1


@dataclass(slots=True)
class LocalClientConfig:
    config_version: int
    mcp_base_url: str | None
    ui_base_url: str | None
    feature_claude_desktop: bool
    codex_enabled: bool
    codex_source: str
    allow_file_secret_fallback: bool

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> LocalClientConfig:
        raw_version = payload.get("config_version")
        version = (
            raw_version if isinstance(raw_version, int) and raw_version > 0 else CONFIG_VERSION
        )
        raw_mcp_base_url = payload.get("mcp_base_url")
        mcp_base_url = raw_mcp_base_url.strip() if isinstance(raw_mcp_base_url, str) else None
        raw_ui_base_url = payload.get("ui_base_url")
        ui_base_url = raw_ui_base_url.strip() if isinstance(raw_ui_base_url, str) else None
        raw_codex_enabled = payload.get("codex_enabled")
        if raw_codex_enabled is None and "feature_codex_cli" in payload:
            raw_codex_enabled = payload.get("feature_codex_cli")
            warnings.warn(
                "feature_codex_cli is deprecated; use codex_enabled instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        if raw_codex_enabled is None:
            codex_enabled = False
            codex_source = "default"
        else:
            codex_enabled = _is_truthy(raw_codex_enabled)
            codex_source = "config" if "codex_enabled" in payload else "legacy"
        return cls(
            config_version=version,
            mcp_base_url=mcp_base_url or None,
            ui_base_url=ui_base_url or None,
            feature_claude_desktop=_is_truthy(payload.get("feature_claude_desktop")),
            codex_enabled=codex_enabled,
            codex_source=codex_source,
            allow_file_secret_fallback=_is_truthy(payload.get("allow_file_secret_fallback")),
        )


@dataclass(slots=True)
class ResolvedConfig:
    config_dir: Path
    config_path: Path
    state_dir: Path
    project_root: Path
    mcp_base_url: str
    mcp_server_name: str
    environment: str
    feature_claude_desktop: bool
    codex_enabled: bool
    codex_source: str
    allow_file_secret_fallback: bool
    ui_base_url: str = DEFAULT_UI_BASE_URL
    blocked_ui_base_url: str | None = None
    oauth_domain: str = DEFAULT_OAUTH_DOMAIN
    oauth_client_id: str = DEFAULT_OAUTH_CLIENT_ID


def _darwin_native_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "eve"


def _darwin_use_native_dirs() -> bool:
    native = _darwin_native_dir()
    config_xdg = os.environ.get("XDG_CONFIG_HOME")
    state_xdg = os.environ.get("XDG_STATE_HOME")
    config_candidate = Path(config_xdg) / "eve" if config_xdg else None
    state_candidate = Path(state_xdg) / "eve" if state_xdg else None
    if not (config_xdg or state_xdg):
        return True
    return native.exists() and not any(
        candidate is not None and candidate.exists()
        for candidate in (config_candidate, state_candidate)
    )


def get_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    system = platform.system()
    if system == "Darwin":
        native = _darwin_native_dir()
        if xdg and not _darwin_use_native_dirs():
            return Path(xdg) / "eve"
        return native
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "eve"
    if xdg:
        return Path(xdg) / "eve"
    return Path.home() / ".config" / "eve"


def get_state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    system = platform.system()
    if system == "Darwin":
        native = _darwin_native_dir()
        if xdg and not _darwin_use_native_dirs():
            return Path(xdg) / "eve"
        return native
    if system == "Windows":
        local_appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if local_appdata:
            return Path(local_appdata) / "eve" / "state"
    if xdg:
        return Path(xdg) / "eve"
    return Path.home() / ".local" / "state" / "eve"


def get_importer_ledger_path() -> Path:
    return get_state_dir() / "importer.sqlite3"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def load_local_config() -> LocalClientConfig:
    path = get_config_path()
    if not path.exists():
        return LocalClientConfig(
            config_version=CONFIG_VERSION,
            mcp_base_url=None,
            ui_base_url=None,
            feature_claude_desktop=False,
            codex_enabled=False,
            codex_source="default",
            allow_file_secret_fallback=False,
        )
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            return LocalClientConfig(
                config_version=CONFIG_VERSION,
                mcp_base_url=None,
                ui_base_url=None,
                feature_claude_desktop=False,
                codex_enabled=False,
                codex_source="default",
                allow_file_secret_fallback=False,
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LocalClientConfig(
            config_version=CONFIG_VERSION,
            mcp_base_url=None,
            ui_base_url=None,
            feature_claude_desktop=False,
            codex_enabled=False,
            codex_source="default",
            allow_file_secret_fallback=False,
        )
    if not isinstance(payload, dict):
        return LocalClientConfig(
            config_version=CONFIG_VERSION,
            mcp_base_url=None,
            ui_base_url=None,
            feature_claude_desktop=False,
            codex_enabled=False,
            codex_source="default",
            allow_file_secret_fallback=False,
        )
    return LocalClientConfig.from_payload(payload)


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _validated_mcp_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return DEFAULT_MCP_BASE_URL
    normalized = value.rstrip("/")
    if not parsed.path and (parsed.hostname or "").startswith("mcp."):
        return normalized + "/mcp"
    return normalized


def resolve_api_base_url(mcp_base_url: str) -> str:
    parsed = urlparse(mcp_base_url.rstrip("/"))
    hostname = parsed.hostname or ""
    if hostname == "mcp.evemem.com":
        return DEFAULT_API_BASE_URL
    if hostname.startswith("mcp."):
        replacement = hostname.replace("mcp.", "api.", 1)
        host = f"[{replacement}]" if ":" in replacement else replacement
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunparse((parsed.scheme, host, "", "", "", "")).rstrip("/")
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[: -len("/mcp")]
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


def _resolved_ui_base_url(value: str) -> tuple[str, str | None]:
    parsed = urlparse(value)
    if not parsed.netloc or parsed.username or parsed.password:
        return DEFAULT_UI_BASE_URL, None
    hostname = parsed.hostname or ""
    allow_custom = _is_truthy(os.environ.get(ALLOW_CUSTOM_UI_CONFIG_ENV_VAR, ""))
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port
    if (parsed.scheme == "https" and port in {None, 443}) or (
        parsed.scheme == "http" and port in {None, 80}
    ):
        netloc = host
    elif port is not None:
        netloc = f"{host}:{port}"
    else:
        netloc = parsed.netloc
    normalized = urlunparse((parsed.scheme, netloc, "", "", "", ""))
    if normalized in OFFICIAL_UI_ORIGINS:
        return normalized, None
    is_allowed_local_http = parsed.scheme == "http" and hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if allow_custom and (parsed.scheme == "https" or is_allowed_local_http):
        return normalized, None
    return DEFAULT_UI_BASE_URL, normalized


def resolve_config(override_mcp_base_url: str | None = None) -> ResolvedConfig:
    local_config = load_local_config()
    mcp_base_url = _validated_mcp_base_url(
        override_mcp_base_url
        or os.environ.get(CONFIG_ENV_VAR)
        or local_config.mcp_base_url
        or DEFAULT_MCP_BASE_URL
    )
    ui_base_url, blocked_ui_base_url = _resolved_ui_base_url(
        os.environ.get(UI_CONFIG_ENV_VAR) or local_config.ui_base_url or DEFAULT_UI_BASE_URL
    )
    environment = "production" if mcp_base_url == DEFAULT_MCP_BASE_URL else "custom"
    feature_claude_desktop = local_config.feature_claude_desktop or _is_truthy(
        os.environ.get(FEATURE_FLAG_ENV_VAR, "")
    )
    codex_disable_env = _is_truthy(os.environ.get(CODEX_DISABLE_ENV_VAR, ""))
    codex_enabled = local_config.codex_enabled and not codex_disable_env
    codex_source = "env" if codex_disable_env else local_config.codex_source
    allow_file_secret_fallback = local_config.allow_file_secret_fallback
    return ResolvedConfig(
        config_dir=get_config_dir(),
        config_path=get_config_path(),
        state_dir=get_state_dir(),
        project_root=Path.cwd().resolve(),
        mcp_base_url=mcp_base_url,
        ui_base_url=ui_base_url,
        mcp_server_name=MCP_SERVER_NAME,
        environment=environment,
        feature_claude_desktop=feature_claude_desktop,
        codex_enabled=codex_enabled,
        codex_source=codex_source,
        allow_file_secret_fallback=allow_file_secret_fallback,
        blocked_ui_base_url=blocked_ui_base_url,
        oauth_domain=(os.environ.get(OAUTH_DOMAIN_ENV_VAR) or DEFAULT_OAUTH_DOMAIN).strip()
        or DEFAULT_OAUTH_DOMAIN,
        oauth_client_id=(os.environ.get(OAUTH_CLIENT_ID_ENV_VAR) or DEFAULT_OAUTH_CLIENT_ID).strip()
        or DEFAULT_OAUTH_CLIENT_ID,
    )


def update_local_config(patch: dict[str, object]) -> Path:
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    current_payload: dict[str, object] = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current_payload = loaded
        except (OSError, json.JSONDecodeError):
            current_payload = {}
    current_payload["config_version"] = CONFIG_VERSION
    current_payload.update(patch)
    temp_path = config_path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(current_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(config_path)
    return config_path
