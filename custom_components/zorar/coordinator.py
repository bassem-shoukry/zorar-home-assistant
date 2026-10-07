"""Data update coordinator for Zorar."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZorarApiClient, ZorarAuthError, ZorarError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type ZorarConfigEntry = ConfigEntry[ZorarCoordinator]


class ZorarCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls GET /strips; data is keyed by strip MAC."""

    config_entry: ZorarConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ZorarConfigEntry, client: ZorarApiClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            strips = await self.client.async_get_strips()
        except ZorarAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ZorarError as err:
            raise UpdateFailed(str(err)) from err
        return {
            strip["mac"]: strip
            for strip in strips
            if isinstance(strip, dict) and strip.get("mac")
        }
