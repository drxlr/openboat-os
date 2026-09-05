#!/usr/bin/env python3
"""A QR code, in the standard library, so a phone can be pointed at a screen.

    python3 -m openboat.qr "http://192.168.1.10:8752"

Typing `http://192.168.1.45:8752` into a phone at the end of a pontoon is exactly the kind of
small friction that stops a thing being used. A QR removes it, and removing it is worth the
two hundred lines below — because the alternative was a dependency, and this package is
stdlib-only so that it runs on a Raspberry Pi with nothing installed.

Deliberately small: **byte mode, error-correction level M, versions 1 to 6**, which is up to
106 characters. That covers every URL this project produces, including one carrying a
32-character key, and stopping at version 6 removes a whole feature from the encoder — the
version information block, which only exists from version 7. A longer string raises rather
than silently producing something that will not scan.

Correctness here is not a matter of taste. A QR that encodes the wrong bytes still *looks*
like a QR, and the failure is discovered by somebody standing in the rain. So the module
ships with a round-trip test that renders the output and decodes it back with an independent
decoder — see `tests/test_qr.py`.
"""

from __future__ import annotations

__all__ = ["encode", "svg", "ascii_art", "QRError"]

#: (data codewords, EC codewords per block, number of blocks) for level M, versions 1-6.
#: Byte capacity is data codewords minus the two taken by the mode and length header.
SPEC = {
    1: (16, 10, 1), 2: (28, 16, 1), 3: (44, 26, 1),
    4: (64, 18, 2), 5: (86, 24, 2), 6: (108, 16, 4),
}
#: Centres of the alignment patterns. Version 1 has none.
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}
#: The 15-bit format strings for level M and masks 0-7, straight from the specification's
#: table. Written out rather than computed: it is the one place a BCH slip would produce a
#: symbol that looks perfect and decodes as nothing.
FORMAT_M = [0x5412, 0x5125, 0x5E7C, 0x5B4B, 0x45F9, 0x40CE, 0x4F97, 0x4AA0]


class QRError(ValueError):
    """The string will not fit, or cannot be encoded."""


# ── GF(256), the field the Reed-Solomon parity is computed in ────────────────────────
EXP = [0] * 512
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:                      # x^8 + x^4 + x^3 + x^2 + 1, the QR primitive
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def _generator(n: int) -> list[int]:
    """The degree-n generator polynomial, (x - a^0)(x - a^1)…(x - a^(n-1)).

    Coefficients run highest power first, and each step is a plain polynomial multiply by
    (x + a^i) — in this field subtraction and addition are both XOR, so the minus signs in
    the usual statement of the product disappear.
    """
    poly = [1]
    for i in range(n):
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= coef                       # the x term, shifting the degree up
            nxt[j + 1] ^= _mul(coef, EXP[i])     # the a^i term
        poly = nxt
    return poly


def _ec(data: list[int], n: int) -> list[int]:
    """The n error-correction codewords for one block."""
    gen = _generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        factor = rem[i]
        if factor:
            for j, g in enumerate(gen):
                rem[i + j] ^= _mul(g, factor)
    return rem[len(data):]


