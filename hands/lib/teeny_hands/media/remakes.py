"""Remakes: a popular short re-made shot by shot with public figures and
Teeny as a bystander, by keyframe swap, bought on Surplus and cut with the
source's own audio and a burned-in label (docs/hands.md, "video").

  hands/video remake-keys {study, source, story, figures?, logline, aspect?, shots, wait_seconds?}
  hands/video remake      {remake_id, sound, wait_seconds?}
  hands/video remake-drop {remake_id}                  -> {remake_id, released_usd}

A sibling of the skit (media/skits.py). hands/remake study has already cut
the source into shots and written each shot's first frame and the source's
audio under state/media/remake/<study>/; bin/studio's `remake` format calls
these verbs only after the remake passed its hard lines, and between them
looks at the keyframes (the cheap money) before the clips (the expensive
money) are bought. Like the skit, this hand makes no judgment of its own
about the words; what it holds, in code:

- **Off by default.** `MEDIA_REMAKE_MAX_PER_DAY` is 0 unless the operator
  sets it; and a remake is one of the `MEDIA_SKIT_MAX_PER_DAY` videos a day
  that skits and remakes share (a remake or a skit that bought anything
  counts, even one that failed after). Both must allow it, or nothing is
  asked of anyone (`off`, `per_day`).
- **The money fences.** The runway tier; every shot reserves
  `MEDIA_REMAKE_MAX_USD_PER_SHOT` ($1.40: after a $0.10 key, Wan 3's 5 s
  ceiling of $1.17 fits) on its row under the media lock
  before anything is bought, and all of them must fit under the shared
  `MEDIA_MAX_USD_PER_DAY` with everything else bought today (`budget`). A
  keyframe books its catalog estimate on a `key` row (an edit carries no
  quote and no bill; an unknown price books KEY_UNKNOWN_MICRO, the dearest
  edit measured) and lowers its shot's reservation by the same amount, so
  the clip may cost only what is left: an image-to-video job whose
  `max_cost_usdc` is over that is cancelled and the next model asked.
- **Two verbs, one set of words.** remake-keys stores everything the render
  needs (the motion prompts, the source shots' lengths, the aspect, the
  keyframes, the study) on its rows; remake takes only the remake's id, its
  sound and a wait, so it can never be handed words nobody screened. It
  refuses an id that is unknown, already rendered or being rendered,
  failed, or older than STALE_SECONDS (2 hours), and a sound that is not
  state/media/remake/<that remake's study>/audio.m4a.
- **Keys bought, never rendered.** remake-drop lets a keyed remake go at
  once (the studio turned its keyframes down); one left pending past
  STALE_SECONDS is let go by the next media budget check
  (assets.release_stale: images, videos, skits, remakes). Either way its
  keys keep what they cost on the ledger, a shot never submitted gives its
  reservation back, and one that was submitted (a render whose process
  died) is `unknown` and keeps what it may cost.
- **Midnight.** The rows are dated when the keys are bought: a remake
  keyed before 00:00 UTC and rendered after counts, money and day cap, on
  the day it was keyed.
- **The cutter first.** The reel service must answer before either verb
  buys anything.
- **The label.** skits.label(), "AI parody · @<X_BOT_USERNAME>", set here,
  never by the caller, and checked on the cut as for a skit.

The keys: each source frame and Teeny's character sheet go to
`POST /v1/images/edits` as `images: [frame, sheet]` (two data URIs), with a
prompt this hand frames around the writer's `edit`: what the two images
are, the edit, Teeny's clause (assets.CHARACTER, then "Exactly one Teeny,"
and the shot's `teeny`), and `MEDIA_REMAKE_STYLE` (STYLE by default). The
models are `MEDIA_REMAKE_EDIT_MODELS` in order; a content refusal (Surplus
answers 400 `request_rejected`, "violates the content policy") or a seller's
trouble moves the shot to the next, at most MAX_EDIT_ATTEMPTS edits a shot;
any other refusal (no credit) stops the remake. An edit is held to what is
left of its shot's cap, and starts only while EDIT_TIMEOUT is left of
`MEDIA_REMAKE_KEYS_WAIT_SECONDS` (300); one that gets no answer may still
be made and billed, so it is booked as bought (`unknown`). The shots' edits
run in parallel; one shot that cannot be keyed stops the others from
trying again, and the remake fails.

The render: one `POST /v1/video/generations` a shot with {model, prompt:
the shot's `motion` and MOTION_TAIL, image_url: the keyframe,
duration_seconds}, plus REMAKE_PARAMS (venice-wan-3-0: resolution
`MEDIA_REMAKE_RESOLUTION` 720p, the remake's aspect ratio, and audio only
when the cut keeps the clips' sound) under `MEDIA_REMAKE_PARAMS`, on the
first of `MEDIA_REMAKE_MODELS` that takes it (venice-wan-3-0 first: it sells
2, 5, 10, 15 to 30 s), asked for the shortest length the model sells that
covers the source shot (a model that sells none, or whose verified ceiling
for it, REMAKE_MAX_PER_SECOND, cannot fit what is left of the shot's cap,
moves the shot on unasked), and polled, landed and cancelled by
media/shots.py as a skit's shots are (`MEDIA_SKIT_WAIT_SECONDS`). Verified on Surplus
(2026-11-20): kling-v3-standard-image-to-video takes a keyframe with a real
face, 5 s quoted $0.49 (ceiling $0.98) and billed $0.49. The cut: each clip
trimmed to its source shot (`trims`), the source's audio as the only sound
(`sound`; with none, or MEDIA_REMAKE_SOUND=0, each clip keeps its own,
trimmed), and the label (deploy/reel/skitcut.py).

The ledger: a `remake` row (cost 0; its `detail` holds the study, source,
story, figures, logline, aspect, label, the shots' words and lengths, the
keys, and once cut the frames and what it all cost), a `key` row a bought
keyframe and a `shot` row a clip, each `source_id` the remake's row.

DRY_RUN (on by default) answers what would be made and calls nothing.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import math
import re
import threading
import time
from typing import Any

from .. import hand
from . import assets, shots, skits
from .shots import _Shot, _Stop
from .surplus import Surplus, SurplusError

Json = dict[str, Any]

EDIT_MODELS = "nano-banana-2-edit,seedream-v5-pro-edit,muse-image-edit"
REMAKE_MODELS = ("venice-wan-3-0-image-to-video,wan-2-7-image-to-video,kling-v3-standard-image-to-video,"
                 "kling-v3-pro-image-to-video,veo3-1-fast-image-to-video")
MAX_SHOTS = 6
#: What a shot reserves by default (MEDIA_REMAKE_MAX_USD_PER_SHOT): a $0.10
#: key and Wan 3's 5 s ceiling ($1.17 at 720p) fit; six shots hold $8.40.
PER_SHOT = "1.40"
MAX_EDIT_ATTEMPTS = 2
#: What a keyframe of unknown price books: nano-banana-2-edit's $0.10, the
#: dearest edit measured (2026-11-20).
KEY_UNKNOWN_MICRO = 100_000
#: Keys older than this are never rendered: their remake is failed (the
#: sweep is assets.release_stale, run by every media budget check).
STALE_SECONDS = assets.REMAKE_STALE_SECONDS
#: A render that started and whose process died is let go after this too.
RENDER_SECONDS = assets.REMAKE_RENDER_SECONDS
#: How long one edit may take. No edit starts unless this much of the keys'
#: wait is left: one cut short by the client may still be made and billed.
EDIT_TIMEOUT = 240.0
#: max_cost_usdc a second asked, in micro-USDC, where it is known (as
#: skits.MAX_PER_SECOND): a clip whose length cannot fit what is left of its
#: shot's cap is never submitted. wan-3-0 image-to-video is taken to be its
#: text-to-video sibling's 234000 at 720p until a quote says otherwise;
#: wan-2-7 image-to-video 5 s was quoted max 1089000 on 2026-11-20 (billed
#: 467500). Other models are held to their own answer's ceiling.
REMAKE_MAX_PER_SECOND = {"venice-wan-3-0-image-to-video": 234000, "wan-2-7-image-to-video": 217800}
#: The fields a model is sent beyond {model, prompt, image_url,
#: duration_seconds}, as skits.ONE_TAKE_PARAMS: wan-3-0's resolution
#: (MEDIA_REMAKE_RESOLUTION, 720p), the remake's aspect ratio, and `audio`,
#: which the hand sets: off when the cut lays the source's sound over the
#: clips, on when it keeps theirs. MEDIA_REMAKE_PARAMS is laid over these.
REMAKE_PARAMS: dict[str, Json] = {
    "venice-wan-3-0-image-to-video": {"resolution": "{resolution}", "aspect_ratio": "{aspect}", "audio": False},
}
STUDY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
SHOT_SECONDS = (1.0, 10.0)
EDIT_CHARS = (40, 900)
MOTION_CHARS = (40, 600)
TEENY_CHARS = 160
STORY_CHARS = 600
SOURCE_FIELDS = ("video_id", "url", "title", "source")
SCENE = "The first image is the scene; the second image is Teeny's character sheet."
TEENY_DEFAULT = "a small bystander somewhere in the scene"
STYLE = ("Keep the framing, the set and the lighting of the first image. Photorealistic. No text, no captions, "
         "no logos or brand names, no character sheet.")
MOTION_TAIL = "The small yellow character stays where it is. One continuous shot, no cuts. No text."


# --- what the verbs take ------------------------------------------------------------------


def _words(value: Any, what: str, low: int, high: int) -> str:
    if not isinstance(value, str):
        raise hand.Refused(f"{what} is text", rule="body")
    value = " ".join(value.split())
    if not low <= len(value) <= high:
        raise hand.Refused(f"{what} is {low} to {high} characters", rule="body")
    return value


def _wait(body: Json) -> int | None:
    wait = body.get("wait_seconds")
    if wait is not None and (isinstance(wait, bool) or not isinstance(wait, int) or wait < 60):
        raise hand.Refused("wait_seconds is a whole number of at least 60", rule="body")
    return wait


def frame_path(study: str, n: int) -> str:
    return f"state/media/remake/{study}/shot-{n}.jpg"


def sound_path(study: str) -> str:
    return f"state/media/remake/{study}/audio.m4a"


def keys_of(body: Any) -> Json:
    """remake-keys' job as this hand takes it, every field checked."""
    if not isinstance(body, dict):
        raise hand.Refused("remake-keys takes a JSON object", rule="body")
    unknown = sorted(set(body) - {"study", "source", "story", "figures", "logline", "aspect", "shots", "wait_seconds"})
    if unknown:
        raise hand.Refused(f"not taken here: {', '.join(unknown)}", rule="body")
    study = body.get("study")
    if not isinstance(study, str) or not STUDY.fullmatch(study):
        raise hand.Refused("study is the id hands/remake study gave", rule="body")
    source = body.get("source")
    if not isinstance(source, dict) or set(source) - set(SOURCE_FIELDS) or not all(
            isinstance(source.get(k), str) and len(source[k]) <= 500 for k in SOURCE_FIELDS) or not all(
            source[k].strip() for k in ("video_id", "url", "source")):
        raise hand.Refused(f"source is {{{', '.join(SOURCE_FIELDS)}}}", rule="body")
    figures = body.get("figures") or []
    if not isinstance(figures, list) or len(figures) > skits.MAX_FIGURES or not all(
            isinstance(f, str) and f.strip() and len(f) <= 60 for f in figures):
        raise hand.Refused(f"figures lists at most {skits.MAX_FIGURES} names", rule="body")
    aspect = body.get("aspect") or "16:9"
    if aspect not in skits.ASPECTS:
        raise hand.Refused(f"aspect is one of {', '.join(skits.ASPECTS)}", rule="body")
    given = body.get("shots")
    if not isinstance(given, list) or not 1 <= len(given) <= MAX_SHOTS:
        raise hand.Refused(f"a remake is 1 to {MAX_SHOTS} shots", rule="body")
    kept = []
    for n, item in enumerate(given, 1):
        if not isinstance(item, dict):
            raise hand.Refused(f"shot {n} is an object", rule="body")
        extra = sorted(set(item) - {"n", "frame", "seconds", "edit", "motion", "teeny"})
        if extra:
            raise hand.Refused(f"shot {n}: not taken here: {', '.join(extra)}", rule="body")
        if item.get("n") != n or isinstance(item.get("n"), bool):
            raise hand.Refused(f"shot {n} is numbered {n}: the shots go 1 to N in order", rule="body")
        if item.get("frame") != frame_path(study, n):
            raise hand.Refused(f"shot {n}'s frame is {frame_path(study, n)}", rule="body")
        seconds = item.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not (
                SHOT_SECONDS[0] <= seconds <= SHOT_SECONDS[1]):
            raise hand.Refused(f"shot {n}'s seconds is {SHOT_SECONDS[0]} to {SHOT_SECONDS[1]}", rule="body")
        teeny = item.get("teeny", "")
        kept.append({"n": n, "seconds": round(float(seconds), 3),
                     "edit": _words(item.get("edit"), f"shot {n}'s edit", *EDIT_CHARS),
                     "motion": _words(item.get("motion"), f"shot {n}'s motion", *MOTION_CHARS),
                     "teeny": _words(teeny, f"shot {n}'s teeny", 0, TEENY_CHARS)})
    return {"study": study, "source": {k: source[k].strip() for k in SOURCE_FIELDS},
            "story": _words(body.get("story"), "story", 1, STORY_CHARS), "figures": [f.strip() for f in figures],
            "logline": _words(body.get("logline"), "logline", 1, skits.MAX_LOGLINE), "aspect": aspect,
            "shots": kept, "wait": _wait(body)}


