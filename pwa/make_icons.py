#!/usr/bin/env python3
"""Generate the PWA icons (a Poke Ball on the app's brand green), stdlib only.

Committed output, not a build artifact: run once, commit the PNGs beside it.
    python3 pwa/make_icons.py
Sizes: 512 (manifest any/maskable), 192 (manifest), 180 (iOS apple-touch-icon).
The ball sits inside the maskable safe zone so home-screen cropping keeps it whole.
"""
import zlib, struct, os

GREEN = (15, 138, 60, 255)     # --accent
RED   = (214, 60, 50, 255)
WHITE = (250, 250, 247, 255)   # --surface
DARK  = (17, 17, 17, 255)      # --ink


def _png(size, pixels):
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)   # 8-bit RGBA
    raw = b"".join(b"\x00" + bytes(pixels[y * size * 4:(y + 1) * size * 4])
                   for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _icon(size):
    px = bytearray(size * size * 4)
    c = size / 2.0
    R = size * 0.36            # ball radius (0.72 diameter -> maskable-safe)
    outline = size * 0.021
    band = size * 0.052        # equator band half-height
    btn_o = size * 0.128       # button outer radius
    btn_i = size * 0.082       # button inner radius
    for y in range(size):
        for x in range(size):
            dx, dy = x - c + 0.5, y - c + 0.5
            d = (dx * dx + dy * dy) ** 0.5
            if d > R:
                col = GREEN
            elif d > R - outline:
                col = DARK
            elif d <= btn_i:
                col = WHITE
            elif d <= btn_o:
                col = DARK
            elif abs(dy) <= band:
                col = DARK
            else:
                col = RED if dy < 0 else WHITE
            i = (y * size + x) * 4
            px[i:i + 4] = bytes(col)
    return px


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for sz in (512, 192, 180):
        data = _png(sz, _icon(sz))
        with open(os.path.join(here, f"icon-{sz}.png"), "wb") as f:
            f.write(data)
        print(f"wrote icon-{sz}.png ({len(data)} bytes)")
