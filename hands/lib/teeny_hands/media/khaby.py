"""Khaby videos: a Khaby Lame video with its setup kept exactly as it is and
his reaction re-made with Teeny standing where he stood, doing his deadpan
beats, cut with the source's own audio and a burned-in label (docs/hands.md,
"video").

  hands/video khaby keys   {dissect, story?, logline, reactor?, segments: [{n, edit, motion}], wait_seconds?}
  hands/video khaby render {khaby_id, wait_seconds?}
  hands/video khaby drop   {khaby_id}                  -> {khaby_id, released_usd}

A sibling of the remake, on the machinery it shares (media/remakes.py's
`Format`: the caps, the keyframe edits, the per-shot clip plan, the
render's claim and the drop), with the same stale sweep
(assets.release_stale) and label. The remake renders one take from one
keyframe; a khaby video never does (its setup is real footage, and only its
reactions are made), so its keys and its render are its own, the remake's
per-shot path: a reservation, a keyframe and an image-to-video clip for
each reaction. What differs:

- **The timeline is the only word on the video.** hands/khaby dissect has
  written state/media/khaby/<dissect>/timeline.json, source.mp4, audio.m4a
  and a shot-N.jpg a cut. `keys` reads that timeline itself and takes the
  segments, their lengths, the frames, the aspect, the video and the audio
  from it, never from the body; a timeline missing, not `usable`, or whose
  files are gone is refused (`timeline`). The body gives only the words: an
  `edit` and a `motion` for each editable (reaction) segment, in order.
- **Kept segments cost nothing.** A setup or `other` segment gets no key, no
  job and no reservation: the cut takes it straight from source.mp4
  (`starts` and `trims` from the timeline), and segments kept next to each
  other are one clip.
- **One clip a reaction.** A reaction segment is keyed from the first frame
  of its first ingest shot (shot-N.jpg) and rendered as ONE clip from that
  keyframe for the segment's whole length, however many ingest shots it
  spans.
- **Lengths before money.** A reaction outside 1 to 10 s (a generated clip's
  trim, remakes.SHOT_SECONDS), one no model of `MEDIA_KHABY_MODELS` sells a
  length to cover (the catalog, when it can be read), a kept piece outside
  the cutter's 0.1 to 60 s, or a cut of more than MAX_CLIPS clips is refused
  (`length`) before anything is bought.
- **Who reacts.** Teeny, and for now only Teeny (`reactor`, default
  "teeny"): no real person is generated, and Khaby himself never appears in
  what is made (the edit takes him out of the frame).
- **Render takes only the id.** `render` replays what `keys` stored: the
  sound is the timeline's audio, checked to be still on disk under the
  dissect's folder with the source (`not_ready` when the dissect's folder
  was swept, KHABY_KEEP_HOURS).

Off by default: `MEDIA_KHABY_MAX_PER_DAY` is 0, and a khaby video is one of
the `MEDIA_SKIT_MAX_PER_DAY` videos a day skits, remakes and khaby videos
share (the flavor registry). `MEDIA_KHABY_MAX_USD_PER_SHOT` ($1.20),
`MEDIA_KHABY_EDIT_MODELS`, `MEDIA_KHABY_MODELS`,
`MEDIA_KHABY_KEYS_WAIT_SECONDS` (300), `MEDIA_KHABY_STYLE`,
`MEDIA_KHABY_PARAMS` and `MEDIA_KHABY_RESOLUTION` mirror the remake's.

The ledger: a `khaby` row (cost 0; its detail holds the dissect, its source,
the words, the aspect, the label, the pieces of the cut, the reactions'
words and lengths, the keys, and once cut the frames and the cost), a `key`
row a keyframe and a `shot` row a clip, each `source_id` the khaby row.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

from .. import hand
from . import assets, remakes, shots, skits
from .remakes import Format, _words
from .shots import _Shot
from .surplus import Surplus

Json = dict[str, Any]

ROOT = "khaby"
#: The cutter's MAX_REMAKE_CLIPS (deploy/reel/skitcut.py): the most clips a
#: cut with trims joins.
MAX_CLIPS = 6
#: A generated clip's length, as the cutter trims it (skitcut.TRIM_SECONDS).
CLIP_SECONDS = remakes.SHOT_SECONDS
#: A kept piece of the source, as the cutter trims it (skitcut.SOURCE_TRIM_SECONDS).
KEPT_SECONDS = (0.1, 60.0)
REACTORS = ("teeny",)
#: What a reaction reserves by default (MEDIA_KHABY_MAX_USD_PER_SHOT).
PER_SHOT = "1.20"
EDIT_MODELS = remakes.EDIT_MODELS
MODELS = remakes.REMAKE_MODELS
STALE_SECONDS = remakes.STALE_SECONDS
DISSECT = remakes.STUDY
#: Where Teeny goes in the keyframe: the man's place, the man gone.
REACTOR = ("standing exactly where the man stood, in his place and his pose, the same size in the frame; the man "
           "himself is gone from the picture and no other person is added")
MOTION_TAIL = ("The small yellow character is the only one who moves, and nobody else comes into the shot. "
               "One continuous shot, no cuts. No text.")


def _path(dissect: str, name: str) -> str:
    return f"state/media/{ROOT}/{dissect}/{name}"


# --- the timeline ---------------------------------------------------------------------------


def _on_disk(path: Any) -> bool:
    return isinstance(path, str) and (hand.root() / path).is_file()


def timeline_of(dissect: str) -> Json:
    """The dissect's kept timeline, usable and with its files on disk;
    refused (`timeline`) otherwise."""
    def refuse(why: str) -> hand.Refused:
        return hand.Refused(f"the timeline of {dissect} {why}", rule="timeline")

    try:
        kept = json.loads((hand.root() / _path(dissect, "timeline.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise refuse("is not there: hands/khaby dissect makes it, and it is kept KHABY_KEEP_HOURS") from None
    if not isinstance(kept, dict) or kept.get("dissect_id") != dissect:
        raise refuse("is not one hands/khaby dissect wrote")
    if kept.get("usable") is not True:
        raise refuse(f"is not usable: {str(kept.get('why') or 'no reason given')[:300]}")
    if kept.get("video") != _path(dissect, "source.mp4") or not _on_disk(kept["video"]):
        raise refuse("has no source.mp4 on disk any more")
    if kept.get("audio") is not None and (kept["audio"] != _path(dissect, "audio.m4a") or not _on_disk(kept["audio"])):
        raise refuse("has no audio.m4a on disk any more")
    if kept.get("aspect") not in skits.ASPECTS:
        raise refuse(f"has an aspect that is not one of {', '.join(skits.ASPECTS)}")
    if not isinstance(kept.get("segments"), list) or not kept["segments"] or not isinstance(kept.get("shots"), list):
        raise refuse("has no segments")
    return kept


def _layout(dissect: str, kept: Json) -> tuple[list[Json], list[Json]]:
    """(reactions, pieces): each reaction segment {n, seconds, frame}, and
    the cut in order, a kept piece {start, seconds} (kept segments next to
    each other merged) or a reaction {n, seconds}. Refused `length` for a
    length the clips or the cutter cannot take."""
    frames = {s["n"]: s["frame"] for s in kept["shots"]}
    reactions: list[Json] = []
    pieces: list[Json] = []
    for seg in kept["segments"]:
        start, end = float(seg["start"]), float(seg["end"])
        seconds = round(end - start, 3)
        if seg["editable"]:
            if not CLIP_SECONDS[0] <= seconds <= CLIP_SECONDS[1]:
                raise hand.Refused(f"segment {seg['n']}, a reaction, runs {seconds:g} s: a generated clip is "
                                   f"{CLIP_SECONDS[0]:g} to {CLIP_SECONDS[1]:g} s", rule="length")
            first = seg["shots"][0]
            frame = frames.get(first)
            if frame != _path(dissect, f"shot-{first}.jpg") or not _on_disk(frame):
                raise hand.Refused(f"the timeline of {dissect} has no shot-{first}.jpg on disk any more",
                                   rule="timeline")
            reactions.append({"n": seg["n"], "seconds": seconds, "frame": frame})
            pieces.append({"n": seg["n"], "seconds": seconds})
        elif pieces and "start" in pieces[-1]:
            pieces[-1]["seconds"] = round(end - pieces[-1]["start"], 3)
        else:
            pieces.append({"start": round(start, 3), "seconds": seconds})
    for piece in pieces:
        if "start" in piece and not KEPT_SECONDS[0] <= piece["seconds"] <= KEPT_SECONDS[1]:
            raise hand.Refused(f"a kept piece from {piece['start']:g} s runs {piece['seconds']:g} s: the cutter keeps "
                               f"{KEPT_SECONDS[0]:g} to {KEPT_SECONDS[1]:g} s", rule="length")
    if len(pieces) > MAX_CLIPS:
        raise hand.Refused(f"the cut would join {len(pieces)} clips: the cutter joins at most {MAX_CLIPS}",
                           rule="length")
    return reactions, pieces


# --- what the verbs take ------------------------------------------------------------------------


def keys_of(body: Any) -> Json:
    """keys' job: the body's words checked, and everything about the video
    read from the dissect's timeline."""
    if not isinstance(body, dict):
        raise hand.Refused("video khaby keys takes a JSON object", rule="body")
    unknown = sorted(set(body) - {"dissect", "story", "logline", "reactor", "segments", "wait_seconds"})
    if unknown:
        raise hand.Refused(f"not taken here: {', '.join(unknown)} (the timeline says the rest)", rule="body")
    dissect = body.get("dissect")
    if not isinstance(dissect, str) or not DISSECT.fullmatch(dissect):
        raise hand.Refused("dissect is the id hands/khaby dissect gave", rule="body")
    reactor = body.get("reactor", "teeny")
    if reactor not in REACTORS:
        raise hand.Refused("reactor is teeny: for now Teeny is the only one who reacts", rule="reactor")
    given = body.get("segments")
    if not isinstance(given, list) or not given:
        raise hand.Refused("segments gives the words for each reaction segment of the timeline", rule="body")
    words = []
    for i, item in enumerate(given, 1):
        if not isinstance(item, dict) or set(item) - {"n", "edit", "motion"}:
            raise hand.Refused(f"segment {i} is {{n, edit, motion}} and nothing else", rule="body")
        n = item.get("n")
        if isinstance(n, bool) or not isinstance(n, int):
            raise hand.Refused(f"segment {i}'s n is the timeline's segment number", rule="body")
        words.append({"n": n, "edit": _words(item.get("edit"), f"segment {n}'s edit", *remakes.EDIT_CHARS),
                      "motion": _words(item.get("motion"), f"segment {n}'s motion", *remakes.MOTION_CHARS)})
    job = {"logline": _words(body.get("logline"), "logline", 1, skits.MAX_LOGLINE),
           "story": _words(body.get("story", ""), "story", 0, remakes.STORY_CHARS), "wait": remakes._wait(body)}
    kept = timeline_of(dissect)
    try:
        editable = [s["n"] for s in kept["segments"] if s["editable"]]
        if [w["n"] for w in words] != editable:
            raise hand.Refused(f"segments are the timeline's reaction segments, in order: n = {editable}", rule="body")
        reactions, pieces = _layout(dissect, kept)
    except (KeyError, TypeError, ValueError, IndexError):
        raise hand.Refused(f"the timeline of {dissect} is not one hands/khaby dissect wrote", rule="timeline") from None
    source = kept.get("source") if isinstance(kept.get("source"), dict) else {}
    return {"dissect": dissect, "source": {k: source.get(k) for k in ("video_id", "url", "title", "source")},
            **job, "reactor": reactor, "aspect": kept["aspect"], "video": kept["video"], "audio": kept["audio"],
            "pieces": pieces, "shots": [{**r, **w} for r, w in zip(reactions, words)]}