def render_of(body: Any) -> Json:
    """remake's job: the remake's id, its sound and a wait; nothing else."""
    if not isinstance(body, dict):
        raise hand.Refused("remake takes a JSON object", rule="body")
    unknown = sorted(set(body) - {"remake_id", "sound", "wait_seconds"})
    if unknown:
        raise hand.Refused(f"not taken here: {', '.join(unknown)} (the render replays what remake-keys stored)",
                           rule="body")
    remake_id = body.get("remake_id")
    if isinstance(remake_id, bool) or not isinstance(remake_id, int) or remake_id < 1:
        raise hand.Refused("remake_id is the id remake-keys answered", rule="remake_id")
    sound = body.get("sound")
    if sound is not None and not isinstance(sound, str):
        raise hand.Refused("sound is the source's audio, state/media/remake/<study>/audio.m4a, or null", rule="sound")
    return {"remake_id": remake_id, "sound": sound, "wait": _wait(body)}


# --- the words sent ---------------------------------------------------------------------------


def edit_prompt(shot: Json) -> str:
    """The keyframe's prompt: the hand's frame around the writer's edit."""
    style = (hand.setting("MEDIA_REMAKE_STYLE") or STYLE).strip()
    teeny = (shot.get("teeny") or TEENY_DEFAULT).rstrip(" .")
    return f"{SCENE} {shot['edit']} {assets.CHARACTER} Exactly one Teeny, {teeny}. {style}".strip()


