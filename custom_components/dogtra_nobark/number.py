"""The collar's levels and sensitivities, settable, and the level a Nick is sent at."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CollarConfigEntry
from .coordinator import CollarCoordinator
from .entity import CollarControlEntity, collar_device_info
from .protocol import (
    CORRECTION_LEVELS,
    INSTRUCTION_ADJUST_ENTER,
    INSTRUCTION_ADJUST_SAVE,
    INSTRUCTION_MODE,
    MODE_CODES,
    MODE_COUNT_ONLY,
    SENSITIVITY_LEVELS,
    TRAINING_LEVELS,
    CollarState,
    CommandError,
    settings_frame,
)

Plan = Callable[[CollarState], list[tuple[bytes, bool]]]


def _correction_level(value: int) -> Plan:
    def plan(state: CollarState) -> list[tuple[bytes, bool]]:
        if state.mode_code != MODE_CODES["manual"]:
            raise CommandError("the correction level applies in Manual mode; select Manual first")
        return [(settings_frame(state, INSTRUCTION_MODE, correction_level=value), True)]
    return plan


def _auto_increase(*, low: int | None = None, high: int | None = None) -> Plan:
    def plan(state: CollarState) -> list[tuple[bytes, bool]]:
        if state.mode_code != MODE_CODES["auto_increase"]:
            raise CommandError(
                "the Auto Increase range applies in Auto Increase mode; select it first"
            )
        return [(settings_frame(state, INSTRUCTION_MODE, auto_increase_min=low,
                                auto_increase_max=high), True)]
    return plan


def _sensitivities(*, sound: int | None = None, vibration: int | None = None) -> Plan:
    # Sequence: begin sensitivity adjustment (stimulation suspended), then save the new
    # values. Both written with response.
    def plan(state: CollarState) -> list[tuple[bytes, bool]]:
        return [
            (settings_frame(state, INSTRUCTION_ADJUST_ENTER, mode=MODE_COUNT_ONLY), True),
            (settings_frame(state, INSTRUCTION_ADJUST_SAVE, sound_sensitivity=sound,
                            vibration_sensitivity=vibration), True),
        ]
    return plan


@dataclass(frozen=True, kw_only=True)
class CollarNumberDescription(NumberEntityDescription):
    value: Callable[[CollarState], int | None]
    plan: Callable[[int], Plan]


NUMBERS: tuple[CollarNumberDescription, ...] = (
    CollarNumberDescription(
        key="correction_level_set", translation_key="correction_level_set",
        icon="mdi:flash", mode=NumberMode.SLIDER,
        native_min_value=CORRECTION_LEVELS.start, native_max_value=CORRECTION_LEVELS.stop - 1,
        native_step=1,
        value=lambda s: s.correction_level if s.mode_code == MODE_CODES["manual"] else None,
        plan=_correction_level,
    ),
    CollarNumberDescription(
        key="auto_increase_min_set", translation_key="auto_increase_min_set",
        icon="mdi:arrow-collapse-down", mode=NumberMode.SLIDER,
        native_min_value=CORRECTION_LEVELS.start, native_max_value=CORRECTION_LEVELS.stop - 1,
        native_step=1,
        value=lambda s: (s.auto_increase_levels[1]
                         if s.mode_code == MODE_CODES["auto_increase"] else None),
        plan=lambda v: _auto_increase(low=v),
    ),
    CollarNumberDescription(
        key="auto_increase_max_set", translation_key="auto_increase_max_set",
        icon="mdi:arrow-collapse-up", mode=NumberMode.SLIDER,
        native_min_value=CORRECTION_LEVELS.start, native_max_value=CORRECTION_LEVELS.stop - 1,
        native_step=1,
        value=lambda s: (s.auto_increase_levels[2]
                         if s.mode_code == MODE_CODES["auto_increase"] else None),
        plan=lambda v: _auto_increase(high=v),
    ),
    CollarNumberDescription(
        key="sound_sensitivity_set", translation_key="sound_sensitivity_set",
        icon="mdi:microphone", mode=NumberMode.SLIDER,
        native_min_value=SENSITIVITY_LEVELS.start, native_max_value=SENSITIVITY_LEVELS.stop - 1,
        native_step=1,
        value=lambda s: s.sound_sensitivity,
        plan=lambda v: _sensitivities(sound=v),
    ),
    CollarNumberDescription(
        key="vibration_sensitivity_set", translation_key="vibration_sensitivity_set",
        icon="mdi:vibrate", mode=NumberMode.SLIDER,
        native_min_value=SENSITIVITY_LEVELS.start, native_max_value=SENSITIVITY_LEVELS.stop - 1,
        native_step=1,
        value=lambda s: s.vibration_sensitivity,
        plan=lambda v: _sensitivities(vibration=v),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: CollarConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([*(CollarNumber(coordinator, d) for d in NUMBERS), NickLevel(coordinator)])


class CollarNumber(CollarControlEntity, NumberEntity):
    """A setting the collar holds; shows what it last reported, writes on change."""

    entity_description: CollarNumberDescription

    def __init__(self, coordinator: CollarCoordinator, description: CollarNumberDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return None if data is None else self.entity_description.value(data)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_command(
            self.entity_description.plan(int(value)), changes_settings=True
        )


class NickLevel(RestoreNumber):
    """The level the Nick button sends. Held in Home Assistant, not on the collar, and
    restored across restarts. Its maximum is the cap from the integration's options."""

    _attr_has_entity_name = True
    _attr_translation_key = "nick_level"
    _attr_icon = "mdi:flash-alert"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = TRAINING_LEVELS.start
    _attr_native_step = 1

    def __init__(self, coordinator: CollarCoordinator) -> None:
        address = coordinator.device.address
        self.coordinator = coordinator
        self._attr_native_max_value = coordinator.max_nick_level
        self._attr_unique_id = f"{address}_nick_level"
        self._attr_device_info = collar_device_info(address)
        self._attr_native_value = coordinator.nick_level

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._set(int(last.native_value))

    def _set(self, value: int) -> None:
        value = max(TRAINING_LEVELS.start, min(value, self.coordinator.max_nick_level))
        self._attr_native_value = value
        self.coordinator.nick_level = value

    async def async_set_native_value(self, value: float) -> None:
        self._set(int(value))
        self.async_write_ha_state()
