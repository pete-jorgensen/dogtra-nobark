"""Polls the collar's status frame on a timer, but only while the collar is demonstrably advertising.

Built on Home Assistant's ``ActiveBluetoothDataUpdateCoordinator``, for three reasons that
all matter on a device worn by a dog:

* **A poll needs a fresh advertisement.** The collar only advertises when nothing is
  connected to it, so a poll never fires while the phone app holds the link -- the failed
  connects a blind timer would rack up simply do not happen.
* **The route is the freshest one.** ``service_info.device`` is the ``BLEDevice`` bound to
  whichever adapter or proxy just heard the collar, so as the dog moves between rooms the
  connection follows without any route-refresh code here.
* **Availability is presence, not poll success.** Entities stay on the last good frame while
  the collar is in earshot and go unavailable only when it has not been heard for HA's
  standard unavailability window, so opening the app briefly does not blank the sensors.

**The base class alone never polls this collar after power-on.** It considers a poll only
inside its advertisement callback, and Home Assistant's Bluetooth manager does not call back
for an advertisement identical to the previous one -- and this collar's advertisement
carries nothing that changes, so the base class reads it in the seconds after power-on
and then never again, however long it stays in range. HA still timestamps every
advertisement it suppresses, so ``_async_check_poll`` asks on a timer for the latest one and replays it through the same callback; the interval gate, the
debouncer and the poll itself stay the base class's.

Every poll is connect → read one characteristic → disconnect. Commands (``async_command``)
are the one other kind of connection, made only when an entity asks; they share the
device's lock with polls. A training command's read-back becomes the coordinator's data. A
settings command's cannot: the collar applies settings only when the link closes, so the
read-back on the command's own connection is the old state. The coordinator shows what was
written and forces a poll a few seconds after the disconnect, which replaces it with what
the collar actually holds.

**A collar that goes silent must be explicable afterwards.** A connect that hangs can leave
the collar unheard for a long time; without a record of how strongly it was last heard and
what the connect returned, a link left up somewhere cannot be told from a dog that has
walked off. ``PollDiagnostics`` keeps exactly those facts, and they stay readable while the
collar itself is unavailable, which is when they are wanted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import time

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_last_service_info,
    async_scanner_by_source,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    CONFIRM_FIRST,
    CONFIRM_RETRY,
    CONFIRM_TRIES,
    DEFAULT_MAX_NICK_LEVEL,
    POLL_CHECK_INTERVAL,
)
from .device import CollarConnectionError, CollarDevice, CommandPlan
from .polling import (
    advert_is_fresh,
    poll_due,
    signal_ok,
    silent_after_failed_connect,
)
from .protocol import MODE_CODES, TRAINING_LEVELS, CollarState, parse_status

_LOGGER = logging.getLogger(__name__)


CONNECT_OK = "ok"
CONNECT_FAILED = "failed"
CONNECT_SKIPPED = "skipped_weak_signal"


@dataclass
class PollDiagnostics:
    """What was last heard of the collar, and how the last connect went.

    ``heard_*`` is refreshed when a poll starts and when the collar goes quiet or comes
    back -- not on every advertisement, which would write a state a minute for a value
    nobody reads while things work. Once the collar is silent it therefore holds the FINAL
    advertisement: when, how strong, and through which adapter or proxy.
    """

    heard_at: datetime | None = None
    heard_rssi: int | None = None
    heard_source: str | None = None
    connect_result: str | None = None
    connect_error: str | None = None
    connect_at: datetime | None = None
    connect_seconds: float | None = None
    connect_rssi: int | None = None
    connect_source: str | None = None
    silent_after_failed_connect: bool = False


class CollarCoordinator(ActiveBluetoothDataUpdateCoordinator[CollarState]):
    """One connect-read-disconnect per interval, gated on the collar being heard."""

    def __init__(self, hass: HomeAssistant, device: CollarDevice) -> None:
        self._polling = False
        self._last_poll_end: float | None = None
        self._connect_failed = False
        self._was_fresh: bool | None = None
        self.diagnostics = PollDiagnostics()
        #: The Manual-mode level last seen on the collar. The status frame reads 0 in the
        #: other modes, so switching back to Manual restores this rather than guessing.
        self.last_manual_level: int | None = None
        #: The Nick level (held by the Nick level entity) and its ceiling (the options).
        self.nick_level: int = TRAINING_LEVELS.start
        self.max_nick_level: int = DEFAULT_MAX_NICK_LEVEL
        #: A settings command is waiting for a poll to confirm what the collar holds, with
        #: this many forced polls left to try.
        self.confirm_pending = False
        self._confirm_tries = 0
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

    def _needs_poll(
        self, service_info: BluetoothServiceInfoBleak, _last_poll: float | None
    ) -> bool:
        # The base class's poll age is ignored on purpose, for two reasons. It is measured
        # to the ADVERTISEMENT's receive time, and a replayed advertisement is always a
        # little older than now, so every read would land one timer tick late (six minutes,
        # not five). And it is stamped only when a poll ENDS, so anything arriving during a
        # read (up to POLL_DEADLINE long) still looks due and the debouncer queues a second
        # connect straight after the first.
        if self._polling:
            return False
        age = None if self._last_poll_end is None else time.monotonic() - self._last_poll_end
        if not poll_due(age):
            return False
        if not signal_ok(service_info.rssi):
            self._note_skipped(service_info)
            return False
        return True

    async def _poll(self, service_info: BluetoothServiceInfoBleak) -> CollarState:
        self._polling = True
        started = time.monotonic()
        self._note_heard(service_info)
        diag = self.diagnostics
        diag.connect_at = datetime.now(UTC)
        diag.connect_rssi = service_info.rssi
        diag.connect_source = self._scanner_name(service_info.source)
        # Assumed failed until the frame is in hand, so a cancelled poll counts as one.
        diag.connect_result, diag.connect_error = CONNECT_FAILED, "cancelled"
        try:
            state = await self.device.async_read_status(service_info.device)
        except Exception as err:
            diag.connect_error = str(err) or type(err).__name__
            raise
        else:
            diag.connect_result, diag.connect_error = CONNECT_OK, None
            self._note_state(state)
            self.confirm_pending = False
            return state
        finally:
            ended = time.monotonic()
            self._polling = False
            if self.confirm_pending and diag.connect_result != CONNECT_OK:
                self._schedule_confirm(CONFIRM_RETRY)
            self._last_poll_end = ended
            self._connect_failed = diag.connect_result != CONNECT_OK
            diag.connect_seconds = round(ended - started, 1)
            if self._connect_failed:
                # The base class tells listeners only about a poll that succeeded.
                self.async_update_listeners()

    def _note_state(self, state: CollarState) -> None:
        if state.mode_code == MODE_CODES["manual"] and state.correction_level:
            self.last_manual_level = state.correction_level

    async def async_command(
        self, plan: CommandPlan, *, changes_settings: bool = False
    ) -> CollarState:
        """Send one command and publish the state it leaves the collar in.

        Raises ``HomeAssistantError`` with the reason, so a press that did not reach the
        collar says so in the UI instead of looking like it worked.
        """
        written: list[bytes] = []

        def recording(state: CollarState) -> Sequence[tuple[bytes, bool]]:
            frames = plan(state)
            written[:] = [frame for frame, _ in frames]
            return frames

        ble_device = async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device is None:
            raise HomeAssistantError(
                "The collar is not in range of a Bluetooth adapter or proxy that can "
                "connect to it (or the Dogtra phone app is connected to it)"
            )
        try:
            state = await self.device.async_command(ble_device, recording)
        except CollarConnectionError as err:
            raise HomeAssistantError(str(err)) from err
        if changes_settings and written:
            # The read-back predates the change. Show the settings just written (bytes 1-18
            # of the last frame -- the save, not the adjust-enter), until a poll confirms.
            state = parse_status(state.raw[:1] + written[-1][1:19] + state.raw[19:])
            self.confirm_pending = True
            self._confirm_tries = CONFIRM_TRIES
            self._schedule_confirm(CONFIRM_FIRST)
        self._note_state(state)
        self.data = state
        self.async_update_listeners()
        return state

    def _schedule_confirm(self, delay: float) -> None:
        if self._confirm_tries > 0:
            self._on_stop.append(async_call_later(self.hass, delay, self._async_confirm))

    @callback
    def _async_confirm(self, _now: datetime | None = None) -> None:
        """Poll now, out of turn, unless a poll has already confirmed the last command."""
        if not self.confirm_pending or self._confirm_tries <= 0:
            return
        if self._polling:
            # A poll is under way; its own end schedules the next try if it fails.
            return
        self._confirm_tries -= 1
        self._last_poll_end = None
        self._async_check_poll()

    def _scanner_name(self, source: str) -> str:
        scanner = async_scanner_by_source(self.hass, source)
        return scanner.name if scanner is not None else source

    def _note_heard(self, service_info: BluetoothServiceInfoBleak) -> None:
        age = max(0.0, time.monotonic() - service_info.time)
        diag = self.diagnostics
        diag.heard_at = datetime.now(UTC) - timedelta(seconds=age)
        diag.heard_rssi = service_info.rssi
        diag.heard_source = self._scanner_name(service_info.source)

    def _note_skipped(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Record that a due poll was withheld -- once per run of them, not every minute."""
        diag = self.diagnostics
        if diag.connect_result == CONNECT_SKIPPED:
            return
        diag.connect_result, diag.connect_error = CONNECT_SKIPPED, None
        diag.connect_at = datetime.now(UTC)
        diag.connect_seconds = None
        diag.connect_rssi = service_info.rssi
        diag.connect_source = self._scanner_name(service_info.source)
        self._connect_failed = False
        self.async_update_listeners()

    @callback
    def _async_start(self) -> None:
        """Start the base class's callbacks, and the timer that stands in for them."""
        super()._async_start()
        self._on_stop.append(
            async_track_time_interval(
                self.hass, self._async_check_poll, POLL_CHECK_INTERVAL
            )
        )

    @callback
    def _async_check_poll(self, _now: datetime | None = None) -> None:
        """Replay the collar's latest advertisement if a poll is due and it is still fresh."""
        service_info = async_last_service_info(self.hass, self.address, connectable=True)
        if service_info is None:
            return
        # time.monotonic() shares its epoch with HA's coarse monotonic clock, which stamps
        # service_info.time; they differ by milliseconds against a threshold of a minute.
        now = time.monotonic()
        fresh = advert_is_fresh(now, service_info.time)
        silent = silent_after_failed_connect(
            now=now,
            connect_failed=self._connect_failed,
            connect_ended=self._last_poll_end,
            last_heard=service_info.time,
        )
        if fresh != self._was_fresh or silent != self.diagnostics.silent_after_failed_connect:
            # Gone quiet, come back, or the silence has just become attributable.
            self._was_fresh = fresh
            self.diagnostics.silent_after_failed_connect = silent
            self._note_heard(service_info)
            self.async_update_listeners()
        if not fresh:
            return
        if self.needs_poll(service_info):
            self._async_handle_bluetooth_event(service_info, BluetoothChange.ADVERTISEMENT)
