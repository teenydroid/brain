"""Teeny's front for MoneyPrinterTurbo, the `reel` service's entry point
(deploy/reel.Dockerfile, deploy/compose.yaml; docs/hands.md, "reel").

Upstream's code (vendor/moneyprinter, a submodule pinned to a release tag) is
never edited. This file runs beside it in the image: it imports upstream's
FastAPI app as it is and puts Teeny's rules in front of it. What it allows is
plain code in policy.py (no upstream import), which hands/tests/reel tests.

1. One route of Teeny's own, `POST /teeny/v1/reels`, that takes the job
   hands/reel sends and hands it to upstream's own task queue. It pins the
   operator's rules whatever the body says (Teeny's own stills and clips in
   order, one video, no stock, no generated script, no random or bundled
   song, no upload), refuses a field it does not know, checks what upstream
   would only find out later (the font, the colours), and caps the body,
   the script, the materials and every file's size.
2. Each job's files are copied, off the event loop, out of the read-only
   media mount into a folder of the job's own on the storage volume:
   upstream reads local materials only from storage/local_videos/ and
   writes beside them (a still becomes a clip next to it), a bed only from
   storage/bgm/, and, over HTTP, a track only from the task's own folder.
   Copying keeps each of those upstream checks in force.
3. A path allowlist (policy.Gate): every other route upstream serves
   (script and term generation through a model, stock search, uploads, the
   static file mounts) answers 403.
4. Skit cuts (skitcut.py; hands/video skit): `POST /teeny/v1/cuts` joins
   one to four of Teeny's bought text-to-video shots with ffmpeg, keeping
   each shot's own sound (upstream drops a material's audio), evens the
   loudness, lays an optional bed under it and burns the label into a
   corner for the whole length, then takes frames for the viewer check.
   One cut at a time, in a folder of its own on the storage volume, served
   back (`GET /teeny/v1/cuts/{id}/cut.mp4`, `.../frame-N.jpg`) until
   hands/video deletes it (`DELETE /teeny/v1/cuts/{id}`).
5. Cleanup: a task's staged files go when hands/reel deletes the task; at
   start every leftover of an earlier run is removed (upstream keeps task
   state in memory, so a restart orphans them); and a sweep every
   REEL_REAP_MINUTES (60) removes any task nobody collected, for instance
   after the hand gave up waiting.

Network: the only outbound call this service makes is upstream's Edge TTS
request (speech.platform.bing.com, Microsoft's unofficial endpoint). No
model, stock library, upload service or update check is configured or run.
Compose does not restrict egress today; deploy/README.md says how to.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn  # noqa: E402
from fastapi import APIRouter, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

import policy  # noqa: E402
import skitcut  # noqa: E402
from app.asgi import app  # noqa: E402
from app.config import config  # noqa: E402
from app.controllers.manager.base_manager import TaskQueueFullError  # noqa: E402
from app.controllers.v1 import video as video_api  # noqa: E402
from app.models.schema import TaskVideoRequest  # noqa: E402
from app.services import state as sm  # noqa: E402
from app.services import task as tm  # noqa: E402
from app.utils import utils  # noqa: E402

#: Where compose mounts Teeny's media, read-only: state/media/ as media/,
#: brain/assets/ as assets/. A job names its files relative to this.
TEENY = os.environ.get("TEENY_MEDIA", "/teeny")
LOCAL_VIDEOS = utils.storage_dir("local_videos", create=True)
BEDS = utils.storage_dir("bgm", create=True)
TASKS = utils.task_dir()
FONTS = utils.font_dir()
REAP_SECONDS = max(10, int(os.environ.get("REEL_REAP_MINUTES", "60"))) * 60
CUTS = utils.storage_dir("cuts", create=True)
#: One cut at a time: ffmpeg takes the service's cores.
CUTTING = threading.Lock()


def _refuse(status: int, message: str) -> JSONResponse:
    return JSONResponse({"status": status, "message": message}, status_code=status)


def _staged(task_id: str) -> str:
    return os.path.join(LOCAL_VIDEOS, f"teeny-{task_id}")


def _clean(task_id: str) -> None:
    """A task's staged copies and its bed."""
    shutil.rmtree(_staged(task_id), ignore_errors=True)
    for name in os.listdir(BEDS):
        if name.startswith(f"teeny-{task_id}-"):
            try:
                os.remove(os.path.join(BEDS, name))
            except OSError:
                pass