# ── The bitstream ────────────────────────────────────────────────────────────────────
def _codewords(text: str, version: int) -> list[int]:
    body = text.encode("utf-8")
    data_cw, ec_cw, blocks = SPEC[version]
    bits: list[int] = []

    def put(value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    put(0b0100, 4)                       # byte mode
    put(len(body), 8)                    # 8-bit length field, correct for versions 1-9
    for byte in body:
        put(byte, 8)
    put(0, min(4, data_cw * 8 - len(bits)))          # terminator
    bits += [0] * (-len(bits) % 8)                   # pad to a whole codeword
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    # The specified alternating padding, 0xEC then 0x11. `start` is captured before the
    # loop because `words` grows inside it — reading len(words) each time made every pad
    # codeword 0xEC, which is a valid-looking symbol that no scanner will read.
    start = len(words)
    for pad in range(start, data_cw):
        words.append(0xEC if (pad - start) % 2 == 0 else 0x11)
    words = words[:data_cw]

    # Split into blocks, compute parity per block, then interleave — the interleaving is
    # what lets a scanner lose a whole corner of the symbol and still read it.
    per, extra = divmod(data_cw, blocks)
    groups, at = [], 0
    for b in range(blocks):
        size = per + (1 if b >= blocks - extra else 0)
        groups.append(words[at:at + size])
        at += size
    parity = [_ec(g, ec_cw) for g in groups]

    out: list[int] = []
    for i in range(max(len(g) for g in groups)):
        for g in groups:
            if i < len(g):
                out.append(g[i])
    for i in range(ec_cw):
        for pblock in parity:
            out.append(pblock[i])
    return out


# ── The matrix ───────────────────────────────────────────────────────────────────────
def _blank(size: int):
    return [[None] * size for _ in range(size)], [[False] * size for _ in range(size)]


def _place_patterns(m, fixed, version: int) -> None:
    size = len(m)

    def finder(r0: int, c0: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < size and 0 <= cc < size:
                    ring = max(abs(r - 3), abs(c - 3))
                    m[rr][cc] = ring != 2 and ring <= 3
                    fixed[rr][cc] = True

    finder(0, 0); finder(0, size - 7); finder(size - 7, 0)

    for i in range(8, size - 8):                       # timing
        m[6][i] = m[i][6] = (i % 2 == 0)
        fixed[6][i] = fixed[i][6] = True

    for r in ALIGN[version]:                           # alignment
        for c in ALIGN[version]:
            if (r < 9 and c < 9) or (r < 9 and c > size - 10) or (r > size - 10 and c < 9):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = max(abs(dr), abs(dc)) != 1
                    fixed[r + dr][c + dc] = True

    m[size - 8][8] = True                              # the always-dark module
    fixed[size - 8][8] = True

    # Reserve the format areas — and the three counts here are 9, 8 and 7, not 9, 9 and 9.
    # The second copy of the format word is 8 modules along row 8 and 7 modules up column 8
    # (the eighth being the dark module reserved above). Reserving one extra on each of
    # those sides steals two modules from the data stream and shifts every bit after them,
    # which produces a symbol whose patterns are all perfect and whose payload is noise.
    reserved = ([(8, i) for i in range(9)] + [(i, 8) for i in range(9)]
                + [(8, size - 1 - i) for i in range(8)]
                + [(size - 1 - i, 8) for i in range(7)])
    for rr, cc in reserved:
        if 0 <= rr < size and 0 <= cc < size and not fixed[rr][cc]:
            fixed[rr][cc] = True
            m[rr][cc] = False


def _place_data(m, fixed, words: list[int]) -> None:
    size = len(m)
    bits = [(w >> s) & 1 for w in words for s in range(7, -1, -1)]
    at, upward, col = 0, True, size - 1
    while col > 0:
        if col == 6:                                   # the vertical timing column
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if not fixed[row][c]:
                    m[row][c] = bool(bits[at]) if at < len(bits) else False
                    at += 1
        upward = not upward
        col -= 2


MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _penalty(m) -> int:
    """The specification's four penalty rules. Lower is easier for a scanner."""
    size, score = len(m), 0
    for line in list(m) + [list(col) for col in zip(*m)]:
        run, prev = 1, line[0]
        for cell in line[1:]:
            if cell == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, cell
        if run >= 5:
            score += 3 + (run - 5)
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    finder = [True, False, True, True, True, False, True]
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(size - 6):
            if line[i:i + 7] == finder:
                before = line[max(0, i - 4):i]
                after = line[i + 7:i + 11]
                if len(before) == 4 and not any(before):
                    score += 40
                if len(after) == 4 and not any(after):
                    score += 40
    dark = sum(cell for row in m for cell in row)
    score += 10 * (abs(dark * 100 // (size * size) - 50) // 5)
    return score


def _format_bits(m, mask: int) -> None:
    """Write both copies of the 15-bit format word.

    `i` counts from the **most** significant bit, which is the half of this that is easy to
    get backwards: the word's top bit goes at (8, 0), not its bottom one. Both the ordering
    and the second copy's split point below were derived by reading them back out of a
    known-good symbol rather than from memory, because a format word written in reverse
    produces a symbol that looks perfect and decodes as nothing at all.
    """
    size, word = len(m), FORMAT_M[mask]
    for i in range(15):
        bit = bool((word >> (14 - i)) & 1)
        if i < 6:
            m[8][i] = bit
        elif i == 6:
            m[8][7] = bit
        elif i == 7:
            m[8][8] = bit
        elif i == 8:
            m[7][8] = bit
        else:
            m[14 - i][8] = bit
        if i < 7:
            m[size - 1 - i][8] = bit
        else:
            m[8][size - 15 + i] = bit


def encode(text: str) -> list[list[bool]]:
    """The QR matrix for `text`, as rows of booleans where True is a dark module."""
    body = text.encode("utf-8")
    version = next((v for v in sorted(SPEC) if len(body) <= SPEC[v][0] - 2), None)
    if version is None:
        raise QRError(f"{len(body)} bytes is more than this encoder's 106-byte limit "
                      f"(byte mode, level M, versions 1-6)")
    words = _codewords(text, version)
    size = 17 + 4 * version
    m, fixed = _blank(size)
    _place_patterns(m, fixed, version)
    _place_data(m, fixed, words)

    best, best_score = None, None
    for mask in range(8):
        trial = [[bool(cell) for cell in row] for row in m]
        for r in range(size):
            for c in range(size):
                if not fixed[r][c] and MASKS[mask](r, c):
                    trial[r][c] = not trial[r][c]
        _format_bits(trial, mask)
        score = _penalty(trial)
        if best_score is None or score < best_score:
            best, best_score = trial, score
    return best


def svg(matrix, module: int = 8, quiet: int = 4) -> str:
    """The matrix as an SVG, sized in whole modules so it never blurs between pixels."""
    size = len(matrix) + quiet * 2
    rects = "".join(
        f'<rect x="{(c + quiet) * module}" y="{(r + quiet) * module}" '
        f'width="{module}" height="{module}"/>'
        for r, row in enumerate(matrix) for c, cell in enumerate(row) if cell)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size * module}" '
            f'height="{size * module}" viewBox="0 0 {size * module} {size * module}" '
            f'shape-rendering="crispEdges">'
            f'<rect width="100%" height="100%" fill="#fff"/>'
            f'<g fill="#000">{rects}</g></svg>')


def ascii_art(matrix, quiet: int = 2) -> str:
    """Two rows per line using half blocks, so it is square in a terminal.

    Light on dark is inverted from the usual printing convention on purpose: a terminal is
    usually dark, and a scanner needs the *quiet zone* to be the light colour. Printed this
    way round it scans off the screen.
    """
    size = len(matrix)
    grid = ([[False] * (size + quiet * 2) for _ in range(quiet)]
            + [[False] * quiet + list(row) + [False] * quiet for row in matrix]
            + [[False] * (size + quiet * 2) for _ in range(quiet)])
    if len(grid) % 2:
        grid.append([False] * len(grid[0]))
    out = []
    for r in range(0, len(grid), 2):
        line = ""
        for top, bottom in zip(grid[r], grid[r + 1]):
            line += " ▄▀█"[(not top) * 2 + (not bottom)] if False else (
                "█" if (not top and not bottom) else
                "▀" if not top else
                "▄" if not bottom else " ")
        out.append(line)
    return "\n".join(out)


if __name__ == "__main__":
    import sys

    text = " ".join(sys.argv[1:]) or "https://github.com/drxlr/openboat-os"
    print(ascii_art(encode(text)))
    print(text)
