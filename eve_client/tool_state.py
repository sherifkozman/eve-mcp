"""Tool state classification helpers."""

from __future__ import annotations

from typing import Literal

from eve_client.config import ResolvedConfig
from eve_client.models import DetectedTool

CodexLocalState = Literal[
    "disabled_by_env",
    "disabled_by_config",
    "disabled_by_default",
    "disabled_by_legacy",
    "enabled_binary_missing",
    "enabled_unconfigured",
    "enabled_ready",
]
CodexVerifyState = Literal[
    "disabled_by_env",
    "disabled_by_config",
    "disabled_by_default",
    "disabled_by_legacy",
    "enabled_binary_missing",
    "enabled_unconfigured",
    "enabled_broken",
    "enabled_healthy",
]


def classify_codex_disabled_state(config: ResolvedConfig) -> CodexLocalState | None:
    if config.codex_enabled:
        return None
    if config.codex_source == "env":
        return "disabled_by_env"
    if config.codex_source == "config":
        return "disabled_by_config"
    if config.codex_source == "legacy":
        return "disabled_by_legacy"
    return "disabled_by_default"


def classify_codex_local_state(
    config: ResolvedConfig,
    detected: DetectedTool,
    *,
    auth_mode: str = "api-key",
    credential_present: bool,
    eve_configured: bool,
) -> CodexLocalState:
    disabled_state = classify_codex_disabled_state(config)
    if disabled_state is not None:
        return disabled_state
    if not detected.binary_found:
        return "enabled_binary_missing"
    if not credential_present or not eve_configured:
        return "enabled_unconfigured"
    return "enabled_ready"


def classify_codex_verify_state(
    config: ResolvedConfig,
    detected: DetectedTool,
    *,
    auth_mode: str = "api-key",
    credential_present: bool,
    eve_configured: bool,
    connectivity_success: bool | None,
) -> CodexVerifyState:
    local_state = classify_codex_local_state(
        config,
        detected,
        auth_mode=auth_mode,
        credential_present=credential_present,
        eve_configured=eve_configured,
    )
    if local_state != "enabled_ready":
        return local_state
    return "enabled_healthy" if connectivity_success else "enabled_broken"
