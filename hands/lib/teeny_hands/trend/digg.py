"""One call to `digg-pp-cli`, and why it gave nothing when it gave nothing.

The tool is upstream's, unmodified (the Dockerfile's `digg` stage pins the
commit). It runs with a minimal environment: PATH, a locale, and a home of
its own under state/trend/home (HOME and every XDG directory), so the local
store it keeps is written there and nowhere else, and it never sees a key or
a switch from this process's environment.

Every failure is a `DiggError` with a `reason` a caller can branch on:

  missing       the binary is not installed (TREND_DIGG_BIN, or digg-pp-cli on PATH)
  timeout       no answer within TREND_TIMEOUT_SECS
  rate_limited  Digg said to come back later ("temporarily unavailable, retry after ...")
  failed        any other non-zero exit
  bad_json      an answer that is not the JSON asked for
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import hand

BINARY = "digg-pp-cli"
#: The topics `top` reads, in Digg's own names.
TOPICS = ("ai", "tech")
#: Digg's story ids as the posts verb takes them (clusterId, never the short
#: clusterUrlId, which only a local store can resolve).
CLUSTER = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_RATE = re.compile(r"temporarily unavailable|retry after|rate.?limit|too many requests|\b429\b", re.I)


class DiggError(hand.HandError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message, reason=reason)
        self.reason = reason


def home() -> Path:
    """The tool's home: state/trend/home."""
    path = hand.state_dir() / "trend" / "home"
    path.mkdir(parents=True, exist_ok=True)
    return path


def binary() -> str | None:
    """Where the tool is: TREND_DIGG_BIN (secrets/teeny.env), else
    digg-pp-cli on PATH; None when neither is there."""
    named = hand.setting("TREND_DIGG_BIN")
    if named:
        return named if os.access(named, os.X_OK) else None
    return shutil.which(BINARY)


def _env() -> dict[str, str]:
    where = home()
    env = {"PATH": os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
           "HOME": str(where), "NO_COLOR": "1"}
    for name in ("DATA", "CONFIG", "CACHE", "STATE"):
        env[f"XDG_{name}_HOME"] = str(where / name.lower())
    return env


def _said(text: str) -> str:
    """The tool's last words, as one short line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:300]


def call(*argv: str) -> Any:
    """`digg-pp-cli ARGV...`: its JSON answer, or a DiggError."""
    exe = binary()
    if not exe:
        raise DiggError("missing", f"{BINARY} is not installed (set TREND_DIGG_BIN or put it on PATH)")
    secs = hand.number("TREND_TIMEOUT_SECS", 30, 1, 120)
    try:
        done = subprocess.run([exe, *argv], cwd=home(), env=_env(), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=secs)
    except subprocess.TimeoutExpired:
        raise DiggError("timeout", f"{BINARY} {argv[0]} gave no answer within {secs} s") from None
    except OSError as exc:
        raise DiggError("missing", f"{BINARY} could not be run: {exc.strerror or exc}") from None
    words = done.stderr + "\n" + done.stdout
    if done.returncode != 0:
        reason = "rate_limited" if _RATE.search(words) else "failed"
        raise DiggError(reason, f"{BINARY} {argv[0]} exited {done.returncode}: {_said(done.stderr or done.stdout)}")
    if _RATE.search(done.stdout) and not done.stdout.lstrip().startswith(("{", "[")):
        raise DiggError("rate_limited", f"{BINARY} {argv[0]}: {_said(done.stdout)}")
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise DiggError("bad_json", f"{BINARY} {argv[0]} did not answer JSON: {_said(done.stdout)!r}") from None


def rows(got: Any, what: str) -> list[dict]:
    """The rows of an answer, which comes as a bare list or as a
    `{meta, results}` envelope."""
    if isinstance(got, dict):
        got = got.get("results")
    if not isinstance(got, list):
        raise DiggError("bad_json", f"{BINARY} {what} answered with no list of results")
    return [row for row in got if isinstance(row, dict)]


def top(topic: str, limit: int) -> list[dict]:
    """One topic's leaderboard, live, in Digg's rank order."""
    return rows(call("top", "--topic", topic, "--limit", str(limit), "--json", "--data-source", "live"),
                f"top --topic {topic}")


def posts(cluster: str) -> tuple[dict, list[dict]]:
    """One story's sources, live: (its meta, its rows by Digg's rank)."""
    if not CLUSTER.match(cluster):
        raise hand.HandError("a story is named by its clusterId, a UUID")
    got = call("posts", cluster, "--agent", "--data-source", "live")
    meta = got.get("meta") if isinstance(got, dict) and isinstance(got.get("meta"), dict) else {}
    return meta, rows(got, "posts")
