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
  `MEDIA_SKIT_MAX_PER_DAY` skits a UTC day (a skit that bought anything
  counts, even one that failed after); every shot reserves
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
from ..guard import skit as rules
from . import assets
from .surplus import Surplus, SurplusError

Json = dict[str, Any]

SKIT_MODELS = ("kling-v3-pro-text-to-video,seedance-2-0-text-to-video,wan-2-7-text-to-video,"
               "veo3-1-fast-text-to-video")
#: The models a skit's one take is asked of, in order: only seedance-2-0 has
#: been seen to follow a multi-shot script in one job, the same face, set and
#: soundtrack across hard cuts (a paid test, 2026-11-20).
ONE_TAKE_MODELS = "seedance-2-0-text-to-video"
#: The longest one take: what the models sell (seedance 5 to 15 s).
ONE_TAKE_MAX_SECONDS = 15
#: max_cost_usdc a second asked, in micro-USDC, where it is verified: the
#: take is asked for the longest length whose ceiling fits its cap before it
#: is submitted (seedance-2-0 15 s: max 5112000, billed 1420000, 2026-11-20).
#: A model not listed is asked as long as the shots want, and shortened by
#: its own answer's rate when that is over the cap.
MAX_PER_SECOND = {"seedance-2-0-text-to-video": 340800}
ASPECTS = ("16:9", "9:16", "1:1")
MAX_SHOTS = 4
MAX_PROMPT = 900
MAX_LOGLINE = 300
MAX_FIGURES = 3
MAX_LOOK, MAX_SETTING, MAX_SCORE = 200, 300, 200
MAX_BYTES = 200 * 1024 * 1024
FRAME_MAX_BYTES = 5 * 1024 * 1024
POLL_SECONDS = 10.0
#: A job's bill can land a little after it says succeeded (seedance-2-0 on
#: 2026-11-19): read once more after this pause before booking the reservation.
BILL_SECONDS = 5.0
#: A content refusal, in a seller's or a job's words: try the next model.
REFUSAL = re.compile(r"content|policy|safety|moderat|refus|likeness|public figure|celebrit|real person|prohibit|"
                     r"not allowed|violat|blocked|sensitive|nsfw|guideline", re.I)
REFUSAL_STATUSES = {400, 403, 422, 451}
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


