"""Credential store interfaces for the Eve client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from eve_client.models import ToolName


@dataclass(slots=True)
class CredentialRecord:
    tool: ToolName
    auth_mode: str
    source: str
    value_masked: str


@dataclass(slots=True)
class OAuthSession:
    tool: ToolName
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None
    scope: str | None = None
    token_type: str = "Bearer"


class CredentialStore(Protocol):
    def set_api_key(self, tool: ToolName, api_key: str) -> CredentialRecord:
        """Persist an API key for a supported tool."""

    def get_api_key(self, tool: ToolName) -> tuple[str | None, str | None]:
        """Load an API key for a supported tool."""

    def delete_api_key(self, tool: ToolName) -> None:
        """Delete any stored API key for a supported tool."""

    def set_bearer_token(self, tool: ToolName, token: str) -> CredentialRecord:
        """Persist a bearer token for a supported tool."""

    def get_bearer_token(self, tool: ToolName) -> tuple[str | None, str | None]:
        """Load a bearer token for a supported tool."""

    def delete_bearer_token(self, tool: ToolName) -> None:
        """Delete any stored bearer token for a supported tool."""

    def set_oauth_session(self, session: OAuthSession) -> CredentialRecord:
        """Persist a structured OAuth session for a supported tool."""

    def get_oauth_session(self, tool: ToolName) -> tuple[OAuthSession | None, str | None]:
        """Load a structured OAuth session for a supported tool."""

    def delete_oauth_session(self, tool: ToolName) -> None:
        """Delete any stored structured OAuth session for a supported tool."""


class CredentialStoreUnavailableError(RuntimeError):
    """Raised when no approved credential backend is available."""
