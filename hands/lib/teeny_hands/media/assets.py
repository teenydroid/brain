"""Images and videos Teeny makes for its posts, bought on Surplus.

  hands/image generate {prompt, alt?}           one still of Teeny, saved to state/media/
  hands/video generate {prompt, image_prompt?}  a still, then animated
  hands/image scenes [--last N]                 what the last pictures showed, and the words they repeat

Both write a `media_assets` row (what was asked, which model made it, what it
cost, the file) and return the path, which `hands/x upload --file` takes. They
publish nothing: a post attaches a file only through hands/x.

A still is kept under X's 5 MB limit for an image (media/fit.py): one over
it is re-encoded and, if need be, scaled before it is written, so a paid
picture is never one X refuses at upload. `alt`, when given, is kept on the
row (`alt`) for `hands/x upload --alt`: what the picture shows, for people
who cannot see it.

Every image is an edit of Teeny's character sheet (brain/assets/
teeny-reference.png, MEDIA_REFERENCE): the prompt says what Teeny is doing and
where, the sheet keeps Teeny looking like Teeny. The cheapest models that still
keep Teeny on model go first, the rest are fallbacks,
tried in order when a seller can't take the job (MEDIA_IMAGE_MODELS,
MEDIA_VIDEO_MODELS). A video is always image-to-video: one of those stills,
animated, so Teeny stays on model in motion too, at a fraction of what a
text-to-video costs.

The caps are code, in secrets/teeny.env (defaults in brackets):
  MEDIA_IMAGE_MAX_PER_DAY [20]  MEDIA_VIDEO_MAX_PER_DAY [2]
  MEDIA_MAX_USD_PER_DAY [1.00]  every image and video today, estimates included
  MEDIA_VIDEO_MAX_USD_PER_JOB [0.20]  a video quoted above this is cancelled
A failed generation costs nothing on Surplus and nothing here, except a
two-pass picture's paid scene pass (_make_image). DRY_RUN (on by
default) answers what would be generated and spends nothing.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from .. import hand
from . import figures, fit
from .surplus import Surplus, SurplusError

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_assets (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('image', 'video', 'reel', 'song', 'music', 'meme', 'comic', 'skit', 'shot', 'remake', 'key', 'khaby', 'film', 'audio')),
  status      TEXT NOT NULL CHECK (status IN ('pending', 'done', 'failed', 'unknown')),
  prompt      TEXT NOT NULL,
  model       TEXT,
  job_id      TEXT,
  path        TEXT,
  source_id   INTEGER REFERENCES media_assets (id),
  purpose     TEXT NOT NULL DEFAULT 'post' CHECK (purpose IN ('post', 'video_frame', 'portrait')),
  cost_micro  INTEGER NOT NULL DEFAULT 0,
  estimated   INTEGER NOT NULL DEFAULT 1,
  error       TEXT,
  created_at  REAL NOT NULL,
  done_at     REAL
);
CREATE INDEX IF NOT EXISTS media_assets_day ON media_assets (kind, created_at);
"""

#: Flare first: every model here goes through Venice's gateway, which refuses
#: a real person's photo in a mocking scene for all of them alike, uncensored
#: included. Given a stylised portrait in its place (_stylise), flare drew the
#: refused Jensen Huang scene and qwen-edit-uncensored still refused it
#: (2026-11-23), so qwen, cheaper, is the stricter one with stylised faces.
IMAGE_MODELS = "gpt-image-2-5-flare-edit,qwen-edit-uncensored,nano-banana-2-1-edit,muse-image-edit"
#: The harmless ask that turns a figure's photo into the portrait a picture
#: draws from (2026-11-23, qwen-edit-uncensored: a good caricature of Jensen).
STYLISE = ("Redraw this person as a bold stylised editorial cartoon caricature: thick ink lines, flat colours, "
           "exaggerated features, clearly still recognisable as the same person. Plain background, head and "
           "shoulders.")
VIDEO_MODELS = "longcat-distilled-image-to-video,pixverse-c1-image-to-video,seedance-1-5-pro-image-to-video"
#: Every image starts from Teeny's character sheet, so Teeny stays on model:
#: the images are edits of it, never free text-to-image.
REFERENCE = "brain/assets/teeny-reference.png"
CHARACTER = (
    "The yellow character from the reference sheet is Teeny. Keep Teeny exactly as in the reference: the same "
    "yellow rounded body, the ball on top, the small arms, black eyes and pink cheeks, soft 3D render. "
    "One Teeny, no text, no labels, no character sheet. Only Teeny is soft and cute: everyone and everything "
    "else is drawn as the scene says."
)
MAX_PROMPT = 1500
#: X's longest alt text, in characters.
MAX_ALT = 1000
IMAGE_MAX_BYTES = 20 * 1024 * 1024
VIDEO_MAX_BYTES = 200 * 1024 * 1024
VIDEO_SECONDS = 5
POLL_SECONDS = 10.0
#: Seller-side trouble: try the next model. Anything else (a bad prompt, no
#: credit) would fail the same way on every model.
FALLBACK_STATUSES = {404, 408, 409, 429, 500, 502, 503, 504}

