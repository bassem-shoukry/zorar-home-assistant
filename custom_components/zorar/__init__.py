"""The Zorar integration: cloud control of Zorar (MTTL-W01) smart power strips."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZorarApiClient
from .const import CONF_API_TOKEN, CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN
from .coordinator import ZorarConfigEntry, ZorarCoordinator

PLATFORMS: list[Platform] = [Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ZorarConfigEntry) -> bool:
    """Set up Zorar from a config entry."""
    client = ZorarApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_TOKEN],
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    coordinator = ZorarCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZorarConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ZorarConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a device only once its strip is gone from the account."""
    coordinator: ZorarCoordinator | None = getattr(entry, "runtime_data", None)
    data = (coordinator.data if coordinator is not None else None) or {}
    return not any(
        domain == DOMAIN and mac in data for domain, mac in device_entry.identifiers
    )
