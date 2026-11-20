"""Skits: short, absurd, cinematic parody videos bought on Surplus, one
text-to-video job a shot, cut together with each shot's own sound and a
burned-in label (docs/hands.md, "video").

  hands/video skit {shots, aspect?, story, figures?, logline, wait_seconds?, bed?}

bin/studio's `skit` format calls this only after the skit passed its hard
lines (`hands/guard skit`: code's screen, then the judge), and looks at the
cut's frames after (`hands/guard view-skit`). This hand makes no judgment of
its own about the words: like hands/image's prompt, they are the caller's to
screen. What it does hold, in code:

- **Off by default.** `MEDIA_SKIT_MAX_PER_DAY` is 0 unless the operator sets
  it in secrets/teeny.env: every call is refused (`off`) and nothing is
  asked of anyone.
- **The money fences.** The runway tier (no media on a low wallet); at most
  `MEDIA_SKIT_MAX_PER_DAY` skits and remakes together a UTC day (one that
  bought anything counts, even one that failed after; media/remakes.py);
  every shot reserves `MEDIA_SKIT_MAX_USD_PER_SHOT` ($1.60) on its row
  before the first job, and
  all of them together must fit under the shared `MEDIA_MAX_USD_PER_DAY`
  ($1.00) with everything else bought today, or nothing starts (`budget`). A
  shot whose job can cost more than its cap (`max_cost_usdc`, the true
  ceiling, about twice what Surplus bills; else the estimate) is cancelled
  and the next model asked; the shot's row carries that ceiling until the
  real cost comes (read once more, after a short pause, when a succeeded
  job carries no bill yet), and keeps it, as an estimate, when none does.
  So a three-shot kling skit reserves about $4.31 until its shots land and
  then costs about $2.15.
- **The cutter first.** The reel service must answer before any money is
  spent: a shot nothing could label is never bought.
- **The label.** The cut carries "AI parody · @<X_BOT_USERNAME>" (or "AI
  parody" with no username) in a corner for its whole length; the label is
  set here, never by the caller, and a cut the service does not report as
  labelled with exactly that label is not handed over (`unlabelled`).

The shots: each is one `POST /v1/video/generations` with the shot's prompt
(and MEDIA_SKIT_STYLE after it, when set) and `duration_seconds`
(`MEDIA_SKIT_SHOT_SECONDS`, 5, unless the shot names its own, snapped to
the nearest length the model sells, ties going longer, from the catalog's
`durations_seconds`, read once a skit; as asked when the catalog cannot be
read or lists none), on the first
of `MEDIA_SKIT_MODELS` that takes it, with an idempotency key per skit, shot
and model. Any other field a model family needs (an aspect ratio, a
resolution, native audio) comes from `MEDIA_SKIT_PARAMS`, a JSON object of
model name (or "*") to fields, where "{aspect}", "{seconds}" and
"{resolution}" (`MEDIA_SKIT_RESOLUTION`, 1080p) are filled in. Verified on
Surplus (2026-11-19, a paid smoke test): {model, prompt, duration_seconds}
with an idempotency key is accepted by kling-v3-pro, seedance-2-0 and
veo3-1-fast text-to-video with a real public figure in the prompt, quoting
`estimated_cost_usdc` in micro-USDC as a string. Not verified: any aspect,
resolution or audio field, so none is sent by default. A seller's
trouble (a 404, 408, 409, 429 or 5xx, no answer) or a content refusal (a
4xx, or a failed job, that says content, policy, safety, likeness and the
like: some hosted models refuse real people) moves the shot to the next
model; any other refusal (no credit, a bad key) stops the whole skit. Every
shot's job is started before any is waited on, then all are polled together
until `wait_seconds` (at most `MEDIA_SKIT_WAIT_SECONDS`, 900) runs out; a
shot that cannot be made cancels the others still running.

The ledger is generate_video's: one `media_assets` row a shot (kind `shot`,
`source_id` the skit's row) with its model, job, quote and then real cost,
and a failed one costs nothing; one row for the skit (kind `skit`, cost 0:
the shots carry the money) with the cut's path and, in `detail`, the story,
the figures, the logline, the label, the models and what it all cost. A
shot left pending by a process that died keeps its reservation, which
counts as spent.

DRY_RUN (on by default) answers what would be made and calls nothing.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .. import hand
from . import assets, shots as _shots
# The shot machinery lives in media/shots.py, shared with media/remakes.py;
# every name it had here is still here.
from .shots import (BILL_SECONDS, FRAME_MAX_BYTES, MAX_BYTES, POLL_SECONDS, REFUSAL,  # noqa: F401
                    REFUSAL_STATUSES, _RESERVED, _Shot, _Stop, _cancel, _end_shots, _micros, _refusal, _set,
                    lengths, snap)
from .surplus import Surplus, SurplusError  # noqa: F401

Json = dict[str, Any]

SKIT_MODELS = ("kling-v3-pro-text-to-video,seedance-2-0-text-to-video,wan-2-7-text-to-video,"
               "veo3-1-fast-text-to-video")
ASPECTS = ("16:9", "9:16", "1:1")
MAX_SHOTS = 4
MAX_PROMPT = 900
MAX_LOGLINE = 300
MAX_FIGURES = 3
LABEL = "AI parody"


def label() -> str:
    """The words every skit carries in its corner."""
    name = (hand.setting("X_BOT_USERNAME") or "").strip().lstrip("@")
    return f"{LABEL} · @{name}" if re.fullmatch(r"[A-Za-z0-9_]{1,15}", name) else LABEL


def _text(body: Json, key: str, limit: int, required: bool = True) -> str:
    value = body.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise hand.Refused(f"a skit needs its {key}", rule="body")
    value = " ".join(value.split())
    if len(value) > limit:
        raise hand.Refused(f"a skit's {key} is at most {limit} characters", rule="body")
    return value


def job_of(body: Any) -> Json:
    """The skit as this hand takes it, every field checked."""
    if not isinstance(body, dict):
        raise hand.Refused("skit takes a JSON object", rule="body")
    unknown = sorted(set(body) - {"shots", "aspect", "story", "figures", "logline", "wait_seconds", "bed"})
    if unknown:
        raise hand.Refused(f"not taken here: {', '.join(unknown)}", rule="body")
    shots = body.get("shots")
    if not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS:
        raise hand.Refused(f"a skit is 1 to {MAX_SHOTS} shots", rule="body")
    default_secs = hand.number("MEDIA_SKIT_SHOT_SECONDS", 5, 3, 10)
    kept = []
    for n, shot in enumerate(shots, 1):
        prompt = shot.get("prompt") if isinstance(shot, dict) else shot
        if not isinstance(prompt, str) or not prompt.strip():
            raise hand.Refused(f"shot {n} has no prompt", rule="body")
        prompt = " ".join(prompt.split())
        if len(prompt) > MAX_PROMPT:
            raise hand.Refused(f"shot {n} is over {MAX_PROMPT} characters", rule="body")
        seconds = shot.get("seconds", default_secs) if isinstance(shot, dict) else default_secs
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 3 <= seconds <= 10:
            raise hand.Refused(f"shot {n}'s seconds is 3 to 10", rule="body")
        kept.append({"prompt": prompt, "seconds": seconds})
    aspect = body.get("aspect") or "16:9"
    if aspect not in ASPECTS:
        raise hand.Refused(f"aspect is one of {', '.join(ASPECTS)}", rule="body")
    figures = body.get("figures") or []
    if not isinstance(figures, list) or len(figures) > MAX_FIGURES or not all(
            isinstance(f, str) and f.strip() and len(f) <= 60 for f in figures):
        raise hand.Refused(f"figures lists at most {MAX_FIGURES} names", rule="body")
    wait = body.get("wait_seconds")
    if wait is not None and (isinstance(wait, bool) or not isinstance(wait, int) or wait < 60):
        raise hand.Refused("wait_seconds is a whole number of at least 60", rule="body")
    bed = body.get("bed")
    if bed is not None and (not isinstance(bed, str) or not bed.strip()):
        raise hand.Refused("bed is the path of a music file", rule="body")
    return {"shots": kept, "aspect": aspect, "story": _text(body, "story", 80), "figures": [f.strip() for f in figures],
            "logline": _text(body, "logline", MAX_LOGLINE), "wait": wait, "bed": bed}


def _params() -> dict[str, Json]:
    return _shots.params("MEDIA_SKIT_PARAMS")


def request_body(model: str, prompt: str, seconds: int, aspect: str, params: dict[str, Json]) -> Json:
    """One shot's job: the fields generate_video already sends, then
    MEDIA_SKIT_PARAMS's for every model ("*") and for this one, with
    {aspect}, {seconds} and {resolution} filled in. The model, the prompt
    and the length are always this hand's."""
    return _shots.request_body(model, prompt, seconds, aspect, params)


