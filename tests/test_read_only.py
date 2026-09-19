"""The integration must never be able to command the collar.

Static check over the shipped package -- every module, recursively, with comments and
string literals removed by the tokenizer rather than by a regex, so a forbidden call
cannot hide behind a '#' inside a string or in a subpackage.

Scope note: this list is STRICTER than ``scripts/test-no-writes.sh`` and that is on
purpose, not drift. The capture tool may enable notifications (a CCCD descriptor write
that cannot actuate); the integration does not need them -- it polls -- so it ships with
no write of any kind. If bark-event
notifications are ever added here, relaxing ``start_notify`` below is the conversation to
have first; the collar delivers corrections to a dog.
"""

from __future__ import annotations

import io
import pathlib
import re
import tokenize

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "dogtra_nobark"

#: Patterns checked against CODE (comments and string literals removed).
FORBIDDEN_IN_CODE = (
    r"write_gatt_char",
    r"write_gatt_descriptor",
    r"start_notify",
    r"\.pair\s*\(",
    r"\bpair\s*=",            # establish_connection(..., pair=True) bonds through the adapter
    r"normalize_uuid_16\s*\(\s*0x2a58",
)
#: Patterns checked against code AND string literals (comments removed): a UUID is a
#: string, so blanking strings would hide exactly the thing being looked for.
FORBIDDEN_IN_STRINGS = (
    r"\b2a58\b",              # the command characteristic, in any UUID spelling
)
FORBIDDEN = FORBIDDEN_IN_CODE + FORBIDDEN_IN_STRINGS


def _strip(src: str, *, keep_strings: bool) -> str:
    """Source with comments (and optionally string literals) removed via the tokenizer."""
    drop = {tokenize.COMMENT} | (set() if keep_strings else {tokenize.STRING})
    return " ".join(
        tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type not in drop
    )


def _hits(src: str) -> list[str]:
    code, with_strings = _strip(src, keep_strings=False), _strip(src, keep_strings=True)
    return [p for p in FORBIDDEN_IN_CODE if re.search(p, code, re.IGNORECASE)] + \
           [p for p in FORBIDDEN_IN_STRINGS if re.search(p, with_strings, re.IGNORECASE)]


def test_no_write_path_exists():
    offenders = []
    for py in PKG.rglob("*.py"):
        for pat in _hits(py.read_text()):
            offenders.append(f"{py.relative_to(PKG)}: {pat}")
    assert not offenders, "write-capable code found: " + ", ".join(offenders)


def test_guard_actually_catches_things():
    """The guard must fail on the shapes it claims to catch, or it proves nothing."""
    samples = {
        "await client.write_gatt_char(u, b)": r"write_gatt_char",
        "establish_connection(C, d, n, pair=True)": r"\bpair\s*=",
        "client.read_gatt_char('2a58')": r"\b2a58\b",
        "x = 1  # write_gatt_char in a comment is fine": None,
        "s = 'write_gatt_char in a string is fine'": None,
    }
    for src, expect in samples.items():
        hits = _hits(src)
        assert (hits[0] if hits else None) == expect, src
