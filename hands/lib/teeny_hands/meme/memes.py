"""Memes and comics: Teeny's pictures with exact words drawn on by code
(docs/hands.md, "meme").

  hands/meme caption {image, top?, bottom?, style?, alt?}
  hands/meme comic {panels: [{image, caption?}], layout?, captions?, alt?}

The picture comes from `state/media/` (hands/image's stills) or
`brain/assets/` (the ready art in `brain/assets/tiny/`); the words are drawn
by compose.py, so they are exactly the words given. The result is saved to
`state/media/meme-<id>.<ext>` or `comic-<id>.<ext>`, under X's 5 MB limit
for a still (media/fit.py), and recorded in `media_assets` (kind `meme` or
`comic`, cost 0, model `compositor`, how it was set in `detail`, the alt
text in `alt`), so `hands/x upload --file PATH --alt TEXT` takes it.

Words are public, so they are checked before anything is drawn: at most
MAX_TEXT characters a block, no control characters, no link, nothing the
guard's lexicon holds (the outbound screen: no model, no hold). The
studio's own gates (truth, stranger, viewer, the guard's judge) still read
every word as a post's: this is the floor, not the gate.

A meme costs nothing (no model, no network), so the caps are a count:
MEME_MAX_PER_DAY [30] memes and comics together a UTC day, and the runway
does not apply. Under DRY_RUN the words are checked and set in memory
(so a caption that would not fit is refused just the same), and the answer
says what would be made; no file is written and no row is kept.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import hand
from ..media import assets, fit
from . import compose

Json = dict[str, Any]

#: The folders a picture may come from, as for hands/x upload.
SOURCE_ROOTS = ("state/media", "brain/assets")
#: The largest picture file read, and the most characters in one block.
MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_TEXT = 120
MAX_PANEL_TEXT = 90
MODEL = "compositor"


def _setting_cap() -> int:
    return hand.number("MEME_MAX_PER_DAY", 30, 0, 200)


def resolve_source(path: Any) -> Path:
    """The real file behind `path` (relative to the checkout unless
    absolute) when it is a regular file under state/media/ or brain/assets/;
    symlinks and `..` resolved first. Anything else is refused (`path`)."""
    refusal = hand.Refused("a picture must be a file under state/media/ or brain/assets/", rule="path",
                           allowed=list(SOURCE_ROOTS))
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise refusal
    root = hand.root().resolve()
    candidate = Path(path.strip())
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise refusal from None
    allowed = [(root / folder).resolve() for folder in SOURCE_ROOTS]
    if not resolved.is_file() or not any(resolved.is_relative_to(folder) for folder in allowed):
        raise refusal
    return resolved


def _open(path: Any) -> tuple[Any, str]:
    """The picture at `path`, decoded, and where it came from (relative)."""
    Image = fit.pillow()[0]
    resolved = resolve_source(path)
    if resolved.stat().st_size > MAX_SOURCE_BYTES:
        raise hand.Refused(f"a picture is at most {MAX_SOURCE_BYTES // (1024 * 1024)} MB", rule="source")
    try:
        image = Image.open(resolved)
        if getattr(image, "is_animated", False):
            raise hand.Refused("a meme is drawn on a still, not an animation", rule="source")
        image.close()
        image = fit.open_checked(resolved)
    except hand.Refused:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise hand.Refused(f"the picture could not be read ({type(exc).__name__})", rule="source") from None
    return image, str(resolved.relative_to(hand.root().resolve()))


def words(value: Any, name: str, limit: int) -> str | None:
    """One block of words: None when absent or blank, else the text with
    each line's spaces folded. Refused when it is not a string, too long,
    carries a control character or a link, or the lexicon holds it."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise hand.Refused(f"{name} must be a string", rule="text")
    lines = [" ".join(line.split()) for line in value.strip().split("\n")]
    text = "\n".join(line for line in lines if line)
    if not text:
        return None
    if len(text) > limit:
        raise hand.Refused(f"{name} is at most {limit} characters", rule="text", field=name)
    if any(not (c.isprintable() or c == "\n") for c in text):
        raise hand.Refused(f"{name} carries a control character", rule="text", field=name)
    from ..x.posting_rules import has_link
    from ..guard.screen import OUTBOUND, screen_text

    if has_link(text):
        raise hand.Refused(f"{name} carries a link; a link on a picture cannot be followed", rule="text", field=name)
    found = screen_text(text, OUTBOUND)
    if found is not None:
        raise hand.Refused(f"the guard would hold {name} ({found.category}); say it another way", rule="text",
                           field=name, category=found.category)
    return text


