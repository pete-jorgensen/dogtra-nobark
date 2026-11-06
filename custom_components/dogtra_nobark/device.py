"""Transport: connect over the route Home Assistant hands us, read one frame, disconnect.

ONE WRITE PATH. The collar delivers corrections to a dog, so exactly one function here
writes to it -- ``async_command`` -- and only to the command characteristic, only frames
built by ``protocol.py``. There is no notification subscription and no pairing.
``tests/test_write_paths.py`` fails if a write appears anywhere else in the package.

WORKS THROUGH BLUETOOTH PROXIES. The ``BLEDevice`` arrives from the coordinator's
advertisement callback, already bound to whichever adapter or ESPHome/bt-proxy heard the
collar. Nothing here constructs a ``BleakClient`` from a bare address -- that would pin the
integration to the HA host's own adapter, which is the usual reason an integration "does
not work over my proxy".
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
import logging

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    COMMAND_SETTLE,
    COMMAND_UUID,
    CONNECT_ATTEMPTS,
    POLL_DEADLINE,
    STATUS_UUID,
)
from .protocol import CollarState, FrameError, parse_status

_LOGGER = logging.getLogger(__name__)


class CollarConnectionError(Exception):
    """Could not reach the collar, or it did not answer."""


#: What a command sends, decided from the frame read on the same connection: each entry is
#: (frame, write with response). See ``protocol.py`` for the builders. Settings frames
#: are written WITH response; only the training buttons are written without.
CommandPlan = Callable[[CollarState], Sequence[tuple[bytes, bool]]]


def _is_missing_characteristic(err: Exception) -> bool:
    """The signature of a stale GATT cache.

    Proxies do not honour Service Changed, so after a firmware update -- or a proxy that
    cached a wrong table -- the read fails with "characteristic ... not found" on every
    poll, forever, unless the cache is cleared. Matched on the message because bleak's
    exception class for it is not stable across versions.
    """
    return "not found" in str(err).lower()


class CollarDevice:
    """One collar, addressed by its Bluetooth address."""

    def __init__(self, address: str) -> None:
        self.address = address
        # The collar takes one connection at a time: a poll and a command must not race.
        self._lock = asyncio.Lock()

    async def async_read_status(self, ble_device: BLEDevice) -> CollarState:
        """Connect, read the status frame once, disconnect -- within one deadline.

        The collar drops an idle link after roughly 40 s and the phone app holds it
        exclusively while open, so a held connection is neither possible nor wanted. The
        whole exchange runs under ``POLL_DEADLINE`` so a link the collar drops mid-read can
        never leave a poll pending unbounded.
        """
        async with self._lock:
            try:
                async with asyncio.timeout(POLL_DEADLINE):
                    return await self._read_with_cache_recovery(ble_device)
            except TimeoutError as err:
                raise CollarConnectionError(
                    f"{self.address}: no status frame within {POLL_DEADLINE:.0f} s"
                ) from err

    async def async_command(self, ble_device: BLEDevice, plan: CommandPlan) -> CollarState:
        """Connect, read the current frame, write what ``plan`` builds from it, read back.

        The frame is read on the same connection the command goes out on, so a settings
        command echoes what the collar holds now, not what the last poll saw. The read-back
        is the collar's answer; the caller shows it rather than assuming the write took.
        """
        async with self._lock:
            try:
                async with asyncio.timeout(POLL_DEADLINE):
                    return await self._command(ble_device, plan)
            except TimeoutError as err:
                raise CollarConnectionError(
                    f"{self.address}: command not completed within {POLL_DEADLINE:.0f} s"
                ) from err

    async def _command(self, ble_device: BLEDevice, plan: CommandPlan) -> CollarState:
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.address,
                max_attempts=CONNECT_ATTEMPTS,
            )
        except Exception as err:  # noqa: BLE001 -- bleak raises a wide family
            raise CollarConnectionError(f"could not connect to {self.address}: {err}") from err
        try:
            state = parse_status(bytes(await client.read_gatt_char(STATUS_UUID)))
            for frame, response in plan(state):
                _LOGGER.debug("%s: write %s (response=%s)", self.address, frame.hex(), response)
                await client.write_gatt_char(COMMAND_UUID, frame, response=response)
            await asyncio.sleep(COMMAND_SETTLE)
            return parse_status(bytes(await client.read_gatt_char(STATUS_UUID)))
        except FrameError as err:
            raise CollarConnectionError(f"{self.address}: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise CollarConnectionError(f"{self.address}: command failed: {err}") from err
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 -- best effort
                _LOGGER.debug("%s: disconnect failed", self.address, exc_info=True)

    async def _read_with_cache_recovery(self, ble_device: BLEDevice) -> CollarState:
        """One read; on a stale service cache, clear it and read once more."""
        for attempt in (1, 2):
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    self.address,
                    max_attempts=CONNECT_ATTEMPTS,
                )
            except Exception as err:  # noqa: BLE001 -- bleak raises a wide family
                raise CollarConnectionError(
                    f"could not connect to {self.address}: {err}"
                ) from err
            try:
                raw = bytes(await client.read_gatt_char(STATUS_UUID))
            except Exception as err:  # noqa: BLE001
                if attempt == 1 and _is_missing_characteristic(err):
                    _LOGGER.debug(
                        "%s: status characteristic missing from the cached services; "
                        "clearing the cache and reconnecting", self.address,
                    )
                    try:
                        await client.clear_cache()
                    except Exception:  # noqa: BLE001 -- best effort; the reconnect is the fix
                        _LOGGER.debug("%s: clear_cache failed", self.address, exc_info=True)
                    continue
                raise CollarConnectionError(
                    f"{self.address}: status read failed: {err}"
                ) from err
            finally:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001 -- best effort; the read already happened
                    _LOGGER.debug("%s: disconnect failed", self.address, exc_info=True)
            try:
                return parse_status(raw)
            except FrameError as err:
                raise CollarConnectionError(
                    f"{self.address}: {err} (raw {raw.hex()})"
                ) from err
        raise CollarConnectionError(f"{self.address}: unreachable after clearing the cache")
