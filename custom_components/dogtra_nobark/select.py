"""The correction mode, settable."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CollarConfigEntry
from .entity import CollarControlEntity
from .protocol import (
    CORRECTION_LEVELS,
    INSTRUCTION_MODE,
    MODE_CODES,
    CollarState,
    settings_frame,
)

#: Selecting a mode here does not touch the Auto Increase min/max range: switching to Auto
#: Increase keeps the range the collar already holds (the range itself is set with the
#: Auto Increase number entities).
OPTIONS = ["manual", "auto_increase", "pager", "count_only"]


async def async_setup_entry(
    hass: HomeAssistant, entry: CollarConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([CollarModeSelect(entry.runtime_data)])


class CollarModeSelect(CollarControlEntity, SelectEntity):
    """What the collar does when the dog barks."""

    _attr_translation_key = "mode_select"
    _attr_icon = "mdi:tune-variant"
    _attr_options = OPTIONS

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mode_select")

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        return data.mode if data is not None and data.mode in OPTIONS else None

    async def async_select_option(self, option: str) -> None:
        code = MODE_CODES[option]
        # Manual needs a level, and the status frame reads 0 outside Manual: restore the
        # last Manual level seen, else the lowest one.
        level = self.coordinator.last_manual_level or CORRECTION_LEVELS.start

        def plan(state: CollarState) -> list[tuple[bytes, bool]]:
            if option == "manual":
                return [(settings_frame(state, INSTRUCTION_MODE, mode=code,
                                        correction_level=level), True)]
            return [(settings_frame(state, INSTRUCTION_MODE, mode=code), True)]

        await self.coordinator.async_command(plan, changes_settings=True)