def plan(per_shot: int) -> _shots.Plan:
    """A skit's shots: text-to-video, each asked for the length on sale
    nearest its own, under MEDIA_SKIT_MAX_USD_PER_SHOT."""
    return _shots.Plan(prefix="skit", key="teeny-skit", style="MEDIA_SKIT_STYLE", params="MEDIA_SKIT_PARAMS",
                       body=lambda model, prompt, seconds, aspect, params, shot: request_body(
                           model, prompt, seconds, aspect, params),
                       length=lambda shot, sold: snap(shot.seconds, sold), per_shot=per_shot)


#: The formats that share MEDIA_SKIT_MAX_PER_DAY: one bought video a day,
#: whichever it is.
VIDEO_KINDS = ("skit", "remake")


def _count_today(conn, kinds: tuple[str, ...] = VIDEO_KINDS) -> int:
    """Skits and remakes (or only `kinds`) today that bought anything (a
    shot or a keyframe) or are still going."""
    marks = ", ".join("?" for _ in kinds)
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM media_assets s WHERE s.kind IN ({marks}) AND s.created_at >= ? AND"
        " (s.status != 'failed' OR EXISTS (SELECT 1 FROM media_assets t WHERE t.kind IN ('shot', 'key')"
        " AND t.source_id = s.id AND t.status != 'failed' AND t.cost_micro > 0))",
        (*kinds, hand.day_start()),
    ).fetchone()["n"]


