"""Teeny's ingest service, the `ingest` compose service's entry point
(deploy/ingest.Dockerfile, deploy/compose.yaml; docs/hands.md, "remake").

It finds and fetches a short video on YouTube, TikTok or Instagram with a
pinned yt-dlp and studies its shape with ffmpeg, for hands/remake. It holds
no key and mounts nothing of Teeny's: its one optional setting is
INGEST_PROXY_URL (passed to yt-dlp as --proxy; YouTube refuses the box's
address without one). What it allows is plain code in policy.py (the URL
allowlist, yt-dlp's command lines, the bodies and routes) and shots.py (the
cuts and the ffmpeg command lines), which hands/tests/ingest tests as they
run; this file puts them together.

Routes (answers are {"status": 200, "data": ...}, or {"status": N,
"message": ...} with 400 a body or host it will not take, 404, 422 a video
it could not fetch or will not take, 429 busy):

  GET    /ping
  POST   /teeny/v1/probes   {url}           one video's facts
  POST   /teeny/v1/listings {url, max}      an account or playlist page, flat
  POST   /teeny/v1/fetches  {url, ...}      the excerpt, its shots and frames
  GET    /teeny/v1/fetches/{id}/{source.mp4|audio.m4a|shot-N.jpg}
  DELETE /teeny/v1/fetches/{id}

One job at a time (probe, listing or fetch; a second is answered 429). A
fetch lives in a folder of its own under INGEST_DIR/fetches until it is
deleted or the sweep (every five minutes, anything older than
INGEST_REAP_MINUTES, 60) removes it; at start every leftover goes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import policy  # noqa: E402
import shots  # noqa: E402

#: The seconds each tool may take.
PROBE_SECONDS = 90
DOWNLOAD_SECONDS = 420
FFMPEG_SECONDS = 240
SOCKET_TIMEOUT = 30.0
MIME = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".jpg": "image/jpeg"}

Runner = Callable[[list[str], float], tuple[int, str, str]]


class Failure(Exception):
    """A request answered with `status` and `message`."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def run_tool(argv: list[str], timeout: float) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of a command, never through a shell, with
    an environment of its own: a path, a home on the tmpfs and a locale."""
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": "/tmp", "LANG": "C.UTF-8"}
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]} ran out of time ({timeout:.0f} s)"
    except OSError as exc:
        return 127, "", f"{argv[0]} could not start ({type(exc).__name__})"
    return done.returncode, done.stdout, done.stderr


class Service:
    def __init__(self, data_dir: str, proxy: str | None = None, runner: Runner = run_tool,
                 ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.root = Path(data_dir)
        self.fetches = self.root / "fetches"
        self.fetches.mkdir(parents=True, exist_ok=True)
        self.proxy = proxy or None
        if self.proxy and not policy.proxy_ok(self.proxy):
            # Said once, never with the value (it may hold a login).
            print("ingest: INGEST_PROXY_URL is set but is not a proxy address (http, https, socks5 or socks5h); "
                  "it is ignored", file=sys.stderr)
            self.proxy = None
        self.run = runner
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self._busy = threading.Lock()

    # -- helpers -----------------------------------------------------------------------------

    def _taken(self, check: Callable[[Any], Any], body: Any) -> Any:
        try:
            return check(body)
        except policy.RequestError as exc:
            raise Failure(400, str(exc)) from None

    def _hide(self, text: str) -> str:
        return policy.scrub(text, self.proxy)

    def _ytdlp_json(self, argv: list[str]) -> dict:
        code, out, err = self.run(argv, PROBE_SECONDS)
        if code != 0:
            raise Failure(422, f"yt-dlp could not read it: {self._last_line(err)}")
        try:
            info = json.loads(out)
        except ValueError:
            raise Failure(422, "yt-dlp answered something unreadable") from None
        if not isinstance(info, dict):
            raise Failure(422, "yt-dlp answered something unreadable")
        return info

    def _last_line(self, err: str) -> str:
        lines = [line for line in err.splitlines() if line.strip()]
        errors = [line for line in lines if line.startswith("ERROR")]
        return self._hide((errors or lines or ["no reason given"])[-1])

    def _ffmpeg(self, argv: list[str], what: str) -> tuple[str, str]:
        code, out, err = self.run(argv, FFMPEG_SECONDS)
        if code != 0:
            raise Failure(422, f"ffmpeg could not {what}: {self._hide(err.strip().splitlines()[-1] if err.strip() else 'no reason given')}")
        return out, err

    def _probe_file(self, path: Path) -> dict[str, Any]:
        code, out, err = self.run(shots.ffprobe_argv(self.ffprobe, str(path)), PROBE_SECONDS)
        if code != 0:
            raise Failure(422, "ffprobe could not read the clip")
        try:
            return shots.parse_probe(out)
        except ValueError as exc:
            raise Failure(422, f"the clip is unusable: {exc}") from None

    def _job(self):
        if not self._busy.acquire(blocking=False):
            raise Failure(429, "busy with another job; try again shortly")
        return self._busy

    # -- the jobs ----------------------------------------------------------------------------

    def probe(self, body: Any) -> dict[str, Any]:
        checked = self._taken(policy.check_probe, body)
        lock = self._job()
        try:
            return self._probed(checked)
        finally:
            lock.release()

    def _probed(self, checked: dict[str, str]) -> dict[str, Any]:
        info = self._ytdlp_json(policy.probe_argv(checked["url"], self.proxy))
        why = policy.refusal(info)
        if why:
            raise Failure(422, f"not taken: {why}")
        try:
            return policy.probe_fields(info, checked["source"], checked["url"])
        except policy.RequestError as exc:
            raise Failure(422, str(exc)) from None

    def listing(self, body: Any) -> dict[str, Any]:
        checked = self._taken(policy.check_listing, body)
        lock = self._job()
        try:
            info = self._ytdlp_json(policy.listing_argv(checked["url"], checked["max"], self.proxy))
        finally:
            lock.release()
        entries = info.get("entries")
        if not isinstance(entries, list):
            raise Failure(422, "that page is not a list of videos")
        found = [e for e in (policy.listing_entry(entry) for entry in entries) if e]
        return {"entries": found[: checked["max"]]}

    def fetch(self, body: Any) -> dict[str, Any]:
        checked = self._taken(policy.check_fetch, body)
        lock = self._job()
        folder = self.fetches / policy.new_fetch_id()
        try:
            probe = self._probed(checked)
            if checked["start"] >= probe["seconds"] - 1.0:
                raise Failure(422, "start is at or past the end of the video")
            folder.mkdir()
            return self._fetched(folder, checked, probe)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        finally:
            lock.release()

    def _fetched(self, folder: Path, checked: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
        code, _, err = self.run(policy.download_argv(checked["url"], str(folder / "dl.%(ext)s"), checked["max_height"],
                                                     self.proxy), DOWNLOAD_SECONDS)
        downloaded = sorted(folder.glob("dl.*"))
        # 101 is yt-dlp's "stopped at --max-downloads 1": the one file is there.
        if code not in (0, 101) or not downloaded:
            raise Failure(422, f"yt-dlp could not download it: {self._last_line(err)}")
        original = downloaded[0]
        if original.stat().st_size > policy.MAX_DOWNLOAD_BYTES:
            raise Failure(422, f"the download is too big (over {policy.MAX_DOWNLOAD})")
        clip = self._probe_file(original)
        fps = float(shots.fps_text(clip["fps"]))
        wanted = min(checked["max_seconds"], clip["seconds"] - checked["start"])
        if wanted < 1.0:
            raise Failure(422, "start is at or past the end of the video")
        first = folder / "ex.mp4"
        self._ffmpeg(shots.excerpt_argv(self.ffmpeg, str(original), str(first), checked["start"], wanted, fps,
                                        checked["max_height"], clip["audio"]), "cut the excerpt")
        taken = self._probe_file(first)
        seconds = min(taken["seconds"], wanted)
        _, scenes = self._ffmpeg(shots.scene_argv(self.ffmpeg, str(first)), "look for scene cuts")
        try:
            plan = shots.plan(shots.parse_cuts(scenes), fps, seconds, checked["min_shot"], checked["max_shot"],
                              checked["max_shots"])
        except ValueError as exc:
            raise Failure(422, str(exc)) from None
        source = folder / "source.mp4"
        if plan["seconds"] + 0.5 / fps < taken["seconds"]:
            self._ffmpeg(shots.excerpt_argv(self.ffmpeg, str(original), str(source), checked["start"],
                                            plan["seconds"], fps, checked["max_height"], clip["audio"]),
                         "cut the excerpt")
            first.unlink(missing_ok=True)
        else:
            first.replace(source)
        files = ["source.mp4"]
        if clip["audio"]:
            self._ffmpeg(shots.audio_argv(self.ffmpeg, str(source), str(folder / "audio.m4a"), plan["seconds"]),
                         "take the sound")
            files.append("audio.m4a")
        for shot in plan["shots"]:
            name = shots.frame_name(shot["n"])
            self._ffmpeg(shots.frame_argv(self.ffmpeg, str(source), str(folder / name), shots.frame_time(shot)),
                         "take a frame")
            if not (folder / name).is_file() or (folder / name).stat().st_size == 0:
                raise Failure(422, "ffmpeg took no frame")
            shot["frame"] = name
            files.append(name)
        original.unlink(missing_ok=True)
        return {"fetch_id": folder.name, "source": probe,
                "excerpt": {"start": checked["start"], "seconds": plan["seconds"]},
                "aspect": shots.aspect(clip["width"], clip["height"]), "fps": round(fps, 3),
                "shots": plan["shots"], "files": files}

    # -- files and cleanup -------------------------------------------------------------------

    def file_path(self, fetch_id: str, name: str) -> Path:
        if not isinstance(fetch_id, str) or not policy.FETCH_ID.fullmatch(fetch_id) or \
                not isinstance(name, str) or not policy.FILE.fullmatch(name):
            raise Failure(404, "no such file")
        path = self.fetches / fetch_id / name
        if path.is_symlink() or not path.is_file():
            raise Failure(404, "no such file")
        return path

    def delete(self, fetch_id: str) -> dict[str, str]:
        if not isinstance(fetch_id, str) or not policy.FETCH_ID.fullmatch(fetch_id):
            raise Failure(404, "no such fetch")
        folder = self.fetches / fetch_id
        if not folder.is_dir():
            raise Failure(404, "no such fetch")
        shutil.rmtree(folder, ignore_errors=True)
        return {"fetch_id": fetch_id}

    def sweep(self, max_age: float) -> None:
        """Removes fetches older than `max_age` seconds (a negative age: all)."""
        entries = []
        for entry in self.fetches.iterdir():
            try:
                entries.append((entry.name, entry.stat().st_mtime))
            except OSError:
                pass
        for name in policy.stale(entries, time.time(), max_age):
            shutil.rmtree(self.fetches / name, ignore_errors=True)


# --- http -------------------------------------------------------------------------------------


def make_server(service: Service, host: str, port: int, timeout: float = SOCKET_TIMEOUT) -> ThreadingHTTPServer:
    timeout_seconds = timeout
    jobs = {"probe": service.probe, "listing": service.listing, "fetch": service.fetch}

    class Handler(BaseHTTPRequestHandler):
        server_version = "teeny-ingest"
        #: A client that goes quiet mid-request is dropped, not waited on.
        timeout = timeout_seconds
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # the access log would carry the URLs asked for
            pass

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(raw)
            self.close_connection = True

        def _fail(self, failure: Failure) -> None:
            self._send(failure.status, {"status": failure.status, "message": failure.message})

        def _body(self) -> Any:
            length = self.headers.get("content-length")
            if length is None or not length.isdigit() or int(length) > policy.MAX_BODY_BYTES:
                raise Failure(400, f"a body is JSON of at most {policy.MAX_BODY_BYTES} bytes, with a content-length")
            try:
                return json.loads(self.rfile.read(int(length)) or b"null")
            except ValueError:
                raise Failure(400, "the body is not JSON") from None

        def _handle(self) -> None:
            try:
                found = policy.route(self.command, self.path)
                if found is None:
                    raise Failure(404, "not served here")
                name, args = found
                if name == "ping":
                    return self._send(200, {"status": 200, "data": "pong"})
                if name in jobs:
                    return self._send(200, {"status": 200, "data": jobs[name](self._body())})
                if name == "delete":
                    return self._send(200, {"status": 200, "data": service.delete(args["fetch_id"])})
                path = service.file_path(args["fetch_id"], args["name"])
                size = path.stat().st_size
                with open(path, "rb") as fh:
                    self.send_response(200)
                    self.send_header("content-type", MIME[path.suffix])
                    self.send_header("content-length", str(size))
                    self.send_header("connection", "close")
                    self.end_headers()
                    shutil.copyfileobj(fh, self.wfile)
                self.close_connection = True
            except Failure as failure:
                self._fail(failure)
            except (TimeoutError, ConnectionError):  # a client that went quiet or left: nothing to answer
                self.close_connection = True
            except Exception as exc:  # noqa: BLE001 - one job's failure must never stop the service
                print(f"ingest: {type(exc).__name__}", file=sys.stderr)
                self._fail(Failure(500, "the service failed on that request"))

        do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_HEAD = _handle

    return ThreadingHTTPServer((host, port), Handler)


def _reaper(service: Service, max_age: float) -> None:
    while True:
        time.sleep(300)
        try:
            service.sweep(max_age)
        except Exception as exc:  # noqa: BLE001 - a sweep must never stop the service
            print(f"ingest sweep failed: {type(exc).__name__}", file=sys.stderr)


def main() -> None:
    service = Service(os.environ.get("INGEST_DIR", "/data"), proxy=os.environ.get("INGEST_PROXY_URL") or None)
    service.sweep(-1)
    minutes = max(10, int(os.environ.get("INGEST_REAP_MINUTES", "60") or "60"))
    threading.Thread(target=_reaper, args=(service, minutes * 60), name="teeny-reaper", daemon=True).start()
    make_server(service, "0.0.0.0", int(os.environ.get("INGEST_PORT", "8090"))).serve_forever()


if __name__ == "__main__":
    main()
