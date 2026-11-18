"""Clips with Teeny's voice and captions, and songs, made from Teeny's own
pictures and clips (docs/hands.md, "reel").

  hands/reel make {kind, script, materials, aspect?, voice?, captions?, bed?, audio?, title?}
  hands/reel music {style, lyrics?, seconds?}

`make` hands the job to the `reel` service (MoneyPrinterTurbo, a submodule
at vendor/moneyprinter, run by deploy/compose.yaml) and waits for it:
Teeny's stills and 5-second clips from hands/image and hands/video, in the
order given, under the script read by an Edge voice with word-timed
captions, or, for a song, under a track `music` made. The mp4 is copied to
state/media/reel-<id>.mp4, checked, and recorded in `media_assets` (kind
`reel` or `song`), so `hands/x upload --file` takes it. Nothing is posted.

Every job pins the operator's rules, whatever the caller sent: local
materials only (never stock footage), in sequence, one video, no generated
script, no bundled or random song (a bed only when one is named), no upload.
The caller's body never reaches the service as it is: only the fields built
here do.

The words (script, title, lyrics) are the caller's to screen, as for
hands/image's prompt: bin/act puts a description past `hands/guard check`
before it calls a media hand, and whatever wires a reel or a song in must do
the same with every word that ends up on screen or in the voice. This hand
makes no model call of its own.

`music` buys a track on Surplus Intelligence (SURPLUS_API_KEY, the image and
video hands' account): `POST /v1/music/generations`, an async job like a
video's. Its route and the model's market are confirmed; the job's answer
and result shapes are assumed to be the video route's, so it stays off until
REEL_MUSIC=1 and the first paid run is checked by hand.

The caps are code, in secrets/teeny.env (defaults in brackets):
  REEL_MAX_PER_DAY [1]            clips a UTC day
  REEL_SONGS_MAX_PER_WEEK [1]     songs (and music tracks) in 7 days
  REEL_MUSIC_MAX_USD_PER_JOB [0.70]  a track quoted above this is cancelled
  MEDIA_MAX_USD_PER_DAY [1.00]    shared with hands/image and hands/video
and the runway tier that cuts pictures and clips cuts these too. A failed
job counts toward nothing. DRY_RUN (on by default) answers what would be
made and calls nothing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import hand
from ..media import assets
from ..media.surplus import Surplus, SurplusError
from ..x.media import UPLOAD_ROOTS, sniff
from .service import STATE_COMPLETE, STATE_FAILED, Reel, ReelError

Json = dict[str, Any]

KINDS = ("clip", "song")
ASPECTS = ("9:16", "16:9", "1:1")
CAPTIONS = {"word": "word_by_word", "sentence": "sentence"}
MAX_SCRIPT = 1500
MAX_TITLE = 120
MAX_MATERIALS = 12
#: X takes a video of up to 140 seconds without Premium.
MAX_SECONDS = 140
MAX_BYTES = 200 * 1024 * 1024
AUDIO_MAX_BYTES = 30 * 1024 * 1024
POLL_SECONDS = 5.0
#: One of Edge's neural voices: they need no key, and their word timing is
#: exact, so the captions land on the words.
EDGE_VOICE = re.compile(r"[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural(-Female|-Male)?")
VOICE = "en-US-AnaNeural-Female"
#: Teeny's look: upstream's bold Latin face, Teeny-yellow words, a black edge.
FONT = "BeVietnamPro-Bold.ttf"
TEXT_COLOR = "#FFE14D"
STROKE_COLOR = "#000000"
COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
MUSIC_MODELS = "elevenlabs-music-v2-5,elevenlabs-music-v2"
MAX_STYLE = 500
MAX_LYRICS = 2000
WEEK = 7 * 86400.0


# --- the job ------------------------------------------------------------------------------


def _text(body: Json, key: str, limit: int, required: bool) -> str:
    value = body.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise hand.Refused(f"{key} is {'a text' if not required else 'needed'}", rule="body")
    value = value.strip()
    if len(value) > limit:
        raise hand.Refused(f"{key} is at most {limit} characters", rule="body")
    return value


def resolve(path: Any, what: str) -> Path:
    """The real file behind `path` (relative to the checkout unless absolute),
    when it is a regular file under state/media/ or brain/assets/, the same
    folders hands/x upload takes from. Symlinks and `..` are resolved first,
    so neither can point it elsewhere."""
    root = hand.root().resolve()
    refusal = hand.Refused(f"{what} is a file under state/media/ or brain/assets/", rule="path", allowed=list(UPLOAD_ROOTS))
    if not isinstance(path, str) or not path.strip():
        raise refusal
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise refusal from None
    if not resolved.is_file() or not any(resolved.is_relative_to((root / folder).resolve()) for folder in UPLOAD_ROOTS):
        raise refusal
    return resolved


def _service_path(resolved: Path) -> str:
    """Where the reel service sees the file: compose mounts state/media/ at
    media/ and brain/assets/ at assets/, read-only."""
    root = hand.root().resolve()
    for folder, mount in zip(UPLOAD_ROOTS, ("media", "assets")):
        base = (root / folder).resolve()
        if resolved.is_relative_to(base):
            return f"{mount}/{resolved.relative_to(base).as_posix()}"
    raise hand.Refused("not under state/media/ or brain/assets/", rule="path")


def audio_type(data: bytes) -> str | None:
    """An audio file's type from its first bytes."""
    if data.startswith(b"ID3") or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"fLaC"):
        return "flac"
    if data[4:8] == b"ftyp":
        return "m4a"
    return None


