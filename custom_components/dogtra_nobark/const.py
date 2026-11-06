"""Constants for the Dogtra SMART NOBARK integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "dogtra_nobark"
MANUFACTURER = "Dogtra"
MODEL = "SMART NOBARK"

#: The collar advertises as DOGTRA_<address tail>. Its Generic Access device name is the
#: generic "Arduino", so the advertised name is the only identifier.
LOCAL_NAME_PREFIX = "DOGTRA_"

#: The one service, and the one characteristic this integration reads. `0x181A` is the SIG
#: Environmental Sensing number reused as a private service; `0x2A59` ("Analog Output") is
#: the collar's 26-byte status frame. See docs/frame-format.md in the repository.
SERVICE_UUID = "0000181a-0000-1000-8000-00805f9b34fb"
STATUS_UUID = "00002a59-0000-1000-8000-00805f9b34fb"

#: The command pipe. Written only by ``device.CollarDevice.async_command``, only with frames
#: built in ``protocol.py`` (docs/frame-format.md, "Commands").
COMMAND_UUID = "00002a58-0000-1000-8000-00805f9b34fb"

#: Pause between the last write and the read-back (seconds). For training commands the
#: read-back is the collar's state; for SETTINGS it is not -- see SETTINGS_CONFIRM_DELAYS.
COMMAND_SETTLE = 0.5

#: The collar applies a settings write only once the link closes: a read on the same
#: connection returns the OLD values (docs/frame-format.md, "Commands"). So after a settings command a fresh poll is forced CONFIRM_FIRST seconds after
#: the disconnect -- the collar advertises again within ~3 s -- and, since about half of all
#: connects fail, retried CONFIRM_RETRY seconds after each failed one, CONFIRM_TRIES in all.
#: Retries count from the END of the failed poll, so a retry never lands inside a connect
#: that is still hanging -- one that did would be skipped.
CONFIRM_FIRST = 5.0
CONFIRM_RETRY = 15.0
CONFIRM_TRIES = 3

#: Options: the highest Nick level the integration will send, whatever is asked of it. The
#: collar's own range is 1..100; the cap is the safety net for an automation or a mis-tap.
CONF_MAX_NICK_LEVEL = "max_nick_level"
DEFAULT_MAX_NICK_LEVEL = 20

#: Poll cadence. Counts do not need to be live; the collar drops an idle link after ~40 s
#: so every poll is connect-read-disconnect, and shorter intervals only cost the proxies
#: airtime. The phone app holds the link exclusively while open, during which polls fail
#: -- that is expected, not a fault.
SCAN_INTERVAL = timedelta(minutes=5)

#: How often the coordinator's own timer asks whether a poll is due. The timer exists
#: because the collar's advertisement never changes, and Home Assistant only calls an
#: integration back for an advertisement that differs from the previous one -- so after the
#: first seconds following power-on there is no callback left to hang a poll on. Shorter
#: than SCAN_INTERVAL so a poll skipped while the phone app held the link is retried within
#: a minute of the app closing, not five.
POLL_CHECK_INTERVAL = timedelta(minutes=1)

#: A poll is attempted only if the collar was heard this recently (seconds). Any BLE
#: advertising interval is at most 10.24 s, so a minute of silence is a held link or an
#: absent dog, never a slow advertiser.
ADVERT_FRESH = 60.0

#: Total connection attempts per poll (bleak-retry-connector's ``max_attempts``; 0 would
#: mean no attempt at all, so this is a count, not a retry count). One: a second attempt is
#: routed afresh and can land on a different proxy the instant the first gives up, so a
#: single hung connect would turn into a burst of attempts from several proxies, and a
#: collar holding a stale link stops advertising. A failed poll already waits a full SCAN_INTERVAL,
#: which is the retry.
CONNECT_ATTEMPTS = 1
#: Hard ceiling on one connect-read-disconnect. bleak's own connect timeout is fixed at
#: its default and is not a parameter of ``establish_connection``; this is the only knob.
#: A healthy exchange takes 3-5 s, so 20 s is generous; a longer ceiling only gives a
#: connect that is never going to finish longer to hold the collar's attention.
POLL_DEADLINE = 20.0

#: No connect is attempted below this signal strength (dBm). A connect at the edge of range
#: is the one that hangs instead of failing, and the dog walking back into range costs at
#: most one POLL_CHECK_INTERVAL. The "last connect" diagnostic records the signal of every
#: attempt, so the threshold can be tuned for a given installation.
MIN_POLL_RSSI = -85

#: How long after a failed connect the collar may stay unheard before the silence is
#: attributed to that connect (seconds). A collar whose link was dropped advertises again
#: within ~3 s, so a minute and a half of nothing is not the normal aftermath of a failure.
SILENT_AFTER_FAILURE = 90.0

STATUS_FRAME_LEN = 26