def render_of(body: Any) -> Json:
    """render's job: the id and a wait; nothing else."""
    if not isinstance(body, dict):
        raise hand.Refused("video khaby render takes a JSON object", rule="body")
    unknown = sorted(set(body) - {"khaby_id", "wait_seconds"})
    if unknown:
        raise hand.Refused(f"not taken here: {', '.join(unknown)} (the render replays what video khaby keys stored)",
                           rule="body")
    khaby_id = body.get("khaby_id")
    if isinstance(khaby_id, bool) or not isinstance(khaby_id, int) or khaby_id < 1:
        raise hand.Refused("khaby_id is the id video khaby keys answered", rule="khaby_id")
    return {"khaby_id": khaby_id, "wait": remakes._wait(body)}


# --- the words, the checks and the cut ------------------------------------------------------------


def edit_prompt(shot: Json) -> str:
    """The keyframe's prompt: the hand's frame around the writer's edit,
    Teeny in the man's place."""
    style = (hand.setting("MEDIA_KHABY_STYLE") or remakes.STYLE).strip()
    return f"{remakes.SCENE} {shot['edit']} {assets.CHARACTER} Exactly one Teeny, {REACTOR}. {style}".strip()


def _sold(job: Json, api: Surplus, models: list[str]) -> None:
    """Refused (`length`) when no model sells a length that covers a
    reaction (the catalog; nothing is refused when it cannot be read)."""
    sold = shots.lengths(api)
    for shot in job["shots"]:
        if all(shots.at_least(shot["seconds"], sold.get(model)) is None for model in models):
            raise hand.Refused(f"segment {shot['n']}, a reaction, runs {shot['seconds']:g} s: no model of "
                               "MEDIA_KHABY_MODELS sells a clip that long", rule="length")


