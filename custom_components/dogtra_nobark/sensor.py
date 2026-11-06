"""Every field the status frame carries, as sensors. All read-only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)

from . import CollarConfigEntry
from .coordinator import (
    CONNECT_FAILED,
    CONNECT_OK,
    CONNECT_SKIPPED,
    CollarCoordinator,
    PollDiagnostics,
)
from .entity import CollarDiagnosticEntity, collar_device_info
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
    # Auto Increase: the level it is at now and the range it climbs within, as the app's
    # 1..10 levels (the collar stores raw intensities; protocol.level_from_intensity).
    CollarSensorDescription(
        key="auto_increase_level", translation_key="auto_increase_level",
        icon="mdi:stairs-up", state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.auto_increase_levels[0],
    ),
    CollarSensorDescription(
        key="auto_increase_min", translation_key="auto_increase_min",
        icon="mdi:arrow-collapse-down", state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.auto_increase_levels[1],
    ),
    CollarSensorDescription(
        key="auto_increase_max", translation_key="auto_increase_max",
        icon="mdi:arrow-collapse-up", state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.auto_increase_levels[2],
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
        # Shown in hours: a wear time reads better as 9.7 h than as tens of thousands of seconds.
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
        self._attr_device_info = collar_device_info(address)

    @property
    def native_value(self) -> int | str | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, int | bool] | None:
        # The raw mode code rides on the mode sensor so that a code outside the known set
        # is recorded the first time it appears, instead of being lost behind a disabled
        # diagnostic; the detection toggles ride with it because they shape what the mode does.
        if self.entity_description.key == "mode" and self.coordinator.data is not None:
            data = self.coordinator.data
            return {"mode_code": data.mode_code, **data.detection}
        return None

    @property
    def available(self) -> bool:
        # Presence, via the coordinator: the collar is in earshot of some adapter or proxy.
        # A failed poll does NOT blank the sensors -- the last good frame stands while the
        # collar is still heard (the phone app holding the link is the common case) -- but
        # nothing is shown before the first frame has been read.
        return super().available and self.coordinator.data is not None


@dataclass(frozen=True, kw_only=True)
class ContactSensorDescription(SensorEntityDescription):
    value: Callable[[PollDiagnostics], datetime | str | None]
    attributes: Callable[[PollDiagnostics], dict[str, object]]


CONTACT_SENSORS: tuple[ContactSensorDescription, ...] = (
    ContactSensorDescription(
        # While the collar is present this is as of the last poll; once it goes quiet it is
        # the final advertisement -- when, how strong, and heard by what.
        key="last_heard", translation_key="last_heard",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: d.heard_at,
        attributes=lambda d: {"rssi": d.heard_rssi, "heard_by": d.heard_source},
    ),
    ContactSensorDescription(
        key="last_connect", translation_key="last_connect",
        device_class=SensorDeviceClass.ENUM,
        options=[CONNECT_OK, CONNECT_FAILED, CONNECT_SKIPPED],
        icon="mdi:bluetooth-connect",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: d.connect_result,
        attributes=lambda d: {
            "at": d.connect_at,
            "seconds": d.connect_seconds,
            "rssi": d.connect_rssi,
            "through": d.connect_source,
            "error": d.connect_error,
        },
    ),
)


class CollarContactSensor(CollarDiagnosticEntity, SensorEntity):
    """What was last heard of the collar, or how the last connect went."""

    entity_description: ContactSensorDescription

    def __init__(
        self, coordinator: CollarCoordinator, description: ContactSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> datetime | str | None:
        return self.entity_description.value(self.coordinator.diagnostics)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return self.entity_description.attributes(self.coordinator.diagnostics)


async def async_setup_entry(
    hass: HomeAssistant, entry: CollarConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(CollarSensor(coordinator, d) for d in SENSORS),
            *(CollarContactSensor(coordinator, d) for d in CONTACT_SENSORS),
        ]
    )
