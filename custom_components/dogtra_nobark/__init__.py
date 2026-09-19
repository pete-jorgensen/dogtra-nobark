"""Dogtra SMART NOBARK bark collar over Bluetooth LE -- read-only."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .coordinator import CollarCoordinator
from .device import CollarDevice

PLATFORMS: list[Platform] = [Platform.SENSOR]

type CollarConfigEntry = ConfigEntry[CollarCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CollarConfigEntry) -> bool:
    """Set up one collar."""
    address: str = entry.data[CONF_ADDRESS]
    coordinator = CollarCoordinator(hass, CollarDevice(address))
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No blocking first refresh: the coordinator polls on the first advertisement it hears,
    # so setup never waits on a 20-40 s BLE connect and never fails just because the dog is
    # out of range or the phone app holds the link at the moment HA starts.
    entry.async_on_unload(coordinator.async_start())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CollarConfigEntry) -> bool:
    """Unload one collar."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