def _audio(path: Any, what: str) -> str:
    resolved = resolve(path, what)
    with open(resolved, "rb") as fh:
        head = fh.read(16)
    kind = audio_type(head)
    if kind is None or resolved.suffix.lower().lstrip(".") not in ("mp3", "wav", "ogg", "flac", "m4a", "aac", "opus"):
        raise hand.Refused(f"{what} is an mp3, wav, ogg, flac or m4a file", rule="type")
    return _service_path(resolved)


def _setting_color(name: str, default: str) -> str:
    value = hand.setting(name) or default
    return value if COLOR.fullmatch(value) else default


def build_job(body: Any) -> Json:
    """The job the service gets, from the caller's body: every field is set
    here, the rules first, and only the checked parts of the body are used."""
    if not isinstance(body, dict):
        raise hand.Refused("make takes a JSON object", rule="body")
    kind = body.get("kind")
    if kind not in KINDS:
        raise hand.Refused("kind is clip or song", rule="body")
    script = _text(body, "script", MAX_SCRIPT, True)
    title = _text(body, "title", MAX_TITLE, False)
    aspect = body.get("aspect", "9:16")
    if aspect not in ASPECTS:
        raise hand.Refused("aspect is 9:16, 16:9 or 1:1", rule="body")
    materials = body.get("materials")
    if not isinstance(materials, list) or not 1 <= len(materials) <= MAX_MATERIALS:
        raise hand.Refused(f"materials lists 1 to {MAX_MATERIALS} of Teeny's pictures or clips", rule="body")
    urls = []
    for path in materials:
        resolved = resolve(path, "a material")
        with open(resolved, "rb") as fh:
            mime = sniff(fh.read(16))
        if mime not in ("image/png", "image/jpeg", "image/webp", "video/mp4"):
            raise hand.Refused("a material is a PNG, JPEG or WebP picture or an MP4 clip", rule="type")
        urls.append({"provider": "local", "url": _service_path(resolved)})
    voice = body.get("voice") or hand.setting("REEL_VOICE") or VOICE
    if not isinstance(voice, str) or not EDGE_VOICE.fullmatch(voice):
        raise hand.Refused("voice is an Edge voice, like en-US-AnaNeural-Female", rule="body")
    captions = body.get("captions", "word")
    if captions not in CAPTIONS:
        raise hand.Refused("captions is word or sentence", rule="body")

    job: Json = {
        # The rules, whatever the body says.
        "video_source": "local",
        "video_concat_mode": "sequential",
        "video_count": 1,
        "match_materials_to_script": False,
        "video_transition_mode": None,
        "bgm_type": "",
        "bgm_file": "",
        "bgm_volume": 0.0,
        # The piece.
        "video_subject": title or script[:MAX_TITLE],
        "video_script": script,
        "video_materials": urls,
        "video_aspect": aspect,
        "video_fit_mode": "cover",
        "image_motion": "zoom",
        "video_clip_duration": 5,
        "video_language": "en",
        "voice_name": voice,
        "voice_volume": 1.0,
        "voice_rate": 1.0,
        "subtitle_enabled": True,
        "subtitle_position": "bottom",
        "subtitle_display_mode": CAPTIONS[captions],
        "subtitle_animation": "none",
        "font_name": hand.setting("REEL_FONT") or FONT,
        "font_size": hand.number("REEL_FONT_SIZE", 88, 40, 160),
        "text_fore_color": _setting_color("REEL_TEXT_COLOR", TEXT_COLOR),
        "text_background_color": False,
        "stroke_color": _setting_color("REEL_STROKE_COLOR", STROKE_COLOR),
        "stroke_width": 3.0,
        "n_threads": 2,
    }
    if kind == "clip":
        if body.get("audio"):
            raise hand.Refused("a clip is voiced from its script; audio is for a song", rule="body")
        if body.get("bed"):
            job["bgm_type"] = "custom"
            job["bgm_file"] = _audio(body["bed"], "bed")
            # No ducking upstream yet, so the bed sits low under the voice.
            job["bgm_volume"] = hand.number("REEL_BED_VOLUME_PCT", 12, 1, 60) / 100
    else:
        if body.get("bed"):
            raise hand.Refused("a song is its own music; bed is for a clip", rule="body")
        if not body.get("audio"):
            raise hand.Refused("a song needs its track (audio), from hands/reel music", rule="body")
        job["audio"] = _audio(body["audio"], "audio")
        # A track has no Edge timing, and upstream times a track's words only
        # with Whisper, which the service never loads: a song goes out with
        # its pictures and no captions (docs/hands.md, "reel", gaps).
        job["subtitle_enabled"] = False
    return job


