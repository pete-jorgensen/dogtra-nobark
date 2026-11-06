"""The app's remote buttons: Page (vibration) and Nick (one short pulse of stimulation)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CollarConfigEntry
from .coordinator import CollarCoordinator
from .entity import CollarControlEntity
from .protocol import TRAINING_NICK, TRAINING_PAGER, training_frame


async def async_setup_entry(
    hass: HomeAssistant, entry: CollarConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([PageButton(coordinator), NickButton(coordinator)])


class PageButton(CollarControlEntity, ButtonEntity):
    _attr_translation_key = "page"
    _attr_icon = "mdi:vibrate"

    def __init__(self, coordinator: CollarCoordinator) -> None:
        super().__init__(coordinator, "page")

    async def async_press(self) -> None:
        await self.coordinator.async_command(
            lambda _state: [(training_frame(TRAINING_PAGER), False)]
        )


class NickButton(CollarControlEntity, ButtonEntity):
    """Sends one Nick at the level the Nick level entity holds (capped in the options)."""

    _attr_translation_key = "nick"
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator: CollarCoordinator) -> None:
        super().__init__(coordinator, "nick")

    async def async_press(self) -> None:
        # The cap is applied again here, not only on the slider: the button is what sends.
        level = min(self.coordinator.nick_level, self.coordinator.max_nick_level)
        await self.coordinator.async_command(
            lambda _state: [(training_frame(TRAINING_NICK, level), False)]
        )
