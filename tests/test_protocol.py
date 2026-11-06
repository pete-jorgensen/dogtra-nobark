"""The parser against sample status frames and their expected decoded values."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "dogtra_nobark"


def _load(name: str):
    """Import a module from the component without importing homeassistant."""
    spec = importlib.util.spec_from_file_location(f"dogtra_nobark.{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"dogtra_nobark.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


# const has no HA imports; protocol imports only const.
sys.modules.setdefault("dogtra_nobark", type(sys)("dogtra_nobark"))
sys.modules["dogtra_nobark"].__path__ = [str(PKG)]  # type: ignore[attr-defined]
_load("const")
protocol = _load("protocol")
parse_status, FrameError = protocol.parse_status, protocol.FrameError


# (frame hex, expected decoded values)
SAMPLE_FRAMES = [
    # expected: battery 87 %, mode="pager", bark 0, whine&howl 0, timer 00:08:02
    ("010200010138570a02000000000000ef00080200000000000000",
     dict(battery=87, mode="pager", bark_count=0, whine_and_howl_count=0,
          uptime_seconds=8 * 60 + 2, sound_sensitivity=2)),
    # expected: Count Only, 5 barks, 0 howls
    ("0105000c0c2d510a03000000000000ef0408330b000500000000",
     dict(mode="count_only", bark_count=5, howl_count=0, whine_count=0, battery=81)),
    # expected: Manual level 2, sound 4, vibration 1, bark 5, whine 1
    ("0101020c0c2d4e1201010000010000ef06343200000600000001",
     dict(mode="manual", correction_level=2, sound_sensitivity=4,
          vibration_sensitivity=1, whine_count=1)),
    # expected: mode="pager", sound 2, vibration 2, bark 7
    ("0102000c0c2d4c0a02000000000000ef07353300000700000001",
     dict(mode="pager", correction_level=0, sound_sensitivity=2,
          vibration_sensitivity=2, bark_count=7, whine_count=1)),
    # expected: bark 7, Whine & Howl 9
    ("0102000c0c2d4c0a02000101000100ef0816330f000700050004",
     dict(bark_count=7, howl_count=5, whine_count=4, whine_and_howl_count=9,
          uptime_seconds=8 * 3600 + 22 * 60 + 51, pending=15)),
]


@pytest.mark.parametrize("hexframe,expected", SAMPLE_FRAMES)
def test_decodes_sample_frames(hexframe, expected):
    state = parse_status(bytes.fromhex(hexframe))
    for field, want in expected.items():
        assert getattr(state, field) == want, field


def test_counters_are_big_endian_u16():
    frame = bytearray.fromhex("0102000c0c2d4c0a02000000000000ef07353300000700000001")
    frame[20:22] = (300).to_bytes(2, "big")
    assert parse_status(bytes(frame)).bark_count == 300


def test_unknown_mode_keeps_its_number():
    frame = bytearray.fromhex("0102000c0c2d4c0a02000000000000ef07353300000700000001")
    frame[1] = 4
    assert parse_status(bytes(frame)).mode == "mode_4"


def test_refuses_wrong_length_and_missing_marker():
    with pytest.raises(FrameError):
        parse_status(b"\x00" * 25)
    bad = bytearray.fromhex("0102000c0c2d4c0a02000000000000ef07353300000700000001")
    bad[15] = 0x00
    with pytest.raises(FrameError):
        parse_status(bytes(bad))


def test_sound_sensitivity_rejects_off_grid_values():
    assert protocol.sound_sensitivity_level(10) == 2
    assert protocol.sound_sensitivity_level(18) == 4
    assert protocol.sound_sensitivity_level(11) is None


def test_byte_15_is_the_detection_bitmask_not_a_marker():
    frame = bytearray.fromhex("0102000c0c2d4c0a02000000000000ef07353300000700000001")
    assert parse_status(bytes(frame)).detection == {
        "detect_bark": True, "detect_howl": True, "detect_whine": True,
        "low_volume_correction": False,
    }
    frame[15] = 0x9F          # bark on, howl off, whine off, low-volume on
    assert parse_status(bytes(frame)).detection == {
        "detect_bark": True, "detect_howl": False, "detect_whine": False,
        "low_volume_correction": True,
    }
    frame[15] = 0x0F          # every toggle off is still a valid frame
    assert not any(parse_status(bytes(frame)).detection.values())


def test_byte_15_low_nibble_must_be_1111():
    frame = bytearray.fromhex("0102000c0c2d4c0a02000000000000ef07353300000700000001")
    frame[15] = 0xE0
    with pytest.raises(FrameError):
        parse_status(bytes(frame))
