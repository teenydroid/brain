"""Shared by the hands/guard tests: a scratch TEENY_ROOT and a fake core.

The judge is a model call through the core's JSON-RPC, so the gate and judge
tests point the hand at a fake core on 127.0.0.1 that answers every chat with
one scripted reply and remembers what it was asked. Nothing here talks to a
real model.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
LIB = HERE.parent.parent / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import hand  # noqa: E402

# The gate logs what it holds and cuts, as the Rust traced it. The tests that
# care assert it with assertLogs; the rest keep the run's output clean.
logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())


class Scratch(unittest.TestCase):
    """A scratch TEENY_ROOT per test, with the env cache cleared. The judge
    is off unless a test turns it on, as `SafetyGateOptions::default()` was."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "OPENHUMAN_CORE_URL", "OPENHUMAN_CORE_TOKEN", "TEENY_NOW"):
            os.environ.pop(key, None)
        self.secrets("SAFETY_JUDGE=0\n")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def secrets(self, text: str) -> None:
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None


class FakeCore:
    """Answers every RPC with the next scripted reply (the last one repeats),
    and keeps each request's method and params."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.calls.append(body)
                reply = fake.replies.pop(0) if len(fake.replies) > 1 else fake.replies[0]
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def messages(self) -> list[str]:
        return [call["params"]["message"] for call in self.calls]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def result(value: Any) -> dict[str, Any]:
    """A JSON-RPC success carrying `value`."""
    return {"jsonrpc": "2.0", "id": 1, "result": value}


def error(message: str) -> dict[str, Any]:
    """A JSON-RPC error carrying `message`, as the core reports a failed call."""
    return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": message}}


def verdict(verdict: str, category: str | None = None, reason: str = "") -> dict[str, Any]:
    """A core reply whose text is the judge's JSON answer."""
    answer: dict[str, Any] = {"reason": reason, "verdict": verdict}
    if category is not None:
        answer["category"] = category
    return result({"response": json.dumps(answer)})


class WithCore(Scratch):
    """A scratch root wired to a fake core, with the judge on."""

    def core(self, *replies: Any) -> FakeCore:
        fake = FakeCore(*replies)
        self.addCleanup(fake.close)
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text(fake.url + "\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        self.secrets("SAFETY_JUDGE=1\n")
        return fake
