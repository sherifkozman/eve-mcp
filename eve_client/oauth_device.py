"""Auth0 device authorization flow for Eve-owned CLI OAuth."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class OAuthDeviceFlowError(RuntimeError):
    """Raised when the device authorization flow cannot complete."""


@dataclass(slots=True)
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


@dataclass(slots=True)
class DeviceTokenResult:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    token_type: str
    scope: str | None


def _form_request(url: str, payload: dict[str, str], timeout: float) -> dict[str, object]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as decode_exc:  # pragma: no cover - defensive
            raise OAuthDeviceFlowError(f"OAuth endpoint returned HTTP {exc.code}") from decode_exc
        description = payload.get("error_description") or payload.get("error") or f"HTTP {exc.code}"
        raise OAuthDeviceFlowError(str(description)) from None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise OAuthDeviceFlowError("OAuth endpoint returned invalid JSON") from exc
    if not isinstance(parsed, dict):  # pragma: no cover - defensive
        raise OAuthDeviceFlowError("OAuth endpoint returned invalid payload")
    return parsed


def start_auth0_device_authorization(
    *,
    domain: str,
    client_id: str,
    audience: str,
    scopes: tuple[str, ...],
    timeout: float = 10.0,
) -> DeviceAuthorization:
    payload = {
        "client_id": client_id,
        "audience": audience,
        "scope": " ".join(scopes),
    }
    response = _form_request(f"https://{domain}/oauth/device/code", payload, timeout)
    device_code = response.get("device_code")
    user_code = response.get("user_code")
    verification_uri = response.get("verification_uri")
    if not all(isinstance(value, str) and value for value in (device_code, user_code, verification_uri)):
        raise OAuthDeviceFlowError("Device authorization response is missing required fields")
    verification_uri_complete = response.get("verification_uri_complete")
    expires_in = response.get("expires_in")
    interval = response.get("interval")
    return DeviceAuthorization(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete if isinstance(verification_uri_complete, str) else None,
        expires_in=expires_in if isinstance(expires_in, int) and expires_in > 0 else 600,
        interval=interval if isinstance(interval, int) and interval > 0 else 5,
    )


def poll_auth0_device_token(
    *,
    domain: str,
    client_id: str,
    device_code: str,
    expires_in: int,
    interval: int,
    timeout: float = 10.0,
) -> DeviceTokenResult:
    deadline = time.monotonic() + expires_in
    current_interval = max(interval, 1)
    while time.monotonic() < deadline:
        try:
            response = _form_request(
                f"https://{domain}/oauth/token",
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": client_id,
                },
                timeout,
            )
        except OAuthDeviceFlowError as exc:
            message = str(exc)
            normalized = message.lower().replace("-", "_").replace(" ", "_")
            if "authorization_pending" in normalized or "yet_to_authorize_device_code" in normalized:
                time.sleep(current_interval)
                continue
            if "slow_down" in normalized:
                current_interval += 5
                time.sleep(current_interval)
                continue
            if "expired_token" in normalized:
                raise OAuthDeviceFlowError("Device authorization expired before completion") from None
            raise
        access_token = response.get("access_token")
        token_type = response.get("token_type")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthDeviceFlowError("OAuth token response did not include an access token")
        return DeviceTokenResult(
            access_token=access_token,
            refresh_token=response.get("refresh_token") if isinstance(response.get("refresh_token"), str) else None,
            expires_in=response.get("expires_in") if isinstance(response.get("expires_in"), int) else None,
            token_type=token_type if isinstance(token_type, str) and token_type else "Bearer",
            scope=response.get("scope") if isinstance(response.get("scope"), str) else None,
        )
    raise OAuthDeviceFlowError("Timed out waiting for OAuth device authorization")


def refresh_auth0_token(
    *,
    domain: str,
    client_id: str,
    refresh_token: str,
    timeout: float = 10.0,
) -> DeviceTokenResult:
    response = _form_request(
        f"https://{domain}/oauth/token",
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        },
        timeout,
    )
    access_token = response.get("access_token")
    token_type = response.get("token_type")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthDeviceFlowError("Refresh response did not include an access token")
    return DeviceTokenResult(
        access_token=access_token,
        refresh_token=response.get("refresh_token") if isinstance(response.get("refresh_token"), str) else refresh_token,
        expires_in=response.get("expires_in") if isinstance(response.get("expires_in"), int) else None,
        token_type=token_type if isinstance(token_type, str) and token_type else "Bearer",
        scope=response.get("scope") if isinstance(response.get("scope"), str) else None,
    )
