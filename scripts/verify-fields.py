#!/usr/bin/env python3
"""
Test candidate byte->field hypotheses against every capture in data/ AND against the
app's own display. Prints a verdict per hypothesis; claims nothing it cannot check.

    python3 scripts/verify-fields.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

TARGET = "00002a59"
REFERENCE_APP_TIME = dt.datetime(2026, 9, 18, 14, 34, tzinfo=dt.timezone.utc)


def load(root: pathlib.Path):
    rows = []
    for f in sorted(root.rglob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        stamp = d.get("stamp")
        if not stamp:
            continue
        t0 = dt.datetime.fromisoformat(stamp)
        for s in d.get("services", []):
            for c in s.get("characteristics", []):
                if c.get("uuid", "").startswith(TARGET) and c.get("value_hex"):
                    rows.append((t0, bytes.fromhex(c["value_hex"])))
        for r in d.get("series", []):
            for u, v in r.get("values", {}).items():
                if u.startswith(TARGET) and not str(v).startswith("<"):
                    rows.append((t0 + dt.timedelta(seconds=r["t"]), bytes.fromhex(v)))
    rows.sort(key=lambda r: r[0])
    # collapse consecutive duplicates, keeping the FIRST time each value was seen
    out = [rows[0]]
    for t, b in rows[1:]:
        if b != out[-1][1]:
            out.append((t, b))
    return out


def h_timer(rows) -> bool:
    """HYPOTHESIS: bytes 16,17,18 are hours, minutes, seconds of a running timer.

    Test: the timer's own delta between consecutive samples must equal the wall-clock
    delta. That is a strong test -- it must hold across every pair, and a coincidence
    cannot survive four independent intervals.
    """
    print("=== H1: bytes 16,17,18 = h,m,s of a running timer ===")
    ok = True
    prev = None
    for t, b in rows:
        secs = b[16] * 3600 + b[17] * 60 + b[18]
        disp = f"{b[16]:02d}:{b[17]:02d}:{b[18]:02d}"
        line = f"  {t:%H:%M:%S}  timer {disp} ({secs:6d}s)"
        if prev:
            pt, ps = prev
            wall = (t - pt).total_seconds()
            dtimer = secs - ps
            if dtimer < 0:
                line += f"   RESET (wall +{wall:.0f}s)"
            else:
                err = dtimer - wall
                flag = "ok" if abs(err) <= 2 else f"MISMATCH {err:+.0f}s"
                line += f"   timer +{dtimer:5d}s vs wall +{wall:5.0f}s   {flag}"
                if abs(err) > 2:
                    ok = False
        print(line)
        prev = (t, secs)
    print(f"  VERDICT: {'CONFIRMED' if ok else 'FAILED'} "
          f"-- every interval matches wall clock within 2s\n" if ok else
          f"  VERDICT: FAILED\n")
    return ok


def h_battery(rows, app_pct, app_time) -> bool:
    """HYPOTHESIS: byte 6 is batteryLevel as a whole percent."""
    print("=== H2: byte 6 = batteryLevel (percent) ===")
    for t, b in rows:
        print(f"  {t:%m-%d %H:%M:%S}  byte6 = {b[6]:3d}")
    near = min(rows, key=lambda r: abs((r[0] - app_time).total_seconds()))
    print(f"  app displayed {app_pct}% at {app_time:%H:%M}; "
          f"nearest capture {near[0]:%H:%M:%S} has byte6 = {near[1][6]}")
    ok = near[1][6] == app_pct
    print(f"  VERDICT: {'CONFIRMED' if ok else 'MISMATCH'} "
          f"-- exact match against the vendor app's own display\n")
    return ok


def h_counters(rows) -> None:
    """HYPOTHESIS: bytes 9-14 are the six bark/howl/whine count+stimulate fields."""
    print("=== H3: bytes 9-14 = bark/howl/whine counts and stimulates ===")
    allzero = all(all(v == 0 for v in b[9:15]) for _, b in rows)
    print(f"  bytes 9-14 are zero in every capture: {allzero}")
    print("  app showed Bark 0, Whine & Howl 0, COUNT 00 -- consistent.")
    print("  VERDICT: CONSISTENT, NOT CONFIRMED -- a field that has only ever been 0")
    print("           cannot be told from an unused byte. Needs a real bark.\n")


def main() -> int:
    rows = load(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
    print(f"{len(rows)} distinct payloads\n")
    # The UTC instant of the reference app reading that the frames in tests/ were checked
    # against; the battery check picks the capture nearest to it.
    app_time = REFERENCE_APP_TIME
    h_timer(rows)
    h_battery(rows, 87, app_time)
    h_counters(rows)
    print("Ground truth from the app at that time: battery 87%, Correction Mode Pager,\n"
          "  Bark 0, Whine & Howl 0, vibration-motion sensitivity 3, volume-sound\n"
          "  sensitivity 2, timer 00:13:38.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
