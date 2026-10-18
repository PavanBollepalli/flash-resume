#!/usr/bin/env python3
"""Generate the Flash Resume toolbar icons: a monochrome "flash bolt" mark —
a near-black rounded square with an off-white lightning bolt.

Pure stdlib (zlib + struct), 4x supersampled so the small sizes stay crisp.
The same mark is drawn as an inline SVG in popup.html / history.html, so the
toolbar icon and the in-app brand share one silhouette.

Run:  python design/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

# The mark, in two colors. The toolbar icon is the canonical rendering; in-app
# the square/bolt swap with the theme so it reads on light and dark surfaces.
SQUARE = (0x0A, 0x0A, 0x0A)  # near-black
BOLT = (0xF0, 0xF0, 0xEB)  # warm off-white

# Lightning-bolt polygon in 0..1 coordinates (x right, y down).
BOLT_POLY = [
    (0.62, 0.09),
    (0.21, 0.57),
    (0.45, 0.57),
    (0.37, 0.93),
    (0.81, 0.41),
    (0.55, 0.41),
]

CORNER_R = 0.22  # corner radius of the rounded square, as a fraction of size
SS = 4  # supersampling factor per axis


def _in_rounded_square(px: float, py: float, r: float) -> bool:
    if not (0.0 <= px <= 1.0 and 0.0 <= py <= 1.0):
        return False
    corners = (
        (r, r, px < r, py < r),
        (1 - r, r, px > 1 - r, py < r),
        (r, 1 - r, px < r, py > 1 - r),
        (1 - r, 1 - r, px > 1 - r, py > 1 - r),
    )
    for cx, cy, in_x, in_y in corners:
        if in_x and in_y:
            return (px - cx) ** 2 + (py - cy) ** 2 <= r * r
    return True


def _in_poly(px: float, py: float) -> bool:
    """Even-odd ray cast against BOLT_POLY."""
    inside = False
    n = len(BOLT_POLY)
    j = n - 1
    for i in range(n):
        xi, yi = BOLT_POLY[i]
        xj, yj = BOLT_POLY[j]
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_cross:
                inside = not inside
        j = i
    return inside


def _render(size: int) -> bytes:
    """Render one icon and return raw RGBA rows (no filter bytes)."""
    rows = []
    inv = 1.0 / (size * SS)
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc_r = acc_g = acc_b = acc_a = 0
            for sy in range(SS):
                py = (y * SS + sy + 0.5) * inv
                for sx in range(SS):
                    px = (x * SS + sx + 0.5) * inv
                    if not _in_rounded_square(px, py, CORNER_R):
                        continue  # transparent
                    r, g, b = BOLT if _in_poly(px, py) else SQUARE
                    acc_r += r
                    acc_g += g
                    acc_b += b
                    acc_a += 255
            a = acc_a / (SS * SS)
            if acc_a:
                row += bytes((
                    round(acc_r * 255 / acc_a),
                    round(acc_g * 255 / acc_a),
                    round(acc_b * 255 / acc_a),
                    round(a),
                ))
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(bytes(row))
    return rows


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _png(size: int, rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + r for r in rows)  # filter type 0 per scanline
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "extension" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)
    for size in (16, 48, 128):
        path = out_dir / f"icon{size}.png"
        path.write_bytes(_png(size, _render(size)))
        print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
