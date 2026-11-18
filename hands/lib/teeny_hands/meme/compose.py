"""The compositor: exact words drawn onto pictures by code, never by a
model, so a caption says what it was written to say, spelled right.

  caption(picture, top, bottom, style)  one picture, words over it or on a band
  comic(pictures, captions, layout, position)  2 to 4 pictures in one frame, each lettered

Everything here is arithmetic on Pillow images; nothing reads the ledger or
the network. Text is never changed: not upper-cased, not cut, not
hyphenated. It is wrapped at spaces (and at a newline the writer put in),
and its size is fitted: the largest size at which every line fits the box,
down to a floor. A caption that would need to go below that floor is
refused (`Unfit`) rather than shrunk to nothing: words too small to read on
a phone are worse than no meme. So is a character the font has no glyph for
(an emoji, most non-Latin scripts), which would print as a box.

The floors are a share of the picture's width, because X shows a picture
scaled to the width of the timeline (about 500 to 600 points on a phone):
what is legible is a share of the width, not a count of pixels.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import hand
from ..media import fit

FONT_DIR = "brain/assets/fonts"
#: The fonts ship with the code (the checkout this file is in), not with
#: whatever TEENY_ROOT a run points at.
FONT_ROOT = Path(__file__).resolve().parents[4] / FONT_DIR
IMPACT_FONT = "Anton-Regular.ttf"
LABEL_FONT = "AtkinsonHyperlegible-Bold.ttf"
STYLES = ("impact", "label", "subtitle")
LAYOUTS = ("row", "grid", "strip")
POSITIONS = ("under", "over")

#: A picture is drawn on at this width at least (a smaller one is scaled up
#: first, so the letters are not blurred by X scaling it up later) and at
#: most this long a side (a larger one is scaled down: X shows no more).
MIN_WIDTH = 800
MAX_SIDE = 2048
#: The shortest side a picture may have to be captioned at all.
MIN_INPUT_SIDE = 200

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
INK = (17, 17, 17)


@dataclass(frozen=True)
class Spec:
    """How one style sets its words, as shares of the picture's width
    (sizes, margins) and height (how tall a block may grow)."""

    font: str
    max_size: float  # the largest font size, a share of the width
    min_size: float  # the floor: below this it is refused
    min_px: int  # and never below this many pixels
    box_height: float  # the most of the picture's height one block may take
    max_lines: int
    stroke: float  # outline width, a share of the font size (0: none)


SPECS = {
    "impact": Spec(IMPACT_FONT, 0.11, 0.045, 28, 0.30, 3, 0.07),
    "label": Spec(LABEL_FONT, 0.065, 0.034, 24, 0.40, 4, 0.0),
    "subtitle": Spec(LABEL_FONT, 0.052, 0.032, 22, 0.22, 2, 0.11),
}
#: The panel caption of a comic: the lettering under or over each picture.
#: Its floor is a share of the whole frame's width, which is what X scales.
PANEL = Spec(LABEL_FONT, 0.085, 0.017, 24, 0.30, 3, 0.0)
#: The margin around a block of words, a share of the width.
MARGIN = 0.04


class Unfit(Exception):
    """The words cannot be set legibly; the reason says why."""


def _pil() -> Any:
    return fit.pillow()


@functools.lru_cache(maxsize=64)
def _font(name: str, size: int) -> Any:
    ImageFont = _pil()[2]
    path = FONT_ROOT / name
    if not path.is_file():
        raise hand.HandError(f"the caption font is missing ({FONT_DIR}/{name})")
    return ImageFont.truetype(str(path), size)


@functools.lru_cache(maxsize=8)
def _notdef(name: str) -> tuple[tuple[int, int], bytes]:
    """What the font draws for a character it does not have: a code point in
    a private-use plane no Latin font fills."""
    mask = _font(name, 48).getmask("\U000f0000")
    return mask.size, bytes(mask)


def missing_glyphs(text: str, font_name: str) -> list[str]:
    """Each character of `text` (once) the font cannot draw."""
    font = _font(font_name, 48)
    notdef = _notdef(font_name)
    missing: list[str] = []
    for char in dict.fromkeys(text):
        if char.isspace():
            continue
        mask = font.getmask(char)
        if (mask.size, bytes(mask)) == notdef or not char.isprintable() or mask.size[0] == 0:
            missing.append(char)
    return missing


def _width(font: Any, text: str, stroke: int) -> float:
    return font.getlength(text) + 2 * stroke


def _wrap(text: str, font: Any, width: float, stroke: int) -> list[str] | None:
    """`text` in lines no wider than `width`, broken only at spaces and at
    the writer's own newlines; None when a single word is wider."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            continue
        line = words[0]
        if _width(font, line, stroke) > width:
            return None
        for word in words[1:]:
            if _width(font, word, stroke) > width:
                return None
            candidate = f"{line} {word}"
            if _width(font, candidate, stroke) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