# --- the ledger and the caps ---------------------------------------------------------------


def _count(conn, kind: str, since: float) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM media_assets WHERE kind = ? AND status != 'failed' AND created_at >= ?",
        (kind, since),
    ).fetchone()["n"]


def _runway(what: str) -> None:
    from .. import runway

    lim = runway.limits()
    if not lim["media"]:
        raise hand.Refused(f"no {what} on the {lim['tier']} runway tier ({lim['why']})", rule="runway", tier=lim["tier"])


def _budget(conn, extra_micro: int) -> None:
    budget = assets._usd("MEDIA_MAX_USD_PER_DAY", "1.00")
    spent = assets._spent_today(conn)
    if spent + extra_micro > budget:
        raise hand.Refused(
            f"that would take today's media spend past ${budget / 1e6:.2f}",
            rule="budget",
            spent_usd=round(spent / 1e6, 4),
            budget_usd=budget / 1e6,
        )


def _claim(kind: str, prompt: str, detail: Json, reserve_micro: int = 0) -> int:
    """The caps, then a pending row, under the media lock: the row counts
    toward the caps from here, and carries `reserve_micro` as its cost until
    the real one replaces it, so a second caller, hands/video's budget check
    included, sees the reservation and cannot take the same money."""
    with hand.lock("media"):
        conn = assets._db()
        try:
            _runway({"reel": "clips", "song": "songs", "music": "music"}[kind])
            if kind == "reel":
                limit = hand.number("REEL_MAX_PER_DAY", 1, 0, 10)
                if _count(conn, "reel", hand.day_start()) >= limit:
                    raise hand.Refused(f"today's {limit} clip(s) are made", rule="per_day", limit=limit)
            else:
                limit = hand.number("REEL_SONGS_MAX_PER_WEEK", 1, 0, 7)
                if _count(conn, kind, hand.now() - WEEK) >= limit:
                    what = "song" if kind == "song" else "track"
                    raise hand.Refused(f"this week's {limit} {what}(s) are made", rule="per_week", limit=limit)
            _budget(conn, reserve_micro)
            with hand.transaction(conn):
                return conn.execute(
                    "INSERT INTO media_assets (kind, status, prompt, purpose, cost_micro, estimated, detail, created_at)"
                    " VALUES (?, 'pending', ?, 'post', ?, 1, ?, ?)",
                    (kind, prompt, reserve_micro, json.dumps(detail, sort_keys=True), hand.now()),
                ).lastrowid
        finally:
            conn.close()


