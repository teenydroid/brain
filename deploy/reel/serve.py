"""Teeny's front for MoneyPrinterTurbo, the `reel` service's entry point
(deploy/reel.Dockerfile, deploy/compose.yaml; docs/hands.md, "reel").

Upstream's code (vendor/moneyprinter, a submodule pinned to a release tag) is
never edited. This file runs beside it in the image: it imports upstream's
FastAPI app as it is and puts two things in front of it.

1. One route of Teeny's own, `POST /teeny/v1/reels`, that takes the job
   hands/reel sends and hands it to upstream's own task queue. It pins the
   operator's rules whatever the body says (Teeny's own stills and clips in
   order, one video, no stock, no generated script, no random or bundled
   song, no upload), refuses a field it does not know, and, for a song, copies
   the track from the read-only media mount into the job's own task folder.
   Upstream's HTTP API takes `custom_audio_file` only from inside that folder,
   which a caller cannot write to before the task exists; copying it in here
   keeps upstream's own check (task-local audio only) in force rather than
   switching it off.
2. A path allowlist: every other route upstream serves (script and term
   generation through a model, stock search, uploads, the music and material
   upload endpoints, the static file mounts) answers 403. What stays open is
   what hands/reel uses: the task status, its download, its deletion, and the
   health check.

Nothing here reaches the network. The voice (Edge TTS) is upstream's own call.
"""

from __future__ import annotations

import os
import re
import shutil

import uvicorn
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.asgi import app
from app.config import config
from app.controllers.manager.base_manager import TaskQueueFullError
from app.controllers.v1 import video as video_api
from app.models.schema import TaskVideoRequest
from app.services import state as sm
from app.services import task as tm
from app.utils import file_security, utils

#: Where compose mounts Teeny's media, read-only: state/media/ as media/,
#: brain/assets/ as assets/. Upstream reads local materials only from here.
MATERIALS = utils.storage_dir("local_videos", create=True)

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
#: `audio`, the song's track, which only this front reads.
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

AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
#: Only Edge voices: their word timing is exact, and they need no key.
EDGE_VOICE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural(-Female|-Male)?$")


def _refuse(status: int, message: str) -> JSONResponse:
    return JSONResponse({"status": status, "message": message}, status_code=status)


def _material(path: object) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("a material is a path under media/ or assets/")
    return file_security.resolve_path_within_directory(MATERIALS, path)


router = APIRouter()


@router.post("/teeny/v1/reels")
async def create_reel(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return _refuse(400, "the body is not JSON")
    if not isinstance(body, dict):
        return _refuse(400, "the body is a JSON object")
    unknown = sorted(set(body) - ALLOWED)
    if unknown:
        return _refuse(400, f"not taken here: {', '.join(unknown)}")
    for key, value in PINNED.items():
        if key in body and body[key] != value:
            return _refuse(400, f"{key} is always {value!r}")
    bgm_type = body.get("bgm_type", "")
    if bgm_type not in ("", "custom"):
        return _refuse(400, "bgm_type is '' (no bed) or 'custom' (a named file)")
    if bgm_type == "custom" and not body.get("bgm_file"):
        return _refuse(400, "a custom bed names its bgm_file")
    if bgm_type == "" and body.get("bgm_file"):
        return _refuse(400, "bgm_file needs bgm_type 'custom'")
    voice = body.get("voice_name", "")
    if voice and not EDGE_VOICE.match(str(voice)):
        return _refuse(400, "voice_name is an Edge voice")
    materials = body.get("video_materials") or []
    if not isinstance(materials, list) or not materials:
        return _refuse(400, "video_materials lists Teeny's own stills and clips")
    try:
        for item in materials:
            if not isinstance(item, dict) or item.get("provider", "local") != "local" or set(item) - {"provider", "url", "duration"}:
                raise ValueError("a material is {provider: 'local', url}")
            _material(item.get("url"))
        if body.get("bgm_file"):
            # Upstream resolves a bed inside storage/bgm/, where the same
            # media is mounted read-only as media/.
            file_security.resolve_path_within_directory(utils.storage_dir("bgm", create=True), body["bgm_file"])
    except ValueError as exc:
        return _refuse(400, str(exc))

    audio = body.pop("audio", None)
    fields = {**body, **PINNED}
    fields["video_materials"] = [{"provider": "local", "url": m["url"]} for m in materials]
    try:
        params = TaskVideoRequest(**fields)
    except ValidationError as exc:
        return _refuse(400, f"not a valid job: {exc.errors()[:3]}")

    task_id = utils.get_uuid()
    if audio:
        try:
            source = _material(audio)
        except ValueError as exc:
            return _refuse(400, f"audio: {exc}")
        suffix = os.path.splitext(source)[1].lower()
        if suffix not in AUDIO_SUFFIXES:
            return _refuse(400, "audio is an mp3, wav, m4a, aac, flac, ogg or opus file")
        name = f"teeny-audio{suffix}"
        shutil.copyfile(source, os.path.join(utils.task_dir(task_id), name))
        params.custom_audio_file = name

    sm.state.update_task(task_id)
    try:
        video_api.task_manager.add_task(tm.start, task_id=task_id, params=params, stop_at="video")
    except TaskQueueFullError as exc:
        sm.state.delete_task(task_id)
        return _refuse(429, str(exc))
    except Exception:
        sm.state.delete_task(task_id)
        raise
    return {"status": 200, "data": {"task_id": task_id}}


# Upstream mounts static files at "/" last, which would swallow a route
# added after it, so Teeny's goes first.
app.router.routes[0:0] = router.routes


def _open(method: str, path: str) -> bool:
    if method == "POST" and path == "/teeny/v1/reels":
        return True
    if method in ("GET", "DELETE") and re.fullmatch(r"/api/v1/tasks/[0-9a-f-]{36}", path):
        return True
    if method in ("GET", "HEAD") and re.fullmatch(r"/api/v1/download/[0-9a-f-]{36}/final-1\.mp4", path):
        return True
    return method == "GET" and path == "/ping"


@app.middleware("http")
async def only_what_teeny_uses(request: Request, call_next):
    if not _open(request.method, request.url.path):
        return _refuse(403, "not served here")
    return await call_next(request)


if __name__ == "__main__":
    uvicorn.run(app, host=config.listen_host, port=config.listen_port, log_level="warning")