def _claim(job: Json, per_shot: int, limit: int, the_label: str) -> tuple[int, list[int]]:
    """The caps, then the skit's row and a row a shot carrying its
    reservation, under the media lock, so every other media hand's budget
    check sees the money from here on."""
    from .. import runway

    with hand.lock("media"):
        conn = assets._db()
        try:
            lim = runway.limits()
            if not lim["media"]:
                raise hand.Refused(f"no skits on the {lim['tier']} runway tier ({lim['why']})", rule="runway",
                                   tier=lim["tier"])
            if _count_today(conn) >= limit:
                raise hand.Refused(f"today's {limit} skit(s) and remake(s) are made (MEDIA_SKIT_MAX_PER_DAY)",
                                   rule="per_day",
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
            detail = {"story": job["story"], "figures": job["figures"], "logline": job["logline"],
                      "aspect": job["aspect"], "label": the_label}
            with hand.transaction(conn):
                skit_id = conn.execute(
                    "INSERT INTO media_assets (kind, status, prompt, purpose, cost_micro, estimated, detail, created_at)"
                    " VALUES ('skit', 'pending', ?, 'post', 0, 1, ?, ?)",
                    (job["logline"], json.dumps(detail, ensure_ascii=False, sort_keys=True), hand.now()),
                ).lastrowid
                shots = [conn.execute(
                    "INSERT INTO media_assets (kind, status, prompt, source_id, purpose, cost_micro, estimated,"
                    " created_at) VALUES ('shot', 'pending', ?, ?, 'post', ?, 1, ?)",
                    (shot["prompt"], skit_id, per_shot, hand.now()),
                ).lastrowid for shot in job["shots"]]
            return skit_id, shots
        finally:
            conn.close()


def _submit(api: Surplus, conn, skit_id: int, shot: _Shot, aspect: str, per_shot: int, params: dict[str, Json],
            sold: dict[str, list[int]] | None = None) -> bool:
    """The shot's job on the next model that takes it; False when none is
    left. _Stop for a refusal every model would give."""
    return _shots.submit(api, conn, skit_id, shot, aspect, plan(per_shot), params, sold)


def _poll(api: Surplus, conn, skit_id: int, shots: list[_Shot], aspect: str, per_shot: int, params: dict[str, Json],
          wait: int, sleep, clock, sold: dict[str, list[int]] | None = None) -> None:
    _shots.poll(api, conn, skit_id, shots, aspect, plan(per_shot), params, wait, sleep, clock, sold)


def _land(api: Surplus, conn, skit_id: int, shot: _Shot, status: Json, sleep=time.sleep) -> None:
    _shots.land(api, conn, skit_id, shot, status, plan(int(shot.cap or 0)), sleep)


def generate_skit(body: Any, api: Surplus | None = None, reel: Any = None, sleep=time.sleep,
                  clock=time.monotonic) -> Json:
    job = job_of(body)
    the_label = label()
    models = assets._models("MEDIA_SKIT_MODELS", SKIT_MODELS)
    per_shot = assets._usd("MEDIA_SKIT_MAX_USD_PER_SHOT", "1.60")
    limit = hand.number("MEDIA_SKIT_MAX_PER_DAY", 0, 0, 10)
    params = _params()
    if hand.dry_run():
        return {"dry_run": True, "would_generate": "skit", "shots": job["shots"], "aspect": job["aspect"],
                "models": models, "label": the_label, "max_usd_per_shot": per_shot / 1e6,
                "reserve_usd": round(per_shot * len(job["shots"]) / 1e6, 4), "enabled": limit > 0,
                "requests": [request_body(models[0], s["prompt"], s["seconds"], job["aspect"], params)
                             for s in job["shots"]] if models else []}
    if limit == 0:
        raise hand.Refused("skits are off: MEDIA_SKIT_MAX_PER_DAY is 0 (the operator turns them on and sets the "
                           "budget in secrets/teeny.env)", rule="off")
    if not models:
        raise hand.HandError("no skit model is configured (MEDIA_SKIT_MODELS)")
    wait = hand.number("MEDIA_SKIT_WAIT_SECONDS", 900, 60, 1700)
    if job["wait"] is not None:
        wait = min(wait, job["wait"])
    from ..reel import reels
    from ..reel.service import Reel, ReelError

    bed = reels._audio(job["bed"], "bed") if job["bed"] else None
    reel = reel or Reel()
    try:
        reel.ping()
    except ReelError as exc:
        raise hand.HandError(f"the reel service, which cuts and labels a skit, is not answering, so nothing was "
                             f"bought ({exc})") from None
    api = api or Surplus()
    # The lengths each model sells, read once: a shot is asked for the one
    # nearest its own (veo sells 4, 6 and 8 s; seedance 5 to 15).
    skit_id, rows = _claim(job, per_shot, limit, the_label)
    sold = lengths(api)
    shots = [_Shot(n, row, s["prompt"], s["seconds"], models) for n, (row, s) in enumerate(zip(rows, job["shots"]), 1)]
    conn = assets._db()
    cuts: list[str] = []
    try:
        failed = _shots.make(api, conn, skit_id, shots, job["aspect"], plan(per_shot), params, wait, sleep, clock, sold)
        if failed:
            why = "; ".join(f"shot {s.n}: {s.failed or 'not made'}" for s in failed)[:1000]
            assets._finish(conn, skit_id, status="failed", cost_micro=0, error=why)
            raise hand.HandError(f"the skit was not made: {why}", asset_id=skit_id,
                                 spent_usd=round(sum(s.cost for s in shots if s.path) / 1e6, 4))
        # Every shot is in: the cut, with its label, on the reel service.
        cut_job: Json = {"clips": [reels._service_path(reels.resolve(s.path, "a shot")) for s in shots],
                         "label": the_label, "aspect": job["aspect"]}
        if bed:
            cut_job.update(bed=bed, bed_volume_pct=hand.number("REEL_BED_VOLUME_PCT", 12, 1, 60))
        try:
            made, info, path, frames = _shots.keep_cut(reel, cut_job, f"skit-{skit_id}", the_label,
                                                       hand.number("MEDIA_SKIT_CUT_SECONDS", 300, 60, 900), cuts)
        except (ReelError, hand.HandError, hand.Refused, KeyError, TypeError) as exc:
            why = f"the cut failed: {getattr(exc, 'reason', None) or exc}"[:1000]
            assets._finish(conn, skit_id, status="failed", cost_micro=0, error=why)
            raise hand.HandError(f"the skit was not made: {why}", asset_id=skit_id,
                                 spent_usd=round(sum(s.cost for s in shots) / 1e6, 4)) from None
        cost = sum(s.cost for s in shots)
        detail = {"story": job["story"], "figures": job["figures"], "logline": job["logline"], "aspect": job["aspect"],
                  "label": the_label, "labelled": True, "shots": [s.row for s in shots],
                  "models": [s.model for s in shots], "shot_lengths": [s.asked for s in shots],
                  "cost_usd": round(cost / 1e6, 6), "frames": frames,
                  "seconds": info.get("seconds") or made.get("seconds"), "shot_seconds": made.get("shots"),
                  "shot_audio": made.get("audio"), "bed": bed}
        assets._finish(conn, skit_id, status="done", path=str(path.relative_to(hand.root())), cost_micro=0,
                       estimated=0, detail=json.dumps(detail, ensure_ascii=False, sort_keys=True))
        row = conn.execute("SELECT * FROM media_assets WHERE id = ?", (skit_id,)).fetchone()
        return {"asset": reels.public(row), "frames": frames, "label": the_label, "labelled": True,
                "cost_usd": round(cost / 1e6, 6), "models": [s.model for s in shots]}
    except BaseException:
        # Whatever stopped it, a shot still marked pending is cancelled and
        # left failed, never counted twice.
        _shots.abandon(api, conn, skit_id, shots)
        raise
    finally:
        _shots.drop_cuts(reel, cuts)
        conn.close()