@dataclass(frozen=True)
class Block:
    """Words set at one size: the lines, the font, the outline, the height."""

    lines: tuple[str, ...]
    size: int
    font_name: str
    stroke: int
    line_height: int
    gap: int

    @property
    def height(self) -> int:
        return len(self.lines) * self.line_height + (len(self.lines) - 1) * self.gap

    @property
    def font(self) -> Any:
        return _font(self.font_name, self.size)


def _set_at(text: str, spec: Spec, size: int, width: float) -> Block | None:
    font = _font(spec.font, size)
    stroke = round(size * spec.stroke) if spec.stroke else 0
    lines = _wrap(text, font, width, stroke)
    if lines is None or not lines:
        return None
    ascent, descent = font.getmetrics()
    return Block(tuple(lines), size, spec.font, stroke, ascent + descent + 2 * stroke, max(1, round(size * 0.06)))


def floor_px(spec: Spec, reference_width: int) -> int:
    return max(spec.min_px, round(spec.min_size * reference_width))


def fit_block(text: str, spec: Spec, width: float, max_height: float, reference_width: int,
              largest: int | None = None) -> Block:
    """The largest setting of `text` (at most `largest`, else the style's
    own largest) whose lines fit `width`, number at most the style's lines
    and stand at most `max_height` tall; Unfit when only a size under the
    floor would."""
    floor = floor_px(spec, reference_width)
    top = largest if largest is not None else max(floor, round(spec.max_size * reference_width))
    if top < floor:
        raise Unfit(f"the words would have to be smaller than {floor} px to fit")
    low, high, best = floor, top, None
    while low <= high:  # the largest size that fits; fitting is monotone in size
        size = (low + high) // 2
        block = _set_at(text, spec, size, width)
        if block is not None and len(block.lines) <= spec.max_lines and block.height <= max_height:
            best, low = block, size + 1
        else:
            high = size - 1
    if best is None:
        block = _set_at(text, spec, floor, width)
        if block is None:
            raise Unfit("a word is too long for the picture's width even at the smallest legible size")
        if len(block.lines) > spec.max_lines:
            raise Unfit(f"the words need {len(block.lines)} lines at the smallest legible size; at most "
                        f"{spec.max_lines} fit")
        raise Unfit("the words do not fit the space they have at the smallest legible size")
    return best


def _draw(draw: Any, block: Block, centre_x: float, top: float, fill: tuple, outline: tuple | None) -> None:
    y = top
    for line in block.lines:
        kwargs: dict[str, Any] = {}
        if block.stroke and outline is not None:
            kwargs = {"stroke_width": block.stroke, "stroke_fill": outline}
        draw.text((centre_x, y + block.stroke), line, font=block.font, fill=fill, anchor="ma", **kwargs)
        y += block.line_height + block.gap


def prepare(image: Any) -> Any:
    """A picture made ready to draw on: turned upright (EXIF), flattened to
    RGB on white, and scaled into MIN_WIDTH..MAX_SIDE. Unfit when it is too
    small to caption."""
    Image, _, _, ImageOps = _pil()
    if min(image.size) < MIN_INPUT_SIDE:
        raise Unfit(f"the picture is {image.size[0]}x{image.size[1]}; a side under {MIN_INPUT_SIDE} px is too "
                    f"small to caption")
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, WHITE)
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    else:
        image = image.convert("RGB")
    width, height = image.size
    scale = 1.0
    if max(width, height) > MAX_SIDE:
        scale = MAX_SIDE / max(width, height)
    elif width < MIN_WIDTH:
        scale = min(MIN_WIDTH / width, MAX_SIDE / max(width, height))
    if scale != 1.0:
        image = image.resize((round(width * scale), round(height * scale)), Image.Resampling.LANCZOS)
    return image