Json = dict[str, Any]


def _db():
    conn = hand.open_db(SCHEMA)
    allow_reel_kinds(conn)
    hand.add_columns(conn, "media_assets", {"detail": "TEXT", "alt": "TEXT"})
    return conn


KINDS_CHECK = "kind IN ('image', 'video', 'reel', 'song', 'music', 'meme', 'comic', 'skit', 'shot', 'remake', 'key', 'khaby', 'film', 'audio')"
STATUS_CHECK = "status IN ('pending', 'done', 'failed', 'unknown')"
#: `portrait`: a public figure's stylised portrait, bought once (_stylise).
PURPOSE_CHECK = "purpose IN ('post', 'video_frame', 'portrait')"


def _current(sql: str) -> bool:
    return KINDS_CHECK in sql and STATUS_CHECK in sql and PURPOSE_CHECK in sql


def allow_reel_kinds(conn) -> None:
    """A ledger made before hands/reel checks `kind` against image and video
    and `status` against pending, done and failed, and SQLite cannot alter a
    check: the table is rebuilt once, rows and columns as they are, with
    `reel` (a clip with voice and captions), `song` (a song as a video),
    `music` (a bought track), `meme` and `comic` (hands/meme's pictures with
    words drawn on), `skit` (hands/video skit's cut), `shot` (one of its
    bought text-to-video clips, or a remake's image-to-video clip), `remake`
    (hands/video remake's cut), `key` (a remake's or a khaby video's bought
    keyframe), `khaby` (hands/video khaby's cut), `film` (hands/film's story
    film) and `audio` (a film's bought narration, score or effect) allowed,
    and the status `unknown` (money was
    committed and the outcome is not known: it counts as spent), and the
    purpose `portrait` (a figure's stylised portrait), as
    x/posting_rules.py does for threads.

    The rebuild is one transaction: interrupted, nothing changes and the next
    call tries again. A concurrent migrator waits on the write lock and then
    finds it done. Rows whose source_id names no row (an orphan from before)
    are copied as they are and named on stderr; they were already there and
    nothing reads that link to decide anything. If the rebuild fails for any
    other reason it is rolled back and named on stderr, and the ledger stays
    as it was, so hands/image and hands/video keep working; only a new kind
    or status is refused, by the check itself."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'media_assets'").fetchone()
    if row is None or _current(row[0]):
        return
    rebuilt = re.sub(r"CREATE TABLE\s+(IF NOT EXISTS\s+)?\"?media_assets\"?", "CREATE TABLE media_assets_new", row[0], count=1)
    rebuilt = re.sub(r"kind IN \([^)]*\)", KINDS_CHECK, rebuilt, count=1)
    rebuilt = re.sub(r"status IN \([^)]*\)", STATUS_CHECK, rebuilt, count=1)
    rebuilt = re.sub(r"purpose IN \([^)]*\)", PURPOSE_CHECK, rebuilt, count=1)
    # SQLite's own recipe for a table rebuild: foreign keys off (a video's
    # source_id names a still in the same table, and dropping the old table
    # would count every such row as a violation), checked after, then on.
    # The pragma cannot change inside a transaction.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        orphans_before = len(conn.execute("PRAGMA foreign_key_check(media_assets)").fetchall())
        with hand.transaction(conn):
            again = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'media_assets'").fetchone()
            if _current(again[0]):
                return
            conn.execute("DROP TABLE IF EXISTS media_assets_new")
            conn.execute(rebuilt)
            conn.execute("INSERT INTO media_assets_new SELECT * FROM media_assets")
            conn.execute("DROP TABLE media_assets")
            conn.execute("ALTER TABLE media_assets_new RENAME TO media_assets")
            conn.execute("CREATE INDEX IF NOT EXISTS media_assets_day ON media_assets (kind, created_at)")
            orphans = len(conn.execute("PRAGMA foreign_key_check(media_assets)").fetchall())
            if orphans > orphans_before:
                raise sqlite3.IntegrityError(f"the rebuild left {orphans - orphans_before} new orphan source_id(s)")
        if orphans_before:
            print(f"media_assets: {orphans_before} row(s) name a source_id that is gone; kept as they were", file=sys.stderr)
    except sqlite3.Error as exc:
        if "locked" in str(exc):
            raise
        print(f"media_assets: not rebuilt for hands/reel ({exc}); the ledger is unchanged", file=sys.stderr)
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _models(name: str, default: str) -> list[str]:
    return [m.strip() for m in (hand.setting(name) or default).split(",") if m.strip()]


def _usd(name: str, default: str) -> int:
    raw = hand.setting(name) or default
    try:
        value = float(raw)
    except ValueError:
        raise hand.HandError(f"{name} must be a number of dollars, got {raw!r}") from None
    if not 0 <= value <= 100:
        raise hand.HandError(f"{name} must be between 0 and 100 dollars")
    return int(round(value * 1_000_000))


def _prompt(body: Any, key: str = "prompt") -> str:
    if not isinstance(body, dict) or not isinstance(body.get(key), str) or not body[key].strip():
        raise hand.Refused(f"generate needs a {key}")
    text = body[key].strip()
    if len(text) > MAX_PROMPT:
        raise hand.Refused(f"a {key} is at most {MAX_PROMPT} characters")
    style = (hand.setting("MEDIA_STYLE") or "").strip()
    return f"{text}. {style}" if style else text


def alt_text(body: Any) -> str | None:
    """The body's `alt`: None when absent, else a non-blank string of at
    most MAX_ALT characters, whitespace folded."""
    if not isinstance(body, dict) or body.get("alt") is None:
        return None
    alt = body["alt"]
    if not isinstance(alt, str) or not alt.strip():
        raise hand.Refused("alt must be a non-empty string", rule="alt")
    alt = " ".join(alt.split())
    if len(alt) > MAX_ALT:
        raise hand.Refused(f"alt text is at most {MAX_ALT} characters (X's limit)", rule="alt")
    return alt


#: A remake's (or a khaby video's) keys are rendered within this or never
#: (media/remakes.py); a render begun and never finished (its process died)
#: is let go after REMAKE_RENDER_SECONDS more.
REMAKE_STALE_SECONDS = 2 * 3600
REMAKE_RENDER_SECONDS = 3600
#: The kinds whose keyframes are bought before their clips: a pending one is
#: released as a remake is.
KEYED_KINDS = ("remake", "khaby")


def release_remake(conn, remake_id: int, why: str) -> int:
    """A pending remake (or khaby video) failed and its shots let go, in one transaction:
    a shot that was never submitted gives its reservation back; one with a
    job (it may be billed: its process died while it ran) is `unknown` and
    keeps what it may cost; keys keep what they cost. The micro-USDC given
    back. Not inside a transaction."""
    why = why[:1000]
    with hand.transaction(conn):
        released = conn.execute(
            "SELECT COALESCE(SUM(cost_micro), 0) AS n FROM media_assets WHERE kind = 'shot' AND source_id = ?"
            " AND status = 'pending' AND (job_id IS NULL OR cost_micro = 0)", (remake_id,)).fetchone()["n"]
        conn.execute("UPDATE media_assets SET status = 'unknown', error = ?, done_at = ? WHERE kind = 'shot'"
                     " AND source_id = ? AND status = 'pending' AND job_id IS NOT NULL AND cost_micro > 0",
                     (why, hand.now(), remake_id))
        conn.execute("UPDATE media_assets SET status = 'failed', cost_micro = 0, error = ?, done_at = ? WHERE"
                     " kind = 'shot' AND source_id = ? AND status = 'pending'", (why, hand.now(), remake_id))
        conn.execute("UPDATE media_assets SET status = 'failed', cost_micro = 0, error = ?, done_at = ? WHERE id = ?"
                     f" AND kind IN {KEYED_KINDS} AND status = 'pending'", (why, hand.now(), remake_id))
    return int(released)


def release_stale(conn) -> None:
    """Every remake or khaby video whose keys were bought and never rendered within
    REMAKE_STALE_SECONDS (a render begun, REMAKE_RENDER_SECONDS more) let
    go, so its reservations stop holding today's budget. Run by every
    budget check (_check_caps, skits and remakes), under the media lock."""
    now = hand.now()
    rows = conn.execute(f"SELECT id, created_at, detail FROM media_assets WHERE kind IN {KEYED_KINDS}"
                        " AND status = 'pending' AND created_at < ?", (now - REMAKE_STALE_SECONDS,)).fetchall()
    for row in rows:
        try:
            stage = (json.loads(row["detail"] or "{}") or {}).get("stage")
        except (ValueError, AttributeError):
            stage = None
        if stage == "rendering" and row["created_at"] >= now - REMAKE_STALE_SECONDS - REMAKE_RENDER_SECONDS:
            continue
        release_remake(conn, row["id"], "expired: its keys were never rendered")


#: A story film's own rows, and every row it bought (docs/video.md §12.6):
#: a film is paid from its own weekly budget (media/films.py), never from
#: MEDIA_MAX_USD_PER_DAY, so the daily skit is never starved by one.
FILM_OWNED = "(kind = 'film' OR source_id IN (SELECT id FROM media_assets WHERE kind = 'film'))"


def _spent_today(conn) -> int:
    """Today's media spend, film rows aside. A failed row that still carries
    a cost was paid for (a two-pass picture whose likeness pass was refused
    keeps its scene pass's cost), so it counts too."""
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_micro), 0) AS spent FROM media_assets WHERE (status != 'failed' OR cost_micro > 0)"
        " AND created_at >= ?"
        f" AND NOT {FILM_OWNED}",
        (hand.day_start(),),
    ).fetchone()
    return int(row["spent"])


