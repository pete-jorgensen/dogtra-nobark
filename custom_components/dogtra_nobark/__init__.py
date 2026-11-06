"""Dogtra SMART NOBARK bark collar over Bluetooth LE: its status, and its commands."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_MAX_NICK_LEVEL, DEFAULT_MAX_NICK_LEVEL
from .coordinator import CollarCoordinator
from .device import CollarDevice

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]

type CollarConfigEntry = ConfigEntry[CollarCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CollarConfigEntry) -> bool:
    """Set up one collar."""
    address: str = entry.data[CONF_ADDRESS]
    coordinator = CollarCoordinator(hass, CollarDevice(address))
    coordinator.max_nick_level = int(
        entry.options.get(CONF_MAX_NICK_LEVEL, DEFAULT_MAX_NICK_LEVEL)
    )
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No blocking first refresh: the coordinator polls on the first advertisement it hears
    # and from its own timer after that, so setup never waits on a BLE connect and
    # never fails just because the dog is out of range or the phone app holds the link at
    # the moment HA starts.
    entry.async_on_unload(coordinator.async_start())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CollarConfigEntry) -> bool:
    """Unload one collar."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: CollarConfigEntry) -> None:
    """A new Nick cap takes effect by reloading, so the slider's maximum follows it."""
    await hass.config_entries.async_reload(entry.entry_id)
