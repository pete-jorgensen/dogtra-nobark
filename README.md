# dogtra-nobark

Home Assistant integration for the **Dogtra SMART NOBARK** bark collar over Bluetooth LE —
bark, howl and whine counts, battery and every setting, polled through Home Assistant's
Bluetooth proxies; the collar's remote buttons (Page, Nick) and its settings as controls —
plus a description of the collar's Bluetooth protocol.

The vendor app shows these numbers only while a phone is next to the dog. This puts them in
Home Assistant, where they can be graphed per day, alerted on, and read from any room the
collar wanders through, as long as a Bluetooth adapter or ESPHome/bt-proxy is within range.

## What you get

| Entity | What it is |
| --- | --- |
| Barks, Howls, Whines | the collar's own counters (`total_increasing`; the app's Reset starts a new cycle) |
| Whines and howls | the combined figure the app displays |
| Battery | percent |
| Correction mode | Manual / Auto increase / Pager / Count only, with the app's detection toggles as attributes |
| Correction level | the level set in the app (Manual mode) |
| Sound sensitivity, Vibration sensitivity | as set in the app |
| Uptime | the collar's own timer, which the app's Reset also zeroes |
| Auto increase level / min / max | where Auto Increase has climbed to, and its range, as the app's 1–10 levels |

| Control | What it does |
| --- | --- |
| **Page** (button) | vibrates the collar |
| **Nick** (button) | one short pulse of stimulation, at the *Nick level* |
| Nick level | 1–100, held in Home Assistant; **never sent above the cap set in the integration's options** (default 20) |
| Set correction mode | Manual / Auto Increase / Pager / Count only |
| Set correction level | 1–10, in Manual mode |
| Set auto increase min / max | 1–10, in Auto Increase mode |
| Set sound / vibration sensitivity | 1–5 |

The collar delivers corrections to a dog, so the write path is deliberately narrow: one
function writes, only to the command characteristic, only frames built by `protocol.py`,
which refuses anything out of range and builds no instruction but the ones listed above.
`tests/test_write_paths.py` fails the build if a write appears anywhere else. Put Page and
Nick behind a confirmation on any dashboard (`confirmation:` on the tap action).

A setting shows its new value at once; the collar applies it when the connection closes, so
the integration reads it back a few seconds later and shows what the collar actually holds.
About half of Bluetooth connections to the collar fail at the first try; a press that did not
reach the collar raises an error rather than looking as if it worked.

## Install

### From HACS (recommended)

1. Open HACS in Home Assistant.
2. Search for **Dogtra SMART NOBARK (Bluetooth)** and download it.
3. Restart Home Assistant.
4. Go to *Settings → Devices & services* and add the integration. The collar is discovered
   by its `DOGTRA_*` advertisement name, so it is offered there. If it does not appear, add
   the integration manually. The collar must be switched on and in range of an adapter or
   proxy, **and the Dogtra phone app must be closed** (see below).

### Manual copy (alternative)

1. Copy `custom_components/dogtra_nobark` into your Home Assistant `config/custom_components/`.
2. Restart Home Assistant.
3. Add the integration as described in step 4 above.

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

## The protocol

`docs/frame-format.md` documents the collar's GATT profile, every byte of its 26-byte status
frame, and the commands: the 3-byte training frame (page, nick) and the 27-byte settings
frame. Only byte 19 of the status frame is unmapped.

## Tests

```
pytest
```

`test_protocol.py` feeds sample status frames through the parser and asserts the decoded
values; `test_commands.py` checks every command the integration can build and everything it
refuses; `test_write_paths.py` proves there is exactly one write, to one characteristic;
`test_polling.py` covers polling and the read-back after a settings change.

## Licence

MIT. Not affiliated with Dogtra.
