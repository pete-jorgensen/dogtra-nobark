"""What every entity of one collar shares."""

from __future__ import annotations

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import CollarCoordinator


def collar_device_info(address: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, address)},
        connections={(CONNECTION_BLUETOOTH, address)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name=f"Dogtra collar {address[-5:].replace(':', '')}",
    )


class CollarDiagnosticEntity(PassiveBluetoothCoordinatorEntity[CollarCoordinator]):
    """An entity about our contact with the collar, not about the collar.

    Always available: these say what was last heard and how the last connect went, and the
    moment they are needed is the moment the collar itself is unavailable.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: CollarCoordinator, key: str) -> None:
        super().__init__(coordinator)
        address = coordinator.device.address
        self._attr_unique_id = f"{address}_{key}"
        self._attr_device_info = collar_device_info(address)

    @property
    def available(self) -> bool:
        return True


class CollarControlEntity(PassiveBluetoothCoordinatorEntity[CollarCoordinator]):
    """An entity that sends a command to the collar.

    Available while the collar is heard and a frame has been read: every command echoes
    the collar's current settings, and a press with nothing in range can only fail.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: CollarCoordinator, key: str) -> None:
        super().__init__(coordinator)
        address = coordinator.device.address
        self._attr_unique_id = f"{address}_{key}"
        self._attr_device_info = collar_device_info(address)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
