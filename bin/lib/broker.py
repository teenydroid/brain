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

The mind rehearses on a switch of its own. Unless secrets/teeny.env says
MIND_DRY_RUN=0, `serve` forces DRY_RUN on for itself and so for every hand it
runs: the mind's wakes, its notes and what it would have done (the ledger)
are real, and nothing it asks for leaves the box, while the rest of Teeny
goes on as it is. The box's own DRY_RUN holds it either way.

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
import re
import secrets
import subprocess
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

import cron_policy  # noqa: E402
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


def _read_cold(intent: Json) -> None:
    """An own post on X, read cold before it is sent, as a beat reads it
    (tick._clear_to_a_stranger): ValueError when a stranger could not follow
    it. No rewrite is asked here: the agent rewrites and sends it again."""
    why = tick._unclear(tick._cold_read(tick._text(intent), intent.get("meaning")))
    if why:
        raise ValueError(f"not clear to a stranger: {why}. It was not sent: say it in everyday words, with no count "
                         "or name from your own workings, and send it again, or let it go")


def do(body: Json, entry: Json) -> Json:
    """One intent, the way a beat carries it out (tick.carry_out): the route
    (its fields, and its daily cap where one applies), an own post read cold,
    the cap counted once the call reaches the hand, then the hand, once."""
    entry["intent"] = _clipped(body)
    verb = body.get("do")
    entry["verb"] = _clipped(verb) if isinstance(verb, str) else None
    if verb in tick.OWN:
        raise Refused(f"{verb!r} is not a broker verb: it is the beat's own")
    if not isinstance(verb, str) or verb not in tick.ROUTES:
        raise Refused(f"no such verb {_clipped(verb)!r}; the broker verbs are {', '.join(sorted(tick.ROUTES))}")
    try:
        name, argv, hand_body = tick.ROUTES[verb](body)
        if verb == "post_x" and name == "x":
            _read_cold(body)
    except ValueError as exc:
        raise Refused(str(exc)) from None
    # A deadline already past: a hand's "not yet" (pacing) comes straight back
    # as its refusal, with its wait, rather than holding the broker.
    code, out, held = tick._send(verb, name, argv, hand_body, 0.0)
    answer = _hand_answer(code, out, entry)
    if held:
        answer["held"] = {k: held[k] for k in ("rule", "wait_seconds") if k in held}
    return answer


def goals(body: Json, entry: Json) -> Json:
    entry["verb"] = "goals"
    return _hand_answer(*tick._run_hand("goals", ["list"], None), entry)


# --- wakes: the mind's own cron jobs --------------------------------------------------
#
# A wake is a core cron job that starts the `mind` agent on a note, at one
# moment or on a schedule. It is always an agent job for `mind` in an isolated
# session delivering nothing: nothing in the request decides the job's type,
# agent or command, so no wake can ever run a shell. Its name is always
# `mind.<slug>`, which keeps it apart from bin/seed-cron's `teeny.*` jobs, and
# only a `mind.*` job is ever listed or removed here.

WAKE_PREFIX = "mind."
WAKE_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
WAKE_AGENT = "mind"
MAX_NOTE_CHARS = 2000
MAX_WAKES = 40
MAX_AHEAD = dt.timedelta(days=90)
DEFAULT_WAKES_PER_DAY = 48
#: The most MIND_WAKES_PER_DAY may be set to: a wake every MIN_GAP_MINUTES.
MAX_WAKES_PER_DAY = 24 * 60 // cron_policy.MIN_GAP_MINUTES
#: Seconds a core RPC may take.
OH_TIMEOUT_SECS = 120


