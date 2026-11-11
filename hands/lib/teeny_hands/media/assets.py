"""Images and videos Teeny makes for its posts, bought on Surplus.

  hands/image generate {prompt}                 one still, saved to state/media/
  hands/video generate {prompt, image_prompt?}  a still, then animated

Both write a `media_assets` row (what was asked, which model made it, what it
cost, the file) and return the path, which `hands/x upload --file` takes. They
publish nothing: a post attaches a file only through hands/x.

The cheapest models that still look right go first, the rest are fallbacks,
tried in order when a seller can't take the job (MEDIA_IMAGE_MODELS,
MEDIA_VIDEO_MODELS). A video is always image-to-video: a fraction-of-a-cent
still animated, which costs a fifth of the cheapest text-to-video that looks
as good.

The caps are code, in secrets/teeny.env (defaults in brackets):
  MEDIA_IMAGE_MAX_PER_DAY [8]   MEDIA_VIDEO_MAX_PER_DAY [2]
  MEDIA_MAX_USD_PER_DAY [1.00]  every image and video today, estimates included
  MEDIA_VIDEO_MAX_USD_PER_JOB [0.20]  a video quoted above this is cancelled
A failed generation costs nothing on Surplus and nothing here. DRY_RUN (on by
default) answers what would be generated and spends nothing.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

from .. import hand
from .surplus import Surplus, SurplusError

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_assets (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('image', 'video')),
  status      TEXT NOT NULL CHECK (status IN ('pending', 'done', 'failed')),
  prompt      TEXT NOT NULL,
  model       TEXT,
  job_id      TEXT,
  path        TEXT,
  source_id   INTEGER REFERENCES media_assets (id),
  cost_micro  INTEGER NOT NULL DEFAULT 0,
  estimated   INTEGER NOT NULL DEFAULT 1,
  error       TEXT,
  created_at  REAL NOT NULL,
  done_at     REAL
);
CREATE INDEX IF NOT EXISTS media_assets_day ON media_assets (kind, created_at);
"""

IMAGE_MODELS = "flux.2-klein-4b,venice-z-image-turbo"
VIDEO_MODELS = "longcat-distilled-image-to-video,pixverse-c1-image-to-video,seedance-1-5-pro-image-to-video"
IMAGE_SIZE = "1024x1024"
MAX_PROMPT = 1500
IMAGE_MAX_BYTES = 20 * 1024 * 1024
VIDEO_MAX_BYTES = 200 * 1024 * 1024
VIDEO_SECONDS = 5
POLL_SECONDS = 10.0
#: Seller-side trouble: try the next model. Anything else (a bad prompt, no
#: credit) would fail the same way on every model.
FALLBACK_STATUSES = {404, 408, 409, 429, 500, 502, 503, 504}

Json = dict[str, Any]


def _db():
    return hand.open_db(SCHEMA)


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


def _spent_today(conn) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_micro), 0) AS spent FROM media_assets WHERE status != 'failed' AND created_at >= ?",
        (hand.day_start(),),
    ).fetchone()
    return int(row["spent"])


def _count_today(conn, kind: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM media_assets WHERE kind = ? AND status != 'failed' AND created_at >= ? AND source_id IS NULL OR"
        " (kind = ? AND status != 'failed' AND created_at >= ? AND kind = 'video')",
        (kind, hand.day_start(), kind, hand.day_start()),
    ).fetchone()["n"]


