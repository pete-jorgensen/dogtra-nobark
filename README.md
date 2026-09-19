# dogtra-nobark

Home Assistant integration for the **Dogtra SMART NOBARK** bark collar over Bluetooth LE —
read-only bark, howl and whine counts, battery, correction mode and sensitivities, polled
through Home Assistant's Bluetooth proxies — plus the documented decode of the collar's
status frame that makes it possible.

The vendor app shows these numbers only while a phone is next to the dog. This puts them in
Home Assistant, where they can be graphed per day, alerted on, and read from any room the
collar wanders through, as long as a Bluetooth adapter or ESPHome/bt-proxy is within range.

## What you get

| Entity | What it is |
| --- | --- |
| Barks, Howls, Whines | the collar's own counters (`total_increasing`; the app's Reset starts a new cycle) |
| Whines and howls | the combined figure the app displays |
| Battery | percent |
| Correction mode | Manual / Pager / Count only |
| Correction level | the level set in the app (Manual mode) |
| Sound sensitivity, Vibration sensitivity | as set in the app |
| Uptime | the collar's own timer, which the app's Reset also zeroes |

Everything is **read-only**. The integration never writes to the collar — it delivers
corrections to a dog, and there is no write path in the code. `tests/test_read_only.py`
fails the build if one ever appears.

## Install

1. Copy `custom_components/dogtra_nobark` into your Home Assistant `config/custom_components/`
   (or add this repository to HACS as a custom repository).
2. Restart Home Assistant.
3. The collar is discovered by its `DOGTRA_*` advertisement name: *Settings → Devices &
   services* will offer it. If it does not appear, add the integration manually — the
   collar must be switched on, in range of an adapter or proxy, **and the Dogtra phone app
   must be closed** (see below).

Works with any adapter Home Assistant's `bluetooth` integration knows, including ESPHome
Bluetooth proxies. The collar is found through the proxy that hears it best at the moment
of each poll, so it follows the dog between rooms.

## Two things that look like faults and are not

**Every sensor goes unavailable while the Dogtra app is open on a phone.** The collar
accepts one Bluetooth connection at a time and the app holds it. While connected the collar
stops advertising, the integration stops polling, and the sensors show their last value
until the collar has not been heard for a while. Close the app and they resume on the next
poll (every 5 minutes while the collar is heard).

**The counts and the uptime drop to zero together when Reset is pressed in the app.** The
counters are `total_increasing`, so Home Assistant's long-term statistics treat the drop as
a new cycle and per-day graphs keep their history; the running totals on the entities do
reset, because the collar's did.

## The status frame

`docs/frame-format.md` documents the collar's GATT profile and every byte of its 26-byte
status frame. Battery, mode, correction level, both sensitivities, the h:m:s uptime timer
and the three 16-bit counters are each confirmed against what the vendor app displayed for
the same collar at the same time. `scripts/collar_read.py` is a read-only BlueZ tool that
captures frames from a Linux host with a Bluetooth adapter; `scripts/verify-fields.py`
re-runs the confirmations over a directory of captures.

Not decoded: the auto-increase intensity table (three raw bytes) and one byte that tracks
the timer's hours plus seven. Neither is needed for the sensors above.

## Tests

```
pytest
```

`test_protocol.py` feeds captured frames through the parser and asserts the values the app
showed at the time; `test_read_only.py` proves no write-capable call exists in the package.

## Licence

MIT. Not affiliated with Dogtra.