def motion_prompt(motion: str) -> str:
    return f"{motion} {MOTION_TAIL}"


def model_params(model: str, params: dict[str, Json], clip_sound: bool) -> dict[str, Json]:
    """A clip's fields for `model`: its built-in ones (REMAKE_PARAMS, with
    `audio` on only when the cut keeps the clips' sound), then the
    operator's MEDIA_REMAKE_PARAMS over them."""
    built = dict(REMAKE_PARAMS.get(model) or {})
    if "audio" in built:
        built["audio"] = clip_sound
    return {"*": dict(params.get("*") or {}), model: {**built, **(params.get(model) or {})}}


def plan(per_shot: int, clip_sound: bool = False) -> shots.Plan:
    """A remake's clips: image-to-video from each keyframe, each asked for
    the shortest length on sale that covers its source shot, never one
    whose verified ceiling cannot fit what is left of the shot's cap."""
    return shots.Plan(prefix="remake", key="teeny-remake", style=None, params="MEDIA_REMAKE_PARAMS",
                      body=lambda model, prompt, seconds, aspect, params, shot: shots.request_body(
                          model, prompt, seconds, aspect, model_params(model, params, clip_sound), image=shot.image,
                          resolution_setting="MEDIA_REMAKE_RESOLUTION"),
                      length=lambda shot, sold: shots.at_least(shot.floor, sold), per_shot=per_shot,
                      max_per_second=REMAKE_MAX_PER_SECOND)


# --- caps and the ledger -----------------------------------------------------------------------


def _limits() -> tuple[int, int]:
    return hand.number("MEDIA_REMAKE_MAX_PER_DAY", 0, 0, 10), hand.number("MEDIA_SKIT_MAX_PER_DAY", 0, 0, 10)


