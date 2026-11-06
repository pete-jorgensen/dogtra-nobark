"""The command builders against the protocol (docs/frame-format.md, "Commands").

The expected frames are sample frames for the given inputs; the reference status
frame is Manual level 2, sound 4, vibration 1.
"""

from __future__ import annotations

import pytest

from test_protocol import protocol

P = protocol
MANUAL = P.parse_status(bytes.fromhex("0101020c0c2d4e1201010000010000ef06343200000600000001"))
PAGING_MODE = P.parse_status(bytes.fromhex("0102000c0c2d4c0a02000000000000ef07353300000700000001"))


def test_training_frames():
    assert P.training_frame(P.TRAINING_PAGER) == bytes.fromhex("0d0200")
    assert P.training_frame(P.TRAINING_NICK, 7) == bytes.fromhex("0d0107")
    assert P.training_frame(P.TRAINING_NICK, 100) == bytes.fromhex("0d0164")


@pytest.mark.parametrize("level", [0, 101, -1])
def test_nick_level_out_of_range_is_refused(level):
    with pytest.raises(P.CommandError):
        P.training_frame(P.TRAINING_NICK, level)


def test_constant_and_unknown_training_kinds_are_refused():
    for kind in (0, 3, 4, 5):
        with pytest.raises(P.CommandError):
            P.training_frame(kind, 10)


def test_settings_frame_echoes_status_bytes_1_to_18_and_zeroes_the_rest():
    frame = P.settings_frame(MANUAL, P.INSTRUCTION_MODE)
    assert len(frame) == P.SETTINGS_FRAME_LEN == 27
    assert frame[0] == 0x02
    assert frame[1:19] == MANUAL.raw[1:19]
    assert frame[19:] == bytes(8)


def test_mode_to_pager_changes_only_the_mode_byte():
    frame = P.settings_frame(MANUAL, P.INSTRUCTION_MODE, mode=P.MODE_CODES["pager"])
    assert frame[1] == 2
    assert frame[2:19] == MANUAL.raw[2:19]


def test_manual_from_pager_sets_mode_and_level():
    frame = P.settings_frame(PAGING_MODE, P.INSTRUCTION_MODE, mode=1, correction_level=3)
    assert (frame[1], frame[2]) == (1, 3)


def test_sensitivities_use_the_raw_encodings_the_status_frame_reads_back():
    frame = P.settings_frame(MANUAL, P.INSTRUCTION_ADJUST_SAVE,
                             sound_sensitivity=3, vibration_sensitivity=5)
    assert (frame[0], frame[7], frame[8]) == (0xD1, 14, 5)
    # Round trip: the builder's encoding is the parser's decoding.
    echoed = bytearray(MANUAL.raw)
    echoed[7] = frame[7]
    assert P.parse_status(bytes(echoed)).sound_sensitivity == 3


@pytest.mark.parametrize("kwargs", [
    dict(correction_level=0), dict(correction_level=11),
    dict(sound_sensitivity=0), dict(sound_sensitivity=6),
    dict(vibration_sensitivity=6), dict(mode=4), dict(mode=7),
])
def test_out_of_range_settings_are_refused(kwargs):
    with pytest.raises(P.CommandError):
        P.settings_frame(MANUAL, P.INSTRUCTION_MODE, **kwargs)


_BUILDABLE = {P.INSTRUCTION_MODE, P.INSTRUCTION_ADJUST_ENTER, P.INSTRUCTION_ADJUST_SAVE}


@pytest.mark.parametrize("instruction", [i for i in range(256) if i not in _BUILDABLE])
def test_only_mode_and_sensitivity_instructions_are_built(instruction):
    with pytest.raises(P.CommandError):
        P.settings_frame(MANUAL, instruction)


def test_auto_increase_keeps_the_range_the_collar_holds():
    frame = P.settings_frame(PAGING_MODE, P.INSTRUCTION_MODE, mode=P.MODE_CODES["auto_increase"])
    assert frame[1] == 3
    assert frame[3:6] == PAGING_MODE.raw[3:6]


def test_auto_increase_without_a_range_is_refused():
    blank = bytearray(PAGING_MODE.raw)
    blank[3:6] = b"\x00\x00\x00"
    with pytest.raises(P.CommandError):
        P.settings_frame(P.parse_status(bytes(blank)), P.INSTRUCTION_MODE, mode=3)


def test_auto_increase_level_conversion():
    # Sample points: level 2 is raw 12 and level 5 is raw 45.
    assert [P.intensity_from_level(n) for n in (1, 2, 5, 10)] == [1, 12, 45, 100]
    assert [P.level_from_intensity(r) for r in (1, 12, 45, 100)] == [1, 2, 5, 10]
    assert all(P.level_from_intensity(P.intensity_from_level(n)) == n for n in range(1, 11))
    assert P.level_from_intensity(0) == 1 and P.level_from_intensity(200) == 10
    assert PAGING_MODE.auto_increase_levels == (2, 2, 5)          # raw 0c 0c 2d


def test_auto_increase_range_frame():
    frame = P.settings_frame(PAGING_MODE, P.INSTRUCTION_MODE, auto_increase_max=7)
    assert (frame[3], frame[4], frame[5]) == (PAGING_MODE.raw[3], 12, 67)
    frame = P.settings_frame(PAGING_MODE, P.INSTRUCTION_MODE, auto_increase_min=1, auto_increase_max=3)
    assert (frame[4], frame[5]) == (1, 23)


@pytest.mark.parametrize("kwargs", [dict(auto_increase_min=6), dict(auto_increase_max=1),
                                    dict(auto_increase_min=0), dict(auto_increase_max=11)])
def test_bad_auto_increase_ranges_are_refused(kwargs):
    # PAGING_MODE holds 2..5: a min above the max, a max below the min, or off the 1..10 scale.
    with pytest.raises(P.CommandError):
        P.settings_frame(PAGING_MODE, P.INSTRUCTION_MODE, **kwargs)
