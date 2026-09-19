"""Every field the status frame carries, as sensors. All read-only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)

from . import CollarConfigEntry
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import CollarCoordinator
from .protocol import MODES, CollarState


@dataclass(frozen=True, kw_only=True)
class CollarSensorDescription(SensorEntityDescription):
    value: Callable[[CollarState], int | str | None]


#: TOTAL_INCREASING: the app's Reset button zeroes the counters (with the uptime timer),
#: and this is the state class whose statistics treat a drop to zero as a new cycle and
#: keep summing from there. TOTAL without a last_reset would book the reset as a negative
#: delta and the daily statistic would go negative.
_COUNT = dict(state_class=SensorStateClass.TOTAL_INCREASING)

SENSORS: tuple[CollarSensorDescription, ...] = (
    CollarSensorDescription(
        key="bark_count", translation_key="bark_count", icon="mdi:dog-side",
        value=lambda s: s.bark_count, **_COUNT,
    ),
    CollarSensorDescription(
        key="howl_count", translation_key="howl_count", icon="mdi:dog-side",
        value=lambda s: s.howl_count, **_COUNT,
    ),
    CollarSensorDescription(
        key="whine_count", translation_key="whine_count", icon="mdi:dog-side",
        value=lambda s: s.whine_count, **_COUNT,
    ),
    CollarSensorDescription(
        # What the vendor app displays as one number.
        key="whine_and_howl_count", translation_key="whine_and_howl_count",
        icon="mdi:dog-side", value=lambda s: s.whine_and_howl_count, **_COUNT,
    ),
    CollarSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.battery,
    ),
    CollarSensorDescription(
        key="mode", translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(MODES.values()),
        icon="mdi:tune",
        value=lambda s: s.mode if s.mode in MODES.values() else None,
    ),
    CollarSensorDescription(
        key="correction_level", translation_key="correction_level",
        icon="mdi:flash", state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.correction_level,
    ),
    CollarSensorDescription(
        key="sound_sensitivity", translation_key="sound_sensitivity",
        icon="mdi:microphone", state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.sound_sensitivity,
    ),
    CollarSensorDescription(
        key="vibration_sensitivity", translation_key="vibration_sensitivity",
        icon="mdi:vibrate", state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.vibration_sensitivity,
    ),
    CollarSensorDescription(
        key="uptime", translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        # Shown in hours: 34,786.00 s on a tile reads as nothing; 9.7 h reads as a day worn.
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.uptime_seconds,
    ),
    CollarSensorDescription(
        key="status_frame", translation_key="status_frame",
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
        value=lambda s: s.raw.hex(),
    ),
)


class CollarSensor(PassiveBluetoothCoordinatorEntity[CollarCoordinator], SensorEntity):
    """One decoded field."""

    _attr_has_entity_name = True
    entity_description: CollarSensorDescription

    def __init__(
        self, coordinator: CollarCoordinator, description: CollarSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        address = coordinator.device.address
        self._attr_unique_id = f"{address}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=f"Dogtra collar {address[-5:].replace(':', '')}",
        )

    @property
    def native_value(self) -> int | str | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        # The raw mode code rides on the mode sensor so that a code outside the known set
        # (Auto Increase has never been captured) is recorded the first time it appears,
        # instead of being lost behind a disabled diagnostic.
        if self.entity_description.key == "mode" and self.coordinator.data is not None:
            return {"mode_code": self.coordinator.data.mode_code}
        return None

    @property
    def available(self) -> bool:
        # Presence, via the coordinator: the collar is in earshot of some adapter or proxy.
        # A failed poll does NOT blank the sensors -- the last good frame stands while the
        # collar is still heard (the phone app holding the link is the common case) -- but
        # nothing is shown before the first frame has been read.
        return super().available and self.coordinator.data is not None


async def async_setup_entry(
    hass: HomeAssistant, entry: CollarConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the sensors."""
    async_add_entities(CollarSensor(entry.runtime_data, d) for d in SENSORS)
