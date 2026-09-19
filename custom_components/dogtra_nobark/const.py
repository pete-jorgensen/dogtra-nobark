"""Constants for the Dogtra SMART NOBARK integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "dogtra_nobark"
MANUFACTURER = "Dogtra"
MODEL = "SMART NOBARK"

#: The collar advertises as DOGTRA_<address tail>. Its Generic Access device name reads
#: "Arduino" (the vendor never changed the ArduinoBLE default), so the advert name is the
#: only identifier.
LOCAL_NAME_PREFIX = "DOGTRA_"

#: The one service, and the one characteristic this integration reads. `0x181A` is the SIG
#: Environmental Sensing number reused as a private service; `0x2A59` ("Analog Output") is
#: the collar's 26-byte status frame. See docs/frame-format.md in the repository.
SERVICE_UUID = "0000181a-0000-1000-8000-00805f9b34fb"
STATUS_UUID = "00002a59-0000-1000-8000-00805f9b34fb"

#: ⛔ Deliberately absent: any write characteristic. `0x2A58` is the collar's command pipe
#: and this integration never writes to the collar -- it delivers corrections to a dog.

#: Poll cadence. Counts do not need to be live; the collar drops an idle link after ~40 s
#: so every poll is connect-read-disconnect, and shorter intervals only cost the proxies
#: airtime. The phone app holds the link exclusively while open, during which polls fail
#: -- that is expected, not a fault.
SCAN_INTERVAL = timedelta(minutes=5)

#: Total connection attempts per poll (bleak-retry-connector's ``max_attempts``; 0 would
#: mean no attempt at all, so this is a count, not a retry count).
CONNECT_ATTEMPTS = 2
#: Hard ceiling on one connect-read-disconnect. bleak's own connect timeout is fixed at
#: its default and is not a parameter of ``establish_connection``; this is the only knob.
POLL_DEADLINE = 60.0

STATUS_FRAME_LEN = 26
