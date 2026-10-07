"""Config flow for Zorar."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    ZorarApiClient,
    ZorarAuthError,
    ZorarConnectionError,
    ZorarError,
    ZorarForbiddenError,
    ZorarPairing,
    ZorarPairingClient,
    ZorarPairingExpiredError,
    ZorarRateLimitError,
)
from .const import CONF_API_TOKEN, CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
LOCAL_SUFFIXES = (".local", ".test", ".lan")
PRIVATE_IPV4_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)


def is_secure_base_url(url: str) -> bool:
    """Return True for https, or plain http to a local/private host only.

    The bearer token travels with every request, so it must not cross the
    internet unencrypted.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http":
        return False
    if host in LOCAL_HOSTS or host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in PRIVATE_IPV4_NETWORKS
    )


class ZorarConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zorar."""

    VERSION = 1

    _pairing: ZorarPairing | None = None
    _pairing_base_url: str = DEFAULT_BASE_URL
    _poll_task: asyncio.Task[str | None] | None = None
    _paired_token: str | None = None

    async def _async_validate(
        self, token: str, base_url: str
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Return (account, errors) for a token. Never logs the token."""
        client = ZorarApiClient(async_get_clientsession(self.hass), token, base_url)
        try:
            return await client.async_get_me(), {}
        except (ZorarAuthError, ZorarForbiddenError):
            return None, {"base": "invalid_auth"}
        except ZorarConnectionError:
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error validating the Zorar token")
            return None, {"base": "unknown"}

    async def _async_finish(
        self, account: dict[str, Any], token: str, base_url: str
    ) -> ConfigFlowResult:
        """Create the entry, or update it when re-authenticating."""
        await self.async_set_unique_id(str(account["id"]))
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates={CONF_API_TOKEN: token, CONF_BASE_URL: base_url},
            )
        # Same account again: keep its entry, with the new credentials.
        self._abort_if_unique_id_configured(
            updates={CONF_API_TOKEN: token, CONF_BASE_URL: base_url}
        )
        return self.async_create_entry(
            title=account.get("name") or "Zorar",
            data={CONF_API_TOKEN: token, CONF_BASE_URL: base_url},
        )

    def _flow_in_progress(self) -> bool:
        try:
            self.hass.config_entries.flow.async_get(self.flow_id)
        except UnknownFlow:
            return False
        return True

    def _reauth_base_url(self) -> str:
        return self._get_reauth_entry().data.get(CONF_BASE_URL, DEFAULT_BASE_URL)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer pairing with the app, or pasting a token."""
        return self.async_show_menu(step_id="user", menu_options=["pair", "token"])

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for an API token from the Zorar app."""
        reauth = self.source == SOURCE_REAUTH
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            if reauth:
                base_url = self._reauth_base_url()
            else:
                base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            if not is_secure_base_url(base_url):
                account, errors = None, {CONF_BASE_URL: "insecure_url"}
            else:
                account, errors = await self._async_validate(token, base_url)
            if account is not None:
                return await self._async_finish(account, token, base_url)

        fields: dict[Any, Any] = {vol.Required(CONF_API_TOKEN): TOKEN_SELECTOR}
        if not reauth:
            fields[vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL)] = URL_SELECTOR
        return self.async_show_form(
            step_id="token", data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start a pairing; the user approves it in the Zorar app.

        Pairing uses the default server (or the entry's server on reauth); a
        self-hosted server can still be set up with a token.
        """
        if self.source == SOURCE_REAUTH:
            base_url = self._reauth_base_url()
        else:
            base_url = DEFAULT_BASE_URL
        errors: dict[str, str] = {}
        if not is_secure_base_url(base_url):
            errors = {"base": "insecure_url"}
        else:
            client = ZorarPairingClient(async_get_clientsession(self.hass), base_url)
            try:
                pairing = await client.async_start()
            except ZorarError:
                errors = {"base": "cannot_connect"}
            except Exception:
                _LOGGER.exception("Unexpected error starting a Zorar pairing")
                errors = {"base": "unknown"}
            else:
                if not self._flow_in_progress():
                    # Aborted while starting: never start polling.
                    return self.async_abort(reason="unknown")
                self._pairing = pairing
                self._pairing_base_url = base_url
                self._paired_token = None
                self._poll_task = None
                return await self.async_step_pair_wait()

        # Submitting this empty form retries.
        return self.async_show_form(
            step_id="pair", data_schema=vol.Schema({}), errors=errors
        )

    async def async_step_pair_wait(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the pairing code while polling for approval."""
        pairing = self._pairing
        assert pairing is not None
        if self._poll_task is None:
            self._poll_task = self.hass.async_create_background_task(
                self._async_poll_for_token(pairing), "zorar pairing poll"
            )
        if not self._poll_task.done():
            return self.async_show_progress(
                step_id="pair_wait",
                progress_action="wait_for_approval",
                description_placeholders={
                    "code": pairing.user_code,
                    "minutes": str(max(1, round(pairing.expires_in / 60))),
                },
                progress_task=self._poll_task,
            )

        task, self._poll_task = self._poll_task, None
        self._paired_token = None if task.cancelled() else task.result()
        self._pairing = None
        if self._paired_token is None:
            return self.async_show_progress_done(next_step_id="pair_expired")
        return self.async_show_progress_done(next_step_id="pair_finish")

    async def _async_poll_for_token(self, pairing: ZorarPairing) -> str | None:
        """Poll until the app approves the pairing; None once it expires.

        Network errors are retried quietly until the code expires. Home
        Assistant cancels this task when the flow is aborted or removed.
        """
        client = ZorarPairingClient(
            async_get_clientsession(self.hass), self._pairing_base_url
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + pairing.expires_in
        interval = pairing.interval
        waited = 0
        while True:
            await asyncio.sleep(interval)
            waited += interval
            if waited > pairing.expires_in or loop.time() > deadline:
                return None
            try:
                token = await client.async_poll(pairing.device_code)
            except ZorarRateLimitError:
                interval *= 2
                continue
            except ZorarPairingExpiredError:
                return None
            except ZorarError:
                continue
            except Exception:
                _LOGGER.exception("Unexpected error polling a Zorar pairing")
                continue
            if token is not None:
                return token

    async def async_step_pair_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the token the app approved and finish."""
        token, self._paired_token = self._paired_token, None
        if token is None:
            return self.async_abort(reason="unknown")
        base_url = self._pairing_base_url
        account, errors = await self._async_validate(token, base_url)
        if account is None:
            return self.async_abort(reason=errors["base"])
        return await self._async_finish(account, token, base_url)

    async def async_step_pair_expired(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The code expired: try again, or paste a token instead."""
        return self.async_show_menu(
            step_id="pair_expired", menu_options=["pair", "token"]
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when the token is rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair again, or paste a new token."""
        return self.async_show_menu(
            step_id="reauth_confirm", menu_options=["pair", "token"]
        )
