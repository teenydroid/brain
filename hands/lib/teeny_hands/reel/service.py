"""The `reel` service over HTTP: MoneyPrinterTurbo behind Teeny's own front
(deploy/reel/serve.py), on the compose network at REEL_URL.

These calls, and the front answers 403 to anything else:

  POST   /teeny/v1/reels                        a job; its task id
  GET    /api/v1/tasks/{id}                     the task's state
  GET    /api/v1/download/{id}/final-1.mp4      the render
  DELETE /api/v1/tasks/{id}                     the task and its files
  GET    /ping                                  the service is up
  POST   /teeny/v1/cuts                         a skit's cut (deploy/reel/skitcut.py): its id,
                                                length, frames and label, once it is made
  GET    /teeny/v1/cuts/{id}/cut.mp4            the cut
  GET    /teeny/v1/cuts/{id}/frame-N.jpg        one of its frames
  DELETE /teeny/v1/cuts/{id}                    the cut and its frames

No key: the service binds the compose network only. Redirects are not
followed, and an error says the status and the service's short message.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from .. import hand

DEFAULT_URL = "http://reel:8080"
_TASK = re.compile(r"[0-9a-f-]{36}")
_MESSAGE_CHARS = 300

#: Upstream's task states (app/models/const.py).
STATE_FAILED = -1
STATE_COMPLETE = 1
STATE_PROCESSING = 4


class ReelError(hand.HandError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401 - urllib's hook
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _task(task_id: str) -> str:
    if not isinstance(task_id, str) or not _TASK.fullmatch(task_id):
        raise ReelError("the reel service answered a task id that is not one")
    return task_id


class Reel:
    def __init__(self, base: str | None = None) -> None:
        self._base = (base or hand.setting("REEL_URL") or DEFAULT_URL).rstrip("/")

    def _open(self, method: str, path: str, body: Any = None, timeout: float = 30.0, limit: int = 1 << 20) -> bytes:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"accept": "application/json", "user-agent": "teeny-hands-reel"}
        if data is not None:
            headers["content-type"] = "application/json"
        request = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                raw = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            try:
                parsed = json.loads(exc.read() or b"{}")
                message = parsed.get("message") if isinstance(parsed, dict) else None
            except (ValueError, OSError):
                message = None
            text = f"the reel service answered {exc.code}"
            raise ReelError(f"{text}: {str(message)[:_MESSAGE_CHARS]}" if message else text, exc.code) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ReelError(f"could not reach the reel service ({type(getattr(exc, 'reason', exc)).__name__})") from None
        if len(raw) > limit:
            raise ReelError(f"the reel service sent more than {limit // (1024 * 1024)} MB")
        return raw

    def _json(self, method: str, path: str, body: Any = None, timeout: float = 30.0) -> dict:
        raw = self._open(method, path, body, timeout=timeout)
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            raise ReelError("the reel service sent something unreadable") from None
        if not isinstance(parsed, dict):
            raise ReelError("the reel service sent something unreadable")
        return parsed

    def submit(self, job: dict) -> str:
        answer = self._json("POST", "/teeny/v1/reels", job)
        return _task((answer.get("data") or {}).get("task_id"))

    def status(self, task_id: str) -> dict:
        data = self._json("GET", f"/api/v1/tasks/{_task(task_id)}").get("data")
        if not isinstance(data, dict):
            raise ReelError("the reel service sent a task with no state")
        return data

    def download(self, task_id: str, limit: int) -> bytes:
        return self._open("GET", f"/api/v1/download/{_task(task_id)}/final-1.mp4", timeout=120.0, limit=limit)

    def delete(self, task_id: str) -> None:
        self._open("DELETE", f"/api/v1/tasks/{_task(task_id)}")

    def ping(self) -> None:
        """Nothing when the service answers; ReelError when it does not."""
        self._open("GET", "/ping", timeout=10.0)

    def cut(self, job: dict, timeout: float = 600.0) -> dict:
        """A skit's cut, made before the answer comes: {cut_id, seconds,
        shots, frames, label, labelled, audio}."""
        data = self._json("POST", "/teeny/v1/cuts", job, timeout=timeout).get("data")
        if not isinstance(data, dict):
            raise ReelError("the reel service answered a cut with nothing in it")
        _task(data.get("cut_id"))
        return data

    def cut_file(self, cut_id: str, name: str, limit: int) -> bytes:
        if not re.fullmatch(r"cut\.mp4|frame-[0-9]\.jpg", name or ""):
            raise ReelError("the reel service named a file a cut does not have")
        return self._open("GET", f"/teeny/v1/cuts/{_task(cut_id)}/{name}", timeout=120.0, limit=limit)

    def cut_delete(self, cut_id: str) -> None:
        self._open("DELETE", f"/teeny/v1/cuts/{_task(cut_id)}")
