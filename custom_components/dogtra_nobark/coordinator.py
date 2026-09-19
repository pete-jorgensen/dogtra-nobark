"""Polls the collar's status frame, but only while the collar is demonstrably advertising.

Built on Home Assistant's ``ActiveBluetoothDataUpdateCoordinator`` rather than a blind
timer, for three reasons that all matter on a device worn by a dog:

* **A poll is triggered by an advertisement.** The collar only advertises when nothing is
  connected to it, so a poll never fires while the phone app holds the link -- the failed
  connects a timer would rack up simply do not happen.
* **The route is the freshest one.** ``service_info.device`` is the ``BLEDevice`` bound to
  whichever adapter or proxy just heard the collar, so as the dog moves between rooms the
  connection follows without any route-refresh code here.
* **Availability is presence, not poll success.** Entities stay on the last good frame while
  the collar is in earshot and go unavailable only when it has not been heard for HA's
  standard unavailability window. Opening the app for thirty seconds no longer blanks every
  sensor for five minutes.

Every connection here is still connect → read one characteristic → disconnect, and the
package contains no write path (see ``device.py`` and ``tests/test_read_only.py``).
"""

from __future__ import annotations

import logging

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import HomeAssistant

from .const import SCAN_INTERVAL
from .device import CollarDevice
from .protocol import CollarState

_LOGGER = logging.getLogger(__name__)


class CollarCoordinator(ActiveBluetoothDataUpdateCoordinator[CollarState]):
    """One connect-read-disconnect per interval, gated on the collar being heard."""

    def __init__(self, hass: HomeAssistant, device: CollarDevice) -> None:
        super().__init__(
            hass,
            _LOGGER,
            address=device.address,
            # PASSIVE is enough: the collar puts its name in the advertisement itself, and
            # nothing else it broadcasts is useful. Active scanning would cost the proxies
            # airtime for no data.
            mode=BluetoothScanningMode.PASSIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._poll,
            connectable=True,
        )
        self.device = device

    @staticmethod
    def _needs_poll(_service_info: BluetoothServiceInfoBleak, last_poll: float | None) -> bool:
        """Poll on the first advertisement after start, then no more than once per interval."""
        return last_poll is None or last_poll >= SCAN_INTERVAL.total_seconds()

    async def _poll(self, service_info: BluetoothServiceInfoBleak) -> CollarState:
        return await self.device.async_read_status(service_info.device)