def _only(body: Any, allowed: set[str], what: str) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise hand.HandError(f"stdin must be a JSON object ({what})")
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise hand.HandError(f"unknown field(s): {', '.join(unknown)}")
    return body


def _choice(body: dict[str, Any], key: str, choices: tuple[str, ...], default: str) -> str:
    value = body.get(key, default)
    if value is None:
        return default
    if value not in choices:
        raise hand.Refused(f"{key} is one of {', '.join(choices)}", rule=key)
    return value


def _unfit(exc: compose.Unfit) -> hand.Refused:
    return hand.Refused(f"the words would not fit legibly: {exc}", rule="fit")


def _quote(*texts: str | None) -> str:
    return " / ".join(f'"{t}"' for t in texts if t)


def _check_cap(conn) -> None:
    limit = _setting_cap()
    used = conn.execute(
        "SELECT COUNT(*) AS n FROM media_assets WHERE kind IN ('meme', 'comic') AND status != 'failed'"
        " AND created_at >= ?",
        (hand.day_start(),),
    ).fetchone()["n"]
    if used >= limit:
        raise hand.Refused(f"today's {limit} meme(s) and comic(s) are made", rule="per_day", limit=limit)


def _encode(image: Any, prefer: str) -> tuple[bytes, str]:
    return fit.encode(image, prefer)


def _save(kind: str, prompt: str, image: Any, prefer: str, detail: Json, alt: str | None) -> Json:
    """The finished picture: encoded under 5 MB, a row claimed, the file
    written whole (a `.part` renamed), the row done. A failure after the
    claim leaves the row failed, which counts toward nothing."""
    data, ext = _encode(image, prefer)
    with hand.lock("meme"):
        conn = assets._db()
        try:
            _check_cap(conn)
            asset_id = assets._insert(conn, kind, prompt, None, "post", alt)
            path = assets._media_dir() / f"{kind}-{asset_id}.{ext}"
            part = path.with_name(path.name + ".part")
            try:
                part.write_bytes(data)
                os.replace(part, path)
            except OSError as exc:
                part.unlink(missing_ok=True)
                assets._finish(conn, asset_id, status="failed", error=f"could not write the file ({type(exc).__name__})")
                raise hand.HandError(f"the {kind} could not be saved ({type(exc).__name__})") from None
            detail = {**detail, "bytes": len(data), "size": list(image.size)}
            assets._finish(conn, asset_id, status="done", model=MODEL, path=str(path.relative_to(hand.root())),
                           cost_micro=0, estimated=0, detail=json.dumps(detail, ensure_ascii=False))
            row = conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
            return {"asset": public(row)}
        finally:
            conn.close()


def _prefer(source: str) -> str:
    return "png" if source.lower().endswith(".png") else "jpg"


