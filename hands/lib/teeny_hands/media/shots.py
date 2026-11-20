"""Bought video shots: the machinery a skit (media/skits.py) and a remake
(media/remakes.py) share. One `POST /v1/video/generations` a shot, on the
first model that takes it, every shot started before any is waited on, all
polled together, each landed into state/media/ with its real cost on its
ledger row (docs/hands.md, "video").

What differs between the two formats is a `Plan`: the file prefix and the
idempotency-key prefix, the settings the style and the extra job fields come
from, how a shot's length is picked from what a model sells, and what one
shot may cost at most. A `_Shot` may carry its own cap (a remake's shot has
already spent some of its reservation on its keyframe), the keyframe it
animates (`image`, sent as `image_url`) and the source shot's length
(`floor`).

The rules, in code:

- A seller's trouble (a 404, 408, 409, 429 or 5xx, no answer) or a content
  refusal (a 4xx, or a failed job, whose words say content, policy, safety,
  likeness and the like) moves the shot to the next model; any other
  refusal raises `_Stop`, and the caller stops everything.
- A job whose `max_cost_usdc` (the true ceiling, about twice the bill; else
  its estimate) is over the shot's cap is cancelled and the next model
  asked. The shot's row carries that ceiling until the real cost comes.
- A succeeded job whose bill is not there yet is read once more after
  BILL_SECONDS. Its artifact is downloaded with retries (a 502 from
  Surplus's own host was seen for a job that had succeeded and been billed,
  2026-11-20) until the wait runs out; a shot that was billed but whose file
  never came is `unknown` on the ledger, its cost kept, counted as spent.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from .. import hand
from . import assets
from .surplus import Surplus, SurplusError

Json = dict[str, Any]

MAX_BYTES = 200 * 1024 * 1024
FRAME_MAX_BYTES = 5 * 1024 * 1024
POLL_SECONDS = 10.0
#: A job's bill can land a little after it says succeeded (seedance-2-0 on
#: 2026-11-19): read once more after this pause before booking the reservation.
BILL_SECONDS = 5.0
#: Waits between tries of an artifact's download, the last repeated.
DOWNLOAD_BACKOFF = (5.0, 10.0, 20.0, 40.0, 60.0)
#: How long one try of a download may take; a retry is made only while the
#: wait still has a pause and this much left.
DOWNLOAD_TIMEOUT = 120.0
#: A download answer worth trying again: the host's passing trouble. A 404
#: or a 409 says the artifact is not there to be had.
DOWNLOAD_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
#: A content refusal, in a seller's or a job's words: try the next model.
REFUSAL = re.compile(r"content|policy|safety|moderat|refus|likeness|public figure|celebrit|real person|prohibit|"
                     r"not allowed|violat|blocked|sensitive|nsfw|guideline", re.I)
REFUSAL_STATUSES = {400, 403, 422, 451}
#: Fields a job always has from this hand, never from a params setting.
_RESERVED = {"model", "prompt", "duration_seconds", "image_url"}


class _Shot:
    def __init__(self, n: int, row: int, prompt: str, seconds: int, models: list[str], name: str | None = None,
                 image: str | None = None, floor: float | None = None, cap: int | None = None) -> None:
        self.n, self.row, self.prompt, self.seconds = n, row, prompt, seconds
        #: The file's name after `<prefix>-<id>-`: `shot-<n>`, or a skit's `take`.
        self.name = name or f"shot-{n}"
        #: What the landed row's detail carries beyond the length and the tries.
        self.extra: Json = {}
        self.timed_out = False
        self.models = list(models)
        #: The keyframe this shot animates (a data URI, sent as image_url), if any.
        self.image = image
        #: The source shot's length, which a remake's clip must reach.
        self.floor = floor
        #: What this shot may still cost; the plan's per_shot when None.
        self.cap = cap
        self.tried: list[str] = []
        self.model: str | None = None
        self.job: str | None = None
        self.path: str | None = None
        self.cost = 0
        self.asked = seconds
        self.failed: str | None = None


class _Stop(Exception):
    """A refusal no other model would answer differently: the format stops."""


@dataclass(frozen=True)
class Plan:
    """What one format's shots are: their files (`<prefix>-<id>-shot-<n>.mp4`),
    their idempotency keys (`<key>-<id>-shot<n>-<model>`), the setting whose
    words follow every prompt (`style`, or None), the setting of extra job
    fields (`params`), a job's body (`body(model, prompt, seconds, aspect,
    params, shot)`), the length a model is asked for (`length(shot, sold)`:
    None when the model sells none that will do), the most a shot may cost
    (`per_shot`, micro-USDC), and the verified ceiling a second asked of
    some models (`max_per_second`): a length whose ceiling cannot fit what
    the shot may cost is never submitted."""

    prefix: str
    key: str
    style: str | None
    params: str
    body: Callable[..., Json]
    length: Callable[["_Shot", list[int] | None], int | None]
    per_shot: int
    max_per_second: dict[str, int] | None = None


def params(name: str) -> dict[str, Json]:
    """The operator's extra job fields: a JSON object of model (or "*") to
    an object of fields, from the setting `name`."""
    raw = (hand.setting(name) or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise hand.HandError(f"{name} is not JSON: an object of model (or \"*\") to fields") from None
    if not isinstance(parsed, dict) or not all(isinstance(v, dict) for v in parsed.values()):
        raise hand.HandError(f"{name} is an object of model (or \"*\") to an object of fields")
    return parsed


def request_body(model: str, prompt: str, seconds: int, aspect: str, params: dict[str, Json],
                 image: str | None = None, resolution_setting: str = "MEDIA_SKIT_RESOLUTION") -> Json:
    """One shot's job: {model, prompt, duration_seconds} (and `image_url`
    when the shot animates a keyframe), then the params' fields for every
    model ("*") and for this one, with {aspect}, {seconds} and {resolution}
    (the setting `resolution_setting`, 720p) filled in. The model, the
    prompt, the length and the image are always this hand's."""
    resolution = (hand.setting(resolution_setting) or "720p").strip()
    body: Json = {"model": model, "prompt": prompt, "duration_seconds": seconds}
    if image is not None:
        body["image_url"] = image
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


#: The length asked of a model whose lengths are unknown: 5 s is the one
#: every image-to-video model was seen to take.
UNKNOWN_FLOOR = 5


def at_least(floor: float, sold: list[int] | None) -> int | None:
    """The shortest length on sale that is at least `floor` seconds (a
    clip may be trimmed, never stretched); None when the model sells none
    that long. When the model lists none, the whole seconds of `floor`, and
    never under UNKNOWN_FLOOR."""
    if not sold:
        return max(UNKNOWN_FLOOR, math.ceil(floor - 1e-9))
    fits = [n for n in sold if n >= floor - 1e-9]
    return min(fits) if fits else None


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


def submit(api: Surplus, conn, parent_id: int, shot: _Shot, aspect: str, plan: Plan, job_params: dict[str, Json],
           sold: dict[str, list[int]] | None = None) -> bool:
    """The shot's job on the next model that takes it; False when none is
    left. _Stop for a refusal every model would give."""
    style = (hand.setting(plan.style) or "").strip() if plan.style else ""
    prompt = f"{shot.prompt} {style}".strip() if style else shot.prompt
    cap = shot.cap if shot.cap is not None else plan.per_shot
    if shot.cap is not None and cap <= 0:
        shot.tried.append("nothing is left of its cap")
        return False
    while shot.models:
        model = shot.models.pop(0)
        seconds = plan.length(shot, (sold or {}).get(model))
        if seconds is None:
            shot.tried.append(f"{model}: sells no length of at least {shot.floor} s")
            continue
        rate = (plan.max_per_second or {}).get(model)
        if rate is not None and seconds * rate > cap:
            shot.tried.append(f"{model}: {seconds} s can cost up to ${seconds * rate / 1e6:.3f}, over the "
                              f"${cap / 1e6:.2f} left of the shot's cap")
            continue
        try:
            job = api.call("POST", "/v1/video/generations", plan.body(model, prompt, seconds, aspect, job_params, shot),
                           timeout=60, headers={"idempotency-key": f"{plan.key}-{parent_id}-shot{shot.n}-{model}"})
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
        if charge is not None and charge > cap:
            _cancel(api, job_id)
            shot.tried.append(f"{model}: up to ${charge / 1e6:.3f} (quoted ${(estimate or 0) / 1e6:.3f}), over the "
                              f"${cap / 1e6:.2f} a shot cap")
            continue
        shot.model, shot.job, shot.asked = model, job_id, seconds
        shot.cost = charge if charge is not None else cap
        _set(conn, shot.row, model=model, job_id=job_id, cost_micro=shot.cost, estimated=1,
             detail=json.dumps({"seconds": seconds, "tried": shot.tried}, ensure_ascii=False))
        return True
    return False


def poll(api: Surplus, conn, parent_id: int, shots: list[_Shot], plan: Plan, resubmit, wait: int, deadline: float,
         sleep, clock) -> None:
    """Every shot's job to its end, by `deadline`: done (the file saved and
    the row finished at its real cost) or `failed` set. Any shot that cannot
    be made cancels the rest. `resubmit(shot)` asks the shot's next model."""
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
                land(api, conn, parent_id, shot, status, plan, sleep, clock, deadline)
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


def _retryable(exc: SurplusError) -> bool:
    """A download worth trying again: the host's passing trouble or no
    answer, not an artifact that is gone, too big or sent somewhere else."""
    return exc.status in DOWNLOAD_RETRY_STATUSES or str(exc).startswith("could not")


def _download(api: Surplus, url: str, sleep, clock, deadline: float | None) -> bytes:
    """The artifact, tried again after DOWNLOAD_BACKOFF's waits while the
    host is in trouble and the wait still has the pause and a whole try
    (DOWNLOAD_TIMEOUT) left; the last error otherwise."""
    tries = 0
    while True:
        try:
            return api.download(url, MAX_BYTES, timeout=DOWNLOAD_TIMEOUT)
        except SurplusError as exc:
            pause = DOWNLOAD_BACKOFF[min(tries, len(DOWNLOAD_BACKOFF) - 1)]
            if not _retryable(exc) or deadline is None or clock is None or (
                    clock() + pause + DOWNLOAD_TIMEOUT > deadline):
                raise
            tries += 1
            sleep(pause)


def land(api: Surplus, conn, parent_id: int, shot: _Shot, status: Json, plan: Plan, sleep=time.sleep,
         clock=None, deadline: float | None = None) -> None:
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
        data = _download(api, url, sleep, clock, deadline)
    except SurplusError as exc:
        shot.failed = f"{shot.model}: the download failed ({exc})"
        shot.cost = cost
        _set(conn, shot.row, cost_micro=cost, estimated=0 if actual is not None else 1, status="unknown",
             error=shot.failed, done_at=hand.now())
        return
    if assets._extension(data) != "mp4":
        shot.failed = f"{shot.model}: the artifact is not an mp4"
        shot.cost = cost
        _set(conn, shot.row, cost_micro=cost, estimated=0 if actual is not None else 1, status="unknown",
             error=shot.failed, done_at=hand.now())
        return
    path = assets._media_dir() / f"{plan.prefix}-{parent_id}-{shot.name}.mp4"
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


def stop_all(api: Surplus, conn, shots: list[_Shot]) -> None:
    """Every job still running cancelled, and every shot that never landed
    left failed."""
    for shot in shots:
        if shot.job and not shot.path:
            _cancel(api, shot.job)
            shot.job = None
    _end_shots(conn, shots)


def make(api: Surplus, conn, parent_id: int, shots: list[_Shot], aspect: str, plan: Plan, job_params: dict[str, Json],
         wait: int, sleep, clock, sold: dict[str, list[int]] | None) -> list[_Shot]:
    """Every shot started, then all polled to their end within `wait`; the
    shots that were not made (empty when all landed). Whatever was not made
    is cancelled and left failed."""
    deadline = clock() + wait
    try:
        for shot in shots:
            if not submit(api, conn, parent_id, shot, aspect, plan, job_params, sold):
                shot.failed = "no model could take it: " + ("; ".join(shot.tried) or "none configured")
                break
        if not any(s.failed for s in shots):
            poll(api, conn, parent_id, shots, plan, lambda shot: submit(api, conn, parent_id, shot, aspect, plan,
                                                                        job_params, sold), wait, deadline, sleep, clock)
    except _Stop as stop:
        for shot in shots:
            shot.failed = shot.failed or str(stop)
    failed = [s for s in shots if s.failed or not s.path]
    if failed:
        stop_all(api, conn, shots)
    return failed


def abandon(api: Surplus, conn, parent_id: int, shots: list[_Shot]) -> None:
    """Whatever stopped a format: every shot still running cancelled and
    left failed, never counted twice, and the parent row, if still
    pending, failed. Never raises: the original failure is what the caller
    needs."""
    try:
        stop_all(api, conn, shots)
        row = conn.execute("SELECT status FROM media_assets WHERE id = ?", (parent_id,)).fetchone()
        if row and row["status"] == "pending":
            assets._finish(conn, parent_id, status="failed", cost_micro=0, error="stopped")
    except Exception:  # noqa: BLE001 - the original failure is what the caller needs
        pass


def keep_cut(reel: Any, cut_job: Json, name: str, the_label: str, timeout: int,
             cuts: list[str]) -> tuple[Json, Json, Any, list[str]]:
    """The cut made on the reel service and kept: (what the service
    answered, reels.verify's info, the mp4's path, the frames' paths), as
    state/media/<name>.mp4 and <name>-frame-N.jpg. The cut's id goes into
    `cuts` as soon as it is known, for drop_cuts. A cut not reported
    labelled with exactly `the_label`, a render that is not a sound mp4, a
    frame that is not a JPEG, or no frames at all is never kept: ReelError,
    and whatever was written is removed."""
    from ..reel import reels
    from ..reel.service import ReelError

    written: list[Any] = []
    try:
        made = reel.cut(cut_job, timeout=timeout)
        cut_id = made["cut_id"]
        cuts.append(cut_id)
        if made.get("labelled") is not True or made.get("label") != the_label:
            raise ReelError(f"the cut came back without its label ({made.get('label')!r}): nothing is handed over")
        data = reel.cut_file(cut_id, "cut.mp4", MAX_BYTES)
        info = reels.verify(data)
        path = assets._media_dir() / f"{name}.mp4"
        path.write_bytes(data)
        written.append(path)
        frames = []
        for frame_name in made.get("frames") or []:
            picture = reel.cut_file(cut_id, frame_name, FRAME_MAX_BYTES)
            if not picture.startswith(b"\xff\xd8\xff"):
                raise ReelError("a frame of the cut is not a JPEG")
            frame = assets._media_dir() / f"{name}-{frame_name}"
            frame.write_bytes(picture)
            written.append(frame)
            frames.append(str(frame.relative_to(hand.root())))
        if not frames:
            raise ReelError("the cut came back with no frames for the viewer")
        return made, info, path, frames
    except BaseException:
        for item in written:
            reels._unlink(item)
        raise


def drop_cuts(reel: Any, cuts: list[str]) -> None:
    """The service's copies of the cuts deleted; cleanup never masks the
    outcome."""
    for cut_id in cuts:
        try:
            reel.cut_delete(cut_id)
        except Exception:  # noqa: BLE001 - cleanup must never mask the outcome
            pass
