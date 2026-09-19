"""The collar's 26-byte status frame (characteristic 0x2A59), decoded.

Every offset below was confirmed against the vendor app's own display, one field at a
time -- see docs/frame-format.md. Layout:

    b0        ack flag: 01 on the first read after connect, 06 on repeat reads while b19 != 0
    b1        mode          1 = Manual, 2 = Pager, 5 = Count Only (3/4 unseen; Auto Increase)
    b2        correction level (Manual mode), the UI value directly; 0 in non-stimulating modes
    b3 b4 b5  auto-increase level / min / max as raw intensities (non-linear; not decoded)
    b6        battery, percent
    b7        sound sensitivity, raw = 4*level + 2
    b8        vibration sensitivity, the UI value directly
    b9..b11   bark / howl / whine "occurred this session" flags   (hypothesis, 3 frames)
    b12..b14  bark / howl / whine "stimulated" flags               (hypothesis, 3 frames)
    b15       0xEF fixed frame marker
    b16 b17 b18   uptime timer: hours, minutes, seconds (three bytes, not an integer)
    b19       when non-zero equals hours + 7; meaning open
    b20-21    bark count,  u16 big-endian
    b22-23    howl count,  u16 big-endian     -- the app shows howl + whine as one
    b24-25    whine count, u16 big-endian        "Whine & Howl" figure
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import STATUS_FRAME_LEN

FRAME_MARKER = 0xEF
FRAME_MARKER_OFFSET = 15

MODES: dict[int, str] = {
    1: "manual",
    2: "pager",
    5: "count_only",
}


def mode_name(code: int) -> str:
    """Name a mode code; unknown codes keep their number so they are never mislabelled."""
    return MODES.get(code, f"mode_{code}")


def sound_sensitivity_level(raw: int) -> int | None:
    """UI level from the raw byte. Two confirmed points: 2 -> 10, 4 -> 18."""
    if raw < 6 or (raw - 2) % 4:
        return None      # below level 1, or off the 4n+2 grid -- not a value we have seen
    return (raw - 2) // 4


@dataclass(frozen=True)
class CollarState:
    """One decoded status frame."""

    raw: bytes
    ack: int
    mode_code: int
    correction_level: int
    auto_increase_raw: tuple[int, int, int]
    battery: int
    sound_sensitivity_raw: int
    vibration_sensitivity: int
    occurred_flags: tuple[int, int, int]
    stimulated_flags: tuple[int, int, int]
    uptime_seconds: int
    pending: int
    bark_count: int
    howl_count: int
    whine_count: int

    @property
    def mode(self) -> str:
        return mode_name(self.mode_code)

    @property
    def sound_sensitivity(self) -> int | None:
        return sound_sensitivity_level(self.sound_sensitivity_raw)

    @property
    def whine_and_howl_count(self) -> int:
        """What the app displays as one number."""
        return self.howl_count + self.whine_count


class FrameError(ValueError):
    """The bytes are not a status frame this decoder understands."""


def parse_status(raw: bytes) -> CollarState:
    """Decode a 0x2A59 read. Refuses anything that does not look like the known frame."""
    if len(raw) != STATUS_FRAME_LEN:
        raise FrameError(f"expected {STATUS_FRAME_LEN} bytes, got {len(raw)}")
    if raw[FRAME_MARKER_OFFSET] != FRAME_MARKER:
        raise FrameError(
            f"byte {FRAME_MARKER_OFFSET} is {raw[FRAME_MARKER_OFFSET]:#04x}, "
            f"expected the {FRAME_MARKER:#04x} marker -- not a status frame"
        )
    return CollarState(
        raw=bytes(raw),
        ack=raw[0],
        mode_code=raw[1],
        correction_level=raw[2],
        auto_increase_raw=(raw[3], raw[4], raw[5]),
        battery=raw[6],
        sound_sensitivity_raw=raw[7],
        vibration_sensitivity=raw[8],
        occurred_flags=(raw[9], raw[10], raw[11]),
        stimulated_flags=(raw[12], raw[13], raw[14]),
        uptime_seconds=raw[16] * 3600 + raw[17] * 60 + raw[18],
        pending=raw[19],
        bark_count=int.from_bytes(raw[20:22], "big"),
        howl_count=int.from_bytes(raw[22:24], "big"),
        whine_count=int.from_bytes(raw[24:26], "big"),
    )
