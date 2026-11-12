"""What each source says today, and the `stats` table it lands in.

  x         followers, following, posts       (the x hand's client; X's public
                                               user metrics carry no impressions,
                                               so there are none here)
  moltbook  karma, followers, following, posts, comments   (/agents/me)
  github    followers, public_repos, stars    (stars: every repository GITHUB_USER
                                               owns, forks left out)

A source whose key is not in secrets/teeny.env is skipped and said so; a
source that fails is reported and the others still land. One row per
(date, source, metric): a second snapshot on the same UTC day replaces the
day's value rather than adding a row.
"""

from __future__ import annotations


import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .. import hand

SCHEMA = """
CREATE TABLE IF NOT EXISTS stats (
    date   TEXT NOT NULL,
    source TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL NOT NULL,
    at     REAL NOT NULL,
    PRIMARY KEY (date, source, metric)
);
"""

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def today() -> str:
    return datetime.fromtimestamp(hand.now(), timezone.utc).strftime("%Y-%m-%d")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _pick(raw: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, float]:
    out = {}
    for metric, key in names.items():
        value = _number(raw.get(key))
        if value is not None:
            out[metric] = value
    return out


def x_metrics() -> dict[str, float]:
    from ..x.client import XApi

    handle = hand.setting("X_BOT_USERNAME", "teenydroid") or "teenydroid"
    user = XApi.from_settings().user_by_username(handle)
    if user is None:
        raise hand.HandError(f"X has no account @{handle}")
    # The same answer carries the bio: kept for bin/doctor's disclosure check
    # (x/profile.py), so the doctor never calls X. A store that fails only
    # leaves the check stale.
    try:
        from ..x import profile

        conn = hand.db()
        try:
            profile.remember(conn, handle, user, hand.now())
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"hands/stats: could not keep the X profile: {type(exc).__name__}", file=sys.stderr)
    return _pick(user, {"followers": "followers", "following": "following", "posts": "posts"})


def moltbook_metrics() -> dict[str, float]:
    from ..moltbook import client

    got = client.api().call("GET", "/agents/me")
    agent = got.get("agent") if isinstance(got, Mapping) and isinstance(got.get("agent"), Mapping) else got
    if not isinstance(agent, Mapping):
        raise hand.HandError("Moltbook did not describe the agent")
    return _pick(
        agent,
        {"karma": "karma", "followers": "follower_count", "following": "following_count", "posts": "posts_count", "comments": "comments_count"},
    )


def github_metrics() -> dict[str, float]:
    import urllib.parse

    from ..gh.client import GitHubApi, user

    gh = GitHubApi.from_settings()
    login = user()
    profile = gh.call("GET", "/users/" + urllib.parse.quote(login, safe=""))
    out = _pick(profile if isinstance(profile, Mapping) else {}, {"followers": "followers", "public_repos": "public_repos"})
    stars = 0
    for page in range(1, 11):
        repos = gh.call("GET", f"/users/{urllib.parse.quote(login, safe='')}/repos", [("type", "owner"), ("per_page", "100"), ("page", str(page))])
        repos = repos if isinstance(repos, list) else []
        stars += sum(int(r.get("stargazers_count") or 0) for r in repos if isinstance(r, Mapping) and not r.get("fork"))
        if len(repos) < 100:
            break
    out["stars"] = float(stars)
    return out


#: source -> (the keys it needs, how to read it)
SOURCES: dict[str, tuple[tuple[str, ...], Callable[[], dict[str, float]]]] = {
    "x": (("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"), lambda: x_metrics()),
    "moltbook": (("MOLTBOOK_API_KEY",), lambda: moltbook_metrics()),
    "github": (("GITHUB_TOKEN",), lambda: github_metrics()),
}


def snapshot() -> Json:
    date = today()
    written: dict[str, dict[str, float]] = {}
    skipped: dict[str, str] = {}
    failed: dict[str, str] = {}
    for source, (keys, read) in SOURCES.items():
        missing = [k for k in keys if not hand.setting(k)]
        if missing:
            skipped[source] = f"{', '.join(missing)} not set"
            continue
        try:
            metrics = read()
        except hand.HandError as exc:
            failed[source] = str(exc)
            continue
        written[source] = metrics
    with hand.lock("stats-snapshot", timeout=30):
        conn = db()
        try:
            with hand.transaction(conn):
                for source, metrics in written.items():
                    for metric, value in metrics.items():
                        conn.execute(
                            "INSERT INTO stats (date, source, metric, value, at) VALUES (?, ?, ?, ?, ?)"
                            " ON CONFLICT(date, source, metric) DO UPDATE SET value = excluded.value, at = excluded.at",
                            (date, source, metric, value, hand.now()),
                        )
        finally:
            conn.close()
    out: Json = {"date": date, "written": written}
    if skipped:
        out["skipped"] = skipped
    if failed:
        out["failed"] = failed
    return out


def _latest(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.source, s.metric, s.value, s.date FROM stats s"
        " JOIN (SELECT source, metric, max(date) AS date FROM stats GROUP BY source, metric) m"
        " ON s.source = m.source AND s.metric = m.metric AND s.date = m.date ORDER BY s.source, s.metric"
    ).fetchall()


def _value(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def report(days: int) -> Json:
    conn = db()
    try:
        metrics = []
        for row in _latest(conn):
            cutoff = (datetime.strptime(row["date"], "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
            before = conn.execute(
                "SELECT value, date FROM stats WHERE source = ? AND metric = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                (row["source"], row["metric"], cutoff),
            ).fetchone()
            item: Json = {"source": row["source"], "metric": row["metric"], "value": _value(row["value"]), "date": row["date"]}
            if before is not None:
                item.update({"was": _value(before["value"]), "since": before["date"], "delta": _value(row["value"] - before["value"])})
            metrics.append(item)
        recorded = conn.execute("SELECT count(DISTINCT date) AS n, min(date) AS first FROM stats").fetchone()
    finally:
        conn.close()
    # How many days have a snapshot, and the first: the ship log's day
    # number comes from here, never from a model counting.
    out: Json = {"days": days, "metrics": metrics, "days_recorded": recorded["n"]}
    if recorded["first"]:
        out["first_recorded"] = recorded["first"]
    return out


def get(name: str) -> Json:
    source, _, metric = name.partition(".") if "." in name else ("", "", name)
    conn = db()
    try:
        rows = [r for r in _latest(conn) if r["metric"] == metric and (not source or r["source"] == source)]
    finally:
        conn.close()
    if not rows:
        raise hand.Refused(f"no stats row for {name}; quote no number for it", metric=name)
    if len(rows) > 1:
        raise hand.HandError(f"{metric} is kept for {', '.join(r['source'] for r in rows)}; ask for source.{metric}")
    row = rows[0]
    return {"source": row["source"], "metric": row["metric"], "value": _value(row["value"]), "date": row["date"]}
