"""Skits: short, absurd, cinematic parody videos bought on Surplus, made as
one take (one text-to-video job for the whole film) or, when that cannot be
had, one job a shot cut together with each shot's own sound, always with a
burned-in label (docs/hands.md, "video").

  hands/video skit {shots, aspect?, story, figures?, logline, look?, setting?, score?, wait_seconds?, bed?}

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
  every shot reserves
  `MEDIA_SKIT_MAX_USD_PER_SHOT` ($1.60) on its row before the first job, and
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

One take (the default for two shots or more; `MEDIA_SKIT_ONE_TAKE=0` turns
it off): shots rendered as separate jobs come back with a different face,
set and soundtrack each, because the model remembers nothing between jobs
(2026-11-20, a three-shot kling skit). So the whole skit is one job: one
multi-shot script (guard/skit.py, `script`, the same code the guard's screen
reads: a header with the film's length, hard cuts, one continuous score and
the cast's `look`, the `setting` and the `score` held throughout, then each
shot with its time range) on the first of `MEDIA_SKIT_ONE_TAKE_MODELS` that
takes it (venice-wan-3-0, seedance-2-0, wan-2-7 text-to-video: each seen on
a paid render on 2026-11-20 to follow such a script), with the fields that
model was seen to take (ONE_TAKE_PARAMS: wan-3-0 resolution
`MEDIA_SKIT_RESOLUTION` 720p, the skit's aspect ratio and audio; seedance
resolution; wan-2-7 none), under MEDIA_SKIT_PARAMS. Its length is
`MEDIA_SKIT_ONE_TAKE_SECS` (20) snapped down to what the model sells, cut
to the longest whose ceiling (MAX_PER_SECOND, when the model's is known)
fits the take's cap, the per-shot cap times the shots it replaces (three:
$4.80, so 20 s on wan-3-0 at $4.68, 14 s on seedance, 15 s on wan-2-7; two:
$3.20, so 10 s on wan-3-0), the shots' lengths shared out in proportion; a
job quoted over the cap anyway is cancelled and asked once more at its own
rate. The take reserves that cap on one `shot` row, the
same money its shots would have. A content refusal, a seller's trouble, a
script the model will not take, no length that fits, or a job that fails
without a bill, with no one-take model left, swaps that row for a row a shot
in one transaction (the money held never drops) and renders the skit one
job a shot below, in the time left. No credit or a bad key stops the skit;
a take past the wait, or one billed whose file never came, fails it.

The shots: each is one `POST /v1/video/generations` with the shot's prompt
(guard/skit.py, `shot_prompt`: the look and the setting in front of it, the
score after it, so even a render that remembers nothing gets the same cast)
(and MEDIA_SKIT_STYLE after it, when set) and `duration_seconds`
(`MEDIA_SKIT_SHOT_SECONDS`, 5, unless the shot names its own, snapped to
the nearest length the model sells, ties going longer, from the catalog's
`durations_seconds`, read once a skit; as asked when the catalog cannot be
read or lists none), on the first
of `MEDIA_SKIT_MODELS` that takes it, with an idempotency key per skit, shot
and model. Any other field a model family needs (an aspect ratio, a
resolution, native audio) comes from `MEDIA_SKIT_PARAMS`, a JSON object of
model name (or "*") to fields, where "{aspect}", "{seconds}" and
"{resolution}" (`MEDIA_SKIT_RESOLUTION`, 720p) are filled in. Verified on
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

The ledger is generate_video's: one `media_assets` row a shot, or one for
the take (kind `shot`, `source_id` the skit's row; the take's `detail` says
`one_take` and the lengths of its shots) with its model, job, quote and
then real cost, and a failed one costs nothing; one row for the skit (kind `skit`, cost 0:
the shots carry the money) with the cut's path and, in `detail`, the story,
the figures, the logline, the label, whether it was one take, the models
and what it all cost. A
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
from ..guard import skit as rules
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
#: The models a skit's one take is asked of, in order (MEDIA_SKIT_ONE_TAKE_MODELS):
#: each was seen on a paid render on 2026-11-20 to follow a multi-shot script
#: in one job, the same face, set and soundtrack across hard cuts. wan-3-0:
#: 30 s, six shots, 1280x720 with audio; seedance-2-0: 15 s; wan-2-7: 15 s,
#: 1280x720 with audio.
ONE_TAKE_MODELS = "venice-wan-3-0-text-to-video,seedance-2-0-text-to-video,wan-2-7-text-to-video"
#: The longest one take, by default (MEDIA_SKIT_ONE_TAKE_SECS), always snapped
#: down to a length the model sells (wan-3-0 20; seedance-2-0 and wan-2-7 15),
#: and shortened to fit the cap; ONE_TAKE_SECONDS_RANGE bounds the setting.
ONE_TAKE_SECONDS = 20
ONE_TAKE_SECONDS_RANGE = (5, 30)
#: max_cost_usdc a second asked, in micro-USDC, where it is verified: the
#: take is asked for the longest length whose ceiling fits its cap before it
#: is submitted. 2026-11-20: wan-3-0 30 s at 720p max 7020000 (estimate
#: 2730000, billed 3042000); seedance-2-0 15 s max 5112000 (billed 1420000);
#: wan-2-7 15 s max 3267000 (billed 825000). A model not listed is asked at
#: the limit, and shortened by its own answer's rate when that is over the cap.
MAX_PER_SECOND = {"venice-wan-3-0-text-to-video": 234000, "seedance-2-0-text-to-video": 340800,
                  "wan-2-7-text-to-video": 217800}
#: The fields each one-take model is sent beyond {model, prompt,
#: duration_seconds}, only those a paid render accepted (2026-11-20):
#: wan-3-0 resolution, aspect_ratio and audio; seedance-2-0 resolution (it
#: did not change the price); wan-2-7 none. MEDIA_SKIT_PARAMS is laid over
#: these, so the operator can still change or add any.
ONE_TAKE_PARAMS: dict[str, Json] = {
    "venice-wan-3-0-text-to-video": {"resolution": "{resolution}", "aspect_ratio": "{aspect}", "audio": True},
    "seedance-2-0-text-to-video": {"resolution": "{resolution}"},
}
ASPECTS = ("16:9", "9:16", "1:1")
MAX_SHOTS = 4
MAX_PROMPT = 900
MAX_LOGLINE = 300
MAX_FIGURES = 3
MAX_LOOK, MAX_SETTING, MAX_SCORE = 200, 300, 200
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
    unknown = sorted(set(body) - {"shots", "aspect", "story", "figures", "logline", "wait_seconds", "bed", "look",
                                  "setting", "score"})
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
    figures = [f.strip() for f in figures]
    look = body.get("look") or {}
    if not isinstance(look, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in
                                             look.items()):
        raise hand.Refused("look maps a figure to one line", rule="body")
    looks = {}
    for name, line in look.items():
        figure = next((f for f in figures if f.lower() == " ".join(name.split()).lower()), None)
        if figure is None:
            raise hand.Refused("a look is for a figure the skit names", rule="body")
        line = " ".join(line.split())
        if len(line) > MAX_LOOK:
            raise hand.Refused(f"a look is at most {MAX_LOOK} characters", rule="body")
        looks[figure] = line
    return {"shots": kept, "aspect": aspect, "story": _text(body, "story", 80), "figures": figures,
            "logline": _text(body, "logline", MAX_LOGLINE), "wait": wait, "bed": bed, "look": looks,
            "setting": _text(body, "setting", MAX_SETTING, required=False),
            "score": _text(body, "score", MAX_SCORE, required=False)}


def _held(job: Json) -> Json:
    """The skit as the guard's composer takes it (guard/skit.py, `script`
    and `shot_prompt`): the same function the guard's screen runs, so what
    is screened is what is sent."""
    out: Json = {"figures": job["figures"], "shots": job["shots"]}
    for key in ("look", "setting", "score"):
        if job.get(key):
            out[key] = job[key]
    return out


def take_cap(per_shot: int, shots: int) -> int:
    """The most one take may cost: what the shots it replaces may."""
    return per_shot * shots


def fit(limit: int, cap: int, sold: list[int] | None, rate: int | None) -> int | None:
    """The take's length: the longest the model sells, at most `limit`
    (and the range's most), whose ceiling (`rate` a second, when known) fits
    under `cap`; the least it sells when `limit` is under that and it fits.
    None when no length fits."""
    most = ONE_TAKE_SECONDS_RANGE[1]
    top = min(limit, most)
    lengths = [t for t in (sorted(set(sold)) if sold else range(1, top + 1)) if t <= most]
    affordable = [t for t in lengths if rate is None or t * rate <= cap]
    under = [t for t in affordable if t <= top]
    if under:
        return under[-1]
    if lengths and lengths[0] > top and affordable and affordable[0] == lengths[0]:
        return lengths[0]
    return None


def split(total: int, asked: list[int]) -> list[int]:
    """`total` seconds shared among the shots in proportion to what each
    asked (largest remainder; a tie goes to the later shot, the payoff)."""
    whole = sum(asked)
    if whole == total:
        return list(asked)
    exact = [total * a / whole for a in asked]
    out = [int(x) for x in exact]
    order = sorted(range(len(asked)), key=lambda i: (-(exact[i] - out[i]), -i))
    for i in order[:total - sum(out)]:
        out[i] += 1
    return out


def _params() -> dict[str, Json]:
    return _shots.params("MEDIA_SKIT_PARAMS")


def take_params(model: str, params: dict[str, Json]) -> dict[str, Json]:
    """A one take's fields for `model`: its built-in verified ones
    (ONE_TAKE_PARAMS), then the operator's MEDIA_SKIT_PARAMS over them."""
    built = dict(ONE_TAKE_PARAMS.get(model) or {})
    merged = {"*": dict(params.get("*") or {}), model: {**built, **(params.get(model) or {})}}
    return merged


def take_limit() -> int:
    """The longest one take asked (MEDIA_SKIT_ONE_TAKE_SECS)."""
    return hand.number("MEDIA_SKIT_ONE_TAKE_SECS", ONE_TAKE_SECONDS, *ONE_TAKE_SECONDS_RANGE)


def request_body(model: str, prompt: str, seconds: int, aspect: str, params: dict[str, Json]) -> Json:
    """One shot's job: the fields generate_video already sends, then
    MEDIA_SKIT_PARAMS's for every model ("*") and for this one, with
    {aspect}, {seconds} and {resolution} filled in. The model, the prompt
    and the length are always this hand's."""
    return _shots.request_body(model, prompt, seconds, aspect, params)


def plan(per_shot: int) -> _shots.Plan:
    """A skit's shots: text-to-video, each asked for the length on sale
    nearest its own, under MEDIA_SKIT_MAX_USD_PER_SHOT; files
    skit-<id>-<name>.mp4."""
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


def _claim(job: Json, per_shot: int, limit: int, the_label: str, take: bool = False) -> tuple[int, list[int]]:
    """The caps, then the skit's row and a row a shot carrying its
    reservation (or, for one take, one row carrying all the shots'), under
    the media lock, so every other media hand's budget check sees the money
    from here on."""
    from .. import runway

    with hand.lock("media"):
        conn = assets._db()
        try:
            lim = runway.limits()
            if not lim["media"]:
                raise hand.Refused(f"no skits on the {lim['tier']} runway tier ({lim['why']})", rule="runway",
                                   tier=lim["tier"])
            assets.release_stale(conn)
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
                if take:
                    return skit_id, [_shot_row(conn, skit_id, rules.script(_held(job)), need)]
                held = _held(job)
                shots = [_shot_row(conn, skit_id, rules.shot_prompt(held, n), per_shot) for n in range(len(job["shots"]))]
            return skit_id, shots
        finally:
            conn.close()


def _shot_row(conn, skit_id: int, prompt: str, reserve: int) -> int:
    return conn.execute(
        "INSERT INTO media_assets (kind, status, prompt, source_id, purpose, cost_micro, estimated,"
        " created_at) VALUES ('shot', 'pending', ?, ?, 'post', ?, 1, ?)",
        (prompt, skit_id, reserve, hand.now()),
    ).lastrowid


def _swap(conn, skit_id: int, take_row: int, job: Json, per_shot: int, why: str) -> list[int]:
    """One take that could not be had, for a row a shot: in one transaction
    under the media lock, so the money held never drops below what the
    shots reserve (the take held all of it)."""
    held = _held(job)
    with hand.lock("media"):
        with hand.transaction(conn):
            conn.execute("UPDATE media_assets SET status = 'failed', cost_micro = 0, error = ?, done_at = ? WHERE id = ?",
                         (why[:1000], hand.now(), take_row))
            return [_shot_row(conn, skit_id, rules.shot_prompt(held, n), per_shot) for n in range(len(job["shots"]))]


def _submit(api: Surplus, conn, skit_id: int, shot: _Shot, aspect: str, per_shot: int, params: dict[str, Json],
            sold: dict[str, list[int]] | None = None) -> bool:
    """The shot's job on the next model that takes it; False when none is
    left. _Stop for a refusal every model would give."""
    return _shots.submit(api, conn, skit_id, shot, aspect, plan(per_shot), params, sold)


def _submit_take(api: Surplus, conn, skit_id: int, take: _Shot, job: Json, cap: int, params: dict[str, Json],
                 sold: dict[str, list[int]] | None = None) -> bool:
    """The one take on the next one-take model that takes it, at the longest
    length whose ceiling fits `cap`; False when none is left (the caller
    falls back to one job a shot). A job whose max_cost_usdc is still over
    the cap is cancelled and asked once more, shortened at that job's own
    rate. A seller's trouble, a content refusal or a request the model will
    not take (400, 413, 422) moves to the next model, since the per-shot
    render may still be had; no credit or a bad key stops the skit."""
    style = (hand.setting("MEDIA_SKIT_STYLE") or "").strip()
    asked = [s["seconds"] for s in job["shots"]]
    held = _held(job)
    while take.models:
        model = take.models.pop(0)
        on_sale = (sold or {}).get(model)
        seconds = fit(take_limit(), cap, on_sale, MAX_PER_SECOND.get(model))
        if seconds is None:
            take.tried.append(f"{model}: no length it sells fits the ${cap / 1e6:.2f} cap")
            continue
        retried = False
        while seconds is not None:
            lengths = split(seconds, asked)
            prompt = rules.script(held, lengths, style)
            try:
                answer = api.call("POST", "/v1/video/generations",
                                  request_body(model, prompt, seconds, job["aspect"], take_params(model, params)),
                                  timeout=60,
                                  headers={"idempotency-key": f"teeny-skit-{skit_id}-take-{model}-{seconds}s"})
            except SurplusError as exc:
                if exc.status in assets.FALLBACK_STATUSES or exc.status is None:
                    take.tried.append(f"{model}: {exc}")
                elif exc.status in REFUSAL_STATUSES and _refusal(str(exc)):
                    take.tried.append(f"{model}: refused ({exc})")
                elif exc.status in (400, 413, 422):
                    take.tried.append(f"{model}: would not take the script ({exc})")
                else:
                    take.tried.append(f"{model}: {exc}")
                    raise _Stop(f"the one take: {exc}") from None
                break
            job_id = str((answer or {}).get("id") or "") if isinstance(answer, dict) else ""
            if not job_id:
                take.tried.append(f"{model}: the answer named no job")
                break
            estimate = _micros(answer.get("estimated_cost_usdc"))
            most = _micros(answer.get("max_cost_usdc"))
            charge = most if most is not None else estimate
            if charge is not None and charge > cap:
                _cancel(api, job_id)
                take.tried.append(f"{model}: {seconds} s up to ${charge / 1e6:.3f}, over the ${cap / 1e6:.2f} cap")
                shorter = fit(seconds - 1, cap, on_sale, -(-charge // seconds)) if seconds > 1 and not retried else None
                seconds, retried = shorter, True
                continue
            take.model, take.job, take.asked, take.prompt = model, job_id, seconds, prompt
            take.cost = charge if charge is not None else cap
            take.extra = {"one_take": True, "lengths": lengths, "shots": len(asked)}
            _set(conn, take.row, model=model, job_id=job_id, cost_micro=take.cost, estimated=1, prompt=prompt,
                 detail=json.dumps({"seconds": seconds, "tried": take.tried, **take.extra}, ensure_ascii=False))
            return True
    return False


def _poll(api: Surplus, conn, skit_id: int, shots: list[_Shot], resubmit, wait: int, deadline: float, sleep,
          clock) -> None:
    """Every shot's job to its end: done (the file saved and the row
    finished at its real cost) or `failed` set. Any shot that cannot be made
    cancels the rest. `resubmit(shot)` asks the shot's next model."""
    _shots.poll(api, conn, skit_id, shots, plan(0), resubmit, wait, deadline, sleep, clock)


def _land(api: Surplus, conn, skit_id: int, shot: _Shot, status: Json, sleep=time.sleep) -> None:
    _shots.land(api, conn, skit_id, shot, status, plan(0), sleep)


def _one_take(models: list[str], shots: int) -> bool:
    """Whether a skit of this many shots is made as one take: on unless
    MEDIA_SKIT_ONE_TAKE is off, with a one-take model, and two shots or
    more (one shot is one job either way)."""
    return hand.flag("MEDIA_SKIT_ONE_TAKE", True) and bool(models) and shots >= 2


def generate_skit(body: Any, api: Surplus | None = None, reel: Any = None, sleep=time.sleep,
                  clock=time.monotonic) -> Json:
    job = job_of(body)
    the_label = label()
    models = assets._models("MEDIA_SKIT_MODELS", SKIT_MODELS)
    take_models = assets._models("MEDIA_SKIT_ONE_TAKE_MODELS", ONE_TAKE_MODELS)
    per_shot = assets._usd("MEDIA_SKIT_MAX_USD_PER_SHOT", "1.60")
    limit = hand.number("MEDIA_SKIT_MAX_PER_DAY", 0, 0, 10)
    params = _params()
    n_shots = len(job["shots"])
    one_take = _one_take(take_models, n_shots)
    cap = take_cap(per_shot, n_shots)
    held = _held(job)
    if hand.dry_run():
        out: Json = {"dry_run": True, "would_generate": "skit", "shots": job["shots"], "aspect": job["aspect"],
                     "models": models, "label": the_label, "max_usd_per_shot": per_shot / 1e6,
                     "reserve_usd": round(cap / 1e6, 4), "enabled": limit > 0,
                     "requests": [request_body(models[0], rules.shot_prompt(held, n), s["seconds"], job["aspect"],
                                               params) for n, s in enumerate(job["shots"])] if models else [],
                     "one_take": {"on": one_take, "models": take_models, "max_usd": round(cap / 1e6, 4)}}
        if one_take:
            asked = [s["seconds"] for s in job["shots"]]
            seconds = fit(take_limit(), cap, None, MAX_PER_SECOND.get(take_models[0]))
            if seconds:
                style = (hand.setting("MEDIA_SKIT_STYLE") or "").strip()
                out["one_take"]["request"] = request_body(take_models[0], rules.script(held, split(seconds, asked),
                                                                                        style), seconds,
                                                          job["aspect"], take_params(take_models[0], params))
        return out
    if limit == 0:
        raise hand.Refused("skits are off: MEDIA_SKIT_MAX_PER_DAY is 0 (the operator turns them on and sets the "
                           "budget in secrets/teeny.env)", rule="off")
    if not models and not one_take:
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
    skit_id, rows = _claim(job, per_shot, limit, the_label, take=one_take)
    # The lengths each model sells, read once: a shot is asked for the one
    # nearest its own (veo sells 4, 6 and 8 s; seedance 5 to 15).
    sold = lengths(api)
    deadline = clock() + wait
    conn = assets._db()
    cuts: list[str] = []
    shots: list[_Shot] = []
    take: _Shot | None = None
    try:
        try:
            if one_take:
                # One job for the whole film: one script, one face, one set,
                # one score. Any trouble another model or the per-shot render
                # could get past falls back to one job a shot, in the same
                # reservation.
                take = _Shot(0, rows[0], rules.script(held), sum(s["seconds"] for s in job["shots"]),
                             take_models, name="take")
                shots = [take]
                if _submit_take(api, conn, skit_id, take, job, cap, params, sold):
                    _poll(api, conn, skit_id, shots, lambda t: _submit_take(api, conn, skit_id, t, job, cap, params,
                                                                            sold), wait, deadline, sleep, clock)
                elif not take.failed:
                    take.failed = "no one-take model could take it: " + ("; ".join(take.tried) or "none configured")
                if take.failed and not take.path and take.cost == 0 and not take.timed_out and models:
                    why = f"the one take could not be had, so one job a shot: {take.failed}"
                    take.tried.append(why)
                    rows = _swap(conn, skit_id, take.row, job, per_shot, take.failed)
                    shots = []
                    take = None
            if take is None:
                shots = [_Shot(n, row, rules.shot_prompt(held, n - 1), s["seconds"], models)
                         for n, (row, s) in enumerate(zip(rows, job["shots"]), 1)]
                for shot in shots:
                    if not _submit(api, conn, skit_id, shot, job["aspect"], per_shot, params, sold):
                        shot.failed = "no model could take it: " + ("; ".join(shot.tried) or "none configured")
                        break
                if not any(s.failed for s in shots):
                    _poll(api, conn, skit_id, shots,
                          lambda s: _submit(api, conn, skit_id, s, job["aspect"], per_shot, params, sold),
                          wait, deadline, sleep, clock)
        except _Stop as stop:
            for shot in shots:
                shot.failed = shot.failed or str(stop)
        failed = [s for s in shots if s.failed or not s.path]
        if failed:
            _shots.stop_all(api, conn, shots)
            why = "; ".join(f"{'the one take' if s is take else f'shot {s.n}'}: {s.failed or 'not made'}"
                            for s in failed)[:1000]
            assets._finish(conn, skit_id, status="failed", cost_micro=0, error=why)
            raise hand.HandError(f"the skit was not made: {why}", asset_id=skit_id,
                                 spent_usd=round(sum(s.cost for s in shots if s.path) / 1e6, 4))
        # Every shot is in (or the one take): the cut, with its label, on
        # the reel service. One clip is cut as it is, label burned in.
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
                  "one_take": take is not None, "models": [s.model for s in shots],
                  "shot_lengths": take.extra["lengths"] if take is not None else [s.asked for s in shots],
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