def _finish(asset_id: int, **fields: Any) -> Json:
    conn = assets._db()
    try:
        assets._finish(conn, asset_id, **fields)
        return public(conn.execute("SELECT * FROM media_assets WHERE id = ?", (asset_id,)).fetchone())
    finally:
        conn.close()


def _set(asset_id: int, **fields: Any) -> None:
    conn = assets._db()
    try:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with hand.transaction(conn):
            conn.execute(f"UPDATE media_assets SET {sets} WHERE id = ?", (*fields.values(), asset_id))
    finally:
        conn.close()


def public(row) -> Json:
    out = assets._public(row)
    if "detail" in row.keys() and row["detail"]:
        try:
            out["detail"] = json.loads(row["detail"])
        except ValueError:
            pass
    return out


# --- checking the render ---------------------------------------------------------------------


def _boxes(data: bytes, start: int, end: int):
    at = start
    while at + 8 <= end:
        size, name = struct.unpack(">I4s", data[at : at + 8])
        header = 8
        if size == 1:
            if at + 16 > end:
                return
            size = struct.unpack(">Q", data[at + 8 : at + 16])[0]
            header = 16
        elif size == 0:
            size = end - at
        if size < header or at + size > end:
            return
        yield name, at + header, at + size
        at += size


def mp4_info(data: bytes) -> Json:
    """Seconds and track kinds ('vide', 'soun') from an mp4's moov box, read
    from the header, as no ffprobe is in the spine's image. Every read stays
    inside its box; a box too short for what it must hold is `ValueError`."""
    info: Json = {"seconds": None, "tracks": []}
    for name, body, end in _boxes(data, 0, len(data)):
        if name != b"moov":
            continue
        for child, cbody, cend in _boxes(data, body, end):
            if child == b"mvhd":
                if cbody >= cend:
                    raise ValueError("an empty mvhd")
                version = data[cbody]
                need = 32 if version == 1 else 20
                if cbody + need > cend:
                    raise ValueError("a short mvhd")
                if version == 1:
                    scale, duration = struct.unpack(">IQ", data[cbody + 20 : cbody + 32])
                else:
                    scale, duration = struct.unpack(">II", data[cbody + 12 : cbody + 20])
                info["seconds"] = round(duration / scale, 3) if scale else None
            elif child == b"trak":
                for mdia, mbody, mend in _boxes(data, cbody, cend):
                    if mdia != b"mdia":
                        continue
                    for hdlr, hbody, hend in _boxes(data, mbody, mend):
                        if hdlr == b"hdlr":
                            if hbody + 12 > hend:
                                raise ValueError("a short hdlr")
                            info["tracks"].append(data[hbody + 8 : hbody + 12].decode("latin-1"))
    return info