def _oh(method: str, params: Json) -> Any:
    """One core RPC through bin/oh (TEENY_OH in the tests), its result."""
    exe = os.environ.get("TEENY_OH") or str(hand.root() / "bin" / "oh")
    proc = subprocess.run([exe, method, json.dumps(params)], capture_output=True, text=True, cwd=hand.root(),
                          timeout=OH_TIMEOUT_SECS)
    if proc.returncode != 0:
        raise RuntimeError(f"the core's {method} failed: {proc.stderr.strip()[-300:] or f'exit {proc.returncode}'}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def _wakes() -> list[Json]:
    jobs = _oh("cron_list", {})
    if not isinstance(jobs, list):
        raise RuntimeError("the core's cron_list did not answer a list")
    return [j for j in jobs if isinstance(j, dict) and isinstance(j.get("name"), str)
            and j["name"].startswith(WAKE_PREFIX)]


def _slug(value: Any) -> str:
    if not isinstance(value, str) or not WAKE_SLUG.fullmatch(value):
        raise Refused("name is 1 to 32 lowercase letters, digits and dashes, starting with a letter or digit")
    return value


def _at(value: Any) -> str:
    """A one-off wake's moment, RFC3339 in UTC, between now and MAX_AHEAD."""
    try:
        when = dt.datetime.fromisoformat(value.strip()) if isinstance(value, str) else None
    except ValueError:
        when = None
    if when is None or when.tzinfo is None or when.utcoffset() != dt.timedelta(0):
        raise Refused("at is an RFC3339 time in UTC, like 2026-11-22T09:30:00Z")
    now = dt.datetime.fromtimestamp(hand.now(), dt.timezone.utc)
    if when <= now:
        raise Refused(f"at is in the past (it is {now:%Y-%m-%dT%H:%M:%SZ} now)")
    if when > now + MAX_AHEAD:
        raise Refused(f"at is more than {MAX_AHEAD.days} days away")
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _runs_per_day(expr: str) -> int:
    """How often a five-field cron expression may fire in a day, after the
    cron rules (bin/lib/cron_policy.py): numeric fields, no gap under
    MIN_GAP_MINUTES. Refused when it breaks one."""
    fields = expr.split()
    if len(fields) != 5:
        raise Refused(f"cron is a five-field expression (minute hour day month weekday), got {expr!r}")
    try:
        minutes = cron_policy.fire_minutes(expr, "cron")
        for spec, low, high in ((fields[2], 1, 31), (fields[3], 1, 12), (fields[4], 0, 7)):
            cron_policy._field(spec, low, high, "cron")
    except cron_policy.PolicyError as exc:
        raise Refused(str(exc)) from None
    if cron_policy.min_gap(minutes) < cron_policy.MIN_GAP_MINUTES:
        raise Refused(f"cron {expr!r} fires more often than every {cron_policy.MIN_GAP_MINUTES} minutes")
    return len(minutes)


def _recurring_runs(jobs: list[Json]) -> int:
    """Runs a day of the enabled recurring wakes; one whose expression cannot
    be read counts as the most any may run."""
    runs = 0
    for job in jobs:
        schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
        if schedule.get("kind") != "cron" or job.get("enabled") is False:
            continue
        try:
            runs += _runs_per_day(str(schedule.get("expr") or ""))
        except Refused:
            runs += MAX_WAKES_PER_DAY
    return runs


def _one_offs_path() -> Path:
    return hand.state_dir() / "broker" / "one-offs.json"


def _one_offs_today() -> int:
    """One-off wakes set today (UTC). A one-off deletes itself once it has
    run, so the core's job list cannot say how many ran: counted here, or a
    run could schedule the next one for ever."""
    day = dt.datetime.fromtimestamp(hand.now(), dt.timezone.utc).strftime("%Y-%m-%d")
    try:
        saved = json.loads(_one_offs_path().read_text())
    except (OSError, ValueError):
        return 0
    return saved["count"] if isinstance(saved, dict) and saved.get("day") == day and isinstance(saved.get("count"), int) else 0


def _count_one_off() -> None:
    day = dt.datetime.fromtimestamp(hand.now(), dt.timezone.utc).strftime("%Y-%m-%d")
    path = _one_offs_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write(path, json.dumps({"day": day, "count": _one_offs_today() + 1}) + "\n", 0o600)


def wake(body: Json, entry: Json) -> Json:
    """A new wake, after every rule; under DRY_RUN only what it would be."""
    if isinstance(body.get("name"), str):
        entry["verb"] = body["name"][:LEDGER_CLIP]
    slug = _slug(body.get("name"))
    note = body.get("note")
    note = note.strip() if isinstance(note, str) else ""
    if not 1 <= len(note) <= MAX_NOTE_CHARS:
        raise Refused(f"note is what to do when you wake, 1 to {MAX_NOTE_CHARS} characters")
    if ("at" in body) == ("cron" in body):
        raise Refused("a wake has either at (once) or cron (a schedule), not both or neither")
    if "at" in body:
        schedule: Json = {"kind": "at", "at": _at(body["at"])}
        runs = 1
    else:
        expr = " ".join(body["cron"].split()) if isinstance(body["cron"], str) else ""
        runs = _runs_per_day(expr)
        schedule = {"kind": "cron", "expr": expr, "tz": "UTC"}
    jobs = _wakes()
    name = WAKE_PREFIX + slug
    if any(j["name"] == name for j in jobs):
        raise Refused(f"a wake named {slug} is already set: drop it first")
    if len(jobs) >= MAX_WAKES:
        raise Refused(f"{len(jobs)} wakes are set; the most is {MAX_WAKES}: drop one first")
    limit = hand.number("MIND_WAKES_PER_DAY", DEFAULT_WAKES_PER_DAY, 0, MAX_WAKES_PER_DAY)
    recurring = _recurring_runs(jobs)
    once = _one_offs_today()
    if recurring + once + runs > limit:
        raise Refused(f"your recurring wakes run up to {recurring} times a day, you set {once} one-off wake(s) today, "
                      f"and this adds {runs}; the most is {limit} (MIND_WAKES_PER_DAY)")
    job: Json = {"name": name, "job_type": "agent", "agent_id": WAKE_AGENT, "prompt": note,
                 "session_target": "isolated", "delivery": {"mode": "none"}, "schedule": schedule}
    if schedule["kind"] == "at":
        job["delete_after_run"] = True
    if hand.dry_run():
        return {"status": "ok", "dry_run": True, "wake": slug, "job": job}
    added = _oh("cron_add", job)
    if schedule["kind"] == "at":
        _count_one_off()
    return {"status": "ok", "wake": slug, "job": added}


def _listed(job: Json) -> Json:
    return {"name": job["name"][len(WAKE_PREFIX):], "job": job["name"], "schedule": job.get("schedule"),
            "prompt": job.get("prompt"), "enabled": job.get("enabled") is not False}


def wakes(body: Json, entry: Json) -> Json:
    entry["verb"] = "wakes"
    return {"status": "ok", "wakes": [_listed(j) for j in _wakes()]}


def drop_wake(body: Json, entry: Json) -> Json:
    """Removes the wake `mind.<name>`; nothing else can be named."""
    if isinstance(body.get("name"), str):
        entry["verb"] = body["name"][:LEDGER_CLIP]
    slug = _slug(body.get("name"))
    found = [j for j in _wakes() if j["name"] == WAKE_PREFIX + slug
             and isinstance(j.get("id"), (str, int)) and not isinstance(j.get("id"), bool)]
    if not found:
        raise Refused(f"no wake named {slug}")
    for job in found:
        # As bin/seed-cron passes it: the id as a string.
        _oh("cron_remove", {"job_id": str(job["id"])})
    return {"status": "ok", "dropped": slug}


PATHS: dict[str, Callable[[Json, Json], Json]] = {
    "/do": do,
    "/wake": wake,
    "/wakes": wakes,
    "/drop-wake": drop_wake,
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
    if hand.setting("MIND_DRY_RUN") != "0":
        # The environment can only force a rehearsal on (hand.dry_run), and
        # every hand the broker runs inherits it.
        os.environ["DRY_RUN"] = "1"
        print("[broker] rehearsing: nothing the mind asks for leaves the box (MIND_DRY_RUN=0 in secrets/teeny.env "
              "makes it real)", file=sys.stderr)
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