def fit(asked: int, cap: int, sold: list[int] | None, rate: int | None) -> int | None:
    """The take's length: the longest the model sells, at most `asked` and
    ONE_TAKE_MAX_SECONDS, whose ceiling (`rate` a second, when known) fits
    under `cap`; the least it sells when even `asked` is under that, if
    that fits. None when no length fits."""
    top = min(asked, ONE_TAKE_MAX_SECONDS)
    lengths = sorted(set(sold)) if sold else list(range(1, top + 1))
    fits = [t for t in lengths if rate is None or t * rate <= cap]
    under = [t for t in fits if t <= top]
    if under:
        return under[-1]
    return fits[0] if fits and fits[0] <= ONE_TAKE_MAX_SECONDS and not [t for t in lengths if t <= top] else None


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
    raw = (hand.setting("MEDIA_SKIT_PARAMS") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise hand.HandError("MEDIA_SKIT_PARAMS is not JSON: an object of model (or \"*\") to fields") from None
    if not isinstance(parsed, dict) or not all(isinstance(v, dict) for v in parsed.values()):
        raise hand.HandError("MEDIA_SKIT_PARAMS is an object of model (or \"*\") to an object of fields")
    return parsed


_RESERVED = {"model", "prompt", "duration_seconds", "image_url"}


def request_body(model: str, prompt: str, seconds: int, aspect: str, params: dict[str, Json]) -> Json:
    """One shot's job: the fields generate_video already sends, then
    MEDIA_SKIT_PARAMS's for every model ("*") and for this one, with
    {aspect}, {seconds} and {resolution} filled in. The model, the prompt
    and the length are always this hand's."""
    resolution = (hand.setting("MEDIA_SKIT_RESOLUTION") or "1080p").strip()
    body: Json = {"model": model, "prompt": prompt, "duration_seconds": seconds}
    for scope in ("*", model):
        for key, value in (params.get(scope) or {}).items():
            if key in _RESERVED:
                continue
            if isinstance(value, str):
                value = value.replace("{aspect}", aspect).replace("{seconds}", str(seconds)).replace(
                    "{resolution}", resolution)
            body[key] = value
    return body


def lengths(api: Surplus) -> dict[str, list[int]]:
    """Each model's lengths on sale, from the catalog (GET /v1/models,
    `durations_seconds`: a list of whole seconds, or {min, max}); {} when
    the catalog cannot be read, so every length goes as asked."""
    try:
        catalog = api.call("GET", "/v1/models", timeout=30)
    except Exception:  # noqa: BLE001 - an unreadable catalog only means "as asked"
        return {}
    rows = catalog.get("data") if isinstance(catalog, dict) else catalog
    out: dict[str, list[int]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        sold = row.get("durations_seconds")
        if isinstance(sold, dict) and all(isinstance(sold.get(k), int) for k in ("min", "max")):
            sold = list(range(sold["min"], sold["max"] + 1))
        if isinstance(sold, list):
            whole = sorted({int(x) for x in sold if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0})
            if whole:
                out[row["id"]] = whole
    return out


def snap(seconds: int, sold: list[int] | None) -> int:
    """The length on sale nearest `seconds` (a tie goes to the longer);
    `seconds` itself when the model lists none."""
    if not sold:
        return seconds
    return min(sold, key=lambda n: (abs(n - seconds), -n))


def _micros(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _refusal(text: str) -> bool:
    return bool(REFUSAL.search(text or ""))


def _count_today(conn) -> int:
    """Skits today that bought anything or are still going."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM media_assets s WHERE s.kind = 'skit' AND s.created_at >= ? AND (s.status != 'failed'"
        " OR EXISTS (SELECT 1 FROM media_assets t WHERE t.kind = 'shot' AND t.source_id = s.id"
        " AND t.status != 'failed' AND t.cost_micro > 0))",
        (hand.day_start(),),
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
            if _count_today(conn) >= limit:
                raise hand.Refused(f"today's {limit} skit(s) are made (MEDIA_SKIT_MAX_PER_DAY)", rule="per_day",
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


class _Shot:
    def __init__(self, n: int, row: int, prompt: str, seconds: int, models: list[str], name: str | None = None) -> None:
        self.n, self.row, self.prompt, self.seconds = n, row, prompt, seconds
        self.name = name or f"shot-{n}"
        self.extra: Json = {}
        self.timed_out = False
        self.models = list(models)
        self.tried: list[str] = []
        self.model: str | None = None
        self.job: str | None = None
        self.path: str | None = None
        self.cost = 0
        self.asked = seconds
        self.failed: str | None = None


class _Stop(Exception):
    """A refusal no other model would answer differently: the skit stops."""


def _set(conn, row: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with hand.transaction(conn):
        conn.execute(f"UPDATE media_assets SET {sets} WHERE id = ?", (*fields.values(), row))


def _cancel(api: Surplus, job_id: str | None) -> None:
    if not job_id:
        return
    try:
        api.call("DELETE", f"/v1/video/generations/{job_id}", timeout=30)
    except SurplusError:
        pass


def _submit(api: Surplus, conn, skit_id: int, shot: _Shot, aspect: str, per_shot: int, params: dict[str, Json],
            sold: dict[str, list[int]] | None = None) -> bool:
    """The shot's job on the next model that takes it; False when none is
    left. _Stop for a refusal every model would give."""
    style = (hand.setting("MEDIA_SKIT_STYLE") or "").strip()
    prompt = f"{shot.prompt} {style}".strip() if style else shot.prompt
    while shot.models:
        model = shot.models.pop(0)
        seconds = snap(shot.seconds, (sold or {}).get(model))
        try:
            job = api.call("POST", "/v1/video/generations", request_body(model, prompt, seconds, aspect, params),
                           timeout=60, headers={"idempotency-key": f"teeny-skit-{skit_id}-shot{shot.n}-{model}"})
        except SurplusError as exc:
            if exc.status in assets.FALLBACK_STATUSES or exc.status is None:
                shot.tried.append(f"{model}: {exc}")
                continue
            if exc.status in REFUSAL_STATUSES and _refusal(str(exc)):
                shot.tried.append(f"{model}: refused ({exc})")
                continue
            shot.tried.append(f"{model}: {exc}")
            raise _Stop(f"shot {shot.n}: {exc}") from None
        job_id = str((job or {}).get("id") or "") if isinstance(job, dict) else ""
        if not job_id:
            shot.tried.append(f"{model}: the answer named no job")
            continue
        # What can really be charged: the job's max_cost_usdc, the true
        # ceiling (about twice the bill on 2026-11-19: kling 5 s max 1.4352,
        # billed 0.7176), so the cap and the reservation are that when it is
        # given, else the estimate, else the cap itself.
        estimate = _micros(job.get("estimated_cost_usdc"))
        most = _micros(job.get("max_cost_usdc"))
        charge = most if most is not None else estimate
        if charge is not None and charge > per_shot:
            _cancel(api, job_id)
            shot.tried.append(f"{model}: up to ${charge / 1e6:.3f} (quoted ${(estimate or 0) / 1e6:.3f}), over the "
                              f"${per_shot / 1e6:.2f} a shot cap")
            continue
        shot.model, shot.job, shot.asked = model, job_id, seconds
        shot.cost = charge if charge is not None else per_shot
        _set(conn, shot.row, model=model, job_id=job_id, cost_micro=shot.cost, estimated=1,
             detail=json.dumps({"seconds": seconds, "tried": shot.tried}, ensure_ascii=False))
        return True
    return False


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
        seconds = fit(sum(asked), cap, on_sale, MAX_PER_SECOND.get(model))
        if seconds is None:
            take.tried.append(f"{model}: no length it sells fits the ${cap / 1e6:.2f} cap")
            continue
        retried = False
        while seconds is not None:
            lengths = split(seconds, asked)
            prompt = rules.script(held, lengths, style)
            try:
                answer = api.call("POST", "/v1/video/generations",
                                  request_body(model, prompt, seconds, job["aspect"], params), timeout=60,
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
    while True:
        running = [s for s in shots if s.job and not s.path and not s.failed]
        if not running or any(s.failed for s in shots):
            break
        for shot in running:
            try:
                status = api.call("GET", f"/v1/video/generations/{shot.job}", timeout=30)
            except SurplusError:
                continue  # asked again next round, until the deadline
            word = str((status or {}).get("status") or "") if isinstance(status, dict) else ""
            if word == "succeeded":
                _land(api, conn, skit_id, shot, status, sleep)
            elif word in ("failed", "canceled", "expired"):
                error = status.get("error") if isinstance(status.get("error"), dict) else {}
                said = " ".join(str(error.get(k) or "") for k in ("type", "code", "message"))
                shot.tried.append(f"{shot.model}: {word} ({error.get('type', 'unknown')})")
                _set(conn, shot.row, cost_micro=0)
                shot.job, shot.cost = None, 0
                if _refusal(said) or error.get("retryable", error.get("type") != "invalid_request"):
                    if not resubmit(shot):
                        shot.failed = "no model could make it: " + "; ".join(shot.tried)
                else:
                    shot.failed = "; ".join(shot.tried)
        if any(s.failed for s in shots) or not [s for s in shots if s.job and not s.path]:
            continue
        if clock() >= deadline:
            for shot in shots:
                if shot.job and not shot.path:
                    _cancel(api, shot.job)
                    shot.tried.append(f"{shot.model}: not done after {wait} s, cancelled")
                    shot.failed = f"not done after {wait} s"
                    shot.job, shot.timed_out = None, True
            break
        sleep(POLL_SECONDS)


def _land(api: Surplus, conn, skit_id: int, shot: _Shot, status: Json, sleep=time.sleep) -> None:
    if _micros((status.get("usage") or {}).get("buyer_cost_usdc") if isinstance(status.get("usage"), dict) else None) is None:
        sleep(BILL_SECONDS)
        try:
            again = api.call("GET", f"/v1/video/generations/{shot.job}", timeout=30)
        except SurplusError:
            again = None
        if isinstance(again, dict) and again.get("status") == "succeeded":
            status = again
    url = ((status.get("results") or [{}])[0] or {}).get("url") if isinstance(status.get("results"), list) else None
    actual = _micros((status.get("usage") or {}).get("buyer_cost_usdc")) if isinstance(status.get("usage"), dict) else None
    cost = actual if actual is not None else shot.cost
    if not url:
        shot.failed = f"{shot.model}: succeeded with no artifact"
        shot.cost = cost
        _set(conn, shot.row, cost_micro=cost, estimated=0 if actual is not None else 1, status="unknown",
             error=shot.failed, done_at=hand.now())
        return
    try:
        data = api.download(url, MAX_BYTES)
    except SurplusError as exc:
        shot.failed = f"{shot.model}: the download failed ({exc})"
        _set(conn, shot.row, cost_micro=cost, estimated=0 if actual is not None else 1, status="unknown",
             error=shot.failed, done_at=hand.now())
        return
    if assets._extension(data) != "mp4":
        shot.failed = f"{shot.model}: the artifact is not an mp4"
        _set(conn, shot.row, cost_micro=cost, estimated=0 if actual is not None else 1, status="unknown",
             error=shot.failed, done_at=hand.now())
        return
    path = assets._media_dir() / f"skit-{skit_id}-{shot.name}.mp4"
    path.write_bytes(data)
    shot.path, shot.cost = str(path.relative_to(hand.root())), cost
    _set(conn, shot.row, status="done", path=shot.path, cost_micro=cost, estimated=0 if actual is not None else 1,
         done_at=hand.now(), detail=json.dumps({"seconds": shot.asked, "tried": shot.tried, **shot.extra},
                                               ensure_ascii=False))


def _end_shots(conn, shots: list[_Shot]) -> None:
    """Shots that never landed: failed, costing nothing (a cancelled or
    refused job is not billed), as generate_video leaves its failures."""
    for shot in shots:
        row = conn.execute("SELECT status FROM media_assets WHERE id = ?", (shot.row,)).fetchone()
        if row and row["status"] == "pending":
            _set(conn, shot.row, status="failed", cost_micro=0, error=(shot.failed or "; ".join(shot.tried)
                                                                       or "not made")[:1000], done_at=hand.now())


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
    cut_id = None
    written: list[Any] = []
    try:
        try:
            for shot in shots:
                if not _submit(api, conn, skit_id, shot, job["aspect"], per_shot, params, sold):
                    shot.failed = "no model could take it: " + ("; ".join(shot.tried) or "none configured")
                    break
            if not any(s.failed for s in shots):
                _poll(api, conn, skit_id, shots, job["aspect"], per_shot, params, wait, sleep, clock, sold)
        except _Stop as stop:
            for shot in shots:
                shot.failed = shot.failed or str(stop)
        failed = [s for s in shots if s.failed or not s.path]
        if failed:
            for shot in shots:
                if shot.job and not shot.path:
                    _cancel(api, shot.job)
                    shot.job = None
            _end_shots(conn, shots)
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
            made = reel.cut(cut_job, timeout=hand.number("MEDIA_SKIT_CUT_SECONDS", 300, 60, 900))
            cut_id = made["cut_id"]
            if made.get("labelled") is not True or made.get("label") != the_label:
                raise ReelError(f"the cut came back without its label ({made.get('label')!r}): nothing is handed over")
            data = reel.cut_file(cut_id, "cut.mp4", MAX_BYTES)
            info = reels.verify(data)
            path = assets._media_dir() / f"skit-{skit_id}.mp4"
            path.write_bytes(data)
            written.append(path)
            frames = []
            for name in made.get("frames") or []:
                picture = reel.cut_file(cut_id, name, FRAME_MAX_BYTES)
                if not picture.startswith(b"\xff\xd8\xff"):
                    raise ReelError("a frame of the cut is not a JPEG")
                frame = assets._media_dir() / f"skit-{skit_id}-{name}"
                frame.write_bytes(picture)
                written.append(frame)
                frames.append(str(frame.relative_to(hand.root())))
            if not frames:
                raise ReelError("the cut came back with no frames for the viewer")
        except (ReelError, hand.HandError, hand.Refused, KeyError, TypeError) as exc:
            for item in written:
                reels._unlink(item)
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
        try:
            for shot in shots:
                if shot.job and not shot.path:
                    _cancel(api, shot.job)
                    shot.job = None
            _end_shots(conn, shots)
            row = conn.execute("SELECT status FROM media_assets WHERE id = ?", (skit_id,)).fetchone()
            if row and row["status"] == "pending":
                assets._finish(conn, skit_id, status="failed", cost_micro=0, error="stopped")
        except Exception:  # noqa: BLE001 - the original failure is what the caller needs
            pass
        raise
    finally:
        if cut_id:
            try:
                reel.cut_delete(cut_id)
            except Exception:  # noqa: BLE001 - cleanup must never mask the outcome
                pass
        conn.close()
