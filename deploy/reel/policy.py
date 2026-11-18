"""What Teeny's reel front (deploy/reel/serve.py) allows, as plain code with
no import of upstream's packages, so hands/tests/reel tests it as it runs.

- `is_open(method, path)`: the routes the front lets through to upstream.
- `resolve_source(root, path)`: a job's file under the read-only media mount.
- `check_job(body, fonts_dir)`: the job, its fields filtered and the rules
  pinned, or a `JobError` the front answers 400 with.
- `Gate`: the ASGI middleware that applies `is_open` and calls back after a
  task's deletion, so its staged files go with it.
- `stale(entries, now, max_age)`: which leftovers the sweep removes.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Awaitable, Callable, Iterable

#: Whatever the body says, these hold.
PINNED = {
    "video_source": "local",
    "video_concat_mode": "sequential",
    "video_count": 1,
    "match_materials_to_script": False,
    "video_terms": None,
    "video_music_prompt": "",
    "sonilo_bgm_prompt": "",
    "video_script_prompt": "",
    "custom_system_prompt": "",
    "paragraph_number": 1,
}

#: The fields a job may set (all of them VideoParams fields upstream), plus
#: `audio`, the song's track, which only the front reads.
ALLOWED = {
    "video_subject",
    "video_script",
    "video_aspect",
    "video_fit_mode",
    "image_motion",
    "video_concat_mode",
    "video_transition_mode",
    "video_clip_duration",
    "video_count",
    "video_source",
    "video_materials",
    "match_materials_to_script",
    "video_language",
    "voice_name",
    "voice_volume",
    "voice_rate",
    "bgm_type",
    "bgm_file",
    "bgm_volume",
    "subtitle_enabled",
    "subtitle_position",
    "subtitle_display_mode",
    "subtitle_animation",
    "custom_position",
    "font_name",
    "text_fore_color",
    "text_background_color",
    "rounded_subtitle_background",
    "font_size",
    "stroke_color",
    "stroke_width",
    "n_threads",
    "audio",
}

MAX_BODY_BYTES = 64 * 1024
MAX_SCRIPT = 5000
MAX_SUBJECT = 500
MAX_MATERIALS = 12
FONT_SIZES = (20, 200)
#: A still or a clip, and a bed or a track, at most this big before a copy.
MATERIAL_MAX_BYTES = 200 * 1024 * 1024
AUDIO_MAX_BYTES = 30 * 1024 * 1024
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
#: Only Edge voices: their word timing is exact, and they need no key.
EDGE_VOICE = re.compile(r"[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural(-Female|-Male)?")
COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
TASK = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class JobError(ValueError):
    """A job the front will not take: answered 400 with this message."""


# --- routes -------------------------------------------------------------------------------


def is_open(method: str, path: str) -> bool:
    """The only routes that reach upstream: the job route, a task's status
    and deletion, its one render, and the health check. Everything else,
    trailing slashes, doubled or encoded slashes and dot segments included,
    is shut (the path is matched as it arrives, before any normalising)."""
    if method == "POST" and path == "/teeny/v1/reels":
        return True
    task = re.fullmatch(r"/api/v1/tasks/([0-9a-f-]{36})", path)
    if method in ("GET", "DELETE") and task and TASK.fullmatch(task.group(1)):
        return True
    render = re.fullmatch(r"/api/v1/download/([0-9a-f-]{36})/final-1\.mp4", path)
    if method in ("GET", "HEAD") and render and TASK.fullmatch(render.group(1)):
        return True
    return method == "GET" and path == "/ping"


def _deleted_task(scope: dict, status: int) -> str | None:
    if scope.get("method") != "DELETE" or status != 200:
        return None
    match = re.fullmatch(r"/api/v1/tasks/([0-9a-f-]{36})", scope.get("path", ""))
    return match.group(1) if match and TASK.fullmatch(match.group(1)) else None


class Gate:
    """ASGI middleware: a request to a route `is_open` refuses is answered
    403 without reaching the app; after a task's successful deletion,
    `on_delete(task_id)` runs (the front removes the task's staged files)."""

    def __init__(self, app: Callable[..., Awaitable[None]], on_delete: Callable[[str], None] | None = None) -> None:
        self.app = app
        self.on_delete = on_delete

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        if not is_open(scope.get("method", ""), scope.get("path", "")):
            body = json.dumps({"status": 403, "message": "not served here"}).encode()
            await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        status = {"code": 0}

        async def watch(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        await self.app(scope, receive, watch)
        task = _deleted_task(scope, status["code"])
        if task and self.on_delete:
            self.on_delete(task)


# --- files ----------------------------------------------------------------------------------


def resolve_source(root: str, path: Any, limit: int = MATERIAL_MAX_BYTES) -> str:
    """The real file behind a job's path: under `media/` or `assets/` of the
    read-only mount at `root`, symlinks and `..` resolved first (as upstream's
    file_security does), a regular file, at most `limit` bytes."""
    if not isinstance(path, str) or not path.startswith(("media/", "assets/")) or "\x00" in path:
        raise JobError("a file is a path under media/ or assets/")
    top = path.split("/", 1)[0]
    base = os.path.realpath(os.path.join(root, top))
    resolved = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([base, resolved]) != base:
        raise JobError("a file is a path under media/ or assets/")
    if not os.path.isfile(resolved):
        raise JobError("file does not exist")
    if os.path.getsize(resolved) > limit:
        raise JobError(f"a file is at most {limit // (1024 * 1024)} MB")
    return resolved


def stale(entries: Iterable[tuple[str, float]], now: float, max_age: float) -> list[str]:
    """Of (name, mtime) pairs, the names older than `max_age` seconds."""
    return [name for name, mtime in entries if now - mtime > max_age]


# --- the job ----------------------------------------------------------------------------------


def _color(value: Any, name: str) -> None:
    if not isinstance(value, str) or not COLOR.fullmatch(value):
        raise JobError(f"{name} is a colour like #FFE14D")


def check_job(body: Any, fonts_dir: str) -> dict:
    """The job with its fields checked and the rules pinned. Raises JobError
    for anything the front refuses; returns {"fields", "materials", "bed",
    "audio"} where the last three are the job's own (unresolved) paths."""
    if not isinstance(body, dict):
        raise JobError("the body is a JSON object")
    unknown = sorted(set(body) - ALLOWED)
    if unknown:
        raise JobError(f"not taken here: {', '.join(unknown)}")
    for key, value in PINNED.items():
        if key in body and body[key] != value:
            raise JobError(f"{key} is always {value!r}")
    bgm_type = body.get("bgm_type", "")
    if bgm_type not in ("", "custom"):
        raise JobError("bgm_type is '' (no bed) or 'custom' (a named file)")
    if bgm_type == "custom" and not body.get("bgm_file"):
        raise JobError("a custom bed names its bgm_file")
    if bgm_type == "" and body.get("bgm_file"):
        raise JobError("bgm_file needs bgm_type 'custom'")
    voice = body.get("voice_name", "")
    if voice and not (isinstance(voice, str) and EDGE_VOICE.fullmatch(voice)):
        raise JobError("voice_name is an Edge voice")
    script = body.get("video_script", "")
    if not isinstance(script, str) or not script.strip() or len(script) > MAX_SCRIPT:
        raise JobError(f"video_script is 1 to {MAX_SCRIPT} characters")
    subject = body.get("video_subject", "")
    if not isinstance(subject, str) or len(subject) > MAX_SUBJECT:
        raise JobError(f"video_subject is at most {MAX_SUBJECT} characters")
    size = body.get("font_size", 60)
    if isinstance(size, bool) or not isinstance(size, int) or not FONT_SIZES[0] <= size <= FONT_SIZES[1]:
        raise JobError(f"font_size is {FONT_SIZES[0]} to {FONT_SIZES[1]}")
    font = body.get("font_name", "")
    if font:
        if not isinstance(font, str) or "/" in font or "\\" in font or font.startswith("."):
            raise JobError("font_name is a file name in upstream's resource/fonts")
        if not os.path.isfile(os.path.join(fonts_dir, font)):
            raise JobError(f"no font {font} in resource/fonts")
    for name in ("text_fore_color", "stroke_color"):
        if name in body:
            _color(body[name], name)
    background = body.get("text_background_color", False)
    if background is not False and background is not True:
        _color(background, "text_background_color")
    materials = body.get("video_materials") or []
    if not isinstance(materials, list) or not 1 <= len(materials) <= MAX_MATERIALS:
        raise JobError(f"video_materials lists 1 to {MAX_MATERIALS} of Teeny's own stills and clips")
    paths = []
    for item in materials:
        if not isinstance(item, dict) or item.get("provider", "local") != "local" or set(item) - {"provider", "url"}:
            raise JobError("a material is {provider: 'local', url}")
        if not isinstance(item.get("url"), str):
            raise JobError("a material is {provider: 'local', url}")
        paths.append(item["url"])
    for name in ("bgm_file", "audio"):
        value = body.get(name)
        if value and (not isinstance(value, str) or os.path.splitext(value)[1].lower() not in AUDIO_SUFFIXES):
            raise JobError("a bed or a track is an mp3, wav, m4a, aac, flac, ogg or opus file")
    fields = {k: v for k, v in body.items() if k != "audio"}
    fields.update(PINNED)
    fields["bgm_type"] = bgm_type
    return {"fields": fields, "materials": paths, "bed": body.get("bgm_file") or None, "audio": body.get("audio") or None}


def errors_text(errors: Iterable[dict]) -> str:
    """Pydantic's validation errors as plain text (their `ctx` may hold
    objects JSON cannot carry)."""
    parts = []
    for error in list(errors)[:3]:
        loc = ".".join(str(p) for p in error.get("loc", ()))
        parts.append(f"{loc}: {error.get('msg', 'invalid')}")
    return "; ".join(parts) or "invalid"
