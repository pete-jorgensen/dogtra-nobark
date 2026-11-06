"""The integration may command the collar in exactly one place, and only through it.

The collar delivers corrections to a dog. So: one function writes (``device.py``'s
``_command``, reached only through ``async_command``), it writes only the command
characteristic, and nothing subscribes to notifications or pairs. Static check over the
shipped package, with comments and strings removed by the tokenizer rather than a regex, so
a forbidden call cannot hide behind a '#' inside a string or in a subpackage.
"""

from __future__ import annotations

import ast
import io
import pathlib
import re
import tokenize

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "dogtra_nobark"

#: Never anywhere in code.
FORBIDDEN_IN_CODE = (
    r"write_gatt_descriptor",
    r"start_notify",
    r"\.pair\s*\(",
    r"\bpair\s*=",            # establish_connection(..., pair=True) bonds through the adapter
)
#: Never in code or strings: the two request/response channels.
FORBIDDEN_IN_STRINGS = (
    r"2a5a",                  # no word boundary: in a 128-bit UUID it follows other hex
    r"2a3d",
)
#: The one module and function allowed to write.
WRITER = ("device.py", "_command")


def _strip(src: str, *, keep_strings: bool) -> str:
    drop = {tokenize.COMMENT} | (set() if keep_strings else {tokenize.STRING})
    return " ".join(
        tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type not in drop
    )


def _hits(src: str) -> list[str]:
    code, with_strings = _strip(src, keep_strings=False), _strip(src, keep_strings=True)
    return [p for p in FORBIDDEN_IN_CODE if re.search(p, code, re.IGNORECASE)] + \
           [p for p in FORBIDDEN_IN_STRINGS if re.search(p, with_strings, re.IGNORECASE)]


def _write_calls(src: str) -> list[tuple[str | None, ast.Call]]:
    """Every ``*.write_gatt_char(...)`` call, with the function it sits in."""
    found = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.func: list[str] = []

        def visit_FunctionDef(self, node):
            self.func.append(node.name)
            self.generic_visit(node)
            self.func.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "write_gatt_char":
                found.append((self.func[-1] if self.func else None, node))
            self.generic_visit(node)

    Visitor().visit(ast.parse(src))
    return found


def test_nothing_forbidden_anywhere():
    offenders = [
        f"{py.relative_to(PKG)}: {pat}"
        for py in PKG.rglob("*.py") for pat in _hits(py.read_text())
    ]
    assert not offenders, "forbidden code found: " + ", ".join(offenders)


def test_one_function_writes_and_only_to_the_command_characteristic():
    writes = {
        (py.name, func): calls
        for py in PKG.rglob("*.py")
        for func, calls in [(f, c) for f, c in _write_calls(py.read_text())]
    }
    assert set(writes) == {WRITER}, f"writes found in {sorted(writes)}"
    calls = [c for py in PKG.rglob("*.py") for f, c in _write_calls(py.read_text())]
    assert len(calls) == 1
    target = calls[0].args[0]
    assert isinstance(target, ast.Name) and target.id == "COMMAND_UUID"


def test_command_uuid_is_0x2a58():
    src = (PKG / "const.py").read_text()
    assert 'COMMAND_UUID = "00002a58-0000-1000-8000-00805f9b34fb"' in src


def test_guard_actually_catches_things():
    """The guard must fail on the shapes it claims to catch, or it proves nothing."""
    samples = {
        "await client.start_notify(u, cb)": r"start_notify",
        "establish_connection(C, d, n, pair=True)": r"\bpair\s*=",
        "client.read_gatt_char('00002a3d-0000')": r"2a3d",
        "x = 1  # start_notify in a comment is fine": None,
        "s = 'start_notify in a string is fine'": None,
    }
    for src, expect in samples.items():
        hits = _hits(src)
        assert (hits[0] if hits else None) == expect, src
    stray = "async def poll(c):\n    await c.write_gatt_char(X, b'')\n"
    assert [f for f, _ in _write_calls(stray)] == ["poll"]
