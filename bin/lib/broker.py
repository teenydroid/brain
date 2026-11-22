"""The broker: the one way the jailed `mind` agent acts on the world.

    python3 bin/lib/broker.py serve     # bin/broker serve
    python3 bin/lib/broker.py install   # bin/broker install

The `mind` agent's shell is jailed to state/home/: it cannot read secrets/,
hands/ or bin/, and its environment carries no tokens, but it shares the
host's loopback. This daemon runs outside the jail, on 127.0.0.1 only, and
does every outside action for it by calling the same hands bin/tick calls, the
same way (bin/lib/tick.py ROUTES and _send): each hand keeps its own guard,
caps and DRY_RUN. The broker itself never talks to X, GitHub, mail or a
wallet, and the only core RPCs it makes are the `mind.*` cron jobs below.

Other jails share the loopback (the builder, which runs strangers' build
requests, is jailed to state/workbench/), so every request carries a bearer
token minted afresh at every start into state/home/.broker-token (0600), which
only the mind's jail can read. A missing or wrong token is a 401, and the
token is never written anywhere else.

At start the broker writes, into state/home/: the token, `.broker-url`
(http://127.0.0.1:<BROKER_PORT>, default 7790), and the client
(bin/lib/broker_client.py) as bin/teeny, since the jail cannot read bin/.
`serve` runs only with MIND=1 in secrets/teeny.env; `install` only writes
those files and exits (and rotates the token a running broker holds).

Every request is POST with a JSON object body of at most MAX_BODY bytes:

- /do         one intent, `{"do": <a tick.ROUTES verb>, ...}`, carried out
              as a beat carries it out (the route, its cap, the hand)
- /wake       `{"name", "note", "at" | "cron"}`: a core cron job that wakes
              the `mind` agent on the note, always an agent job
- /wakes      the mind's wakes
- /drop-wake  `{"name"}`: removes one of them, never any other job
- /goals      hands/goals list

The answer is `{"status": "ok" | "refused" | "error", ...}` with HTTP 200,
403 or 500 (401 unauthorised, 404 no such path, 405 not POST), and each
request is one line in state/broker/ledger.jsonl.
"""

from __future__ import annotations

import datetime as dt
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

LIB = Path(__file__).resolve().parent
CODE = LIB.parents[1]
sys.path.insert(0, str(CODE / "hands" / "lib"))
sys.path.insert(0, str(LIB))

from teeny_hands import hand  # noqa: E402

import tick  # noqa: E402

Json = dict[str, Any]

#: Loopback only: the broker is never reachable from off the box.
HOST = "127.0.0.1"
DEFAULT_PORT = 7790
#: The largest request body read.
MAX_BODY = 64 * 1024
#: How much of an oversized body is read and thrown away, so the refusal
#: reaches the client rather than a reset connection.
DRAIN_MAX = 1024 * 1024
#: Seconds a client may take to send its request.
REQUEST_TIMEOUT_SECS = 30
#: The most of any string in an intent the ledger keeps.
LEDGER_CLIP = 200
HTTP_STATUS = {"ok": 200, "refused": 403, "error": 500}


class Refused(Exception):
    """A request the broker will not carry out; its message says why."""


# --- state/home: the token, the url, the client ---------------------------------------


def home() -> Path:
    return hand.state_dir() / "home"


def _ledger_path() -> Path:
    return hand.state_dir() / "broker" / "ledger.jsonl"


def _write(path: Path, text: str, mode: int) -> None:
    """Writes PATH whole, with MODE, by renaming a fresh file over it: a link
    the agent left at PATH is replaced, never followed."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _own_dir(path: Path) -> None:
    """PATH as a real directory: the agent can write state/home, so a link
    (or a file) it left where bin/ belongs is removed, never written through."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    path.mkdir(exist_ok=True)


def install(port: int, token: str) -> None:
    """The token, the client and, last, the url, into state/home/."""
    base = home()
    base.mkdir(parents=True, exist_ok=True)
    _own_dir(base / "bin")
    _write(base / ".broker-token", token + "\n", 0o600)
    _write(base / "bin" / "teeny", (LIB / "broker_client.py").read_text(), 0o755)
    _write(base / ".broker-url", f"http://{HOST}:{port}\n", 0o644)


def port() -> int:
    return hand.number("BROKER_PORT", DEFAULT_PORT, 0, 65535)


# --- the ledger -----------------------------------------------------------------------

_ledger_lock = threading.Lock()


