"""Shared by the hands/rent tests: a scratch TEENY_ROOT and a fake RentAHuman
on 127.0.0.1 that answers MCP `tools/call` over JSON-RPC at /api/mcp, the
way the real one does, reached through `RENTAHUMAN_API_BASE`. Nothing here
talks to the real RentAHuman. The guard is the real one, with the model
judge off."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import unittest
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from teeny_hands import hand

logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

#: Distinctive, so a leak into any output or file is found.
KEY = "rah_FAKEkeyFAKEkeyFAKEkeyFAKE0000"
T0 = 1_795_420_800.0  # 2026-11-23T00:00:00Z
HOUR = 3600.0

Answer = Any  # a reply, or a callable(arguments) -> reply


@dataclass
class Call:
    tool: str
    arguments: dict[str, Any]
    headers: dict[str, str]


def error(code: str, message: str = "no", retry_safe: bool | None = True, **details: Any) -> dict[str, Any]:
    """A tool refusal as RentAHuman sends it (isError with an error object)."""
    body: dict[str, Any] = {"code": code, "message": message}
    if retry_safe is not None:
        body["isRetrySafe"] = retry_safe
    if details:
        body["details"] = details
    return {"__error__": body}


class Crash(Exception):
    """Raised by a handler: the fake answers 502, as a gateway would."""


@dataclass
class FakeRah:
    answers: dict[str, list[Answer]] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # evidence files
                data = fake.files.get(self.path)
                self.send_response(200 if data is not None else 404)
                self.send_header("content-length", str(len(data or b"")))
                self.end_headers()
                self.wfile.write(data or b"")

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                request = json.loads(self.rfile.read(length) or b"{}")
                headers = {k.lower(): v for k, v in self.headers.items()}
                if self.path != "/api/mcp" or headers.get("x-api-key") != KEY:
                    return self._send(200, {"jsonrpc": "2.0", "id": request.get("id"),
                                            "error": {"code": -32001, "message": "Authentication required"}})
                params = request.get("params") or {}
                call = Call(params.get("name"), params.get("arguments") or {}, headers)
                with fake._lock:
                    fake.calls.append(call)
                    queue = fake.answers.get(call.tool) or []
                    answer = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else None)
                try:
                    reply = answer(call.arguments) if callable(answer) else answer
                except Crash:
                    return self._send(502, {"error": "bad gateway"})
                if reply is None:
                    reply = {"__error__": {"code": "not_routed", "message": call.tool, "isRetrySafe": True}}
                if isinstance(reply, dict) and "__error__" in reply:
                    result = {"isError": True, "content": [{"type": "text", "text": json.dumps({"error": reply["__error__"]})}]}
                else:
                    result = {"content": [{"type": "text", "text": json.dumps(reply)}]}
                self._send(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": result})

            def _send(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def on(self, tool: str, *answers: Answer) -> None:
        """TOOL answers ANSWERS in turn; the last one again and again."""
        with self._lock:
            self.answers[tool] = list(answers)

    def made(self, tool: str) -> list[Call]:
        with self._lock:
            return [c for c in self.calls if c.tool == tool]


def preview(total: int, pay: int = 2000) -> dict[str, Any]:
    return {"dryRun": {"preview": {"status": "draft"}, "cost": {
        "spots": 1, "perSpot": {"pay": {"amount": pay, "currency": "usd"}, "total": {"amount": total, "currency": "usd"}},
        "total": {"amount": total, "currency": "usd"}}}}


def created(bounty: str = "b1", status: str = "open", funding: str = "funded", **more: Any) -> dict[str, Any]:
    return {"id": bounty, "status": status, "url": f"https://rentahuman.ai/bounties/{bounty}",
            "funding": {"status": funding}, **more}


def create_bounty(total: int, bounty: str = "b1") -> Callable[[dict], Any]:
    """create_bounty that prices a dry run and posts a real one."""
    return lambda args: preview(total) if args.get("dryRun") else created(bounty)


#: A post body that passes every check.
GOOD = {
    "title": "Hold a sign that says I am a robot's assistant",
    "brief": "Stand at a busy public square for ten minutes holding a sign that says I am a robot's assistant.",
    "done": "Ten minutes with the sign, filmed, people walking past.",
    "price_usd": 18,
    "city": "Austin", "state": "TX", "country": "US",
}


class Scratch(unittest.TestCase):
    settings: dict[str, str] = {}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "TEENY_NOW", "TEENY_HANDS_DEBUG"):
            os.environ.pop(key, None)
        self.at(T0)
        self.rah = FakeRah()
        self.addCleanup(self.rah.close)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {
            "RENTAHUMAN_API_KEY": KEY,
            "RENTAHUMAN_API_BASE": self.rah.base,
            "DRY_RUN": "0",
            "SAFETY_JUDGE": "0",
        }
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def at(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(seconds)

    def jobs(self) -> list[dict[str, Any]]:
        from teeny_hands.rent import store

        return [dict(r) for r in store.db().execute("SELECT * FROM rent_jobs ORDER BY id")]

    def stimuli(self) -> list[dict[str, Any]]:
        from teeny_hands import stimuli

        return [dict(r) for r in stimuli.db().execute("SELECT * FROM stimuli WHERE surface = 'rent' ORDER BY at, id")]

    def leaked(self) -> list[Path]:
        """Every file under the scratch root, but secrets/, holding the key."""
        return [p for p in self.root.rglob("*")
                if p.is_file() and "secrets" not in p.parts and KEY.encode() in p.read_bytes()]