def _ffprobe_seconds(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout.strip()
        return round(float(out), 3)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def verify(data: bytes, limit: int = MAX_BYTES) -> Json:
    """What a render must be before it is kept: an mp4, under the size cap,
    with a picture and a sound track, and no longer than X takes."""
    if sniff(data[:16]) != "video/mp4":
        raise ReelError("the render is not an mp4")
    if len(data) > limit:
        raise ReelError(f"the render is over {limit // (1024 * 1024)} MB")
    try:
        info = mp4_info(data)
    except (ValueError, struct.error, IndexError) as exc:
        raise ReelError(f"the render is not a valid mp4 ({exc})") from None
    if "vide" not in info["tracks"] or "soun" not in info["tracks"]:
        raise ReelError(f"the render lacks a picture or a sound track ({', '.join(info['tracks']) or 'none'})")
    seconds = info["seconds"]
    if not seconds or seconds <= 0 or seconds > MAX_SECONDS:
        raise ReelError(f"the render runs {seconds} s; X takes 0 to {MAX_SECONDS}")
    return info


# --- the verbs ------------------------------------------------------------------------------------


def _await(api: Reel, task_id: str, wait: int, sleep) -> Json:
    deadline = time.monotonic() + wait
    while True:
        task = api.status(task_id)
        if task.get("state") in (STATE_COMPLETE, STATE_FAILED):
            return task
        if time.monotonic() >= deadline:
            return {"state": STATE_FAILED, "failed_stage": "wait", "error": f"not done after {wait} s"}
        sleep(POLL_SECONDS)


def _unlink(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def make(body: Any, api: Reel | None = None, sleep=time.sleep) -> Json:
    job = build_job(body)
    kind = "reel" if body["kind"] == "clip" else "song"
    shown = {k: job[k] for k in ("video_aspect", "voice_name", "subtitle_enabled", "subtitle_display_mode", "bgm_file", "bgm_volume")}
    shown["materials"] = [m["url"] for m in job["video_materials"]]
    if job.get("audio"):
        shown["audio"] = job["audio"]
    if hand.dry_run():
        return {"dry_run": True, "would_make": body["kind"], "script": job["video_script"], "job": shown}
    api = api or Reel()
    wait = hand.number("REEL_WAIT_SECONDS", 900, 60, 3600)
    asset_id = _claim(kind, job["video_script"], shown)
    started = time.monotonic()
    task_id = None
    path = assets._media_dir() / f"reel-{asset_id}.mp4"
    part = path.with_name(path.name + ".part")
    try:
        task_id = api.submit(job)
        _set(asset_id, job_id=task_id, model="moneyprinter")
        task = _await(api, task_id, wait, sleep)
        if task.get("state") != STATE_COMPLETE:
            raise ReelError(f"the render failed at {task.get('failed_stage') or 'an unknown stage'}: {str(task.get('error'))[:300]}")
        data = api.download(task_id, MAX_BYTES)
        info = verify(data)
        part.write_bytes(data)
        os.replace(part, path)
        seconds = _ffprobe_seconds(path) or info["seconds"]
        detail = {**shown, "seconds": seconds, "bytes": len(data), "render_seconds": round(time.monotonic() - started, 1)}
        asset = _finish(
            asset_id,
            status="done",
            path=str(path.relative_to(hand.root())),
            cost_micro=0,
            estimated=0,
            detail=json.dumps(detail, sort_keys=True),
        )
    except BaseException as exc:
        # Whatever stopped it (the service, a bad render, a full disk, the
        # ledger, a signal): no file stays, the row is failed so it counts
        # toward nothing, and the service drops the task.
        _unlink(part, path)
        try:
            _finish(asset_id, status="failed", cost_micro=0, path=None, error=f"{type(exc).__name__}: {exc}"[:1000])
        except BaseException:  # noqa: BLE001 - the original failure is what the caller needs
            pass
        if task_id:
            _forget(api, task_id)
        if isinstance(exc, (ReelError, hand.HandError)):
            raise hand.HandError(f"the {body['kind']} was not made: {exc}", asset_id=asset_id) from None
        raise
    _forget(api, task_id)
    return {"asset": asset}


def _forget(api: Reel, task_id: str) -> None:
    """The task and its copies leave the service once the mp4 is ours (or
    the job failed); a task still running refuses, and the front's sweep
    takes it later."""
    try:
        api.delete(task_id)
    except Exception:  # noqa: BLE001 - cleanup must never mask the outcome
        pass


#: Surplus's job status words. Anything else, at the deadline, is an
#: unknown outcome.
MUSIC_OK = {"succeeded", "completed", "success", "done"}
MUSIC_FAILED = {"failed", "canceled", "cancelled", "expired", "error", "rejected"}
#: A refusal before any job exists: the next model may be asked.
MUSIC_NEXT_MODEL = {404, 409, 429, 503}


def _micros(value: Any) -> int | None:
    """A USDC amount in micros as Surplus writes it ("414000" or 414000);
    None for anything else (a fraction, a negative, text, a bool)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def music(body: Any, api: Surplus | None = None, sleep=time.sleep) -> Json:
    """A track bought on Surplus: `style` (what it sounds like), `lyrics`
    (the words, sung), `seconds` (10 to 180, default 30).

    Fails closed. The row carries REEL_MUSIC_MAX_USD_PER_JOB from the claim;
    once Surplus accepts a job it carries the quote (or that cap when the
    quote is missing or odd), and only a confirmed end replaces it: the real
    cost on success, nothing on a confirmed failure or a confirmed
    cancellation. Anything else after an accepted job (an answer of a shape
    not known here, a status word not known here, the deadline, a failed
    download, an artifact that is not audio) is status `unknown`: spent, with
    the job id kept, counted in the day's budget and the week's cap. After
    any accepted job no other model is tried."""
    if not isinstance(body, dict):
        raise hand.Refused("music takes a JSON object", rule="body")
    style = _text(body, "style", MAX_STYLE, True)
    lyrics = _text(body, "lyrics", MAX_LYRICS, False)
    seconds = body.get("seconds", 30)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 10 <= seconds <= 180:
        raise hand.Refused("seconds is a whole number from 10 to 180", rule="body")
    prompt = style + (f"\n\nLyrics:\n{lyrics}" if lyrics else "\n\nInstrumental, no vocals.")
    models = assets._models("REEL_MUSIC_MODELS", MUSIC_MODELS)
    enabled = hand.flag("REEL_MUSIC", False)
    if hand.dry_run():
        return {"dry_run": True, "would_make": "music", "prompt": prompt, "seconds": seconds, "models": models, "enabled": enabled}
    if not enabled:
        raise hand.Refused("music is off until REEL_MUSIC=1 (the Surplus music route is unverified)", rule="off")
    api = api or Surplus()
    per_job = assets._usd("REEL_MUSIC_MAX_USD_PER_JOB", "0.70")
    wait = hand.number("REEL_MUSIC_WAIT_SECONDS", 600, 60, 1700)
    asset_id = _claim("music", prompt, {"seconds": seconds}, per_job)
    state = {"accepted": False, "booked": per_job, "job_id": None, "file": None}
    try:
        return _buy(api, asset_id, models, prompt, seconds, per_job, wait, sleep, state)
    except BaseException as exc:
        if state["file"] is not None:
            _unlink(state["file"])
        try:
            if _status(asset_id) != "pending":
                pass  # already ended above, with what is known
            elif state["accepted"]:
                _finish(asset_id, status="unknown", cost_micro=state["booked"], estimated=1, path=None, error=f"{type(exc).__name__}: {exc}"[:1000])
            else:
                _finish(asset_id, status="failed", cost_micro=0, path=None, error=f"{type(exc).__name__}: {exc}"[:1000])
        except BaseException:  # noqa: BLE001
            pass
        if isinstance(exc, hand.HandError):
            raise
        if isinstance(exc, Exception):
            raise hand.HandError(f"music: {type(exc).__name__}: {exc}", asset_id=asset_id) from None
        raise


def _status(asset_id: int) -> str | None:
    conn = assets._db()
    try:
        row = conn.execute("SELECT status FROM media_assets WHERE id = ?", (asset_id,)).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def _unknown(asset_id: int, state: Json, why: str) -> None:
    _finish(asset_id, status="unknown", cost_micro=state["booked"], estimated=1, error=why[:1000])
    raise hand.HandError(
        f"the track's outcome is unknown ({why}); ${state['booked'] / 1e6:.2f} is booked as spent",
        asset_id=asset_id,
        job_id=state["job_id"],
        outcome="unknown",
    )


def _buy(api, asset_id: int, models: list[str], prompt: str, seconds: int, per_job: int, wait: int, sleep, state: Json) -> Json:
    tried: list[str] = []
    for model in models:
        try:
            job = api.call(
                "POST",
                "/v1/music/generations",
                {"model": model, "prompt": prompt, "duration_seconds": seconds},
                timeout=60,
                headers={"idempotency-key": f"teeny-music-{asset_id}-{model}"},
            )
        except SurplusError as exc:
            tried.append(f"{model}: {exc}")
            if exc.status is None or exc.status >= 500 and exc.status != 503:
                # No answer, or a server error: the job may exist. Booked.
                state["accepted"] = True
                _set(asset_id, model=model)
                _unknown(asset_id, state, f"{model}: {exc}")
            if exc.status in MUSIC_NEXT_MODEL:
                continue
            break
        # Accepted: from here on no other model is tried.
        state["accepted"] = True
        job_id = job.get("id") if isinstance(job, dict) else None
        estimate = _micros(job.get("estimated_cost_usdc")) if isinstance(job, dict) else None
        state["booked"] = estimate if estimate else per_job
        _set(asset_id, model=model, cost_micro=state["booked"], estimated=1)
        if not isinstance(job_id, str) or not job_id or len(job_id) > 200:
            _unknown(asset_id, state, f"{model}: the answer named no job")
        state["job_id"] = job_id
        _set(asset_id, job_id=job_id)
        if estimate and estimate > per_job:
            if _cancel(api, job_id):
                _finish(asset_id, status="failed", cost_micro=0, error=f"{model}: quoted ${estimate / 1e6:.3f}, over the ${per_job / 1e6:.2f} cap; cancelled")
                raise hand.HandError(f"the track was quoted ${estimate / 1e6:.3f}, over the ${per_job / 1e6:.2f} cap, and cancelled", asset_id=asset_id)
            _unknown(asset_id, state, f"{model}: quoted over the cap and the cancel was not confirmed")
        result, why = _await_music(api, job_id, wait, sleep)
        if result is None:
            _unknown(asset_id, state, f"{model}: {why}")
        status = str(result.get("status") or "").lower()
        if status in MUSIC_FAILED:
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            actual = _micros(usage.get("buyer_cost_usdc"))
            _finish(asset_id, status="failed", cost_micro=actual or 0, estimated=0, error=f"{model}: {status} ({error.get('type', 'unknown')})")
            raise hand.HandError(f"the track was not made: {status}", asset_id=asset_id)
        results = result.get("results")
        first = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}
        url = first.get("url")
        if not isinstance(url, str) or not url:
            _unknown(asset_id, state, f"{model}: {status} with no artifact")
        try:
            data = api.download(url, AUDIO_MAX_BYTES)
        except SurplusError as exc:
            _unknown(asset_id, state, f"{model}: the download failed: {exc}")
        ext = audio_type(data[:16]) if isinstance(data, (bytes, bytearray)) else None
        if ext is None:
            _unknown(asset_id, state, f"{model}: the artifact is not audio")
        path = assets._media_dir() / f"music-{asset_id}.{ext}"
        state["file"] = path
        path.write_bytes(data)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        actual = _micros(usage.get("buyer_cost_usdc"))
        asset = _finish(
            asset_id,
            status="done",
            path=str(path.relative_to(hand.root())),
            cost_micro=actual if actual is not None else state["booked"],
            estimated=0 if actual is not None else 1,
        )
        state["file"] = None
        return {"asset": asset}
    _finish(asset_id, status="failed", cost_micro=0, error="; ".join(tried)[:1000] or "no music model configured")
    raise hand.HandError("no music model could take it: " + ("; ".join(tried)[:500] or "none configured"), asset_id=asset_id)


def _cancel(api: Surplus, job_id: str) -> bool:
    try:
        api.call("DELETE", f"/v1/music/generations/{job_id}", timeout=30)
        return True
    except Exception:  # noqa: BLE001 - a cancel that fails is reported, not raised
        return False


def _await_music(api: Surplus, job_id: str, wait: int, sleep) -> tuple[Json | None, str]:
    """The job's last answer once it reaches a known end; (None, why) for
    the deadline or an answer this code does not understand by then."""
    deadline = time.monotonic() + wait
    last = "no answer"
    while True:
        try:
            status = api.call("GET", f"/v1/music/generations/{job_id}", timeout=30)
        except SurplusError as exc:
            status, last = None, f"polling failed: {exc}"
        if isinstance(status, dict):
            word = str(status.get("status") or "").lower()
            if word in MUSIC_OK or word in MUSIC_FAILED:
                return status, ""
            last = f"status {word or 'missing'!r}"
        elif status is not None:
            last = "an answer that is not an object"
        if time.monotonic() >= deadline:
            _cancel(api, job_id)
            return None, f"not done after {wait} s ({last})"
        sleep(assets.POLL_SECONDS)


def listing(limit: int) -> Json:
    conn = assets._db()
    try:
        rows = conn.execute(
            "SELECT * FROM media_assets WHERE kind IN ('reel', 'song', 'music') ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"assets": [public(r) for r in rows], "spent_today_usd": round(assets._spent_today(conn) / 1e6, 4)}
    finally:
        conn.close()


def get(asset_id: int) -> Json:
    conn = assets._db()
    try:
        row = conn.execute(
            "SELECT * FROM media_assets WHERE id = ? AND kind IN ('reel', 'song', 'music')", (asset_id,)
        ).fetchone()
        if row is None:
            raise hand.Refused(f"no reel, song or music {asset_id}")
        return {"asset": public(row)}
    finally:
        conn.close()
