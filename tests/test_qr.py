#!/usr/bin/env python3
"""The QR encoder, against known-good vectors.

    python3 tests/test_qr.py

A wrong QR still looks exactly like a QR. It is a picture of a square, it has the three
corner patterns, it renders beautifully — and no phone will read it. Three separate bugs in
the first draft of `openboat/qr.py` each produced precisely that, and none of them could have
been caught by looking at the output.

So every vector below came out of an independently generated symbol (macOS CoreImage's
CIQRCodeGenerator, decoded back with Vision) rather than out of the same head that wrote the
encoder. They are the specification's own numbers, checkable by hand against any published
QR reference, and they stay here so the three bugs cannot come back:

  1. the format word written least-significant-bit first instead of most
  2. pad codewords all 0xEC instead of alternating 0xEC/0x11
  3. a malformed generator polynomial
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"{PASS if ok else FAIL}  {what}")


def test_generator_polynomials() -> None:
    """The Reed-Solomon generators, which are published constants."""
    from openboat.qr import _generator

    check(_generator(2) == [1, 3, 2], "generator for 2 EC codewords is x² + 3x + 2")
    check(_generator(10) == [1, 0xD8, 0xC2, 0x9F, 0x6F, 0xC7, 0x5E, 0x5F, 0x71, 0x9D, 0xC1],
          "generator for 10 EC codewords matches the published coefficients")
    check(_generator(16)[0] == 1 and len(_generator(16)) == 17,
          "generator for 16 EC codewords is monic and of the right degree")


def test_parity_against_an_independent_symbol() -> None:
    """Data and parity read out of a symbol this project did not produce."""
    from openboat.qr import _ec

    # Codewords recovered from a CoreImage-generated version 1-M symbol.
    data = [0x20, 0x2B, 0x0B, 0x78, 0xCC, 0x00, 0xEC, 0x11,
            0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11]
    parity = [0x4D, 0xDD, 0x85, 0x13, 0xDB, 0x5F, 0x47, 0x73, 0x9A, 0x65]
    check(_ec(data, 10) == parity, "parity reproduces an independently generated symbol's")


def test_header_and_padding() -> None:
    """Byte-mode header, then the alternating pad — not 0xEC all the way down."""
    from openboat.qr import _codewords

    words = _codewords("HELLO", 1)
    check(words[:7] == [0x40, 0x54, 0x84, 0x54, 0xC4, 0xC4, 0xF0],
          "byte-mode header and payload encode correctly")
    check(words[7:16] == [0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC],
          "pad codewords alternate 0xEC / 0x11")
    check(len(words) == 26, "version 1-M produces 16 data + 10 parity codewords")


def test_structure() -> None:
    """The parts of the symbol a scanner locates before it reads anything."""
    from openboat.qr import QRError, encode

    m = encode("HELLO")
    size = len(m)
    check(size == 21, f"a short string fits version 1, 21 modules (got {size})")

    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        ring_ok = all(m[r0 + r][c0 + c] == (max(abs(r - 3), abs(c - 3)) != 2)
                      for r in range(7) for c in range(7))
        check(ring_ok, f"finder pattern at ({r0},{c0}) is correct")

    check(all(m[6][i] == (i % 2 == 0) for i in range(8, size - 8)),
          "the horizontal timing pattern alternates")
    check(m[size - 8][8], "the always-dark module is dark")

    for text, version in (("A", 1), ("x" * 30, 3), ("x" * 106, 6)):
        check(len(encode(text)) == 17 + 4 * version,
              f"{len(text)} bytes selects version {version}")
    try:
        encode("x" * 107)
        check(False, "a string past the encoder's limit is refused")
    except QRError:
        check(True, "a string past the encoder's limit is refused, not silently mangled")


def test_renderers() -> None:
    from openboat.qr import ascii_art, encode, svg

    m = encode("http://192.168.1.10:8752")
    out = svg(m, module=8, quiet=4)
    check(out.startswith("<svg") and out.endswith("</svg>"), "svg() emits one svg element")
    check('shape-rendering="crispEdges"' in out, "svg() renders modules without blurring")
    art = ascii_art(m)
    check(len(art.splitlines()) >= len(m) // 2, "ascii_art() emits the whole symbol")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_generator_polynomials, test_parity_against_an_independent_symbol,
                 test_header_and_padding, test_structure, test_renderers):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
