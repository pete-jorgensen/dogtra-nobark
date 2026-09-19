#!/usr/bin/env python3
"""
Lay out every captured 0x2A59 payload as a byte grid and say which positions move.

Reads a directory of dump JSONs written by collar_read.py. Pure analysis -- touches no
hardware.

    python3 scripts/analyse-series.py data/
"""
from __future__ import annotations

import json
import pathlib
import sys

TARGET = "00002a59"


def samples_from(path: pathlib.Path):
    """Every (when, label, hexvalue) for the target characteristic, in TIME order.

    ⚠️ Sort on the stamp INSIDE each file, never on the filename. A manual run named
    series.json sorts after dump-*.json while belonging in the middle, and on a time series
    a mis-ordered row invents a trend that is not there.
    """
    out = []
    for f in sorted(path.rglob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        base = d.get("stamp") or ""
        hhmm = base[11:19] or f.name
        for s in d.get("services", []):
            for c in s.get("characteristics", []):
                if c.get("uuid", "").startswith(TARGET) and c.get("value_hex"):
                    out.append((base, f"{hhmm} connect", c["value_hex"]))
        for row in d.get("series", []):
            for u, v in row.get("values", {}).items():
                if u.startswith(TARGET) and not v.startswith("<"):
                    out.append((f"{base}+{row['t']:06.1f}", f"{hhmm} +{row['t']:.0f}s", v))
    out.sort(key=lambda r: r[0])
    return [(lab, v) for _, lab, v in out]


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    rows = samples_from(root)
    if not rows:
        print(f"no {TARGET} samples under {root}")
        return 1

    # De-duplicate consecutive identical values; a repeat carries no information.
    dedup = [rows[0]]
    for lab, v in rows[1:]:
        if v != dedup[-1][1]:
            dedup.append((lab, v))

    blobs = [bytes.fromhex(v) for _, v in dedup]
    n = min(len(b) for b in blobs)
    print(f"{len(rows)} samples, {len(dedup)} DISTINCT values, {n} bytes each\n")

    hdr = "                       " + " ".join(f"{i:02d}" for i in range(n))
    print(hdr)
    print("                       " + "-" * (3 * n - 1))
    for (lab, _), b in zip(dedup, blobs):
        print(f"{lab:22} " + " ".join(f"{x:02x}" for x in b[:n]))

    moved = [i for i in range(n) if len({b[i] for b in blobs}) > 1]
    const = [i for i in range(n) if i not in moved]
    print(f"\nCONSTANT positions ({len(const)}): {const}")
    print(f"   values: " + " ".join(f"{i}={blobs[0][i]:02x}" for i in const))
    print(f"\nMOVED positions ({len(moved)}): {moved}")
    for i in moved:
        seq = [b[i] for b in blobs]
        print(f"   byte {i:2d}: " + " ".join(f"{x:3d}" for x in seq) +
              f"   (hex {' '.join(f'{x:02x}' for x in seq)})")
    print("\n⚠️  Distinct-sample count is the honest denominator. Decoding a field needs it "
          "to be large\n    AND tied to known events -- a moving byte is equally a counter, "
          "a timer, a voltage\n    or a checksum until something discriminates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