def _ready(ask: Json, detail: Json) -> str | None:
    """The dissect's source and audio still on disk, under its own folder:
    the cut's sound (None when the source has no audio)."""
    from ..reel import reels

    dissect = detail["dissect"]
    gone = hand.Refused(f"khaby video {ask['khaby_id']}'s dissect files are gone (KHABY_KEEP_HOURS)", rule="not_ready")
    try:
        video = reels._service_path(reels.resolve(detail["video"], "the dissect's source"))
        sound = reels._audio(detail["audio"], "the dissect's audio") if detail["audio"] is not None else None
    except hand.Refused:
        raise gone from None
    if video != f"media/{ROOT}/{dissect}/source.mp4" or sound not in (None, f"media/{ROOT}/{dissect}/audio.m4a"):
        raise gone
    return sound


def _cut(detail: Json, made: list) -> Json:
    """The clips in segment order: a kept piece from the source with its
    start, a reaction's clip from 0; each trimmed to its length."""
    from ..reel import reels

    clip_of = {shot.n: reels._service_path(reels.resolve(shot.path, "a shot")) for shot in made}
    source = f"media/{ROOT}/{detail['dissect']}/source.mp4"
    pieces = detail["pieces"]
    return {"clips": [source if "start" in p else clip_of[p["n"]] for p in pieces],
            "trims": [p["seconds"] for p in pieces], "starts": [float(p.get("start", 0.0)) for p in pieces]}


