"""Pictures that X will take: under its 5 MB limit for a still.

X refuses a still over 5 MB at upload, after the picture was paid for. On
2026-11-14 one of Teeny's pictures (`image-12.png`, a 1 MP PNG from the
image model) came back at 5,263,141 bytes and its upload failed; the post
went out without it. So every picture a media hand writes passes through
`under_limit` first: a file already under the limit is kept byte for byte,
and a larger one is re-encoded, then shrunk, until it fits.

The order is the one that loses least: a PNG is first saved again with
compression turned up (lossless); then, for a picture with no transparency,
as a JPEG at falling quality (92 down to 75); then it is scaled down by a
tenth at a time and tried again. A picture with transparency stays a PNG
and is only scaled. Nothing here invents a step that cannot end: the size
falls with every pass, and a picture that would have to go under
MIN_SIDE pixels to fit is refused (it would not be worth posting).

Pillow (python3-pil in the image) does the work. It is imported when a
picture is over the limit, so a hand that never meets one runs without it.
"""

from __future__ import annotations

import io
from typing import Any

from .. import hand

#: What a picture made here is kept under. X's limit for a still is 5 MB,
#: which it means as 5 MiB (5,242,880; hands/x upload refuses above that):
#: 4,980,488 bytes uploaded on 2026-11-15, 5,263,141 failed on 2026-11-14.
#: 5,000,000 leaves a margin.
X_IMAGE_MAX_BYTES = 5_000_000
#: The quality steps a JPEG is tried at, best first.
JPEG_QUALITIES = (92, 88, 84, 80, 75)
#: Each scaling pass keeps this much of each side.
SCALE_STEP = 0.9
#: The shortest side a shrunk picture may have.
MIN_SIDE = 320
#: The most pixels a picture handed here may have. Pillow's own guard only
#: warns up to twice its limit (a 45 MP PNG opens), so every file is opened
#: through `open_checked`, which refuses a picture over this from its header,
#: before a byte of it is decoded.
MAX_PIXELS = 40_000_000


def pillow() -> Any:
    """The Pillow modules, or a HandError naming the package to install."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError:
        raise hand.HandError("Pillow is not installed (python3-pil in the image; rebuild the spine image)") from None
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    return Image, ImageDraw, ImageFont, ImageOps


def open_checked(source: Any) -> Any:
    """A picture opened from `source` (a path or a file object) and decoded,
    after its header's size is checked against MAX_PIXELS: refused (`pixels`)
    over it, never decoded."""
    Image = pillow()[0]
    image = Image.open(source)
    width, height = image.size
    if width * height > MAX_PIXELS:
        image.close()
        raise hand.Refused(f"the picture is {width}x{height}, over {MAX_PIXELS:,} pixels", rule="pixels",
                           size=[width, height])
    image.load()
    return image


def normalise(image: Any) -> Any:
    """`image` in a mode the PNG and JPEG encoders take without losing it:
    RGBA when it has transparency, else RGB; 16- and 32-bit greys scaled to
    8 bits first (a plain conversion clips them to white); CMYK and the rest
    converted."""
    if image.mode in ("RGB", "RGBA"):
        return image
    if image.mode in ("I;16", "I;16L", "I;16B", "I;16N", "I"):
        top = 65535 if image.mode.startswith("I;16") else max(1, image.getextrema()[1])
        return image.convert("I").point(lambda v: v * (255 / top)).convert("L").convert("RGB")
    if image.mode == "F":
        return image.convert("L").convert("RGB")
    if image.mode in ("RGBA", "LA", "PA", "La") or (image.mode == "P" and "transparency" in image.info):
        return image.convert("RGBA")
    return image.convert("RGB")


def _has_alpha(image: Any) -> bool:
    if image.mode in ("RGBA", "LA", "PA"):
        return image.getextrema()[-1][0] < 255
    return image.mode == "P" and "transparency" in image.info


def _png(image: Any) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True, compress_level=9)
    return out.getvalue()


def _jpeg(image: Any, quality: int) -> bytes:
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=quality, optimize=True, progressive=True, subsampling="4:2:0")
    return out.getvalue()


def encode(image: Any, ext: str, limit: int = X_IMAGE_MAX_BYTES) -> tuple[bytes, str]:
    """`image` (a Pillow image) as bytes under `limit`, and the extension
    they are: PNG kept when it fits, else JPEG (no transparency), else scaled
    down. Deterministic: the same picture gives the same bytes."""
    Image = pillow()[0]
    image = normalise(image)
    alpha = _has_alpha(image)
    if ext == "png" or alpha:
        data = _png(image)
        if len(data) < limit:
            return data, "png"
    else:
        data = _jpeg(image, JPEG_QUALITIES[0])
        if len(data) < limit:
            return data, "jpg"
    current = image
    while True:
        if alpha:
            data = _png(current)
            if len(data) < limit:
                return data, "png"
        else:
            for quality in JPEG_QUALITIES:
                data = _jpeg(current, quality)
                if len(data) < limit:
                    return data, "jpg"
        width, height = current.size
        width, height = int(width * SCALE_STEP), int(height * SCALE_STEP)
        if min(width, height) < MIN_SIDE:
            raise hand.Refused(f"the picture cannot be brought under {limit:,} bytes without going below "
                               f"{MIN_SIDE} pixels a side", rule="size")
        current = current.resize((width, height), Image.Resampling.LANCZOS)


def under_limit(data: bytes, ext: str, limit: int = X_IMAGE_MAX_BYTES) -> tuple[bytes, str, dict[str, Any] | None]:
    """The picture's bytes under `limit`, its extension, and what was done
    ({from_bytes, to_bytes, from_ext, to_ext, size}) or None when it already
    fit and is kept as it is."""
    if len(data) < limit:
        return data, ext, None
    Image = pillow()[0]
    try:
        image = open_checked(io.BytesIO(data))
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise hand.HandError(f"the picture could not be read to shrink it ({type(exc).__name__})") from None
    out, new_ext = encode(image, ext, limit)
    shrunk = Image.open(io.BytesIO(out))
    return out, new_ext, {"from_bytes": len(data), "to_bytes": len(out), "from_ext": ext, "to_ext": new_ext,
                          "size": list(shrunk.size)}