def caption(body: Any) -> Json:
    """`caption`: one picture with words on it (compose.caption)."""
    body = _only(body, {"image", "top", "bottom", "style", "alt"}, "image, top?, bottom?, style?, alt?")
    top = words(body.get("top"), "top", MAX_TEXT)
    bottom = words(body.get("bottom"), "bottom", MAX_TEXT)
    if top is None and bottom is None:
        raise hand.Refused("a caption needs words: top, bottom or both", rule="text")
    style = _choice(body, "style", compose.STYLES, "impact")
    alt = assets.alt_text(body) or f"{'a picture' if style != 'label' else 'a labelled picture'} with the words {_quote(top, bottom)}"
    image, source = _open(body.get("image"))
    try:
        result, blocks = compose.caption(image, top, bottom, style)
    except compose.Unfit as exc:
        raise _unfit(exc) from None
    detail = {"style": style, "source": source, "blocks": blocks}
    prompt = f"{style} caption on {source}: {_quote(top, bottom)}"
    if hand.dry_run():
        data, ext = _encode(result, _prefer(source))
        return {"dry_run": True, "would_make": "meme", "prompt": prompt, "alt": alt, **detail,
                "size": list(result.size), "bytes": len(data), "format": ext}
    return _save("meme", prompt, result, _prefer(source), detail, alt)


def comic(body: Any) -> Json:
    """`comic`: 2 to 4 pictures in one frame, each lettered (compose.comic)."""
    body = _only(body, {"panels", "layout", "captions", "alt"}, "panels, layout?, captions?, alt?")
    panels = body.get("panels")
    if not isinstance(panels, list) or not 2 <= len(panels) <= 4:
        raise hand.Refused("a comic has 2 to 4 panels", rule="panels")
    default_layout = "grid" if len(panels) == 4 else "row"
    layout = _choice(body, "layout", compose.LAYOUTS, default_layout)
    position = _choice(body, "captions", compose.POSITIONS, "under")
    images, sources, texts = [], [], []
    for n, panel in enumerate(panels, 1):
        panel = _only(panel, {"image", "caption"}, f"panel {n}: image, caption?")
        texts.append(words(panel.get("caption"), f"panel {n} caption", MAX_PANEL_TEXT))
    for panel in panels:
        image, source = _open(panel.get("image"))
        images.append(image)
        sources.append(source)
    try:
        result, setting = compose.comic(images, texts, layout, position)
    except compose.Unfit as exc:
        raise _unfit(exc) from None
    said = [t for t in texts if t]
    alt = assets.alt_text(body) or (f"a {len(panels)}-panel comic" + (f" lettered {_quote(*said)}" if said else ""))
    detail = {"layout": layout, "captions": position, "sources": sources, **setting}
    prompt = f"{len(panels)}-panel {layout} comic: {_quote(*said) or '(no words)'}"
    if hand.dry_run():
        data, ext = _encode(result, "jpg")
        return {"dry_run": True, "would_make": "comic", "prompt": prompt, "alt": alt, **detail,
                "size": list(result.size), "bytes": len(data), "format": ext}
    return _save("comic", prompt, result, "jpg", detail, alt)


def public(row) -> Json:
    out = assets._public(row)
    try:
        out["detail"] = json.loads(row["detail"]) if row["detail"] else None
    except ValueError:
        out["detail"] = None
    return out


def listing(limit: int) -> Json:
    conn = assets._db()
    try:
        rows = conn.execute(
            "SELECT * FROM media_assets WHERE kind IN ('meme', 'comic') ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        made = conn.execute(
            "SELECT COUNT(*) AS n FROM media_assets WHERE kind IN ('meme', 'comic') AND status != 'failed'"
            " AND created_at >= ?",
            (hand.day_start(),),
        ).fetchone()["n"]
        return {"assets": [public(r) for r in rows], "made_today": made, "cap": _setting_cap()}
    finally:
        conn.close()


def get(asset_id: int) -> Json:
    conn = assets._db()
    try:
        row = conn.execute("SELECT * FROM media_assets WHERE id = ? AND kind IN ('meme', 'comic')", (asset_id,)).fetchone()
        if row is None:
            raise hand.Refused(f"no meme or comic {asset_id}")
        return {"asset": public(row)}
    finally:
        conn.close()

