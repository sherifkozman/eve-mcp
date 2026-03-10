from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from eve_client.oauth_device import (
    OAuthDeviceFlowError,
    poll_auth0_device_token,
    refresh_auth0_token,
    start_auth0_device_authorization,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_start_auth0_device_authorization_returns_expected_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=10.0):  # noqa: ARG001
        assert request.full_url == "https://evemem.us.auth0.com/oauth/device/code"
        payload = request.data.decode("utf-8")
        assert "audience=https%3A%2F%2Fmcp.evemem.com%2Fmcp" in payload
        assert "scope=openid+profile+email+memory.read" in payload
        return _FakeResponse(
            {
                "device_code": "device-code",
                "user_code": "USER-CODE",
                "verification_uri": "https://evemem.us.auth0.com/activate",
                "verification_uri_complete": "https://evemem.us.auth0.com/activate?user_code=USER-CODE",
                "expires_in": 900,
                "interval": 5,
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = start_auth0_device_authorization(
        domain="evemem.us.auth0.com",
        client_id="client-id",
        audience="https://mcp.evemem.com/mcp",
        scopes=("openid", "profile", "email", "memory.read"),
    )
    assert result.device_code == "device-code"
    assert result.user_code == "USER-CODE"
    assert result.verification_uri_complete == "https://evemem.us.auth0.com/activate?user_code=USER-CODE"


def test_start_auth0_device_authorization_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=10.0):  # noqa: ARG001
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"invalid_request","error_description":"bad device auth"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(OAuthDeviceFlowError, match="bad device auth"):
        start_auth0_device_authorization(
            domain="evemem.us.auth0.com",
            client_id="client-id",
            audience="https://mcp.evemem.com/mcp",
            scopes=("memory.read",),
        )


def test_poll_auth0_device_token_waits_then_returns_token(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            HTTPError(
                "https://evemem.us.auth0.com/oauth/token",
                400,
                "Bad Request",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"authorization_pending"}'),
            ),
            _FakeResponse(
                {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "openid profile email offline_access memory.read memory.write",
                }
            ),
        ]
    )

    def fake_urlopen(request, timeout=10.0):  # noqa: ARG001
        next_item = next(responses)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    result = poll_auth0_device_token(
        domain="evemem.us.auth0.com",
        client_id="client-id",
        device_code="device-code",
        expires_in=600,
        interval=1,
    )
    assert result.access_token == "access-token"
    assert result.refresh_token == "refresh-token"
    assert result.scope == "openid profile email offline_access memory.read memory.write"


def test_poll_auth0_device_token_handles_auth0_pending_message(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            HTTPError(
                "https://evemem.us.auth0.com/oauth/token",
                400,
                "Bad Request",
                hdrs=None,
                fp=io.BytesIO(b'{"error_description":"User has yet to authorize device code."}'),
            ),
            _FakeResponse(
                {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "memory.read memory.write",
                }
            ),
        ]
    )

    def fake_urlopen(request, timeout=10.0):  # noqa: ARG001
        next_item = next(responses)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    result = poll_auth0_device_token(
        domain="evemem.us.auth0.com",
        client_id="client-id",
        device_code="device-code",
        expires_in=600,
        interval=1,
    )
    assert result.access_token == "access-token"


def test_poll_auth0_device_token_raises_on_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=10.0):  # noqa: ARG001
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"expired_token"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(OAuthDeviceFlowError, match="expired"):
        poll_auth0_device_token(
            domain="evemem.us.auth0.com",
            client_id="client-id",
            device_code="device-code",
            expires_in=600,
            interval=1,
        )


def test_refresh_auth0_token_returns_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=10.0):  # noqa: ARG001
        assert request.full_url == "https://evemem.us.auth0.com/oauth/token"
        payload = request.data.decode("utf-8")
        assert "grant_type=refresh_token" in payload
        assert "refresh_token=refresh-token" in payload
        return _FakeResponse(
            {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "memory.read memory.write",
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = refresh_auth0_token(
        domain="evemem.us.auth0.com",
        client_id="client-id",
        refresh_token="refresh-token",
    )
    assert result.access_token == "new-access-token"
    assert result.refresh_token == "new-refresh-token"