def _stage(task_id: str, checked: dict) -> dict:
    """Resolves and copies the job's files into the task's own folders; the
    job's fields with the copies' paths. Runs in a worker thread."""
    materials = [policy.resolve_source(TEENY, path) for path in checked["materials"]]
    bed = policy.resolve_source(TEENY, checked["bed"], policy.AUDIO_MAX_BYTES) if checked["bed"] else None
    track = policy.resolve_source(TEENY, checked["audio"], policy.AUDIO_MAX_BYTES) if checked["audio"] else None
    fields = dict(checked["fields"])
    staged = _staged(task_id)
    os.makedirs(staged)
    fields["video_materials"] = []
    for index, source in enumerate(materials):
        copy = os.path.join(staged, f"{index:02d}{os.path.splitext(source)[1].lower()}")
        shutil.copyfile(source, copy)
        fields["video_materials"].append({"provider": "local", "url": copy})
    if bed:
        name = f"teeny-{task_id}-bed{os.path.splitext(bed)[1].lower()}"
        shutil.copyfile(bed, os.path.join(BEDS, name))
        fields["bgm_file"] = name
    if track:
        # Upstream's HTTP API takes custom audio only from the task's own
        # folder; the track is put there, and that check stays in force.
        name = f"teeny-audio{os.path.splitext(track)[1].lower()}"
        shutil.copyfile(track, os.path.join(utils.task_dir(task_id), name))
        fields["custom_audio_file"] = name
    return fields


def _forget(task_id: str) -> None:
    sm.state.delete_task(task_id)
    shutil.rmtree(os.path.join(TASKS, task_id), ignore_errors=True)
    _clean(task_id)


router = APIRouter()


@router.post("/teeny/v1/reels")
async def create_reel(request: Request):
    length = request.headers.get("content-length")
    if length is None or not length.isdigit() or int(length) > policy.MAX_BODY_BYTES:
        return _refuse(413, f"a job is at most {policy.MAX_BODY_BYTES} bytes, with a content-length")
    raw = await request.body()
    if len(raw) > policy.MAX_BODY_BYTES:
        return _refuse(413, f"a job is at most {policy.MAX_BODY_BYTES} bytes")
    try:
        checked = policy.check_job(json.loads(raw), FONTS)
    except ValueError as exc:  # JobError, or a body that is not JSON
        return _refuse(400, str(exc) if isinstance(exc, policy.JobError) else "the body is not JSON")

    task_id = utils.get_uuid()
    try:
        fields = await run_in_threadpool(_stage, task_id, checked)
        params = TaskVideoRequest(**fields)
    except policy.JobError as exc:
        await run_in_threadpool(_forget, task_id)
        return _refuse(400, str(exc))
    except ValidationError as exc:
        await run_in_threadpool(_forget, task_id)
        return _refuse(400, f"not a valid job: {policy.errors_text(exc.errors())}")
    except BaseException:
        await run_in_threadpool(_forget, task_id)
        raise

    sm.state.update_task(task_id)
    try:
        video_api.task_manager.add_task(tm.start, task_id=task_id, params=params, stop_at="video")
    except TaskQueueFullError as exc:
        await run_in_threadpool(_forget, task_id)
        return _refuse(429, str(exc))
    except BaseException:
        await run_in_threadpool(_forget, task_id)
        raise
    return {"status": 200, "data": {"task_id": task_id}}


def _cut(cut_id: str, checked: dict) -> dict:
    """One skit's cut, in its own folder. Runs in a worker thread."""
    sources = [policy.resolve_source(TEENY, path) for path in checked["clips"]]
    bed = policy.resolve_source(TEENY, checked["bed"], policy.AUDIO_MAX_BYTES) if checked["bed"] else None
    if bed and os.path.splitext(bed)[1].lower() not in policy.AUDIO_SUFFIXES:
        raise skitcut.CutError("a bed is an mp3, wav, m4a, aac, flac, ogg or opus file")
    # A remake's sound: the source's audio, really under media/remake/ once
    # symlinks are resolved.
    sound = policy.resolve_source(TEENY, checked["sound"], policy.AUDIO_MAX_BYTES) if checked["sound"] else None
    if sound and (not skitcut.under(sound, os.path.join(TEENY, "media", "remake"))
                  or os.path.splitext(sound)[1].lower() not in policy.AUDIO_SUFFIXES):
        raise policy.JobError("a sound is an audio file under media/remake/")
    folder = os.path.join(CUTS, cut_id)
    os.makedirs(folder)
    with CUTTING:
        return skitcut.render(sources, checked["label"], checked["aspect"], folder, os.path.join(FONTS, skitcut.FONT),
                              bed=bed, bed_volume=checked["bed_volume"], trims=checked["trims"], sound=sound)