def _check_caps(conn, kind: str, extra_micro: int) -> None:
    # Runway (docs/roadmap.md, S2): a low wallet makes no pictures or clips.
    from .. import runway

    lim = runway.limits()
    if not lim["media"]:
        raise hand.Refused(f"no {kind}s on the {lim['tier']} runway tier ({lim['why']})", rule="runway", tier=lim["tier"])
    release_stale(conn)
    limit = hand.number(f"MEDIA_{kind.upper()}_MAX_PER_DAY", 20 if kind == "image" else 2, 0, 100)
    used = conn.execute(
        "SELECT COUNT(*) AS n FROM media_assets WHERE kind = ? AND status != 'failed' AND created_at >= ?"
        + (" AND purpose = 'post'" if kind == "image" else ""),
        (kind, hand.day_start()),
    ).fetchone()["n"]
    if used >= limit:
        raise hand.Refused(f"today's {limit} {kind}(s) are made", rule="per_day", limit=limit)
    budget = _usd("MEDIA_MAX_USD_PER_DAY", "1.00")
    spent = _spent_today(conn)
    if spent + extra_micro > budget:
        raise hand.Refused(
            f"that would take today's media spend past ${budget / 1e6:.2f}",
            rule="budget",
            spent_usd=round(spent / 1e6, 4),
            budget_usd=budget / 1e6,
        )