KHABY = Format(kind="khaby", noun="khaby video", setting="MEDIA_KHABY", keys_verb="video khaby keys",
               drop_verb="video khaby drop", edit_prompt=edit_prompt, ready=_ready)


# --- the reservation ------------------------------------------------------------------------------


def _claim(job: Json, per_shot: int, limit: int, shared: int, the_label: str) -> tuple[int, list[int]]:
    """The caps, then the khaby row and a row a reaction carrying its
    reservation, under the media lock. The row's detail is the job's fields
    (all but its shots and wait), the label, the shots, and `per_shot` (every
    reaction is keyed, so the render wants every key)."""
    with hand.lock("media"):
        conn = assets._db()
        try:
            assets.release_stale(conn)
            remakes._runway(KHABY)
            if skits._count_today(conn) >= shared:
                raise hand.Refused(f"today's {shared} video(s) skits, remakes and khaby videos share are made "
                                   "(MEDIA_SKIT_MAX_PER_DAY)", rule="per_day", limit=shared)
            if skits._count_today(conn, (KHABY.kind,)) >= limit:
                raise hand.Refused(f"today's {limit} {KHABY.noun}(s) are made ({KHABY.named('MAX_PER_DAY')})",
                                   rule="per_day", limit=limit)
            budget = assets._usd("MEDIA_MAX_USD_PER_DAY", "1.00")
            spent = assets._spent_today(conn)
            need = per_shot * len(job["shots"])
            if spent + need > budget:
                raise hand.Refused(
                    f"{len(job['shots'])} reaction(s) at up to ${per_shot / 1e6:.2f} each would take today's media "
                    f"spend past ${budget / 1e6:.2f} (MEDIA_MAX_USD_PER_DAY)",
                    rule="budget", spent_usd=round(spent / 1e6, 4), budget_usd=budget / 1e6,
                    need_usd=round(need / 1e6, 4))
            with hand.transaction(conn):
                khaby_id = conn.execute(
                    "INSERT INTO media_assets (kind, status, prompt, purpose, cost_micro, estimated, created_at)"
                    " VALUES (?, 'pending', ?, 'post', 0, 1, ?)", (KHABY.kind, job["logline"], hand.now()),
                ).lastrowid
                rows = [conn.execute(
                    "INSERT INTO media_assets (kind, status, prompt, source_id, purpose, cost_micro, estimated,"
                    " created_at) VALUES ('shot', 'pending', ?, ?, 'post', ?, 1, ?)",
                    (shot["motion"], khaby_id, per_shot, hand.now()),
                ).lastrowid for shot in job["shots"]]
                detail = {"stage": "keying", **{k: v for k, v in job.items() if k not in ("shots", "wait")},
                          "label": the_label, "per_shot": True,
                          "shots": [{**shot, "row": row} for shot, row in zip(job["shots"], rows)]}
                conn.execute("UPDATE media_assets SET detail = ? WHERE id = ?",
                             (json.dumps(detail, ensure_ascii=False, sort_keys=True), khaby_id))
            return khaby_id, rows
        finally:
            conn.close()