def _check_caps(conn, kind: str, extra_micro: int) -> None:
    limit = hand.number(f"MEDIA_{kind.upper()}_MAX_PER_DAY", 8 if kind == "image" else 2, 0, 100)
    used = conn.execute(
        "SELECT COUNT(*) AS n FROM media_assets WHERE kind = ? AND status != 'failed' AND created_at >= ?"
        + (" AND source_id IS NULL" if kind == "image" else ""),
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
        w, h = (int(x) for x in IMAGE_SIZE.split("x"))
        return int(round(price * w * h / 1e6))
    return int(round(price))


def _insert(conn, kind: str, prompt: str, source_id: int | None = None) -> int:
    with hand.transaction(conn):
        return conn.execute(
            "INSERT INTO media_assets (kind, status, prompt, source_id, created_at) VALUES (?, 'pending', ?, ?, ?)",
            (kind, prompt, source_id, hand.now()),
        ).lastrowid


def _finish(conn, asset_id: int, **fields: Any) -> None:
    fields.setdefault("done_at", hand.now())
    sets = ", ".join(f"{k} = ?" for k in fields)
    with hand.transaction(conn):
        conn.execute(f"UPDATE media_assets SET {sets} WHERE id = ?", (*fields.values(), asset_id))


def _public(row) -> Json:
    out = {k: row[k] for k in ("id", "kind", "status", "prompt", "model", "path", "error")}
    out["cost_usd"] = round(row["cost_micro"] / 1e6, 6)
    out["cost_estimated"] = bool(row["estimated"])
    if row["source_id"]:
        out["source_id"] = row["source_id"]
    return out


def _make_image(conn, api: Surplus, prompt: str, source_of: str | None) -> Json:
    """One still: a pending row, each model in turn, the file. Caps checked
    before the first call; for a video's start frame the video's caps hold."""
    asset_id = _insert(conn, "image", prompt, None)
    if source_of:
        with hand.transaction(conn):
            conn.execute("UPDATE media_assets SET error = ? WHERE id = ?", (f"start frame for a {source_of}", asset_id))
    tried: list[str] = []
    for model in _models("MEDIA_IMAGE_MODELS", IMAGE_MODELS):
        estimate = _image_estimate(api, model)
        try:
            answer = api.call(
                "POST",
                "/v1/images/generations",
                {"model": model, "prompt": prompt, "n": 1, "size": IMAGE_SIZE, "response_format": "b64_json"},
                timeout=180,
            )
        except SurplusError as exc:
            tried.append(f"{model}: {exc}")
            if exc.status in FALLBACK_STATUSES or exc.status is None:
                continue
            _finish(conn, asset_id, status="failed", model=model, error=str(exc))
            raise hand.HandError(f"image generation failed: {exc}") from None
        item = (answer.get("data") or [{}])[0]
        if item.get("b64_json"):
            data = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            data = api.download(item["url"], IMAGE_MAX_BYTES)
        else:
            tried.append(f"{model}: no image in the answer")
            continue
        ext = _extension(data)
        if ext not in ("png", "jpg", "webp"):
            tried.append(f"{model}: the answer is not an image")
            continue
        path = _media_dir() / f"image-{asset_id}.{ext}"
        path.write_bytes(data)
        _finish(
            conn,
            asset_id,
            status="done",
            model=model,
            path=str(path.relative_to(hand.root())),
            cost_micro=estimate,
            estimated=1,
            error=None if not source_of else f"start frame for a {source_of}",
        )
        return _public(conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone())
    _finish(conn, asset_id, status="failed", error="; ".join(tried)[:1000] or "no image model configured")
    raise hand.HandError("no image model could take it: " + ("; ".join(tried)[:500] or "none configured"))


def generate_image(body: Any, api: Surplus | None = None) -> Json:
    prompt = _prompt(body)
    if hand.dry_run():
        return {"dry_run": True, "would_generate": "image", "prompt": prompt, "models": _models("MEDIA_IMAGE_MODELS", IMAGE_MODELS)}
    api = api or Surplus()
    with hand.lock("media"):
        conn = _db()
        try:
            _check_caps(conn, "image", 0)
            return {"asset": _make_image(conn, api, prompt, None)}
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
        query = "SELECT * FROM media_assets" + (" WHERE kind = ?" if kind else "") + " ORDER BY id DESC LIMIT ?"
        rows = conn.execute(query, ((kind, limit) if kind else (limit,))).fetchall()
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


def dumps(obj: Any) -> str:
    return json.dumps(obj)
