"""The collar's 26-byte status frame (characteristic 0x2A59), decoded.

Field meanings are in docs/frame-format.md; the commands built at the end of this module
are in its "Commands" section. Status frame layout:

    b0        ack flag: 01 on the first read after connect, 06 on repeat reads while b19 != 0
    b1        mode          1 = Manual, 2 = Pager, 3 = Auto Increase, 5 = Count Only
    b2        correction level (Manual mode), the UI value directly; 0 in non-stimulating modes
    b3 b4 b5  auto-increase current / min / max as raw intensities 1..100; level = (raw-1)*9//99+1
    b6        battery, percent
    b7        sound sensitivity, raw = 4*level + 2
    b8        vibration sensitivity, the UI value directly
    b9..b11   bark / howl / whine "occurred this session" flags
    b12..b14  bark / howl / whine "stimulated" flags
    b15       detection flags: bit7 bark, bit6 howl, bit5 whine (detection on), bit4 low-volume
              correction; the low nibble is always 1111 (0xEF = all detected, low-volume off)
    b16 b17 b18   uptime timer: hours, minutes, seconds (three bytes, not an integer)
    b19       unknown; when non-zero equals hours + 7
    b20-21    bark count,  u16 big-endian
    b22-23    howl count,  u16 big-endian     -- howl + whine is shown as one
    b24-25    whine count, u16 big-endian        "Whine & Howl" figure
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import STATUS_FRAME_LEN

#: Byte 15 is the detection/correction bitmask; its low nibble is always 1111, which is what
#: tells a status frame from anything else.
DETECTION_OFFSET = 15
DETECTION_FIXED_MASK = 0x0F

MODES: dict[int, str] = {
    1: "manual",
    2: "pager",
    3: "auto_increase",
    5: "count_only",
}


def mode_name(code: int) -> str:
    """Name a mode code; unknown codes keep their number so they are never mislabelled."""
    return MODES.get(code, f"mode_{code}")


def level_from_intensity(raw: int) -> int:
    """Level 1..10 from a raw auto-increase intensity 1..100: 12 -> 2, 45 -> 5."""
    raw = min(max(raw, 1), 100)
    return (raw - 1) * 9 // 99 + 1


def intensity_from_level(level: int) -> int:
    """Raw intensity for a level 1..10, the inverse of the above: 2 -> 12, 5 -> 45."""
    level = min(max(level, 1), 10)
    return (level - 1) * 99 // 9 + 1


def sound_sensitivity_level(raw: int) -> int | None:
    """UI level from the raw byte. For example level 2 -> 10, level 4 -> 18."""
    if raw < 6 or (raw - 2) % 4:
        return None      # below level 1, or off the 4n+2 grid -- not a valid level
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
    detection_flags: int
    uptime_seconds: int
    pending: int
    bark_count: int
    howl_count: int
    whine_count: int

    @property
    def mode(self) -> str:
        return mode_name(self.mode_code)

    @property
    def auto_increase_levels(self) -> tuple[int, int, int]:
        """Current, min and max of the Auto Increase range, as 1..10 levels."""
        return tuple(level_from_intensity(r) for r in self.auto_increase_raw)  # type: ignore[return-value]

    @property
    def sound_sensitivity(self) -> int | None:
        return sound_sensitivity_level(self.sound_sensitivity_raw)

    @property
    def detection(self) -> dict[str, bool]:
        """The per-sound detection toggles and the low-volume correction switch (byte 15)."""
        bits = self.detection_flags
        return {
            "detect_bark": bool(bits & 0x80),
            "detect_howl": bool(bits & 0x40),
            "detect_whine": bool(bits & 0x20),
            "low_volume_correction": bool(bits & 0x10),
        }

    @property
    def whine_and_howl_count(self) -> int:
        """Howl and whine as the single combined figure."""
        return self.howl_count + self.whine_count


class FrameError(ValueError):
    """The bytes are not a status frame this decoder understands."""


def parse_status(raw: bytes) -> CollarState:
    """Decode a 0x2A59 read. Refuses anything that does not look like the known frame."""
    if len(raw) != STATUS_FRAME_LEN:
        raise FrameError(f"expected {STATUS_FRAME_LEN} bytes, got {len(raw)}")
    if raw[DETECTION_OFFSET] & DETECTION_FIXED_MASK != DETECTION_FIXED_MASK:
        raise FrameError(
            f"byte {DETECTION_OFFSET} is {raw[DETECTION_OFFSET]:#04x}; its low nibble is "
            f"always 1111 in a status frame -- not a status frame"
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
        detection_flags=raw[15],
        uptime_seconds=raw[16] * 3600 + raw[17] * 60 + raw[18],
        pending=raw[19],
        bark_count=int.from_bytes(raw[20:22], "big"),
        howl_count=int.from_bytes(raw[22:24], "big"),
        whine_count=int.from_bytes(raw[24:26], "big"),
    )


# --- Commands (written to 0x2A58) ----------------------------------------------------------
#
# Frame formats are in docs/frame-format.md, "Commands". Two shapes:
#
#     training   [0x0D, kind, level]           -- the remote buttons
#     settings   the status frame echoed back with byte 0 set to an instruction and the
#                changed fields substituted; bytes 19..26 zero (27 bytes)
#
# Only these builders produce bytes for the collar, and only the instructions below are
# ever sent.

INSTRUCTION_MODE = 0x02
INSTRUCTION_TRAINING = 0x0D
INSTRUCTION_ADJUST_ENTER = 0xD0   # begin sensitivity adjustment: no stimulation meanwhile
INSTRUCTION_ADJUST_SAVE = 0xD1    # finish it, carrying the new sensitivities

TRAINING_NICK = 1
TRAINING_PAGER = 2

SETTINGS_FRAME_LEN = 27
#: Bytes 1..18 of the status frame are the settings echoed back in a settings frame.
_ECHOED = slice(1, 19)

MODE_CODES: dict[str, int] = {name: code for code, name in MODES.items()}
#: The mode sent when entering sensitivity adjustment.
MODE_COUNT_ONLY = MODE_CODES["count_only"]

#: Manual-mode correction level, as the collar's own manual numbers it.
CORRECTION_LEVELS = range(1, 11)
#: The remote's stimulation slider.
TRAINING_LEVELS = range(1, 101)
SENSITIVITY_LEVELS = range(1, 6)


class CommandError(ValueError):
    """A command this module refuses to build."""


def _check(name: str, value: int, allowed: range) -> int:
    if value not in allowed:
        raise CommandError(f"{name} {value} outside {allowed.start}..{allowed.stop - 1}")
    return value


def training_frame(kind: int, level: int = 0) -> bytes:
    """A remote-button command. Pager carries no level; Nick needs one."""
    if kind == TRAINING_PAGER:
        return bytes((INSTRUCTION_TRAINING, TRAINING_PAGER, 0))
    if kind == TRAINING_NICK:
        return bytes((INSTRUCTION_TRAINING, TRAINING_NICK, _check("level", level, TRAINING_LEVELS)))
    # Constant (4) is not offered here.
    raise CommandError(f"training kind {kind} is not supported")


def sound_sensitivity_raw(level: int) -> int:
    """Inverse of ``sound_sensitivity_level``: level 1..5 -> 6, 10, 14, 18, 22."""
    return 4 * _check("sound sensitivity", level, SENSITIVITY_LEVELS) + 2


def settings_frame(
    state: CollarState,
    instruction: int,
    *,
    mode: int | None = None,
    correction_level: int | None = None,
    auto_increase_min: int | None = None,
    auto_increase_max: int | None = None,
    sound_sensitivity: int | None = None,
    vibration_sensitivity: int | None = None,
) -> bytes:
    """The status frame echoed back as a settings command, with the given fields changed.

    Built from a frame read on the same connection, so nothing the phone app changed since
    the last poll is overwritten with a stale value.
    """
    if instruction not in (INSTRUCTION_MODE, INSTRUCTION_ADJUST_ENTER, INSTRUCTION_ADJUST_SAVE):
        raise CommandError(f"instruction {instruction:#04x} is not supported")
    frame = bytearray(SETTINGS_FRAME_LEN)
    frame[0] = instruction
    frame[_ECHOED] = state.raw[_ECHOED]
    if mode is not None:
        if mode not in MODES:
            raise CommandError(f"mode {mode} is not supported")
        if mode == MODE_CODES["auto_increase"] and not all(state.auto_increase_raw[1:]):
            # Its min/max are raw intensities this module cannot derive from levels; the
            # frame echoes the range the collar already holds, so there must be one.
            raise CommandError("no Auto Increase range is set on the collar; set one in the Dogtra app")
        frame[1] = mode
    if correction_level is not None:
        frame[2] = _check("correction level", correction_level, CORRECTION_LEVELS)
    if auto_increase_min is not None or auto_increase_max is not None:
        lo = auto_increase_min if auto_increase_min is not None else state.auto_increase_levels[1]
        hi = auto_increase_max if auto_increase_max is not None else state.auto_increase_levels[2]
        _check("auto-increase min", lo, CORRECTION_LEVELS)
        _check("auto-increase max", hi, CORRECTION_LEVELS)
        if lo > hi:
            raise CommandError(f"auto-increase min {lo} is above max {hi}")
        frame[4], frame[5] = intensity_from_level(lo), intensity_from_level(hi)
    if sound_sensitivity is not None:
        frame[7] = sound_sensitivity_raw(sound_sensitivity)
    if vibration_sensitivity is not None:
        frame[8] = _check("vibration sensitivity", vibration_sensitivity, SENSITIVITY_LEVELS)
    return bytes(frame)