def _requests(khaby_id: int, models: list[str], job_params: dict[str, Json]) -> list[Json]:
    """For a dry run: the clips the render would send, a reaction each, read
    from the ledger (the keyframe named by its path)."""
    if not models:
        return []
    conn = assets._db()
    try:
        row = conn.execute("SELECT * FROM media_assets WHERE id = ? AND kind = ?", (khaby_id, KHABY.kind)).fetchone()
    finally:
        conn.close()
    detail = remakes._detail(row) if row else {}
    keys = {k["n"]: k["path"] for k in detail.get("keys") or []}
    return [shots.request_body(models[0], remakes.motion_prompt(s["motion"], MOTION_TAIL),
                               shots.at_least(s["seconds"], None), detail.get("aspect", "16:9"),
                               remakes.model_params(models[0], job_params, False), image=keys.get(s["n"]),
                               resolution_setting=KHABY.named("RESOLUTION"))
            for s in detail.get("shots") or []]


# --- the verbs ----------------------------------------------------------------------------------


def generate_keys(body: Any, api: Surplus | None = None, reel: Any = None, clock=time.monotonic) -> Json:
    """`video khaby keys`: the caps, the reservation a reaction, and a
    keyframe edit for each reaction (in parallel, as the remake's per-shot
    keys are)."""
    job = keys_of(body)
    the_label = skits.label()
    edit_models = assets._models(KHABY.named("EDIT_MODELS"), EDIT_MODELS)
    models = assets._models(KHABY.named("MODELS"), MODELS)
    per_shot = assets._usd(KHABY.named("MAX_USD_PER_SHOT"), PER_SHOT)
    limit, shared = remakes._limits(KHABY)
    if hand.dry_run():
        return {"dry_run": True, "would_generate": KHABY.kind, "stage": "keys", "shots": len(job["shots"]),
                "aspect": job["aspect"], "edit_models": edit_models, "models": models, "label": the_label,
                "max_usd_per_shot": per_shot / 1e6, "reserve_usd": round(per_shot * len(job["shots"]) / 1e6, 4),
                "enabled": limit > 0 and shared > 0,
                "requests": [{"model": edit_models[0], "prompt": edit_prompt(s), "aspect_ratio": job["aspect"],
                              "images": [s["frame"], assets.REFERENCE]}
                             for s in job["shots"]] if edit_models else []}
    remakes._check_on(limit, shared, KHABY)
    if not edit_models or not models:
        raise hand.HandError(f"no {KHABY.noun} model is configured ({KHABY.named('EDIT_MODELS')}, "
                             f"{KHABY.named('MODELS')})")
    wait = hand.number(KHABY.named("KEYS_WAIT_SECONDS"), 300, 60, 1700)
    if job["wait"] is not None:
        wait = min(wait, job["wait"])
    frames = [remakes._frame_at(s["frame"], s["n"]) for s in job["shots"]]
    reference = remakes._reference()
    remakes._ping(reel, KHABY.noun)
    api = api or Surplus()
    _sold(job, api, models)
    khaby_id, rows = _claim(job, per_shot, limit, shared, the_label)
    specs = [{**s, "row": row} for s, row in zip(job["shots"], rows)]
    conn = assets._db()
    try:
        results = remakes._keys(api, khaby_id, specs, frames, reference, edit_models, per_shot, wait, clock,
                                job["aspect"], KHABY)
        failed = [r for r in results if "failed" in r]
        if failed:
            why = "; ".join(f"shot {r['n']}: {r['failed']}" for r in failed)[:1000]
            remakes._release(conn, khaby_id, why)
            raise hand.HandError(f"the {KHABY.noun}'s keys were not made: {why}", asset_id=khaby_id,
                                 spent_usd=round(remakes._spent_on(conn, khaby_id) / 1e6, 4))
        keys = [{"n": r["n"], "path": r["path"], "model": r["model"], "cost_usd": round(r["cost"] / 1e6, 6)}
                for r in results]
        row = conn.execute("SELECT * FROM media_assets WHERE id = ?", (khaby_id,)).fetchone()
        remakes._set_detail(conn, khaby_id, {**remakes._detail(row), "stage": "keys", "keys": keys})
        reserved = conn.execute("SELECT COALESCE(SUM(cost_micro), 0) AS n FROM media_assets WHERE kind = 'shot'"
                                " AND source_id = ? AND status = 'pending'", (khaby_id,)).fetchone()["n"]
        return {KHABY.id_field: khaby_id, "keys": keys, "reserve_usd": round(reserved / 1e6, 6),
                "cost_usd": round(sum(r["cost"] for r in results) / 1e6, 6)}
    except BaseException:
        try:
            remakes._release(conn, khaby_id, "stopped")
        except Exception:  # noqa: BLE001 - the original failure is what the caller needs
            pass
        raise
    finally:
        conn.close()


