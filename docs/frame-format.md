# The Dogtra SMART NOBARK Bluetooth protocol

What the collar exposes over Bluetooth LE: what each byte of its status frame means, and
the commands that change its settings or send a page or nick. `custom_components/dogtra_nobark/protocol.py`
implements exactly these tables; `tests/test_protocol.py` and `tests/test_commands.py` hold
reference frames.

## The GATT profile

The collar advertises as `DOGTRA_<address tail>` with one service. Its Generic Access
device name reads `Arduino`, so the advertisement name is the only identifier. There is no
Device Information service and no Battery service; everything lives in one vendor service
that reuses a SIG service number and four SIG *generic* characteristic numbers:

| UUID | Properties | Role |
| --- | --- | --- |
| `0x181A` | service | the vendor service (not Environmental Sensing, despite the number) |
| `0x2A59` | read, indicate | **the status frame** |
| `0x2A58` | read, write, write-without-response | **the command pipe** |
| `0x2A5A` | read, write, indicate | request/response channel; refuses a plain read; not used |
| `0x2A3D` | read, write, write-without-response, notify | request/response channel; refuses a plain read; not used |

The collar accepts one connection at a time and stops advertising while connected; it drops
an idle connection after roughly 40 seconds. Nothing is pushed unprompted, so the
integration reads and disconnects rather than subscribing.

## The status frame, 26 bytes

```
 b0    b1  b2  b3 b4 b5   b6    b7 b8   b9..b14        b15   b16 b17 b18   b19    b20..b25
[ack][mode][lv][ auto-inc ][bat][snd][vib][ flags(6) ][ind ][ h : m : s ][ ? ][ 3 × u16 BE ]
```

| Bytes | Field | Detail |
| --- | --- | --- |
| 0 | ack flag | `01` on the first read after connecting; `06` on repeat reads while byte 19 is non-zero |
| 1 | correction mode | `1` Manual, `2` Pager, `3` Auto Increase, `5` Count Only |
| 2 | correction level | the correction level, as-is (Manual mode); `0` in modes that do not stimulate |
| 3, 4, 5 | auto-increase current / min / max | raw intensities 1–100; as 1–10 levels, `level = (raw − 1) × 9 ÷ 99 + 1` (integer division) and `raw = (level − 1) × 99 ÷ 9 + 1` — level 2 = 12, level 5 = 45 |
| 6 | battery | percent |
| 7 | sound sensitivity | raw = 4 × level + 2 (level 2 → 10, level 4 → 18) |
| 8 | vibration sensitivity | the UI value, as-is |
| 9, 10, 11 | bark / howl / whine occurred | per-session flags for the current mode |
| 12, 13, 14 | bark / howl / whine stimulated | `whine stimulated` stays 0 while low-volume correction is off |
| 15 | detection settings | bit 7 bark, bit 6 howl, bit 5 whine (the per-sound detection toggles), bit 4 low-volume correction; the low nibble is always `1111`. `0xEF` = all three detected, low-volume correction off |
| 16, 17, 18 | uptime | hours, minutes, seconds as three separate bytes; reset together with the counters |
| 19 | (unknown) | when non-zero, equals uptime hours + 7 |
| 20–21 | bark count | unsigned 16-bit, big-endian |
| 22–23 | howl count | unsigned 16-bit, big-endian |
| 24–25 | whine count | unsigned 16-bit, big-endian. Howl + whine is reported as one *Whine & Howl* figure |

Bytes 9–14 are **flags rather than counters**: they stay put while the counters at 20–25
advance.

## Commands, written to `0x2A58`

There are two shapes: a 3-byte training command and a 27-byte settings frame.

### Training: page and nick — 3 bytes, written without response

```
[0x0D][kind][level]
```

| kind | Button | level |
| --- | --- | --- |
| `1` | **Nick** — one short pulse of stimulation | 1–100 |
| `2` | **Pager** — vibration | `0` |
| `4` | **Constant** — stimulation for as long as the frame keeps arriving | 1–100. Not implemented here |

### Settings: the status frame sent back — 27 bytes, written with response

Byte 0 is an instruction; bytes 1–18 are the collar's current values (bytes 1–18 of a status
frame read on the same connection) with the changed fields substituted; bytes 19–26 are
zero.

| Instruction | Sets | Notes |
| --- | --- | --- |
| `0x02` mode | byte 1 mode; byte 2 correction level (Manual); bytes 4, 5 auto-increase min/max as raw intensities | |
| `0xD0` | — | entering sensitivity adjustment, sent with byte 1 = `5` (Count Only): no stimulation while adjusting |
| `0xD1` | byte 7 sound sensitivity, byte 8 vibration sensitivity, byte 15 detection settings | leaving it, saving the new values. Sent after `0xD0` |

**The collar applies a settings frame when the connection closes.** A status read on the same
connection still returns the old values; the next connection reads the new ones.

## Reference frames

Five status frames and their expected decoded values are in `tests/test_protocol.py`;
command frames are in `tests/test_commands.py`.