@router.post("/teeny/v1/cuts")
async def create_cut(request: Request):
    length = request.headers.get("content-length")
    if length is None or not length.isdigit() or int(length) > policy.MAX_BODY_BYTES:
        return _refuse(413, f"a cut is at most {policy.MAX_BODY_BYTES} bytes, with a content-length")
    raw = await request.body()
    try:
        checked = skitcut.check_cut(json.loads(raw))
    except ValueError as exc:  # CutError, or a body that is not JSON
        return _refuse(400, str(exc) if isinstance(exc, skitcut.CutError) else "the body is not JSON")
    cut_id = utils.get_uuid()
    try:
        made = await run_in_threadpool(_cut, cut_id, checked)
    except (policy.JobError, skitcut.CutError) as exc:
        shutil.rmtree(os.path.join(CUTS, cut_id), ignore_errors=True)
        return _refuse(400 if isinstance(exc, policy.JobError) else 422, str(exc))
    except BaseException:
        shutil.rmtree(os.path.join(CUTS, cut_id), ignore_errors=True)
        raise
    return {"status": 200, "data": {"cut_id": cut_id, **made}}


@router.get("/teeny/v1/cuts/{cut_id}/{name}")
async def cut_file(cut_id: str, name: str):
    if not policy.TASK.fullmatch(cut_id) or not skitcut.FILE.fullmatch(name):
        return _refuse(404, "no such file")
    path = os.path.join(CUTS, cut_id, name)
    if not os.path.isfile(path):
        return _refuse(404, "no such file")
    return FileResponse(path, media_type="video/mp4" if name.endswith(".mp4") else "image/jpeg")


@router.delete("/teeny/v1/cuts/{cut_id}")
async def cut_delete(cut_id: str):
    if not policy.TASK.fullmatch(cut_id):
        return _refuse(404, "no such cut")
    await run_in_threadpool(shutil.rmtree, os.path.join(CUTS, cut_id), True)
    return {"status": 200, "data": {"cut_id": cut_id}}


# Upstream mounts static files at "/" last, which would swallow a route
# added after it, so Teeny's goes first.
app.router.routes[0:0] = router.routes


def _entries(folder: str, prefix: str = "") -> list[tuple[str, float]]:
    out = []
    for name in os.listdir(folder):
        if name.startswith(prefix):
            try:
                out.append((name, os.path.getmtime(os.path.join(folder, name))))
            except OSError:
                pass
    return out


def sweep(max_age: float) -> None:
    """Removes tasks nobody collected and staged files nobody owns, older
    than `max_age` seconds; a task still running is left alone."""
    now = time.time()
    for name in policy.stale(_entries(TASKS), now, max_age):
        task = sm.state.get_task(name)
        if task and tm.is_task_busy(task):
            continue
        _forget(name)
    for name in policy.stale(_entries(LOCAL_VIDEOS, "teeny-"), now, max_age):
        if not (sm.state.get_task(name[len("teeny-"):]) or {}):
            shutil.rmtree(os.path.join(LOCAL_VIDEOS, name), ignore_errors=True)
    for name in policy.stale(_entries(CUTS), now, max_age):
        shutil.rmtree(os.path.join(CUTS, name), ignore_errors=True)
    for name in policy.stale(_entries(BEDS, "teeny-"), now, max_age):
        try:
            os.remove(os.path.join(BEDS, name))
        except OSError:
            pass


def _reaper() -> None:
    while True:
        time.sleep(300)
        try:
            sweep(REAP_SECONDS)
        except Exception as exc:  # noqa: BLE001 - a sweep must never stop the service
            print(f"reel sweep failed: {type(exc).__name__}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    # Upstream keeps task state in memory: whatever is on the volume from an
    # earlier run belongs to no task now.
    sweep(-1)
    threading.Thread(target=_reaper, name="teeny-reaper", daemon=True).start()
    uvicorn.run(policy.Gate(app, _clean), host=config.listen_host, port=config.listen_port, log_level="warning")