def generate_khaby(body: Any, api: Surplus | None = None, reel: Any = None, sleep=time.sleep,
                   clock=time.monotonic) -> Json:
    """`video khaby render`: the stored job claimed, a clip a reaction
    animated from its keyframe, and the cut of the kept pieces and the clips
    made and kept."""
    ask = render_of(body)
    the_label = skits.label()
    models = assets._models(KHABY.named("MODELS"), MODELS)
    per_shot = assets._usd(KHABY.named("MAX_USD_PER_SHOT"), PER_SHOT)
    limit, shared = remakes._limits(KHABY)
    job_params = shots.params(KHABY.named("PARAMS"))
    if hand.dry_run():
        return {"dry_run": True, "would_generate": KHABY.kind, "stage": "render", KHABY.id_field: ask["khaby_id"],
                "models": models, "label": the_label, "enabled": limit > 0 and shared > 0,
                "requests": _requests(ask["khaby_id"], models, job_params)}
    remakes._check_on(limit, shared, KHABY)
    if not models:
        raise hand.HandError(f"no {KHABY.noun} model is configured ({KHABY.named('MODELS')})")
    wait = hand.number("MEDIA_SKIT_WAIT_SECONDS", 900, 60, 1700)
    if ask["wait"] is not None:
        wait = min(wait, ask["wait"])
    from ..reel.service import ReelError

    reel = remakes._ping(reel, KHABY.noun)
    api = api or Surplus()
    conn = assets._db()
    try:
        job, rows, sound = remakes._claim_render(ask, KHABY)
    except BaseException:
        conn.close()
        raise
    khaby_id = job["id"]
    cuts: list[str] = []
    made_shots: list[_Shot] = []
    try:
        keys = {k["n"]: k for k in job["keys"]}
        by_row = {r["id"]: r for r in rows}
        for spec in job["shots"]:
            row = by_row.get(spec["row"])
            if row is None or row["status"] != "pending":
                raise hand.HandError(f"{KHABY.noun} {khaby_id}'s shot {spec['n']} is not waiting to be made")
            made_shots.append(_Shot(spec["n"], row["id"], remakes.motion_prompt(spec["motion"], MOTION_TAIL),
                                    math.ceil(spec["seconds"] - 1e-9), models,
                                    image=assets._data_uri(hand.root() / keys[spec["n"]]["path"]),
                                    floor=spec["seconds"], cap=row["cost_micro"]))
        sold = shots.lengths(api)
        failed = shots.make(api, conn, khaby_id, made_shots, job["aspect"],
                            remakes.plan(per_shot, clip_sound=sound is None, fmt=KHABY), job_params, wait, sleep,
                            clock, sold)
        spent = remakes._key_costs(conn, khaby_id)
        if failed:
            why = "; ".join(f"shot {s.n}: {s.failed or 'not made'}" for s in failed)[:1000]
            remakes._release(conn, khaby_id, why)
            raise hand.HandError(f"the {KHABY.noun} was not made: {why}", asset_id=khaby_id,
                                 spent_usd=round(remakes._spent_on(conn, khaby_id) / 1e6, 4))
        cut_job: Json = {**_cut(job, made_shots), "label": the_label, "aspect": job["aspect"]}
        if sound:
            cut_job["sound"] = sound
        try:
            made, info, path, frames = shots.keep_cut(reel, cut_job, f"{KHABY.kind}-{khaby_id}", the_label,
                                                      hand.number("MEDIA_SKIT_CUT_SECONDS", 300, 60, 900), cuts)
        except (ReelError, hand.HandError, hand.Refused, KeyError, TypeError) as exc:
            why = f"the cut failed: {getattr(exc, 'reason', None) or exc}"[:1000]
            assets._finish(conn, khaby_id, status="failed", cost_micro=0, error=why)
            raise hand.HandError(f"the {KHABY.noun} was not made: {why}", asset_id=khaby_id,
                                 spent_usd=round(remakes._spent_on(conn, khaby_id) / 1e6, 4)) from None
        cost = spent + sum(s.cost for s in made_shots)
        seconds = info.get("seconds") or made.get("seconds")
        shot_seconds = made.get("trimmed") or made.get("shots")
        detail = {**job, "stage": "done", "labelled": True, "shot_rows": [s.row for s in made_shots],
                  "models": [s.model for s in made_shots], "shot_lengths": [s.asked for s in made_shots],
                  "cost_usd": round(cost / 1e6, 6), "frames": frames, "seconds": seconds, "shot_seconds": shot_seconds,
                  "padded": made.get("padded"), "sound": bool(made.get("sound")) if sound else False}
        detail.pop("id", None)
        assets._finish(conn, khaby_id, status="done", path=str(path.relative_to(hand.root())), cost_micro=0,
                       estimated=0, detail=json.dumps(detail, ensure_ascii=False, sort_keys=True))
        return {"asset": str(path.relative_to(hand.root())), "asset_id": khaby_id, "frames": frames,
                "label": the_label, "labelled": True, "cost_usd": round(cost / 1e6, 6),
                "models": [s.model for s in made_shots], "keys": job["keys"], "seconds": seconds,
                "shot_seconds": shot_seconds}
    except BaseException:
        shots.abandon(api, conn, khaby_id, made_shots)
        try:
            remakes._release(conn, khaby_id, "stopped")
        except Exception:  # noqa: BLE001 - the original failure is what the caller needs
            pass
        raise
    finally:
        shots.drop_cuts(reel, cuts)
        conn.close()


def drop_khaby(body: Any) -> Json:
    """`video khaby drop {khaby_id}`: as remake-drop, for a khaby video."""
    return remakes.drop_with(body, KHABY)
