"""The one condition worth a flag: the collar went silent on a failed connect of ours."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CollarConfigEntry
from .coordinator import CollarCoordinator
from .entity import CollarDiagnosticEntity


class SilentAfterFailedConnect(CollarDiagnosticEntity, BinarySensorEntity):
    """On while the collar has not advertised since a connect of ours failed.

    A connected collar stops advertising, so a link left up anywhere -- a proxy, or the
    collar's own Bluetooth stack -- looks exactly like this, and lasts until something
    releases it. So does the dog walking out of range mid-connect, which is why the last
    signal strength rides along: strong then silent is a stuck link, fading then silent is
    a dog leaving.
    """

    _attr_translation_key = "silent_after_failed_connect"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: CollarCoordinator) -> None:
        super().__init__(coordinator, "silent_after_failed_connect")

    @property
    def is_on(self) -> bool:
        return self.coordinator.diagnostics.silent_after_failed_connect

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        diag = self.coordinator.diagnostics
        return {
            "last_heard": diag.heard_at,
            "last_rssi": diag.heard_rssi,
            "last_heard_by": diag.heard_source,
            "connect_through": diag.connect_source,
            "connect_error": diag.connect_error,
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: CollarConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the flag."""
    async_add_entities([SilentAfterFailedConnect(entry.runtime_data)])
