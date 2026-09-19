# The Dogtra SMART NOBARK status frame

What the collar exposes over Bluetooth LE, and what each byte of its status frame means.
Every field marked *confirmed* was checked against the value the vendor app displayed for
the same collar at the same time; the parser in `custom_components/dogtra_nobark/protocol.py`
implements exactly this table, and `tests/test_protocol.py` holds the reference frames.

## The GATT profile

The collar advertises as `DOGTRA_<address tail>` with one service. Its Generic Access
device name reads `Arduino`, so the advertisement name is the only identifier. There is no
Device Information service and no Battery service; everything lives in one vendor service
that reuses a SIG service number and four SIG *generic* characteristic numbers:

| UUID | Properties | Role |
| --- | --- | --- |
| `0x181A` | service | the vendor service (not Environmental Sensing, despite the number) |
| `0x2A59` | read, indicate | **the status frame — the only thing this project reads** |
| `0x2A58` | read, write, write-without-response | the command pipe — **never written to here** |
| `0x2A5A` | read, write, indicate | request/response channel; refuses a plain read |
| `0x2A3D` | read, write, write-without-response, notify | request/response channel; refuses a plain read |

The collar accepts one connection at a time and stops advertising while connected; it drops
an idle connection after roughly 40 seconds. A subscription (CCCD write) keeps the link up,
but nothing is pushed unprompted, so the integration simply reads and disconnects.

## The status frame, 26 bytes

```
 b0    b1  b2  b3 b4 b5   b6    b7 b8   b9..b14        b15   b16 b17 b18   b19    b20..b25
[ack][mode][lv][ auto-inc ][bat][snd][vib][ flags(6) ][0xEF][ h : m : s ][ ? ][ 3 × u16 BE ]
```

| Bytes | Field | Status | Detail |
| --- | --- | --- | --- |
| 0 | ack flag | confirmed | `01` on the first read after connecting; `06` on repeat reads while byte 19 is non-zero |
| 1 | correction mode | confirmed | `1` Manual, `2` Pager, `5` Count Only. The app also offers Auto Increase; its code has not been observed |
| 2 | correction level | confirmed | the level set in the app, as-is (Manual mode); `0` in modes that do not stimulate |
| 3, 4, 5 | auto-increase level / min / max | located, encoding open | raw intensity values on a non-linear scale (UI 2 ≈ 12, UI 5 ≈ 45) |
| 6 | battery | confirmed | percent |
| 7 | sound sensitivity | confirmed | raw = 4 × level + 2 (level 2 → 10, level 4 → 18) |
| 8 | vibration sensitivity | confirmed | the UI value, as-is |
| 9, 10, 11 | bark / howl / whine occurred | consistent | per-session flags for the current mode |
| 12, 13, 14 | bark / howl / whine stimulated | consistent | `whine stimulated` stays 0 while the app's low-volume correction is off |
| 15 | frame marker | confirmed | always `0xEF` |
| 16, 17, 18 | uptime | confirmed | hours, minutes, seconds as three separate bytes; reset by the app's Reset together with the counters |
| 19 | (unknown) | pattern only | when non-zero, equals uptime hours + 7 |
| 20–21 | bark count | confirmed | unsigned 16-bit, big-endian |
| 22–23 | howl count | confirmed | unsigned 16-bit, big-endian |
| 24–25 | whine count | confirmed | unsigned 16-bit, big-endian. The app shows howl + whine as one *Whine & Howl* figure |

Bytes 9–14 were confirmed to be **flags rather than counters** by the counters at 20–25
moving while they did not.

## Reference frames

Five frames and the app values they were checked against are in `tests/test_protocol.py`;
`scripts/verify-fields.py` re-runs the battery and timer checks over any directory of
captures produced by `scripts/collar_read.py`.
