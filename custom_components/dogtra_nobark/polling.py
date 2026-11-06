"""When to read the collar. Pure functions -- no Home Assistant import, so they test alone."""

from __future__ import annotations

from .const import ADVERT_FRESH, MIN_POLL_RSSI, SCAN_INTERVAL, SILENT_AFTER_FAILURE


def poll_due(seconds_since_last_poll: float | None) -> bool:
    """True on the first chance after start, then no more than once per interval."""
    return (
        seconds_since_last_poll is None
        or seconds_since_last_poll >= SCAN_INTERVAL.total_seconds()
    )


def advert_is_fresh(now: float, advert_time: float) -> bool:
    """True if the collar was heard recently enough that a connect can succeed.

    The collar stops advertising while anything is connected to it, so a stale advert means
    the phone app holds the link (or the dog has left) and a connect would only burn a
    proxy's airtime on a guaranteed failure. Both arguments are monotonic seconds.
    """
    return now - advert_time <= ADVERT_FRESH


def signal_ok(rssi: int | None) -> bool:
    """True if the collar is heard strongly enough for a connect to be worth attempting.

    An advertisement with no signal reading is let through: the reading is missing, not
    weak, and refusing would stop all polling on an adapter that never supplies one.
    """
    return rssi is None or rssi >= MIN_POLL_RSSI


def silent_after_failed_connect(
    *,
    now: float,
    connect_failed: bool,
    connect_ended: float | None,
    last_heard: float | None,
) -> bool:
    """True if the collar has not advertised since a connect of ours failed.

    That is the signature of a link left up somewhere -- on a proxy, or in the collar's own
    Bluetooth stack: the collar stops advertising the moment a connection is accepted, and
    nothing ever releases it. It is also what the dog walking out of range mid-connect looks
    like, which is why the diagnostic carries the last signal strength: strong-then-silent
    is a stuck link, fading-then-silent is a dog leaving. All times are monotonic seconds.
    """
    if not connect_failed or connect_ended is None:
        return False
    # Compared with the END of the attempt, not its start: the collar goes on advertising
    # until the instant it accepts a link, which can be seconds into the attempt.
    if last_heard is not None and last_heard > connect_ended:
        return False
    return now - connect_ended >= SILENT_AFTER_FAILURE
