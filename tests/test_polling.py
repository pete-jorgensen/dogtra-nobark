"""The collar must keep being read when its advertisement never changes.

What this pins: Home Assistant calls an integration back only for an advertisement that
DIFFERS from the previous one, this collar's never does, and a coordinator that polls only
from that callback reads the collar at power-on and then never again.

Home Assistant is not installed where these tests run, so the wiring test loads
``coordinator.py`` against a small model of the HA classes it builds on. The model copies
the two behaviours that matter from HA 2026.9.1's ``active_update_coordinator.py``: a poll is
considered only inside ``_async_handle_bluetooth_event``, and the last-poll time is stamped
when a poll ends. It proves OUR timer reaches that path; it cannot prove HA still behaves as
modelled.
"""

from __future__ import annotations

import asyncio
import importlib
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADDRESS = "AA:BB:CC:DD:EE:FF"


class _Clock:
    now = 1000.0


def _install_fake_homeassistant(monkeypatch, history: dict) -> None:
    """Register just enough of ``homeassistant`` for coordinator.py to import and run."""

    class ActiveBluetoothDataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, hass, logger, *, address, mode, needs_poll_method,
                     poll_method, connectable):
            self.hass = hass
            self.address = address
            self._needs_poll_method = needs_poll_method
            self._poll_method = poll_method
            self._last_poll = None
            self._on_stop = []
            self.polls = 0
            self.listener_updates = 0

        def async_update_listeners(self):
            self.listener_updates += 1

        def needs_poll(self, service_info):
            poll_age = None
            if self._last_poll:
                poll_age = service_info.time - self._last_poll
            return self._needs_poll_method(service_info, poll_age)

        def _async_start(self):
            pass

        def _async_handle_bluetooth_event(self, service_info, change):
            if self.needs_poll(service_info):
                self.polls += 1
                try:
                    asyncio.run(self._poll_method(service_info))
                finally:
                    self._last_poll = _Clock.now

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    tracked = []
    later = []
    module("homeassistant")
    module("homeassistant.components")
    module(
        "homeassistant.components.bluetooth",
        BluetoothChange=types.SimpleNamespace(ADVERTISEMENT="advertisement"),
        BluetoothScanningMode=types.SimpleNamespace(PASSIVE="passive"),
        BluetoothServiceInfoBleak=object,
        async_last_service_info=lambda hass, address, connectable: history.get(address),
        async_ble_device_from_address=lambda hass, address, connectable: object(),
        async_scanner_by_source=lambda hass, source: types.SimpleNamespace(name=f"proxy-{source}"),
    )
    module(
        "homeassistant.components.bluetooth.active_update_coordinator",
        ActiveBluetoothDataUpdateCoordinator=ActiveBluetoothDataUpdateCoordinator,
    )
    module("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    module("homeassistant.exceptions", HomeAssistantError=type("HomeAssistantError", (Exception,), {}))
    module(
        "homeassistant.helpers.event",
        async_track_time_interval=lambda hass, action, interval: (
            tracked.append((action, interval)) or (lambda: None)
        ),
        async_call_later=lambda hass, delay, action: (
            later.append((delay, action)) or (lambda: None)
        ),
        tracked=tracked,
        later=later,
    )
    module("homeassistant.helpers")
    # device.py needs bleak only to talk to a collar; the fake device below replaces it.
    module("bleak")
    module("bleak.backends")
    module("bleak.backends.device", BLEDevice=object)
    module(
        "bleak_retry_connector",
        BleakClientWithServiceCache=object, establish_connection=None,
    )


@pytest.fixture
def coordinator(monkeypatch):
    history: dict = {}
    _install_fake_homeassistant(monkeypatch, history)
    # Import the package's modules without running its __init__ (which pulls in all of HA).
    pkg = types.ModuleType("dogtra_nobark")
    pkg.__path__ = [str(ROOT / "custom_components" / "dogtra_nobark")]
    monkeypatch.setitem(sys.modules, "dogtra_nobark", pkg)
    for name in [m for m in sys.modules if m.startswith("dogtra_nobark.")]:
        monkeypatch.delitem(sys.modules, name)
    mod = importlib.import_module("dogtra_nobark.coordinator")
    _Clock.now = 1000.0
    monkeypatch.setattr(mod.time, "monotonic", lambda: _Clock.now)

    class FakeDevice:
        address = ADDRESS

        async def async_read_status(self, _ble_device):
            return types.SimpleNamespace(mode_code=2, correction_level=0)

    coord = mod.CollarCoordinator(object(), FakeDevice())
    coord._async_start()
    coord.history = history
    return coord


def _advert(at: float, rssi: int | None = -60):
    """The same advertisement every time -- only the receive time and the signal move."""
    return types.SimpleNamespace(
        time=at, device=object(), name="DOGTRA_eeff", rssi=rssi, source="A1"
    )


def _tick(coord, *, at: float, heard_at: float | None = None, rssi: int | None = -60) -> None:
    _Clock.now = at
    if heard_at is not None:
        coord.history[ADDRESS] = _advert(heard_at, rssi)
    coord._async_check_poll()


def _fail_reads(coord) -> None:
    async def boom(_ble_device):
        _Clock.now += 33.0  # a connect that hangs
        raise RuntimeError("link dropped")

    coord.device.async_read_status = boom


def test_the_timer_is_registered(coordinator):
    events = sys.modules["homeassistant.helpers.event"]
    assert len(events.tracked) == 1
    assert len(coordinator._on_stop) == 1, "the timer must be cancelled on unload"


def test_an_unchanged_advertisement_is_still_polled_every_interval(coordinator):
    # Power-on: HA does call back once for the first advertisement.
    coordinator.history[ADDRESS] = _advert(1000.0)
    coordinator._async_handle_bluetooth_event(coordinator.history[ADDRESS], "advertisement")
    assert coordinator.polls == 1
    # From here HA stays silent: identical advertisements only refresh the history.
    for minute in range(1, 61):
        at = 1000.0 + 60 * minute
        _tick(coordinator, at=at, heard_at=at - 2)
    assert coordinator.polls == 1 + 12, "one read per five minutes across the hour"


def test_no_poll_before_the_interval_has_passed(coordinator):
    _tick(coordinator, at=1000.0, heard_at=999.0)
    _tick(coordinator, at=1060.0, heard_at=1059.0)
    _tick(coordinator, at=1240.0, heard_at=1239.0)
    assert coordinator.polls == 1


def test_no_poll_while_the_collar_is_silent(coordinator):
    # Heard once, then the phone app takes the link and the collar stops advertising.
    _tick(coordinator, at=1000.0, heard_at=999.0)
    for minute in range(1, 31):
        _tick(coordinator, at=1000.0 + 60 * minute)
    assert coordinator.polls == 1
    # The app closes; the collar is heard again and the overdue read happens within a tick.
    _tick(coordinator, at=2860.0, heard_at=2858.0)
    assert coordinator.polls == 2


def test_no_poll_when_the_collar_was_never_heard(coordinator):
    _tick(coordinator, at=1000.0)
    assert coordinator.polls == 0


def test_nothing_is_due_while_a_read_is_in_flight(coordinator):
    coordinator.history[ADDRESS] = _advert(999.0)
    coordinator._polling = True
    assert coordinator.needs_poll(coordinator.history[ADDRESS]) is False
    coordinator._polling = False
    assert coordinator.needs_poll(coordinator.history[ADDRESS]) is True


def test_a_failed_read_clears_the_in_flight_flag(coordinator):
    async def boom(_ble_device):
        raise RuntimeError("link dropped")

    coordinator.device.async_read_status = boom
    with pytest.raises(RuntimeError):
        _tick(coordinator, at=1000.0, heard_at=999.0)
    assert coordinator._polling is False


# --- a collar that goes silent must be explicable afterwards -----------------------


def test_a_weak_signal_withholds_the_connect_and_says_so(coordinator):
    _tick(coordinator, at=1000.0, heard_at=999.0, rssi=-92)
    assert coordinator.polls == 0
    diag = coordinator.diagnostics
    assert (diag.connect_result, diag.connect_rssi) == ("skipped_weak_signal", -92)
    written = coordinator.listener_updates
    for minute in range(1, 6):
        _tick(coordinator, at=1000.0 + 60 * minute, heard_at=999.0 + 60 * minute, rssi=-90)
    assert coordinator.polls == 0
    assert coordinator.listener_updates == written, "one record per run of skips, not one a minute"
    # The dog walks back towards a proxy: read within a tick.
    _tick(coordinator, at=1360.0, heard_at=1359.0, rssi=-70)
    assert coordinator.polls == 1
    assert coordinator.diagnostics.connect_result == "ok"


def test_an_advertisement_with_no_signal_reading_is_still_polled(coordinator):
    _tick(coordinator, at=1000.0, heard_at=999.0, rssi=None)
    assert coordinator.polls == 1


def test_a_read_records_how_it_went(coordinator):
    _tick(coordinator, at=1000.0, heard_at=999.0, rssi=-64)
    diag = coordinator.diagnostics
    assert (diag.connect_result, diag.connect_error) == ("ok", None)
    assert (diag.connect_rssi, diag.connect_source) == (-64, "proxy-A1")
    assert (diag.heard_rssi, diag.heard_source) == (-64, "proxy-A1")
    assert diag.heard_at is not None and diag.connect_seconds == 0.0


def test_silence_after_a_failed_connect_is_flagged_and_clears_when_heard_again(coordinator):
    _fail_reads(coordinator)
    with pytest.raises(RuntimeError):
        _tick(coordinator, at=1000.0, heard_at=999.0, rssi=-58)
    diag = coordinator.diagnostics
    assert (diag.connect_result, diag.connect_error) == ("failed", "link dropped")
    assert diag.connect_seconds == 33.0
    assert coordinator.listener_updates >= 1, "a failed poll must reach the entities itself"
    # 60 s after the connect ended: too soon to blame it.
    _tick(coordinator, at=1093.0)
    assert diag.silent_after_failed_connect is False
    _tick(coordinator, at=1153.0)
    assert diag.silent_after_failed_connect is True
    assert diag.heard_rssi == -58, "the FINAL advertisement is what is kept"
    written = coordinator.listener_updates
    for minute in range(1, 30):
        _tick(coordinator, at=1153.0 + 60 * minute)
    assert coordinator.listener_updates == written, "nothing is rewritten while nothing changes"
    # Power-cycled: heard again, and the flag goes before the next read is even due.
    async def fine(_ble_device):
        return types.SimpleNamespace(mode_code=2, correction_level=0)

    coordinator.device.async_read_status = fine
    _tick(coordinator, at=3000.0, heard_at=2999.0)
    assert diag.silent_after_failed_connect is False
    assert coordinator.polls == 2 and diag.connect_result == "ok"


def test_a_failed_connect_is_not_blamed_while_the_collar_keeps_advertising(coordinator):
    # The phone app case: the connect fails, the collar is still there.
    _fail_reads(coordinator)
    with pytest.raises(RuntimeError):
        _tick(coordinator, at=1000.0, heard_at=999.0)
    for minute in range(1, 5):
        at = 1033.0 + 60 * minute
        _tick(coordinator, at=at, heard_at=at - 2)
    assert coordinator.diagnostics.silent_after_failed_connect is False


def test_going_quiet_after_a_good_read_is_not_flagged(coordinator):
    _tick(coordinator, at=1000.0, heard_at=999.0)
    for minute in range(1, 30):
        _tick(coordinator, at=1000.0 + 60 * minute)
    assert coordinator.diagnostics.silent_after_failed_connect is False


MANUAL_FRAME = bytes.fromhex("0101020c0c2d4e1201010000010000ef06343200000600000001")


def test_a_settings_command_shows_what_was_written_then_confirms_with_a_poll(coordinator):
    """The collar applies settings when the link closes, so the command's own read-back is
    the OLD state. The coordinator must show the written values and poll again
    out of turn, rather than publish the stale read-back."""
    protocol = sys.modules["dogtra_nobark.protocol"]
    events = sys.modules["homeassistant.helpers.event"]
    old = protocol.parse_status(MANUAL_FRAME)                   # vibration 1

    async def command(_ble_device, plan):
        plan(old)
        return old                                              # read-back: unchanged

    coordinator.device.async_command = command
    _tick(coordinator, at=1000.0, heard_at=999.0)               # a normal poll first
    polls = coordinator.polls

    def plan(state):
        return [(protocol.settings_frame(state, protocol.INSTRUCTION_ADJUST_ENTER, mode=5), True),
                (protocol.settings_frame(state, protocol.INSTRUCTION_ADJUST_SAVE,
                                         vibration_sensitivity=4), True)]

    shown = asyncio.run(coordinator.async_command(plan, changes_settings=True))
    assert shown.vibration_sensitivity == 4 and coordinator.data is shown
    assert shown.mode == "manual", "the adjust-enter frame's mode must not be shown"
    assert coordinator.confirm_pending
    assert [d for d, _ in events.later] == [5.0]

    _Clock.now = 1010.0
    coordinator.history[ADDRESS] = _advert(1008.0)
    events.later[-1][1](None)                                   # 5 s after the disconnect
    assert coordinator.polls == polls + 1, "a confirming poll runs out of turn"
    assert not coordinator.confirm_pending
    assert len(events.later) == 1, "a confirmed command schedules nothing more"


def test_a_failed_confirm_is_retried_from_its_end_and_gives_up(coordinator):
    protocol = sys.modules["dogtra_nobark.protocol"]
    events = sys.modules["homeassistant.helpers.event"]
    old = protocol.parse_status(MANUAL_FRAME)

    async def command(_ble_device, plan):
        plan(old)
        return old

    coordinator.device.async_command = command
    _tick(coordinator, at=1000.0, heard_at=999.0)
    asyncio.run(coordinator.async_command(
        lambda s: [(protocol.settings_frame(s, protocol.INSTRUCTION_MODE, mode=2), True)],
        changes_settings=True))
    _fail_reads(coordinator)
    for n in range(3):
        _Clock.now = 1010.0 + 60 * n
        coordinator.history[ADDRESS] = _advert(_Clock.now - 1)
        with pytest.raises(RuntimeError):
            events.later[-1][1](None)
    assert [d for d, _ in events.later] == [5.0, 15.0, 15.0], "three tries, then no more"
    polls = coordinator.polls
    events.later[-1][1](None)                                   # tries exhausted
    assert coordinator.polls == polls and coordinator.confirm_pending


def test_a_confirm_that_lands_mid_poll_waits_for_it(coordinator):
    coordinator.confirm_pending = True
    coordinator._confirm_tries = 3
    coordinator._polling = True
    polls = coordinator.polls
    coordinator._async_confirm(None)
    assert coordinator.polls == polls and coordinator._confirm_tries == 3


def test_a_training_command_publishes_its_read_back(coordinator):
    protocol = sys.modules["dogtra_nobark.protocol"]
    events = sys.modules["homeassistant.helpers.event"]
    state = protocol.parse_status(MANUAL_FRAME)

    async def command(_ble_device, plan):
        plan(state)
        return state

    coordinator.device.async_command = command
    assert asyncio.run(coordinator.async_command(
        lambda _s: [(protocol.training_frame(protocol.TRAINING_PAGER), False)])) is state
    assert not coordinator.confirm_pending and events.later == []
