"""A candlestick PNG out of the standard library alone (docs/hands.md, "chart")."""
from __future__ import annotations

import math
import struct
import uuid
import zlib
from pathlib import Path
from typing import Any

from .. import hand

WIDTH = 780
HEIGHT = 360
MARGIN_L = 8
MARGIN_R = 8
MARGIN_T = 8
MARGIN_B = 8
BACKGROUND = (24, 24, 28)
GRID = (60, 60, 68)
UP = (52, 199, 123)
DOWN = (255, 82, 82)
TEXT = (230, 230, 230)

# A tiny 5x7 font for the digits and the few letters the labels need.
_GLYPH_STRINGS = {
    "0": "01110 10001 10011 10101 11001 10001 01110",
    "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010",
    "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110",
    "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00010 01100",
    ".": "00000 00000 00000 00000 00000 01100 01100",
    "-": "00000 00000 00000 11111 00000 00000 00000",
    "h": "10001 10001 10001 11111 10001 10001 10001",
    "i": "01110 00100 00100 00100 00100 00100 01110",
    "g": "01111 10001 10001 01111 00001 00001 01110",
    "l": "01100 00100 00100 00100 00100 00100 01110",
    "o": "01110 10001 10001 10001 10001 10001 01110",
    "w": "10001 10001 10001 10101 10101 10101 01010",
    " ": "00000 00000 00000 00000 00000 00000 00000",
    "e": "01110 10001 11111 11000 10001 10001 01110",
}


def _glyph(ch: str) -> str | None:
    return _GLYPH_STRINGS.get(ch)


def _sig(price: float, digits: int = 6) -> float:
    """Round to `digits` significant places, so 0.0012 stays 0.0012."""
    if not price or not math.isfinite(price):
        return price
    return round(price, max(0, digits - 1 - math.floor(math.log10(abs(price)))))


def _fmt(price: float) -> str:
    """Image label: two decimals above 100, otherwise enough digits to be real."""
    if not price or not math.isfinite(price):
        return "0"
    magnitude = math.floor(math.log10(abs(price)))
    if magnitude >= 2:
        return f"{price:.2f}"
    digits = min(9, max(2, 5 - magnitude))
    if price >= 10**-digits:
        return f"{price:.{digits}f}"
    return f"{price:.2e}"  # smaller than the digits reach: exponent form, never a false zero


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _png(pixels: bytearray, width: int, height: int) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        start = (y * width) * 3
        raw += pixels[start : start + width * 3]
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def path_for(coin: str, days: int) -> Path:
    """Where the PNG for `coin` over `days` goes.

    state/media/chart/, the folder `hands/x upload` accepts, so the PNG a post
    quotes can be attached. One file per read (suffix = hand.now() plus a random token, so same-second and pinned-clock reads still differ): a later
    read never overwrites a chart an earlier caller still holds.
    """
    directory = hand.state_dir() / "media" / "chart"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{coin}-{int(days)}d-{int(hand.now())}-{uuid.uuid4().hex[:8]}.png"


def render(candles: list[tuple], path: Path) -> dict[str, Any]:
    """Draw `candles` (ts, open, high, low, close) to `path` as a PNG.

    Answers the numbers a post may quote: last_close, change_pct, high, low,
    Answers last_close, change_pct, high and low with enough significant digits that sub-cent prices survive.
    """
    if not candles:
        raise hand.Refused("chart: no candles to draw")
    for _ts, o, h, l, c in candles:
        if not all(math.isfinite(v) for v in (o, h, l, c)) or not (
            l <= h and l <= min(o, c) <= max(o, c) <= h
        ):
            raise hand.Refused("chart: bad candle range")
    pixels = bytearray(WIDTH * HEIGHT * 3)
    for i in range(len(pixels) // 3):
        pixels[i * 3 : i * 3 + 3] = bytes(BACKGROUND)

    def put(x: int, y: int, color: tuple) -> None:
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            base = (y * WIDTH + x) * 3
            pixels[base : base + 3] = bytes(color)

    def fill(x: int, y: int, w: int, h: int, color: tuple) -> None:
        for yy in range(max(0, y), min(HEIGHT, y + h)):
            for xx in range(max(0, x), min(WIDTH, x + w)):
                put(xx, yy, color)

    def text(x: int, y: int, message: str, color: tuple) -> None:
        for i, ch in enumerate(message):
            glyph = _glyph(ch)
            if glyph is None:
                continue
            for gy, row in enumerate(glyph.split(" ")):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        put(x + i * 6 + gx, y + gy, color)

    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    high, low = max(highs), min(lows)
    span = (high - low) or 1.0
    plot_h = HEIGHT - MARGIN_T - MARGIN_B
    plot_w = WIDTH - MARGIN_L - MARGIN_R
    n = len(candles)
    slot = plot_w / n
    body_w = max(1, int(slot * 0.6))

    def y_of(price: float) -> int:
        return MARGIN_T + int((high - price) / span * plot_h)

    for index, (_ts, o, h, l, c) in enumerate(candles):
        color = UP if c >= o else DOWN
        x = MARGIN_L + int(slot * index + slot / 2)
        top = max(0, min(HEIGHT - 1, y_of(h)))
        bottom = max(0, min(HEIGHT - 1, y_of(l)))
        for yy in range(top, bottom + 1):
            put(x, yy, color)
        y_open, y_close = y_of(o), y_of(c)
        top, bottom = min(y_open, y_close), max(y_open, y_close)
        fill(x - body_w // 2, top, body_w, max(1, bottom - top + 1), color)

    text(MARGIN_L, HEIGHT - MARGIN_B - 8, _fmt(low) + " low", TEXT)
    label = _fmt(high) + " high"
    text(WIDTH - MARGIN_L - 6 * len(label), MARGIN_T, label, TEXT)
    Path(path).write_bytes(_png(pixels, WIDTH, HEIGHT))
    first_open = float(candles[0][1])
    last_close = float(candles[-1][4])
    return {
        "path": str(path),
        "width": WIDTH,
        "height": HEIGHT,
        "candles": n,
        "last_close": _sig(last_close),
        "change_pct": round((last_close - first_open) / first_open * 100.0, 2) if first_open else 0.0,
        "high": _sig(high),
        "low": _sig(low),
    }
