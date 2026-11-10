"""The contract every hand keeps (docs/hands.md), and what they share.

A hand is `hands/<name> <verb> [--flag value ...]`, with an optional JSON body
on stdin. It prints exactly one JSON object and exits:

  0  done              {"ok": true, ...}
  2  refused by a rule {"ok": false, "refused": "<why>", ...}
  1  error             {"ok": false, "error": "<what>"}

Refused is an answer, not a failure: the caller (a job script, the operator)
decides what to do with a hold or a rule, and the message says which rule.

Everything a hand needs that is not in its arguments comes from the checkout,
because cron shell jobs run with an allowlisted environment and no TEENY_*
(docs/architecture.md, "Hands"):

  secrets/teeny.env   the hands' keys and switches (KEY=VALUE), read by the hand
                      itself so the model never sees them
  secrets/core.token  the core's RPC bearer
  state/core.url      where the core answers (written by bin/spine)
  state/hands.db      every rule that spans calls is a transaction here
  state/locks/        flock files for what must not run twice at once

TEENY_ROOT moves all of it (the tests point it at a scratch directory).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sqlite3
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

# --- outcomes -------------------------------------------------------------------


class Refused(Exception):
    """A rule said no. Exit 2, with the reason and any detail for the caller."""

    def __init__(self, reason: str, **detail: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class HandError(Exception):
    """Something went wrong that is not a rule: exit 1."""


# --- paths and secrets --------------------------------------------------------------


def root() -> Path:
    env = os.environ.get("TEENY_ROOT")
    if env:
        return Path(env)
    # hands/lib/teeny_hands/hand.py -> the checkout
    return Path(__file__).resolve().parents[3]


def state_dir() -> Path:
    path = root() / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def secrets_dir() -> Path:
    return root() / "secrets"


_ENV_CACHE: dict[str, str] | None = None


def _parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def secrets() -> Mapping[str, str]:
    """secrets/teeny.env, read once per process. Missing is empty, not an error:
    a hand that needs a key says which one when it looks it up."""
    global _ENV_CACHE
    if _ENV_CACHE is None:
        path = secrets_dir() / "teeny.env"
        try:
            _ENV_CACHE = _parse_env_file(path.read_text())
        except FileNotFoundError:
            _ENV_CACHE = {}
    return _ENV_CACHE


def setting(name: str, default: str | None = None) -> str | None:
    """A switch or a key from secrets/teeny.env. The process environment does
    not override it: a job's environment is not the operator's to trust, and
    a model that can set variables must not be able to change a rule."""
    value = secrets().get(name)
    return value if value not in (None, "") else default


def require(name: str) -> str:
    value = setting(name)
    if not value:
        raise HandError(f"{name} is not set in secrets/teeny.env")
    return value


def flag(name: str, default: bool) -> bool:
    value = setting(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def dry_run() -> bool:
    """On unless secrets/teeny.env says DRY_RUN=0. The environment can only
    force it on (DRY_RUN=1 for a rehearsal), never off."""
    forced = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")
    return forced or flag("DRY_RUN", True)


# --- state ------------------------------------------------------------------------------


def db() -> sqlite3.Connection:
    """state/hands.db, autocommit with explicit transactions (`transaction`)."""
    conn = sqlite3.connect(state_dir() / "hands.db", timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    # Switching a fresh file to WAL needs an exclusive lock, and SQLite answers
    # "database is locked" to the losers at once rather than waiting out the
    # busy timeout. Many hands start at the same minute (cron), so retry that
    # one statement briefly instead of failing a job over it.
    for attempt in range(50):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == 49:
                raise
            time.sleep(0.02 * (attempt + 1))
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def open_db(schema: str) -> sqlite3.Connection:
    """state/hands.db with `schema` in place, retrying a "database is locked"
    from another hand creating its own tables at the same moment (as
    hands/x and hands/gh each do for theirs)."""
    for attempt in range(20):
        try:
            conn = db()
            conn.executescript(schema)
            return conn
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def number(name: str, default: int, low: int, high: int) -> int:
    """A whole-number setting, clamped to [low, high]; the default when it is
    unset or unreadable, so a typo cannot lift a cap."""
    raw = setting(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return max(low, min(value, high))


def day_start(at: float | None = None) -> float:
    """The start of the UTC day `at` (default now) falls in."""
    import datetime as _dt

    moment = _dt.datetime.fromtimestamp(now() if at is None else at, _dt.timezone.utc)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE: the write lock is taken up front, so a read-then-write
    rule (a claim, a gap check) cannot interleave with another process's."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


@contextlib.contextmanager
def lock(name: str, timeout: float = 120.0) -> Iterator[None]:
    """An exclusive flock on state/locks/<name>.lock, held for the block. Waits
    up to `timeout` seconds, then raises HandError rather than queueing forever."""
    locks = state_dir() / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    with open(locks / f"{name}.lock", "a+") as fh:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise HandError(f"timed out waiting for the {name} lock")
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def now() -> float:
    """Seconds since the epoch. TEENY_NOW pins it, for tests of time rules."""
    pinned = os.environ.get("TEENY_NOW")
    return float(pinned) if pinned else time.time()


# --- the core ------------------------------------------------------------------------------


class CoreError(HandError):
    pass


def core(method: str, params: Mapping[str, Any] | None = None, timeout: float = 300.0) -> Any:
    """One JSON-RPC call to the running core, as bin/oh makes it."""
    if not method.startswith("openhuman."):
        method = f"openhuman.{method}"
    url = os.environ.get("OPENHUMAN_CORE_URL")
    if not url:
        try:
            url = (state_dir() / "core.url").read_text().strip()
        except FileNotFoundError:
            url = "http://127.0.0.1:7788"
    token = os.environ.get("OPENHUMAN_CORE_TOKEN")
    if not token:
        try:
            token = (secrets_dir() / "core.token").read_text().strip()
        except FileNotFoundError as exc:
            raise CoreError("no core token at secrets/core.token") from exc
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": dict(params or {})}).encode()
    request = urllib.request.Request(
        f"{url.rstrip('/')}/rpc",
        data=body,
        headers={"content-type": "application/json", "authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise CoreError(f"{method}: core answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CoreError(f"{method}: core unreachable at {url}") from exc
    if payload.get("error"):
        error = payload["error"]
        raise CoreError(f"{method}: {error.get('message', error) if isinstance(error, dict) else error}")
    result = payload.get("result")
    if isinstance(result, dict) and "logs" in result and "result" in result:
        return result["result"]
    return result


# --- the command line --------------------------------------------------------------------------

Verb = Callable[[argparse.Namespace, Any], Mapping[str, Any]]


def read_body() -> Any:
    """The JSON body on stdin, or None when there is none."""
    if sys.stdin is None or sys.stdin.isatty():
        return None
    text = sys.stdin.read()
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HandError(f"stdin is not JSON: {exc.msg}") from exc


def emit(obj: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def run(name: str, parser: argparse.ArgumentParser, argv: list[str]) -> int:
    """Parse, dispatch to the verb's handler (`set_defaults(handler=...)`),
    and turn its outcome into the contract's JSON and exit code."""
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed its usage to stderr
        emit({"ok": False, "error": f"bad arguments to hands/{name}"})
        return 1 if exc.code else 0
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_usage(sys.stderr)
        emit({"ok": False, "error": f"hands/{name} needs a verb"})
        return 1
    try:
        result = dict(handler(args, read_body() if getattr(args, "takes_body", False) else None))
    except Refused as refused:
        emit({"ok": False, "refused": refused.reason, **refused.detail})
        return 2
    except HandError as exc:
        emit({"ok": False, "error": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001 - the contract owes the caller one JSON line
        if os.environ.get("TEENY_HANDS_DEBUG"):
            traceback.print_exc()
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    result.setdefault("ok", True)
    emit(result)
    return 0