def _media_dir() -> Path:
    folder = hand.state_dir() / "media"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _extension(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp":
        return "mp4"
    return None


def _image_estimate(api: Surplus, model: str) -> int:
    """What the cheapest available seller asks, in USDC micros; 0 if unknown."""
    try:
        market = api.call("GET", f"/api/markets/{model}", timeout=20)
    except SurplusError:
        return 0
    offers = [o for o in market.get("offers", []) if o.get("available") and isinstance(o.get("media_unit_price"), (int, float))]
    if not offers:
        return 0
    best = min(offers, key=lambda o: o["media_unit_price"])
    price = float(best["media_unit_price"])
    if best.get("media_unit") == "megapixel":
        return int(round(price * 2))  # an edit comes back around 2 MP
    return int(round(price))


def _insert(conn, kind: str, prompt: str, source_id: int | None = None, purpose: str = "post",
            alt: str | None = None) -> int:
    with hand.transaction(conn):
        return conn.execute(
            "INSERT INTO media_assets (kind, status, prompt, source_id, purpose, alt, created_at)"
            " VALUES (?, 'pending', ?, ?, ?, ?, ?)",
            (kind, prompt, source_id, purpose, alt, hand.now()),
        ).lastrowid


def _finish(conn, asset_id: int, **fields: Any) -> None:
    fields.setdefault("done_at", hand.now())
    sets = ", ".join(f"{k} = ?" for k in fields)
    with hand.transaction(conn):
        conn.execute(f"UPDATE media_assets SET {sets} WHERE id = ?", (*fields.values(), asset_id))


def _public(row) -> Json:
    out = {k: row[k] for k in ("id", "kind", "status", "purpose", "prompt", "model", "path", "error")}
    out["alt"] = row["alt"] if "alt" in row.keys() else None
    out["cost_usd"] = round(row["cost_micro"] / 1e6, 6)
    out["cost_estimated"] = bool(row["estimated"])
    if row["source_id"]:
        out["source_id"] = row["source_id"]
    return out


def _content_refusal(exc: SurplusError) -> bool:
    return exc.status == 400 and "content policy" in str(exc).lower()


class _Failed(Exception):
    """A model's answer that fails the picture outright (not a fallback)."""

    def __init__(self, model: str, exc: SurplusError):
        super().__init__(str(exc))
        self.model, self.exc = model, exc


def _walk(api: Surplus, body_of, tried: list[str], label: str = "",
          refused: list[str] | None = None) -> tuple[str, int, bytes, str] | None:
    """Each model in turn asked for BODY_OF(model): the first picture back as
    (model, estimate, bytes, extension), or None when every model passed it
    on (each reason in TRIED, after LABEL; each model that refused it for
    content in REFUSED). A failure no other model would answer differently
    raises _Failed."""
    for model in _models("MEDIA_IMAGE_MODELS", IMAGE_MODELS):
        estimate = _image_estimate(api, model)
        try:
            answer = api.call("POST", "/v1/images/edits", body_of(model), timeout=240)
        except SurplusError as exc:
            tried.append(f"{label}{model}: {exc}")
            if refused is not None and _content_refusal(exc):
                refused.append(model)
            # One seller's content filter turning a scene down is that
            # seller's answer, not the scene's: the next model reads it afresh.
            if exc.status in FALLBACK_STATUSES or exc.status is None or _content_refusal(exc):
                continue
            raise _Failed(model, exc) from None
        item = (answer.get("data") or [{}])[0]
        if item.get("b64_json"):
            data = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            data = api.download(item["url"], IMAGE_MAX_BYTES)
        else:
            tried.append(f"{label}{model}: no image in the answer")
            continue
        ext = _extension(data)
        if ext not in ("png", "jpg", "webp"):
            tried.append(f"{label}{model}: the answer is not an image")
            continue
        return model, estimate, data, ext
    return None


def _likeness_body(model: str, scene: bytes, portraits: list[Json]) -> Json:
    """The second pass: the drawn scene and each figure's photo, under a
    fixed, harmless prompt that never carries the scene's words."""
    who = "; ".join(f"photo {i + 2} is {p['name']}" for i, p in enumerate(portraits))
    mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[_extension(scene) or "png"]
    return {"model": model,
            "prompt": f"Picture 1 is the scene; {who}. Redraw each of those people in picture 1 so they are "
                      "unmistakably that person from their photo: their face, hair and glasses. Keep everything "
                      "else in picture 1 exactly as it is.",
            "images": [f"data:{mime};base64," + base64.b64encode(scene).decode(),
                       *[_data_uri(p["path"]) for p in portraits]],
            "response_format": "b64_json"}


def _stylise(conn, api: Surplus, portraits: list[Json]) -> list[Json]:
    """Each figure's stylised portrait in place of the photo (figures.py):
    kept from before, else bought now, once, on its own `image` row (purpose
    `portrait`) whose cost counts today. A figure no model would stylise is
    drawn from the photo, as before. Never raises for a model's answer."""
    out = []
    for found in portraits:
        kept = figures.stylised(found["name"])
        if kept is None:
            row = _insert(conn, "image", f"{STYLISE} ({found['name']})", None, "portrait")
            tried: list[str] = []
            try:
                got = _walk(api, lambda model: {"model": model, "prompt": STYLISE, "image": _data_uri(found["path"]),
                                                "response_format": "b64_json"}, tried)
            except _Failed as failed:
                got, tried = None, [*tried, f"{failed.model}: {failed.exc}"]
            if got is None:
                _finish(conn, row, status="failed", error=("; ".join(tried) or "no image model configured")[:1000])
                print(f"hands/image: no stylised portrait of {found['name']}; drawing from the photo", file=sys.stderr)
                out.append(found)
                continue
            model, estimate, data, _ = got
            kept = figures.keep_stylised(found["name"], data)
            _finish(conn, row, status="done", model=model, path=str(kept.relative_to(hand.root())),
                    cost_micro=estimate, estimated=1)
        out.append({**found, "photo": found["path"], "path": kept})
    return out


def _make_image(conn, api: Surplus, prompt: str, source_of: str | None, alt: str | None = None,
                portraits: list[Json] | None = None) -> Json:
    """One still: a pending row, each model in turn, the file, under X's
    5 MB limit. Caps checked before the first call; for a video's start frame
    the video's caps hold.

    A figure is drawn from its stylised portrait (_stylise), which Venice's
    gateway passes where it refuses the photo. Two-pass likeness, the last
    resort: when every model still turns the portraits down together with
    the scene for content, the scene is drawn with Teeny's sheet alone, then
    one more edit gives its people their likeness from the portraits under a
    harmless prompt. Both passes are paid and counted on the one row; a refusal of the
    second fails the picture."""
    asset_id = _insert(conn, "image", prompt, None, "video_frame" if source_of else "post", alt)
    reference_path = hand.root() / (hand.setting("MEDIA_REFERENCE") or REFERENCE)
    if not reference_path.is_file():
        _finish(conn, asset_id, status="failed", error=f"no reference image at {reference_path.name}")
        raise hand.HandError(f"Teeny's reference image is missing ({REFERENCE})")
    reference = _data_uri(reference_path)
    portraits = _stylise(conn, api, portraits or [])
    tried: list[str] = []
    two_pass = None
    paid = 0
    chain = _models("MEDIA_IMAGE_MODELS", IMAGE_MODELS)
    refused: list[str] = []
    try:
        got = _walk(api, lambda model: _edit_body(model, prompt, reference, portraits), tried, refused=refused)
        if got is None and portraits and chain and refused == chain:
            tried = [f"the portraits with the scene: every model refused them for content ({', '.join(refused)})"]
            got = _walk(api, lambda model: _edit_body(model, prompt, reference, []), tried, "scene pass, ")
            if got is not None:
                scene_model, paid, scene, _ = got
                # The scene is paid for: its cost holds today's budget while
                # the likeness pass runs, and stays if that pass fails.
                _finish(conn, asset_id, status="pending", model=scene_model, cost_micro=paid, done_at=None)
                budget = _usd("MEDIA_MAX_USD_PER_DAY", "1.00")
                spent = _spent_today(conn)
                if spent + _image_estimate(api, chain[0]) > budget:
                    why = f"the likeness pass would take today's media spend past ${budget / 1e6:.2f}"
                    _finish(conn, asset_id, status="failed", error="; ".join([*tried, why])[:1000])
                    raise hand.Refused(why, rule="budget", spent_usd=round(spent / 1e6, 4), budget_usd=budget / 1e6)
                likeness: list[str] = []
                got = _walk(api, lambda model: _likeness_body(model, scene, portraits), likeness, "likeness pass, ")
                tried += likeness
                if got is None:
                    _finish(conn, asset_id, status="failed", error="; ".join(tried)[:1000])
                    raise hand.HandError("no image model could take the likeness pass: " + "; ".join(likeness)[:500])
                two_pass = {"scene": scene_model, "likeness": got[0]}
    except _Failed as failed:
        _finish(conn, asset_id, status="failed", model=failed.model, error=str(failed.exc))
        raise hand.HandError(f"image generation failed: {failed.exc}") from None
    if got is None:
        _finish(conn, asset_id, status="failed", error="; ".join(tried)[:1000] or "no image model configured")
        raise hand.HandError("no image model could take it: " + ("; ".join(tried)[:500] or "none configured"))
    model, estimate, data, ext = got
    # The picture is paid for: a failure to shrink it is recorded on the
    # row with what it cost, never dropped.
    detail = None
    try:
        data, ext, shrunk = fit.under_limit(data, ext)
    except (hand.HandError, hand.Refused) as exc:
        shrunk = None
        detail = f"kept over X's image limit: {getattr(exc, 'reason', None) or exc}"
        print(f"hands/image: picture {asset_id} is {len(data):,} bytes, over X's limit ({detail})", file=sys.stderr)
    if shrunk or two_pass:
        detail = json.dumps({**({"shrunk": shrunk} if shrunk else {}), **({"kept": detail} if detail else {}),
                             **({"two_pass": two_pass} if two_pass else {})})
    path = _media_dir() / f"image-{asset_id}.{ext}"
    path.write_bytes(data)
    _finish(
        conn,
        asset_id,
        status="done",
        model=model,
        path=str(path.relative_to(hand.root())),
        cost_micro=paid + estimate,
        estimated=1,
        detail=detail,
    )
    return _public(conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone())


MAX_FIGURES = 2


def _figure_names(body: Any) -> list[str]:
    """The famous people a picture draws (`figures`), at most MAX_FIGURES."""
    names = body.get("figures") if isinstance(body, dict) else None
    if names in (None, []):
        return []
    if not (isinstance(names, list) and len(names) <= MAX_FIGURES
            and all(isinstance(n, str) and n.strip() and len(n) <= figures.MAX_NAME for n in names)):
        raise hand.Refused(f"figures lists at most {MAX_FIGURES} names", rule="body")
    return list(dict.fromkeys(" ".join(n.split()) for n in names))


def _portraits(names: list[str]) -> list[Json]:
    """Each name's photo. A name that is not a public figure with a photo
    refuses the picture: a private person is never drawn."""
    found = [figures.portrait(n) for n in names]
    missing = [f"{f['name']} {f['why']}" for f in found if f.get("why")]
    if missing:
        raise hand.Refused("a picture draws only public figures: " + "; ".join(missing), rule="figure")
    return found


def _edit_body(model: str, prompt: str, reference: str, portraits: list[Json]) -> Json:
    """The edit a model is asked for: Teeny's sheet alone (`image`), or the
    sheet and one photo per public figure (`images`), named in order."""
    if not portraits:
        return {"model": model, "prompt": f"{CHARACTER} {prompt}", "image": reference, "response_format": "b64_json"}
    who = "; ".join(f"photo {i + 2} is {p['name']}" for i, p in enumerate(portraits))
    return {"model": model,
            "prompt": f"{CHARACTER} Reference 1 is Teeny's sheet; {who}. Draw each of them from their photo as an "
                      f"exaggerated editorial caricature, unmistakably that person. {prompt}",
            "images": [reference, *[_data_uri(p["path"]) for p in portraits]], "response_format": "b64_json"}


def generate_image(body: Any, api: Surplus | None = None) -> Json:
    prompt = _prompt(body)
    alt = alt_text(body)
    names = _figure_names(body)
    if hand.dry_run():
        return {"dry_run": True, "would_generate": "image", "prompt": prompt, "alt": alt, "figures": names,
                "models": _models("MEDIA_IMAGE_MODELS", IMAGE_MODELS)}
    portraits = _portraits(names)
    api = api or Surplus()
    with hand.lock("media"):
        conn = _db()
        try:
            _check_caps(conn, "image", 0)
            return {"asset": _make_image(conn, api, prompt, None, alt, portraits)}
        finally:
            conn.close()


def _data_uri(path: Path) -> str:
    data = path.read_bytes()
    mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[_extension(data) or "png"]
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def generate_video(body: Any, api: Surplus | None = None, sleep=time.sleep) -> Json:
    """A still from `image_prompt` (else `prompt`), then animated as `prompt`
    describes. Waits for the job (MEDIA_VIDEO_WAIT_SECONDS, 900)."""
    prompt = _prompt(body)
    still_prompt = _prompt(body, "image_prompt") if isinstance(body, dict) and body.get("image_prompt") else prompt
    if hand.dry_run():
        return {
            "dry_run": True,
            "would_generate": "video",
            "prompt": prompt,
            "image_prompt": still_prompt,
            "models": _models("MEDIA_VIDEO_MODELS", VIDEO_MODELS),
        }
    api = api or Surplus()
    per_job = _usd("MEDIA_VIDEO_MAX_USD_PER_JOB", "0.20")
    wait = hand.number("MEDIA_VIDEO_WAIT_SECONDS", 900, 60, 1700)
    with hand.lock("media"):
        conn = _db()
        try:
            _check_caps(conn, "video", per_job)
            still = _make_image(conn, api, still_prompt, "video")
            video_id = _insert(conn, "video", prompt, still["id"])
            frame = _data_uri(hand.root() / still["path"])
            tried: list[str] = []
            for model in _models("MEDIA_VIDEO_MODELS", VIDEO_MODELS):
                try:
                    job = api.call(
                        "POST",
                        "/v1/video/generations",
                        {"model": model, "prompt": prompt, "image_url": frame, "duration_seconds": VIDEO_SECONDS},
                        timeout=60,
                        headers={"idempotency-key": f"teeny-video-{video_id}-{model}"},
                    )
                except SurplusError as exc:
                    tried.append(f"{model}: {exc}")
                    if exc.status in FALLBACK_STATUSES or exc.status is None:
                        continue
                    _finish(conn, video_id, status="failed", model=model, error=str(exc))
                    raise hand.HandError(f"video generation failed: {exc}") from None
                job_id = str(job.get("id") or "")
                estimate = int(job.get("estimated_cost_usdc") or 0)
                if estimate > per_job:
                    try:
                        api.call("DELETE", f"/v1/video/generations/{job_id}", timeout=30)
                    except SurplusError:
                        pass
                    tried.append(f"{model}: quoted ${estimate / 1e6:.3f}, over the ${per_job / 1e6:.2f} cap")
                    continue
                with hand.transaction(conn):
                    conn.execute(
                        "UPDATE media_assets SET model = ?, job_id = ?, cost_micro = ? WHERE id = ?",
                        (model, job_id, estimate, video_id),
                    )
                result = _await(api, job_id, wait, sleep)
                if result.get("status") != "succeeded":
                    error = result.get("error") or {}
                    tried.append(f"{model}: {result.get('status')} ({error.get('type', 'unknown')})")
                    with hand.transaction(conn):
                        conn.execute("UPDATE media_assets SET cost_micro = 0 WHERE id = ?", (video_id,))
                    if error.get("retryable", error.get("type") != "invalid_request"):
                        continue
                    break
                url = ((result.get("results") or [{}])[0]).get("url")
                if not url:
                    tried.append(f"{model}: no artifact")
                    continue
                data = api.download(url, VIDEO_MAX_BYTES)
                if _extension(data) != "mp4":
                    tried.append(f"{model}: the artifact is not an mp4")
                    continue
                path = _media_dir() / f"video-{video_id}.mp4"
                path.write_bytes(data)
                actual = (result.get("usage") or {}).get("buyer_cost_usdc")
                _finish(
                    conn,
                    video_id,
                    status="done",
                    path=str(path.relative_to(hand.root())),
                    cost_micro=int(actual) if actual is not None else estimate,
                    estimated=0 if actual is not None else 1,
                )
                return {"asset": _public(conn.execute("SELECT * FROM media_assets WHERE id = ?", (video_id,)).fetchone()), "still": still}
            _finish(conn, video_id, status="failed", cost_micro=0, error="; ".join(tried)[:1000] or "no video model configured")
            raise hand.HandError("no video model could take it: " + ("; ".join(tried)[:500] or "none configured"))
        finally:
            conn.close()


def _await(api: Surplus, job_id: str, wait: int, sleep) -> Json:
    deadline = time.monotonic() + wait
    while True:
        status = api.call("GET", f"/v1/video/generations/{job_id}", timeout=30)
        if status.get("status") in ("succeeded", "failed", "canceled", "expired"):
            return status
        if time.monotonic() >= deadline:
            try:
                api.call("DELETE", f"/v1/video/generations/{job_id}", timeout=30)
            except SurplusError:
                pass
            return {"status": "expired", "error": {"type": "expired", "retryable": True}}
        sleep(POLL_SECONDS)


def listing(kind: str | None, limit: int) -> Json:
    conn = _db()
    try:
        # hands/video lists its skits, remakes, khaby videos, their shots and keys with its clips.
        kinds = ("video", "skit", "shot", "remake", "key", "khaby") if kind == "video" else (kind,) if kind else ()
        marks = ", ".join("?" for _ in kinds)
        query = "SELECT * FROM media_assets" + (f" WHERE kind IN ({marks})" if kinds else "") + " ORDER BY id DESC LIMIT ?"
        rows = conn.execute(query, (*kinds, limit)).fetchall()
        return {"assets": [_public(r) for r in rows], "spent_today_usd": round(_spent_today(conn) / 1e6, 4)}
    finally:
        conn.close()


def get(asset_id: int) -> Json:
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise hand.Refused(f"no media asset {asset_id}")
        return {"asset": _public(row)}
    finally:
        conn.close()




#: Words too common to say anything about a scene.
_STOP = frozenset("""
a an the and or but of in on at to for with from by into onto over under up down out off as is are was were be
its it this that these those his her their our your my me i we you they he she them us one two some any all every
each very just still while then than so not no yes only same small big little smol tiny teeny robot's
""".split())
_WORD = re.compile(r"[a-z][a-z'-]{2,}")


def _scene(prompt: str, style: str) -> str:
    """The scene as the writer asked for it: the house style appended by
    `_prompt` taken off again."""
    if style and prompt.endswith(f". {style}"):
        return prompt[: -len(style) - 2]
    return prompt


def scenes(last: int) -> Json:
    """`scenes --last N`: the scenes of the last N pictures made for posts,
    newest first, and every word (past the common ones) that two or more of
    them share, with how many pictures it is in. A report for the writer,
    who decides what to draw next; this judges nothing."""
    style = (hand.setting("MEDIA_STYLE") or "").strip()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, prompt, created_at FROM media_assets WHERE kind = 'image' AND purpose = 'post'"
            " AND status = 'done' ORDER BY id DESC LIMIT ?",
            (last,),
        ).fetchall()
    finally:
        conn.close()
    shown = [{"id": r["id"], "at": r["created_at"], "scene": _scene(r["prompt"], style)} for r in rows]
    counts: dict[str, int] = {}
    for item in shown:
        words = {w.strip("'-") for w in _WORD.findall(item["scene"].lower())}
        for word in words:
            if len(word) > 2 and word not in _STOP:
                counts[word] = counts.get(word, 0) + 1
    repeated = sorted(((w, n) for w, n in counts.items() if n >= 2), key=lambda wn: (-wn[1], wn[0]))
    return {"pictures": len(shown), "scenes": shown,
            "repeated": [{"word": w, "pictures": n} for w, n in repeated[:20]]}