def _clipped(value: Any) -> Any:
    if isinstance(value, str):
        return value[:LEDGER_CLIP]
    if isinstance(value, dict):
        return {str(k)[:LEDGER_CLIP]: _clipped(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clipped(v) for v in value]
    return value


def _ledger(entry: Json, answer: Json) -> None:
    line: Json = {
        "ts": dt.datetime.fromtimestamp(hand.now(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "path": entry.get("path"),
        "verb": entry.get("verb"),
        "status": answer.get("status"),
        "message": _clipped(answer.get("reason") or answer.get("error")),
        "dry_run": hand.dry_run(),
        "exit": entry.get("exit"),
    }
    if "intent" in entry:
        line["intent"] = entry["intent"]
    path = _ledger_path()
    with _ledger_lock:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")


# --- the verbs ------------------------------------------------------------------------


def _hand_answer(code: int, out: Json, entry: Json) -> Json:
    """A hand's exit code and JSON as the broker's answer."""
    entry["exit"] = code
    status = "ok" if code == 0 else "refused" if code == 2 else "error"
    answer: Json = {"status": status, "exit": code, "result": out}
    if status == "refused":
        answer["reason"] = str(out.get("refused") or "the hand refused")
    elif status == "error":
        answer["error"] = str(out.get("error") or out.get("stderr") or f"the hand exited {code}")
    return answer


def goals(body: Json, entry: Json) -> Json:
    entry["verb"] = "goals"
    return _hand_answer(*tick._run_hand("goals", ["list"], None), entry)


PATHS: dict[str, Callable[[Json, Json], Json]] = {
    "/goals": goals,
}


# --- HTTP -----------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "teeny-broker"
    sys_version = ""
    timeout = REQUEST_TIMEOUT_SECS

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class's name
        # The request line and status only: the headers (the token) never.
        print(f"[broker] {format % args}"[:300], file=sys.stderr)

    def _send(self, http: int, answer: Json, entry: Json) -> None:
        try:
            _ledger(entry, answer)
        finally:
            data = json.dumps(answer, ensure_ascii=False).encode()
            self.send_response(http)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def _authorised(self) -> bool:
        scheme, _, given = (self.headers.get("Authorization") or "").partition(" ")
        return scheme == "Bearer" and hmac.compare_digest(given.strip().encode(), self.server.token.encode())

    def _body(self) -> Json:
        if self.headers.get("Transfer-Encoding"):
            raise Refused("send the body with a Content-Length, not chunked")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Refused("the Content-Length is not a number") from None
        if length < 0:
            raise Refused("the Content-Length is not a number")
        if length > MAX_BODY:
            self.close_connection = True
            left = min(length, DRAIN_MAX)
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
            raise Refused(f"the body is {length} bytes; the most is {MAX_BODY}")
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            return {}
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise Refused("the body is not JSON") from None
        if not isinstance(body, dict):
            raise Refused("the body is a JSON object")
        return body

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        entry: Json = {"path": self.path[:LEDGER_CLIP], "verb": None, "exit": None}
        if not self._authorised():
            return self._send(401, {"status": "refused", "reason": "unauthorised"}, entry)
        verb = PATHS.get(self.path)
        if verb is None:
            return self._send(404, {"status": "refused", "reason": f"no such path; the paths are {', '.join(PATHS)}"},
                              entry)
        try:
            body = self._body()
            with self.server.lock:
                answer = verb(body, entry)
        except Refused as exc:
            answer = {"status": "refused", "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001 - every failure is an answer, never a dropped connection
            answer = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
        self._send(HTTP_STATUS[answer["status"]], answer, entry)

    def _not_post(self) -> None:
        entry: Json = {"path": self.path[:LEDGER_CLIP], "verb": None, "exit": None}
        if not self._authorised():
            return self._send(401, {"status": "refused", "reason": "unauthorised"}, entry)
        self._send(405, {"status": "refused", "reason": "only POST"}, entry)

    do_GET = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_post  # noqa: N815


def bind(port: int, token: str) -> ThreadingHTTPServer:
    """The server on 127.0.0.1:PORT (0: any free port), answering TOKEN only.
    Requests are read in their own threads and carried out one at a time."""
    server = ThreadingHTTPServer((HOST, port), Handler)
    server.daemon_threads = True
    server.token = token
    server.lock = threading.Lock()
    return server


# --- entry ----------------------------------------------------------------------------

USAGE = "usage: bin/broker serve | install"


def serve() -> int:
    if hand.setting("MIND") != "1":
        print("[broker] the mind agent is off: set MIND=1 in secrets/teeny.env to serve it", file=sys.stderr)
        return 1
    token = secrets.token_urlsafe(32)
    server = bind(port(), token)
    try:
        install(server.server_address[1], token)
        print(f"[broker] serving the mind on http://{HOST}:{server.server_address[1]}", file=sys.stderr)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str]) -> int:
    if argv == ["serve"]:
        return serve()
    if argv == ["install"]:
        install(port(), secrets.token_urlsafe(32))
        print(json.dumps({"ok": True, "home": str(home())}))
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
