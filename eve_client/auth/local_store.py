"""Composite local credential storage with keyring-first behavior."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from keyring.errors import KeyringError, PasswordDeleteError

from eve_client.auth.base import (
    CredentialRecord,
    CredentialStore,
    CredentialStoreUnavailableError,
    OAuthSession,
)
from eve_client.auth.file_store import FileCredentialStore
from eve_client.auth.keyring_store import KeyringCredentialStore
from eve_client.models import ToolName

FALLBACK_FILE = "auth-fallback.json"


def _mask_secret(secret: str) -> str:
    if len(secret) <= 4:
        return "****"
    return f"{secret[:2]}****{secret[-2:]}"


def _serialize_oauth_session(session: OAuthSession) -> str:
    payload = {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expires_at": session.expires_at,
        "scope": session.scope,
        "token_type": session.token_type,
    }
    return json.dumps(payload, sort_keys=True)


def _deserialize_oauth_session(tool: ToolName, raw_value: str) -> OAuthSession | None:
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return OAuthSession(tool=tool, access_token=raw_value)
    if not isinstance(payload, dict):
        return None
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None
    refresh_token = payload.get("refresh_token")
    expires_at = payload.get("expires_at")
    scope = payload.get("scope")
    token_type = payload.get("token_type")
    return OAuthSession(
        tool=tool,
        access_token=access_token,
        refresh_token=refresh_token if isinstance(refresh_token, str) and refresh_token else None,
        expires_at=expires_at if isinstance(expires_at, int) else None,
        scope=scope if isinstance(scope, str) and scope else None,
        token_type=token_type if isinstance(token_type, str) and token_type else "Bearer",
    )


class LocalCredentialStore(CredentialStore):
    def __init__(
        self,
        state_dir: Path,
        *,
        allow_file_fallback: bool = True,
        keyring_store: KeyringCredentialStore | None = None,
        file_store: FileCredentialStore | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.allow_file_fallback = allow_file_fallback
        self.keyring_store = keyring_store or KeyringCredentialStore()
        self.file_store = file_store or FileCredentialStore(state_dir / FALLBACK_FILE, state_dir)

    def _key_name(self, tool: ToolName, auth_mode: str) -> str:
        return f"{tool}:{auth_mode}"

    def _set_secret(self, tool: ToolName, auth_mode: str, secret: str) -> CredentialRecord:
        key_name = self._key_name(tool, auth_mode)
        label = "api-key" if auth_mode == "api-key" else auth_mode
        # Write to file store first (avoids keychain prompts on macOS).
        # Keyring is kept as secondary write for backward compat.
        if self.allow_file_fallback:
            payload = self.file_store.load()
            payload[key_name] = secret
            self.file_store.write(payload)
        try:
            self.keyring_store.set(key_name, secret)
        except KeyringError:
            if not self.allow_file_fallback:
                raise CredentialStoreUnavailableError(
                    f"No secure keyring backend available for {tool}; file fallback is disabled."
                ) from None
        source = "file-fallback" if self.allow_file_fallback else "keyring"
        return CredentialRecord(
            tool=tool, auth_mode=auth_mode, source=source, value_masked=_mask_secret(secret)
        )

    def _get_secret(self, tool: ToolName, auth_mode: str) -> tuple[str | None, str | None]:
        key_name = self._key_name(tool, auth_mode)
        # Check file store first (avoids keychain prompts on macOS)
        if self.allow_file_fallback:
            payload = self.file_store.load()
            file_value = payload.get(key_name)
            if file_value:
                return file_value, "file-fallback"
        # Fall back to keyring
        try:
            value = self.keyring_store.get(key_name)
            if value:
                return value, "keyring"
        except KeyringError:
            if not self.allow_file_fallback:
                raise CredentialStoreUnavailableError(
                    f"No secure keyring backend available for {tool}; file fallback is disabled."
                ) from None
        return None, None

    def _delete_secret(self, tool: ToolName, auth_mode: str) -> None:
        key_name = self._key_name(tool, auth_mode)
        with contextlib.suppress(KeyringError, PasswordDeleteError):
            self.keyring_store.delete(key_name)

        payload = self.file_store.load()
        if key_name in payload:
            del payload[key_name]
            self.file_store.write(payload)

    def set_api_key(self, tool: ToolName, api_key: str) -> CredentialRecord:
        return self._set_secret(tool, "api-key", api_key)

    def get_api_key(self, tool: ToolName) -> tuple[str | None, str | None]:
        return self._get_secret(tool, "api-key")

    def delete_api_key(self, tool: ToolName) -> None:
        self._delete_secret(tool, "api-key")

    def set_bearer_token(self, tool: ToolName, token: str) -> CredentialRecord:
        return self.set_oauth_session(OAuthSession(tool=tool, access_token=token))

    def get_bearer_token(self, tool: ToolName) -> tuple[str | None, str | None]:
        session, source = self.get_oauth_session(tool)
        return (session.access_token if session else None, source)

    def delete_bearer_token(self, tool: ToolName) -> None:
        self.delete_oauth_session(tool)

    def set_oauth_session(self, session: OAuthSession) -> CredentialRecord:
        return self._set_secret(session.tool, "oauth", _serialize_oauth_session(session))

    def get_oauth_session(self, tool: ToolName) -> tuple[OAuthSession | None, str | None]:
        raw_value, source = self._get_secret(tool, "oauth")
        if not raw_value:
            return None, source
        return _deserialize_oauth_session(tool, raw_value), source

    def delete_oauth_session(self, tool: ToolName) -> None:
        self._delete_secret(tool, "oauth")