def _check_on(limit: int, shared: int) -> None:
    if limit == 0:
        raise hand.Refused("remakes are off: MEDIA_REMAKE_MAX_PER_DAY is 0 (the operator turns them on and sets the "
                           "budget in secrets/teeny.env)", rule="off")
    if shared == 0:
        raise hand.Refused("remakes are off: MEDIA_SKIT_MAX_PER_DAY, the videos a day skits and remakes share, is 0",
                           rule="off")


def _runway() -> None:
    from .. import runway

    lim = runway.limits()
    if not lim["media"]:
        raise hand.Refused(f"no remakes on the {lim['tier']} runway tier ({lim['why']})", rule="runway",
                           tier=lim["tier"])


def _detail(row) -> Json:
    try:
        parsed = json.loads(row["detail"] or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _set_detail(conn, remake_id: int, detail: Json) -> None:
    shots._set(conn, remake_id, detail=json.dumps(detail, ensure_ascii=False, sort_keys=True))


def _release(conn, remake_id: int, why: str) -> int:
    """assets.release_remake: the remake failed, never-submitted shots'
    reservations given back, submitted ones kept as `unknown`, keys kept."""
    return assets.release_remake(conn, remake_id, why)


def _spent_on(conn, remake_id: int) -> int:
    """What the remake's keys and clips cost or may cost, from the ledger."""
    return conn.execute("SELECT COALESCE(SUM(cost_micro), 0) AS n FROM media_assets WHERE kind IN ('key', 'shot')"
                        " AND source_id = ? AND status IN ('done', 'unknown')", (remake_id,)).fetchone()["n"]


def _claim(job: Json, per_shot: int, limit: int, shared: int, the_label: str) -> tuple[int, list[int]]:
    """The caps, then the remake's row and a row a shot carrying its
    reservation, under the media lock."""
    with hand.lock("media"):
        conn = assets._db()
        try:
            assets.release_stale(conn)
            _runway()
            if skits._count_today(conn) >= shared:
                raise hand.Refused(f"today's {shared} skit(s) and remake(s) are made (MEDIA_SKIT_MAX_PER_DAY)",
                                   rule="per_day", limit=shared)
            if skits._count_today(conn, ("remake",)) >= limit:
                raise hand.Refused(f"today's {limit} remake(s) are made (MEDIA_REMAKE_MAX_PER_DAY)", rule="per_day",
                                   limit=limit)
            budget = assets._usd("MEDIA_MAX_USD_PER_DAY", "1.00")
            spent = assets._spent_today(conn)
            need = per_shot * len(job["shots"])
            if spent + need > budget:
                raise hand.Refused(
                    f"{len(job['shots'])} shot(s) at up to ${per_shot / 1e6:.2f} each would take today's media spend "
                    f"past ${budget / 1e6:.2f} (MEDIA_MAX_USD_PER_DAY)",
                    rule="budget", spent_usd=round(spent / 1e6, 4), budget_usd=budget / 1e6,
                    need_usd=round(need / 1e6, 4))
            with hand.transaction(conn):
                remake_id = conn.execute(
                    "INSERT INTO media_assets (kind, status, prompt, purpose, cost_micro, estimated, created_at)"
                    " VALUES ('remake', 'pending', ?, 'post', 0, 1, ?)", (job["logline"], hand.now()),
                ).lastrowid
                rows = [conn.execute(
                    "INSERT INTO media_assets (kind, status, prompt, source_id, purpose, cost_micro, estimated,"
                    " created_at) VALUES ('shot', 'pending', ?, ?, 'post', ?, 1, ?)",
                    (shot["motion"], remake_id, per_shot, hand.now()),
                ).lastrowid for shot in job["shots"]]
                detail = {"stage": "keying", "study": job["study"], "source": job["source"], "story": job["story"],
                          "figures": job["figures"], "logline": job["logline"], "aspect": job["aspect"],
                          "label": the_label, "shots": [{**shot, "row": row} for shot, row in zip(job["shots"], rows)]}
                conn.execute("UPDATE media_assets SET detail = ? WHERE id = ?",
                             (json.dumps(detail, ensure_ascii=False, sort_keys=True), remake_id))
            return remake_id, rows
        finally:
            conn.close()


# --- the keys ------------------------------------------------------------------------------------


def _frame(study: str, n: int) -> str:
    """Shot n's first frame as a data URI: a JPEG at exactly its place."""
    from ..reel import reels

    path = frame_path(study, n)
    resolved = reels.resolve(path, f"shot {n}'s frame")
    if reels._service_path(resolved) != f"media/remake/{study}/shot-{n}.jpg":
        raise hand.Refused(f"shot {n}'s frame is {path}", rule="frame")
    if assets._extension(resolved.read_bytes()[:16]) != "jpg":
        raise hand.Refused(f"shot {n}'s frame is not a JPEG", rule="frame")
    return assets._data_uri(resolved)


def _reference() -> str:
    path = hand.root() / (hand.setting("MEDIA_REFERENCE") or assets.REFERENCE)
    if not path.is_file():
        raise hand.HandError(f"Teeny's reference image is missing ({assets.REFERENCE})")
    return assets._data_uri(path)


def size_of(data: bytes) -> tuple[int, int] | None:
    """(width, height) read from a PNG's or a JPEG's own header; None when
    it cannot be read."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR" and len(data) >= 24:
        width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        return (width, height) if width and height else None
    if data[:2] == b"\xff\xd8":
        at = 2
        while at + 9 < len(data) and data[at] == 0xFF:
            marker, length = data[at + 1], int.from_bytes(data[at + 2:at + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = int.from_bytes(data[at + 5:at + 7], "big"), int.from_bytes(data[at + 7:at + 9], "big")
                return (width, height) if width and height else None
            if length < 2:
                return None
            at += 2 + length
    return None


#: How far a keyframe's width over height may be from the remake's, as a
#: ratio: 768x1362 for 9:16 is 0.2 % off; 1440x720 is 3.6 times off.
SHAPE_SLACK = 1.2


def shaped(data: bytes, aspect: str) -> bool:
    """Whether a keyframe is in the remake's shape, near enough for the
    cut's crop to keep the scene. One whose size cannot be read is taken."""
    size = size_of(data)
    if size is None:
        return True
    wide, high = (int(n) for n in aspect.split(":"))
    ratio = (size[0] / size[1]) / (wide / high)
    return 1 / SHAPE_SLACK <= ratio <= SHAPE_SLACK


def _picture(api: Surplus, answer: Any) -> tuple[bytes | None, str | None, str | None]:
    """(data, extension, None) for a PNG or JPEG keyframe (a WebP is
    converted to PNG), else (None, None, why)."""
    item = ((answer.get("data") or [{}])[0] or {}) if isinstance(answer, dict) and isinstance(
        answer.get("data"), list) else {}
    try:
        if item.get("b64_json"):
            data = base64.b64decode(item["b64_json"], validate=True)
        elif item.get("url"):
            data = api.download(item["url"], assets.IMAGE_MAX_BYTES)
        else:
            return None, None, "no image in the answer"
    except (binascii.Error, ValueError):
        return None, None, "an image that could not be read"
    except SurplusError as exc:
        return None, None, f"the image's download failed ({exc})"
    ext = assets._extension(data)
    if ext in ("png", "jpg"):
        return data, ext, None
    if ext == "webp":
        try:
            from PIL import Image

            out = io.BytesIO()
            Image.open(io.BytesIO(data)).convert("RGB").save(out, "PNG")
            return out.getvalue(), "png", None
        except Exception as exc:  # noqa: BLE001 - any failure is "not usable", booked as bought
            return None, None, f"a WebP that could not be converted ({type(exc).__name__})"
    return None, None, "the answer is not an image"


def _book(conn, remake_id: int, shot: Json, model: str, prompt: str, cost: int, path: str | None,
          error: str | None) -> None:
    """A bought keyframe on its own row, and its cost off its shot's
    reservation, in one transaction."""
    with hand.transaction(conn):
        conn.execute(
            "INSERT INTO media_assets (kind, status, prompt, model, path, source_id, purpose, cost_micro, estimated,"
            " error, detail, created_at, done_at) VALUES ('key', ?, ?, ?, ?, ?, 'post', ?, 1, ?, ?, ?, ?)",
            ("done" if path else "unknown", prompt, model, path, remake_id, cost, error,
             json.dumps({"n": shot["n"], "shot": shot["row"]}), hand.now(), hand.now()))
        conn.execute("UPDATE media_assets SET cost_micro = MAX(cost_micro - ?, 0) WHERE id = ? AND status = 'pending'",
                     (cost, shot["row"]))


def _refused(exc: SurplusError) -> bool:
    error = exc.body.get("error") if isinstance(exc.body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return exc.status in shots.REFUSAL_STATUSES and (code == "request_rejected" or shots._refusal(str(exc)))


def _key(api: Surplus, remake_id: int, shot: Json, frame: str, reference: str, models: list[str], per_shot: int,
         price, deadline: float, clock, stop: threading.Event, aspect: str) -> Json:
    """One shot's keyframe: at most MAX_EDIT_ATTEMPTS edits, model after
    model. {n, path, model, cost} or {n, failed}; _Stop for a refusal every
    model would give. Writes on a connection of its own (it runs in a
    thread)."""
    conn = assets._db()
    tried: list[str] = []
    prompt = edit_prompt(shot)
    attempts = 0
    left_of_cap = per_shot
    try:
        for model in models:
            if attempts >= MAX_EDIT_ATTEMPTS:
                break
            if stop.is_set():
                tried.append("stopped: another shot could not be keyed")
                break
            if deadline - clock() < EDIT_TIMEOUT:
                tried.append(f"not enough of the keys' wait left for another edit ({EDIT_TIMEOUT:.0f} s)")
                break
            cost = price(model)
            if cost >= left_of_cap:
                tried.append(f"{model}: ${cost / 1e6:.3f} an edit leaves nothing of the ${left_of_cap / 1e6:.2f} left "
                             "of the shot's cap")
                continue
            attempts += 1
            try:
                # The remake's own shape is asked for: left to choose, an
                # edit of a 9:16 frame came back landscape (2026-11-20), and
                # the cut would then crop the cast out of it.
                answer = api.call("POST", "/v1/images/edits", {"model": model, "prompt": prompt,
                                                               "images": [frame, reference],
                                                               "aspect_ratio": aspect,
                                                               "response_format": "b64_json"},
                                  timeout=EDIT_TIMEOUT)
            except SurplusError as exc:
                if exc.status is None:
                    # No answer: the edit may still be made and billed, so
                    # it is booked as bought, its picture unknown.
                    _book(conn, remake_id, shot, model, prompt, cost, None, f"{model}: no answer ({exc})")
                    left_of_cap -= cost
                    tried.append(f"{model}: no answer, booked as bought ({exc})")
                    continue
                if exc.status in assets.FALLBACK_STATUSES:
                    tried.append(f"{model}: {exc}")
                    continue
                if _refused(exc):
                    tried.append(f"{model}: refused ({exc})")
                    continue
                raise _Stop(f"shot {shot['n']}: {model}: {exc}") from None
            data, ext, why = _picture(api, answer)
            if data is None:
                # Answered, so bought: the cost stays on the ledger.
                _book(conn, remake_id, shot, model, prompt, cost, None, f"{model}: {why}")
                left_of_cap -= cost
                tried.append(f"{model}: {why}")
                continue
            if not shaped(data, aspect):
                # Bought, and no use: the cut would crop the cast out of it.
                why = "the keyframe came back {}x{}, not {}".format(*size_of(data), aspect)
                _book(conn, remake_id, shot, model, prompt, cost, None, f"{model}: {why}")
                left_of_cap -= cost
                tried.append(f"{model}: {why}")
                continue
            path = assets._media_dir() / f"remake-{remake_id}-key-{shot['n']}.{ext}"
            path.write_bytes(data)
            rel = str(path.relative_to(hand.root()))
            _book(conn, remake_id, shot, model, prompt, cost, rel, None)
            return {"n": shot["n"], "path": rel, "model": model, "cost": cost}
        return {"n": shot["n"], "failed": "; ".join(tried) or "no edit model configured"}
    finally:
        conn.close()


def _keys(api: Surplus, remake_id: int, specs: list[Json], frames: list[str], reference: str, models: list[str],
          per_shot: int, wait: int, clock, aspect: str) -> list[Json]:
    """Every shot keyed in parallel: one result a shot, in order."""
    prices: dict[str, int] = {}
    pricing = threading.Lock()

    def price(model: str) -> int:
        with pricing:
            if model not in prices:
                prices[model] = assets._image_estimate(api, model) or KEY_UNKNOWN_MICRO
            return prices[model]

    deadline = clock() + wait
    stop = threading.Event()
    results: list[Json] = [{"n": s["n"], "failed": "not started"} for s in specs]

    def run(i: int) -> None:
        try:
            results[i] = _key(api, remake_id, specs[i], frames[i], reference, models, per_shot, price, deadline,
                              clock, stop, aspect)
        except _Stop as exc:
            results[i] = {"n": specs[i]["n"], "failed": str(exc)}
        except BaseException as exc:  # noqa: BLE001 - reported as the shot's failure
            results[i] = {"n": specs[i]["n"], "failed": f"{type(exc).__name__}: {exc}"}
        if "failed" in results[i]:
            stop.set()

    threads = [threading.Thread(target=run, args=(i,), name=f"remake-key-{i + 1}", daemon=True)
               for i in range(len(specs))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def _ping(reel: Any) -> Any:
    from ..reel.service import Reel, ReelError

    reel = reel or Reel()
    try:
        reel.ping()
    except ReelError as exc:
        raise hand.HandError(f"the reel service, which cuts and labels a remake, is not answering, so nothing was "
                             f"bought ({exc})") from None
    return reel


def _key_costs(conn, remake_id: int) -> int:
    return conn.execute("SELECT COALESCE(SUM(cost_micro), 0) AS n FROM media_assets WHERE kind = 'key'"
                        " AND source_id = ? AND status != 'failed'", (remake_id,)).fetchone()["n"]


def generate_keys(body: Any, api: Surplus | None = None, reel: Any = None, clock=time.monotonic) -> Json:
    job = keys_of(body)
    the_label = skits.label()
    edit_models = assets._models("MEDIA_REMAKE_EDIT_MODELS", EDIT_MODELS)
    models = assets._models("MEDIA_REMAKE_MODELS", REMAKE_MODELS)
    per_shot = assets._usd("MEDIA_REMAKE_MAX_USD_PER_SHOT", PER_SHOT)
    limit, shared = _limits()
    if hand.dry_run():
        return {"dry_run": True, "would_generate": "remake", "stage": "keys", "shots": len(job["shots"]),
                "aspect": job["aspect"], "edit_models": edit_models, "models": models, "label": the_label,
                "max_usd_per_shot": per_shot / 1e6, "reserve_usd": round(per_shot * len(job["shots"]) / 1e6, 4),
                "enabled": limit > 0 and shared > 0,
                "requests": [{"model": edit_models[0], "prompt": edit_prompt(s),
                              "images": [frame_path(job["study"], s["n"]), assets.REFERENCE]}
                             for s in job["shots"]] if edit_models else []}
    _check_on(limit, shared)
    if not edit_models or not models:
        raise hand.HandError("no remake model is configured (MEDIA_REMAKE_EDIT_MODELS, MEDIA_REMAKE_MODELS)")
    wait = hand.number("MEDIA_REMAKE_KEYS_WAIT_SECONDS", 300, 60, 1700)
    if job["wait"] is not None:
        wait = min(wait, job["wait"])
    frames = [_frame(job["study"], s["n"]) for s in job["shots"]]
    reference = _reference()
    _ping(reel)
    api = api or Surplus()
    remake_id, rows = _claim(job, per_shot, limit, shared, the_label)
    specs = [{**s, "row": row} for s, row in zip(job["shots"], rows)]
    conn = assets._db()
    try:
        results = _keys(api, remake_id, specs, frames, reference, edit_models, per_shot, wait, clock, job["aspect"])
        failed = [r for r in results if "failed" in r]
        if failed:
            why = "; ".join(f"shot {r['n']}: {r['failed']}" for r in failed)[:1000]
            _release(conn, remake_id, why)
            raise hand.HandError(f"the remake's keys were not made: {why}", asset_id=remake_id,
                                 spent_usd=round(_spent_on(conn, remake_id) / 1e6, 4))
        keys = [{"n": r["n"], "path": r["path"], "model": r["model"], "cost_usd": round(r["cost"] / 1e6, 6)}
                for r in results]
        row = conn.execute("SELECT * FROM media_assets WHERE id = ?", (remake_id,)).fetchone()
        _set_detail(conn, remake_id, {**_detail(row), "stage": "keys", "keys": keys})
        reserved = conn.execute("SELECT COALESCE(SUM(cost_micro), 0) AS n FROM media_assets WHERE kind = 'shot'"
                                " AND source_id = ? AND status = 'pending'", (remake_id,)).fetchone()["n"]
        return {"remake_id": remake_id, "keys": keys, "reserve_usd": round(reserved / 1e6, 6),
                "cost_usd": round(sum(r["cost"] for r in results) / 1e6, 6)}
    except BaseException:
        try:
            _release(conn, remake_id, "stopped")
        except Exception:  # noqa: BLE001 - the original failure is what the caller needs
            pass
        raise
    finally:
        conn.close()


# --- the render -----------------------------------------------------------------------------------


def _sound(ask: Json, study: str) -> str | None:
    """The cut's `sound`: the remake's own source audio, as the reel
    service sees it; None when there is none or the operator turned it off."""
    from ..reel import reels

    if ask["sound"] is None:
        return None
    want = sound_path(study)
    refusal = hand.Refused(f"sound is this remake's source audio, {want}, or null", rule="sound")
    if ask["sound"] != want:
        raise refusal
    try:
        service = reels._audio(want, "the sound")
    except hand.Refused:
        raise refusal from None
    if service != f"media/remake/{study}/audio.m4a":
        raise refusal
    return service if hand.flag("MEDIA_REMAKE_SOUND", True) else None


def _claim_render(ask: Json) -> tuple[Json, Any, str | None]:
    """The remake's stored job, checked and marked as rendering, under the
    media lock: (detail, the shot rows, the sound)."""
    with hand.lock("media"):
        conn = assets._db()
        try:
            _runway()
            assets.release_stale(conn)
            row = conn.execute("SELECT * FROM media_assets WHERE id = ? AND kind = 'remake'",
                               (ask["remake_id"],)).fetchone()
            if row is None:
                raise hand.Refused(f"no remake {ask['remake_id']}", rule="remake_id")
            detail = _detail(row)
            if row["status"] == "done":
                raise hand.Refused(f"remake {row['id']} is already rendered", rule="used")
            if row["status"] == "failed" and str(row["error"] or "").startswith("expired"):
                raise hand.Refused(f"remake {row['id']}'s keys are over {STALE_SECONDS // 3600} hours old",
                                   rule="stale")
            if row["status"] != "pending":
                raise hand.Refused(f"remake {row['id']} failed: {row['error']}", rule="failed")
            if detail.get("stage") == "rendering":
                raise hand.Refused(f"remake {row['id']} is being rendered", rule="used")
            if row["created_at"] < hand.now() - STALE_SECONDS:
                _release(conn, row["id"], "expired: its keys were never rendered")
                raise hand.Refused(f"remake {row['id']}'s keys are over {STALE_SECONDS // 3600} hours old",
                                   rule="stale")
            if detail.get("stage") != "keys" or len(detail.get("keys") or []) != len(detail.get("shots") or [1]):
                raise hand.Refused(f"remake {row['id']}'s keys are not all made", rule="not_ready")
            missing = [k["n"] for k in detail["keys"] if not (hand.root() / k["path"]).is_file()]
            if missing:
                raise hand.Refused(f"remake {row['id']}'s key(s) {missing} are not on disk", rule="not_ready")
            sound = _sound(ask, detail["study"])
            rows = conn.execute("SELECT * FROM media_assets WHERE kind = 'shot' AND source_id = ? ORDER BY id",
                                (row["id"],)).fetchall()
            _set_detail(conn, row["id"], {**detail, "stage": "rendering"})
            return {**detail, "id": row["id"]}, rows, sound
        finally:
            conn.close()


def _requests(remake_id: int, models: list[str], job_params: dict[str, Json], clip_sound: bool) -> list[Json]:
    """For a dry run: the jobs the render would send, read from the ledger
    (the keyframe named by its path)."""
    if not models:
        return []
    conn = assets._db()
    try:
        row = conn.execute("SELECT * FROM media_assets WHERE id = ? AND kind = 'remake'", (remake_id,)).fetchone()
    finally:
        conn.close()
    detail = _detail(row) if row else {}
    keys = {k["n"]: k["path"] for k in detail.get("keys") or []}
    return [shots.request_body(models[0], motion_prompt(s["motion"]), shots.at_least(s["seconds"], None),
                               detail.get("aspect", "16:9"), model_params(models[0], job_params, clip_sound),
                               image=keys.get(s["n"]), resolution_setting="MEDIA_REMAKE_RESOLUTION")
            for s in detail.get("shots") or []]


def generate_remake(body: Any, api: Surplus | None = None, reel: Any = None, sleep=time.sleep,
                    clock=time.monotonic) -> Json:
    ask = render_of(body)
    the_label = skits.label()
    models = assets._models("MEDIA_REMAKE_MODELS", REMAKE_MODELS)
    per_shot = assets._usd("MEDIA_REMAKE_MAX_USD_PER_SHOT", PER_SHOT)
    limit, shared = _limits()
    job_params = shots.params("MEDIA_REMAKE_PARAMS")
    if hand.dry_run():
        return {"dry_run": True, "would_generate": "remake", "stage": "render", "remake_id": ask["remake_id"],
                "sound": ask["sound"] if hand.flag("MEDIA_REMAKE_SOUND", True) else None, "models": models,
                "label": the_label, "enabled": limit > 0 and shared > 0,
                "requests": _requests(ask["remake_id"], models, job_params,
                                      not (ask["sound"] and hand.flag("MEDIA_REMAKE_SOUND", True)))}
    _check_on(limit, shared)
    if not models:
        raise hand.HandError("no remake model is configured (MEDIA_REMAKE_MODELS)")
    wait = hand.number("MEDIA_SKIT_WAIT_SECONDS", 900, 60, 1700)
    if ask["wait"] is not None:
        wait = min(wait, ask["wait"])
    from ..reel import reels
    from ..reel.service import ReelError

    reel = _ping(reel)
    api = api or Surplus()
    conn = assets._db()
    try:
        job, rows, sound = _claim_render(ask)
    except BaseException:
        conn.close()
        raise
    remake_id = job["id"]
    cuts: list[str] = []
    made_shots: list[_Shot] = []
    try:
        keys = {k["n"]: k for k in job["keys"]}
        by_row = {r["id"]: r for r in rows}
        for spec in job["shots"]:
            row = by_row.get(spec["row"])
            if row is None or row["status"] != "pending":
                raise hand.HandError(f"remake {remake_id}'s shot {spec['n']} is not waiting to be made")
            made_shots.append(_Shot(spec["n"], row["id"], motion_prompt(spec["motion"]),
                                    math.ceil(spec["seconds"] - 1e-9), models,
                                    image=assets._data_uri(hand.root() / keys[spec["n"]]["path"]),
                                    floor=spec["seconds"], cap=row["cost_micro"]))
        sold = shots.lengths(api)
        failed = shots.make(api, conn, remake_id, made_shots, job["aspect"], plan(per_shot, clip_sound=sound is None),
                            job_params, wait, sleep, clock, sold)
        spent = _key_costs(conn, remake_id)
        if failed:
            why = "; ".join(f"shot {s.n}: {s.failed or 'not made'}" for s in failed)[:1000]
            _release(conn, remake_id, why)
            raise hand.HandError(f"the remake was not made: {why}", asset_id=remake_id,
                                 spent_usd=round(_spent_on(conn, remake_id) / 1e6, 4))
        cut_job: Json = {"clips": [reels._service_path(reels.resolve(s.path, "a shot")) for s in made_shots],
                         "label": the_label, "aspect": job["aspect"], "trims": [s["seconds"] for s in job["shots"]]}
        if sound:
            cut_job["sound"] = sound
        try:
            made, info, path, frames = shots.keep_cut(reel, cut_job, f"remake-{remake_id}", the_label,
                                                      hand.number("MEDIA_SKIT_CUT_SECONDS", 300, 60, 900), cuts)
        except (ReelError, hand.HandError, hand.Refused, KeyError, TypeError) as exc:
            why = f"the cut failed: {getattr(exc, 'reason', None) or exc}"[:1000]
            assets._finish(conn, remake_id, status="failed", cost_micro=0, error=why)
            raise hand.HandError(f"the remake was not made: {why}", asset_id=remake_id,
                                 spent_usd=round(_spent_on(conn, remake_id) / 1e6, 4)) from None
        cost = spent + sum(s.cost for s in made_shots)
        seconds = info.get("seconds") or made.get("seconds")
        shot_seconds = made.get("trimmed") or made.get("shots")
        detail = {**job, "stage": "done", "labelled": True, "shot_rows": [s.row for s in made_shots],
                  "models": [s.model for s in made_shots], "shot_lengths": [s.asked for s in made_shots],
                  "cost_usd": round(cost / 1e6, 6), "frames": frames, "seconds": seconds, "shot_seconds": shot_seconds,
                  "padded": made.get("padded"), "sound": bool(made.get("sound")) if sound else False}
        detail.pop("id", None)
        assets._finish(conn, remake_id, status="done", path=str(path.relative_to(hand.root())), cost_micro=0,
                       estimated=0, detail=json.dumps(detail, ensure_ascii=False, sort_keys=True))
        return {"asset": str(path.relative_to(hand.root())), "asset_id": remake_id, "frames": frames,
                "label": the_label, "labelled": True, "cost_usd": round(cost / 1e6, 6),
                "models": [s.model for s in made_shots], "keys": job["keys"], "seconds": seconds,
                "shot_seconds": shot_seconds}
    except BaseException:
        shots.abandon(api, conn, remake_id, made_shots)
        try:
            _release(conn, remake_id, "stopped")
        except Exception:  # noqa: BLE001 - the original failure is what the caller needs
            pass
        raise
    finally:
        shots.drop_cuts(reel, cuts)
        conn.close()


# --- dropping a remake ----------------------------------------------------------------------------


def drop_remake(body: Any) -> Json:
    """`remake-drop {remake_id}`: a remake whose keys the studio will not
    render (its keyframes failed the look, or the slot passed) let go now:
    its shots' unspent reservations given back, the remake failed
    ("dropped"), its keys kept on the ledger at what they cost. Dropping one
    already failed or expired gives back nothing; one rendered, or being
    rendered, is refused (`used`). Answers {remake_id, released_usd}."""
    if not isinstance(body, dict) or set(body) != {"remake_id"}:
        raise hand.Refused("remake-drop takes {remake_id} and nothing else", rule="body")
    remake_id = body["remake_id"]
    if isinstance(remake_id, bool) or not isinstance(remake_id, int) or remake_id < 1:
        raise hand.Refused("remake_id is the id remake-keys answered", rule="remake_id")
    if hand.dry_run():
        return {"dry_run": True, "would_drop": remake_id}
    with hand.lock("media"):
        conn = assets._db()
        try:
            row = conn.execute("SELECT * FROM media_assets WHERE id = ? AND kind = 'remake'", (remake_id,)).fetchone()
            if row is None:
                raise hand.Refused(f"no remake {remake_id}", rule="remake_id")
            if row["status"] == "done" or (row["status"] == "pending" and _detail(row).get("stage") == "rendering"):
                raise hand.Refused(f"remake {remake_id} is rendered or being rendered", rule="used")
            released = _release(conn, remake_id, "dropped: its keys were let go unrendered") \
                if row["status"] == "pending" else 0
            return {"remake_id": remake_id, "released_usd": round(released / 1e6, 6)}
        finally:
            conn.close()
