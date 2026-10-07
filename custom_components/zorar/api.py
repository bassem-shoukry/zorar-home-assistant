"""Minimal async client for the Zorar cloud API.

Home Assistant never talks to a strip directly: the Zorar backend owns the
strip connections, exactly as for the mobile app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

PAIRING_START_PATH = "/home-assistant/pairings"
PAIRING_TOKEN_PATH = "/home-assistant/pairings/token"


class ZorarError(Exception):
    """Base error for the Zorar API."""


class ZorarConnectionError(ZorarError):
    """The API could not be reached or returned an unexpected response."""


class ZorarAuthError(ZorarError):
    """The token is invalid or expired (HTTP 401)."""


class ZorarForbiddenError(ZorarError):
    """The token lacks access to the resource (HTTP 403)."""


class ZorarStripOfflineError(ZorarError):
    """The strip is offline, so the command was refused (HTTP 409)."""


class ZorarRateLimitError(ZorarError):
    """Too many commands were sent (HTTP 429)."""


class ZorarPairingExpiredError(ZorarError):
    """The pairing code expired or is unknown to the server (HTTP 410/404)."""


@dataclass(frozen=True, slots=True)
class ZorarPairing:
    """A pairing started on the server, waiting for approval in the app."""

    device_code: str = field(repr=False)
    user_code: str
    expires_in: int
    interval: int


class ZorarPairingClient:
    """Unauthenticated client for the Home Assistant pairing endpoints.

    The device code is a short-lived secret: it is never logged.
    """

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")

    async def async_start(self) -> ZorarPairing:
        """Start a pairing and return the code to show the user."""
        status, payload = await self._post(PAIRING_START_PATH, None)
        data = payload.get("data") if isinstance(payload, dict) else None
        if status != 201 or not isinstance(data, dict):
            raise ZorarConnectionError(f"HTTP {status} starting a pairing")
        try:
            return ZorarPairing(
                device_code=str(data["device_code"]),
                user_code=str(data["user_code"]),
                expires_in=int(data["expires_in"]),
                interval=max(1, int(data["interval"])),
            )
        except (KeyError, TypeError, ValueError) as err:
            raise ZorarConnectionError("Unexpected pairing response") from err

    async def async_poll(self, device_code: str) -> str | None:
        """Return the API token once approved, or None while still pending."""
        status, payload = await self._post(
            PAIRING_TOKEN_PATH, {"device_code": device_code}
        )
        error = payload.get("error") if isinstance(payload, dict) else None
        if status == 200:
            data = payload.get("data") if isinstance(payload, dict) else None
            token = data.get("token") if isinstance(data, dict) else None
            if not isinstance(token, str) or not token:
                raise ZorarConnectionError("Unexpected pairing token response")
            return token
        if status == 400 and error == "authorization_pending":
            return None
        if status == 429 or (status == 400 and error == "slow_down"):
            raise ZorarRateLimitError("Pairing poll rate limited")
        if status in (404, 410):
            raise ZorarPairingExpiredError(f"Pairing ended: HTTP {status}")
        raise ZorarConnectionError(f"HTTP {status} polling a pairing")

    async def _post(self, path: str, json: dict[str, Any] | None) -> tuple[int, Any]:
        try:
            async with self._session.post(
                f"{self._base_url}{path}",
                headers={"Accept": "application/json"},
                json=json,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                status = resp.status
                try:
                    payload = await resp.json(content_type=None)
                except ValueError:
                    payload = None
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Zorar POST %s failed: %s", path, type(err).__name__)
            raise ZorarConnectionError(
                f"Error talking to Zorar: {type(err).__name__}"
            ) from err
        return status, payload


class ZorarApiClient:
    """Client for the Zorar REST API (v1)."""

    def __init__(
        self, session: aiohttp.ClientSession, token: str, base_url: str
    ) -> None:
        self._session = session
        self._token = token
        self._base_url = base_url.rstrip("/")

    async def async_get_me(self) -> dict[str, Any]:
        """Return the account that owns the token."""
        data = await self._request("GET", "/me")
        if not isinstance(data, dict) or "id" not in data:
            raise ZorarConnectionError("Unexpected /me response")
        return data

    async def async_get_strips(self) -> list[dict[str, Any]]:
        """Return the strips visible to the token."""
        data = await self._request("GET", "/strips")
        if not isinstance(data, list):
            raise ZorarConnectionError("Unexpected /strips response")
        return data

    async def async_set_outlet_power(
        self, strip_id: int, channel: int, on: bool
    ) -> dict[str, Any]:
        """Queue an on/off command for one outlet channel."""
        data = await self._request(
            "POST",
            f"/strips/{strip_id}/outlets/{channel}/power",
            json={"on": on},
        )
        return data if isinstance(data, dict) else {}

    async def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                status = resp.status
                if status == 401:
                    raise ZorarAuthError("Token is invalid or expired")
                if status == 403:
                    raise ZorarForbiddenError("Token lacks access")
                if status == 409:
                    raise ZorarStripOfflineError("Strip is offline")
                if status == 429:
                    raise ZorarRateLimitError("Rate limited")
                if status >= 400:
                    raise ZorarConnectionError(f"HTTP {status} for {method} {path}")
                payload = await resp.json(content_type=None)
        except ZorarError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            # Never log the exception itself: aiohttp errors such as
            # TooManyRedirects carry RequestInfo.headers, i.e. the bearer token.
            _LOGGER.debug("Zorar %s %s failed: %s", method, path, type(err).__name__)
            raise ZorarConnectionError(
                f"Error talking to Zorar: {type(err).__name__}"
            ) from err
        except ValueError as err:
            raise ZorarConnectionError("Invalid JSON from Zorar") from err
        if not isinstance(payload, dict) or "data" not in payload:
            raise ZorarConnectionError("Response has no data envelope")
        return payload["data"]