def caption(image: Any, top: str | None, bottom: str | None, style: str) -> tuple[Any, list[dict[str, Any]]]:
    """The picture with its words, and how each block was set
    ({where, lines, size}). `impact` and `subtitle` draw over the picture
    with an outline; `label` adds a white band above (top) or below (bottom)
    and leaves the picture whole."""
    Image, ImageDraw = _pil()[:2]
    spec = SPECS[style]
    for text in (top, bottom):
        if text:
            missing = missing_glyphs(text, spec.font)
            if missing:
                raise Unfit("the caption font cannot draw " + ", ".join(repr(c) for c in missing))
    picture = prepare(image)
    width, height = picture.size
    margin = round(MARGIN * width)
    text_width = width - 2 * margin
    blocks: dict[str, Block] = {}
    for where, text in (("top", top), ("bottom", bottom)):
        if text:
            blocks[where] = fit_block(text, spec, text_width, spec.box_height * height, width)
    if style == "impact" and len(blocks) == 2:
        # One size for both, the smaller, so the meme reads as one.
        size = min(b.size for b in blocks.values())
        blocks = {w: fit_block(t, spec, text_width, spec.box_height * height, width, largest=size)
                  for w, t in (("top", top), ("bottom", bottom))}
    if style == "label":
        pad = round(margin * 0.75)
        bands = {w: b.height + 2 * pad for w, b in blocks.items()}
        frame = Image.new("RGB", (width, height + sum(bands.values())), WHITE)
        frame.paste(picture, (0, bands.get("top", 0)))
        draw = ImageDraw.Draw(frame)
        if "top" in blocks:
            _draw(draw, blocks["top"], width / 2, pad, INK, None)
        if "bottom" in blocks:
            _draw(draw, blocks["bottom"], width / 2, bands.get("top", 0) + height + pad, INK, None)
        result = frame
    else:
        draw = ImageDraw.Draw(picture)
        edge = round(margin * (0.5 if style == "impact" else 1.0))
        if "top" in blocks:
            _draw(draw, blocks["top"], width / 2, edge, WHITE, BLACK)
        if "bottom" in blocks:
            block = blocks["bottom"]
            _draw(draw, block, width / 2, height - edge - block.height, WHITE, BLACK)
        result = picture
    return result, [{"where": w, "lines": list(b.lines), "size": b.size} for w, b in blocks.items()]


def _cells(layout: str, count: int) -> tuple[int, int, int, int]:
    """(columns, rows, cell width, cell height) for a layout."""
    if layout == "row":
        cell = min(720, (2400 - (count + 1) * GUTTER) // count)
        return count, 1, cell, cell
    if layout == "grid":
        return 2, 2, 720, 720
    return 1, count, 900, 675


GUTTER = 16


def comic(images: list[Any], captions: list[str | None], layout: str, position: str) -> tuple[Any, dict[str, Any]]:
    """2 to 4 pictures in one frame, each cropped to its cell, each with its
    caption lettered on a white band under (or over) it, all at one size,
    on a near-black ground. How it was set: {layout, cell, size, panels}."""
    Image, ImageDraw, _, ImageOps = _pil()
    count = len(images)
    if layout == "grid" and count != 4:
        raise Unfit("a grid takes exactly 4 pictures")
    columns, rows, cell_w, cell_h = _cells(layout, count)
    frame_w = columns * cell_w + (columns + 1) * GUTTER
    margin = round(cell_w * 0.05)
    text_width = cell_w - 2 * margin
    worded = [c for c in captions if c]
    for text in worded:
        missing = missing_glyphs(text, PANEL.font)
        if missing:
            raise Unfit("the lettering font cannot draw " + ", ".join(repr(c) for c in missing))
    blocks: list[Block | None] = [None] * count
    band = 0
    if worded:
        max_text = PANEL.box_height * cell_h
        fitted = [fit_block(c, PANEL, text_width, max_text, frame_w, largest=round(PANEL.max_size * cell_w))
                  if c else None for c in captions]
        size = min(b.size for b in fitted if b is not None)
        blocks = [fit_block(c, PANEL, text_width, max_text, frame_w, largest=size) if c else None for c in captions]
        pad = round(margin * 0.6)
        band = max(b.height for b in blocks if b is not None) + 2 * pad
    panel_h = cell_h + band
    frame_h = rows * panel_h + (rows + 1) * GUTTER
    frame = Image.new("RGB", (frame_w, frame_h), INK)
    draw = ImageDraw.Draw(frame)
    for index, image in enumerate(images):
        column, row = (index % columns, index // columns)
        x = GUTTER + column * (cell_w + GUTTER)
        y = GUTTER + row * (panel_h + GUTTER)
        picture = ImageOps.fit(prepare(image), (cell_w, cell_h), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        picture_y = y + (band if position == "over" else 0)
        frame.paste(picture, (x, picture_y))
        if band:
            band_y = y if position == "over" else y + cell_h
            draw.rectangle((x, band_y, x + cell_w - 1, band_y + band - 1), fill=WHITE)
            block = blocks[index]
            if block is not None:
                _draw(draw, block, x + cell_w / 2, band_y + (band - block.height) / 2, INK, None)
    size = next((b.size for b in blocks if b is not None), None)
    return frame, {"layout": layout, "cell": [cell_w, cell_h], "size": size,
                   "panels": [{"lines": list(b.lines) if b else []} for b in blocks]}
