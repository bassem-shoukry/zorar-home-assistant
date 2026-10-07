"""Outlet switches for Zorar strips."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    ZorarAuthError,
    ZorarError,
    ZorarForbiddenError,
    ZorarRateLimitError,
    ZorarStripOfflineError,
)
from .const import (
    DOMAIN,
    MANUFACTURER,
    MODEL,
    OPTIMISTIC_TIMEOUT,
    OUTLET_CHANNELS,
    REFRESH_DELAY_AFTER_COMMAND,
)
from .coordinator import ZorarConfigEntry, ZorarCoordinator

PARALLEL_UPDATES = 1


def strip_device_name(mac: str, strip: dict[str, Any]) -> str:
    """Return the device name for a strip, falling back to its MAC suffix."""
    return strip.get("name") or f"Zorar {mac[-4:]}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZorarConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one switch per outlet channel and keep strip devices in sync."""
    coordinator = entry.runtime_data
    dev_reg = dr.async_get(hass)
    known: set[str] = set()

    @callback
    def _sync_devices() -> None:
        if not coordinator.last_update_success:
            return
        data = coordinator.data or {}

        # Strips no longer visible to the token (released or unshared).
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            macs = {ident[1] for ident in device.identifiers if ident[0] == DOMAIN}
            if macs and not macs & data.keys():
                dev_reg.async_update_device(
                    device.id, remove_config_entry_id=entry.entry_id
                )
                known.difference_update(macs)
        known.intersection_update(data.keys())

        # Strips renamed in the app.
        for mac, strip in data.items():
            if mac not in known:
                continue
            device = dev_reg.async_get_device_by_identifier(
                (DOMAIN, mac), entry.entry_id
            )
            name = strip_device_name(mac, strip)
            if device is not None and device.name != name:
                dev_reg.async_update_device(device.id, name=name)

        new_macs = [mac for mac in data if mac not in known]
        if not new_macs:
            return
        known.update(new_macs)
        async_add_entities(
            ZorarOutletSwitch(coordinator, mac, channel)
            for mac in new_macs
            for channel in OUTLET_CHANNELS
        )

    _sync_devices()
    entry.async_on_unload(coordinator.async_add_listener(_sync_devices))


class ZorarOutletSwitch(CoordinatorEntity[ZorarCoordinator], SwitchEntity):
    """One switchable AC outlet on a Zorar strip."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: ZorarCoordinator, mac: str, channel: int) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._channel = channel
        self._optimistic: bool | None = None
        self._cancel_refresh: CALLBACK_TYPE | None = None
        self._cancel_optimistic: CALLBACK_TYPE | None = None
        self._attr_unique_id = f"{mac}_{channel}"
        strip = self._strip or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            name=strip_device_name(mac, strip),
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=strip.get("firmware"),
        )
        self._update_name()

    @property
    def _strip(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._mac)

    @property
    def _outlet(self) -> dict[str, Any] | None:
        strip = self._strip
        if not strip:
            return None
        for outlet in strip.get("outlets") or []:
            if outlet.get("channel") == self._channel:
                return outlet
        return None

    def _update_name(self) -> None:
        outlet = self._outlet or {}
        name = outlet.get("name")
        if name:
            self._attr_name = name
            self._attr_translation_key = None
            self._attr_translation_placeholders = {}
        else:
            # Unset _attr_name so HA resolves the translated "Outlet {channel}".
            if hasattr(self, "_attr_name"):
                del self._attr_name
            self._attr_translation_key = "outlet"
            self._attr_translation_placeholders = {"channel": str(self._channel)}

    @property
    def available(self) -> bool:
        strip = self._strip
        outlet = self._outlet
        return (
            super().available
            and strip is not None
            and bool(strip.get("is_online"))
            and outlet is not None
            and outlet.get("relay_on") is not None
        )

    @property
    def is_on(self) -> bool | None:
        if self._optimistic is not None:
            return self._optimistic
        outlet = self._outlet
        return None if outlet is None else outlet.get("relay_on")

    @callback
    def _clear_optimistic(self) -> None:
        self._optimistic = None
        if self._cancel_optimistic is not None:
            self._cancel_optimistic()
            self._cancel_optimistic = None

    @callback
    def _handle_coordinator_update(self) -> None:
        # Keep the commanded state until a poll confirms it (or it times out):
        # a poll that was in flight before the command still reports the old
        # state. An offline/unavailable strip always wins straight away.
        if self._optimistic is not None and (
            not self.available
            or (self._outlet or {}).get("relay_on") == self._optimistic
        ):
            self._clear_optimistic()
        self._update_name()
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_power(False)

    async def _async_set_power(self, on: bool) -> None:
        strip = self._strip
        if strip is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="strip_unavailable"
            )
        try:
            await self.coordinator.client.async_set_outlet_power(
                strip["id"], self._channel, on
            )
        except ZorarStripOfflineError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="strip_offline"
            ) from err
        except ZorarRateLimitError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="rate_limited"
            ) from err
        except ZorarAuthError as err:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except ZorarForbiddenError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="forbidden"
            ) from err
        except ZorarError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        self._clear_optimistic()
        self._optimistic = on
        self._cancel_optimistic = async_call_later(
            self.hass, OPTIMISTIC_TIMEOUT, self._optimistic_expired
        )
        self.async_write_ha_state()
        self._schedule_refresh()

    @callback
    def _optimistic_expired(self, _now: datetime) -> None:
        """Stop holding the commanded state; trust the last poll."""
        self._cancel_optimistic = None
        self._optimistic = None
        self.async_write_ha_state()

    @callback
    def _schedule_refresh(self) -> None:
        if self._cancel_refresh is not None:
            self._cancel_refresh()

        @callback
        def _refresh(_now: datetime) -> None:
            self._cancel_refresh = None
            self.hass.async_create_task(self.coordinator.async_request_refresh())

        self._cancel_refresh = async_call_later(
            self.hass, REFRESH_DELAY_AFTER_COMMAND, _refresh
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_refresh is not None:
            self._cancel_refresh()
            self._cancel_refresh = None
        self._clear_optimistic()
        await super().async_will_remove_from_hass()
