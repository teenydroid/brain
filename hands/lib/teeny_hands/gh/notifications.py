"""GitHub intake: what people said to Teeny on GitHub, read once, screened,
and handed to its mind (docs/roadmap.md, A1; DIRECTIVE.md, level 1).

`ingest` reads the token's notifications (`GET /notifications`, from a
cursor in `gh_cursor`) and, for each one, the newest comment it points at
(the issue or pull request itself when nothing was said after it opened).
Each becomes one row in `gh_notifications`, keyed by that comment, so a
notification GitHub sends again never reaches the mind twice:

  offered  reached the mind as a `gh` stimulus
  held     `hands/guard inbound` held it; nothing answers it
  skipped  Teeny's own words, a bot's, a subject that is not an issue or a
           pull request, or anything on someone else's repository that is
           not a mention of Teeny

What reaches the mind, as `gh · <kind>` with ref `<owner/repo>#<n>:<key>`:
`issue` or `pr` (one opened on Teeny's repository), `comment` (on one),
`review` (a review comment on a pull request) and `mention` (Teeny named,
anywhere). The text is the title and the words, after the inbound guard;
the mind answers with `comment_gh`.

Stars come from a second read: the token's own repositories and their star
counts (`gh_stars`). A count that went up is one `gh · star` stimulus per
repository, with the number only (no names: nothing a stranger wrote). The
first read records the counts and says nothing.

Off unless secrets/teeny.env sets GH_INBOUND=1: the notifications API needs
a token that may read notifications (a classic token with the
`notifications` scope), which the operator checks before turning it on.
Nothing here writes to GitHub: notifications are not marked read.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import urllib.parse
from typing import Any, Mapping

from .. import hand
from .client import GitHubApi, GitHubError, is_own, split_repo, user

STATUSES = ("offered", "held", "skipped")
#: The most of one notification's words that reach the mind.
TEXT_CHARS = 4000
#: Notifications read per run (one page).
PAGE = 50
#: Repositories whose stars are read per run.
MAX_REPOS = 100
#: Where a first run starts reading: this far back.
FIRST_LOOKBACK_SECS = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS gh_cursor (
    name   TEXT PRIMARY KEY,
    value  TEXT NOT NULL,
    at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS gh_notifications (
    key        TEXT PRIMARY KEY,
    thread_id  TEXT,
    repo       TEXT NOT NULL,
    number     INTEGER,
    kind       TEXT NOT NULL,
    author     TEXT,
    status     TEXT NOT NULL,
    note       TEXT,
    hold_id    INTEGER,
    at         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS gh_stars (
    repo   TEXT PRIMARY KEY,
    stars  INTEGER NOT NULL,
    at     REAL NOT NULL
);
"""

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def _screen(text: str, context: Mapping[str, Any]) -> Any:
    """The inbound guard's verdict (imported by name, so a test can stand in)."""
    from ..guard.gate import inbound

    return inbound(text, surface="github", context=dict(context))


def _iso(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _api_path(url: Any, gh: GitHubApi) -> str | None:
    """An API URL GitHub handed back, as a path on the client's own base:
    never a request to a host the notification named."""
    if not isinstance(url, str) or not url:
        return None
    base = urllib.parse.urlparse(gh._base)  # noqa: SLF001 - the client's own base, read only
    got = urllib.parse.urlparse(url)
    if (got.scheme, got.netloc) != (base.scheme, base.netloc):
        return None
    path = got.path
    prefix = base.path.rstrip("/")
    if prefix:
        if not path.startswith(prefix + "/"):
            return None
        path = path[len(prefix):]
    return path if path.startswith("/repos/") else None


_NUMBER = re.compile(r"/(?:issues|pulls)/(\d+)$")
_COMMENT = re.compile(r"/(issues|pulls)/comments/(\d+)$")


def _kind(note: Mapping[str, Any], subject_path: str, comment_path: str | None) -> str:
    if note.get("reason") == "mention":
        return "mention"
    if comment_path and comment_path != subject_path:
        found = _COMMENT.search(comment_path)
        return "review" if found and found.group(1) == "pulls" else "comment"
    return "pr" if (note.get("subject") or {}).get("type") == "PullRequest" else "issue"


def _skip_reason(repo_owner: str, note: Mapping[str, Any], author: str | None) -> str | None:
    subject = note.get("subject") if isinstance(note.get("subject"), Mapping) else {}
    if subject.get("type") not in ("Issue", "PullRequest"):
        return f"a {subject.get('type') or 'subject'} notification"
    if not is_own(repo_owner) and note.get("reason") != "mention":
        return "someone else's repository, and not a mention"
    if author and is_own(author):
        return "Teeny's own words"
    if author and author.lower().endswith("[bot]"):
        return "a bot"
    return None


def _one(gh: GitHubApi, conn: sqlite3.Connection, note: Mapping[str, Any], counts: dict[str, int]) -> None:
    from .. import stimuli

    repo_full = ((note.get("repository") or {}).get("full_name")) if isinstance(note.get("repository"), Mapping) else None
    subject = note.get("subject") if isinstance(note.get("subject"), Mapping) else {}
    try:
        owner, name = split_repo(repo_full)
    except hand.HandError:
        return
    repo = f"{owner}/{name}"
    subject_path = _api_path(subject.get("url"), gh) or ""
    comment_path = _api_path(subject.get("latest_comment_url"), gh) or subject_path
    found = _NUMBER.search(subject_path)
    number = int(found.group(1)) if found else None
    comment = _COMMENT.search(comment_path or "")
    key = f"{repo}#{number}:{comment.group(2) if comment else 'opened'}"
    if conn.execute("SELECT 1 FROM gh_notifications WHERE key = ?", (key,)).fetchone():
        return
    kind = _kind(note, subject_path, comment_path)
    item: Mapping[str, Any] = {}
    skip = _skip_reason(owner, note, None)
    if skip is None and comment_path:
        try:
            got = gh.call("GET", comment_path)
        except GitHubError as error:
            if error.status != 404:
                raise
            got = {}
        item = got if isinstance(got, Mapping) else {}
    author = (item.get("user") or {}).get("login") if isinstance(item.get("user"), Mapping) else None
    skip = skip or _skip_reason(owner, note, author)
    title = str(subject.get("title") or "")[:300]
    words = str(item.get("body") or "")[:TEXT_CHARS]
    status, why, hold_id = "skipped", skip, None
    if skip is None:
        if number is None:
            status, why = "skipped", "no issue or pull request number"
        else:
            verdict = _screen(f"{title}\n\n{words}".strip(), {"repo": repo, "number": number, "kind": kind})
            if verdict.kind != "allow":
                status, why, hold_id = "held", verdict.reason, verdict.hold_id
            else:
                status = "offered"
    with hand.transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO gh_notifications (key, thread_id, repo, number, kind, author, status, note, hold_id, at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (key, str(note.get("id") or ""), repo, number, kind, author, status, why, hold_id, hand.now()),
        )
    counts[status] += 1
    if status == "offered":
        who = f"@{author}" if author else "someone"
        where = f"{repo}#{number}"
        said = {"issue": "opened an issue", "pr": "opened a pull request", "comment": "commented",
                "review": "left a review comment", "mention": "mentioned you"}[kind]
        stimuli.add(
            "gh", kind, key,
            f"{who} {said} on {where} ({title}):\n{words}".rstrip(),
            who=author,
            payload={"repo": repo, "number": number, "url": item.get("html_url"), "type": subject.get("type"),
                     "reason": note.get("reason")},
        )


def _cursor(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT value FROM gh_cursor WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else None


def _set_cursor(conn: sqlite3.Connection, name: str, value: str) -> None:
    conn.execute("INSERT INTO gh_cursor (name, value, at) VALUES (?, ?, ?)"
                 " ON CONFLICT(name) DO UPDATE SET value = excluded.value, at = excluded.at", (name, value, hand.now()))


def stars(gh: GitHubApi, conn: sqlite3.Connection) -> int:
    """New stars on Teeny's own repositories since the last read, one
    `gh · star` stimulus per repository; how many stimuli."""
    from .. import stimuli

    got = gh.call("GET", gh._path("users", user(), "repos"), [("type", "owner"), ("per_page", str(MAX_REPOS))])  # noqa: SLF001
    added = 0
    for repo in got if isinstance(got, list) else []:
        if not isinstance(repo, Mapping) or repo.get("fork") or not isinstance(repo.get("full_name"), str):
            continue
        count = repo.get("stargazers_count")
        if not isinstance(count, int):
            continue
        full = repo["full_name"]
        row = conn.execute("SELECT stars FROM gh_stars WHERE repo = ?", (full,)).fetchone()
        with hand.transaction(conn):
            conn.execute("INSERT INTO gh_stars (repo, stars, at) VALUES (?, ?, ?)"
                         " ON CONFLICT(repo) DO UPDATE SET stars = excluded.stars, at = excluded.at", (full, count, hand.now()))
        if row is not None and count > row["stars"]:
            new = count - row["stars"]
            stimuli.add("gh", "star", f"{full}:{count}",
                        f"{new} new star(s) on {full}; it has {count} now.",
                        payload={"repo": full, "stars": count, "new": new})
            added += 1
    return added


def ingest(gh: GitHubApi | None = None) -> Json:
    """Reads new notifications and star counts and hands what matters to the
    mind. Safe to run at any time (a lock); writes nothing to GitHub."""
    if not hand.flag("GH_INBOUND", False):
        return {"skipped": "GitHub intake is off (GH_INBOUND in secrets/teeny.env)"}
    gh = gh or GitHubApi.from_settings()
    with hand.lock("gh-ingest", timeout=60):
        conn = db()
        try:
            since = _cursor(conn, "notifications") or _iso(hand.now() - FIRST_LOOKBACK_SECS)
            got = gh.call("GET", "/notifications", [("all", "true"), ("since", since), ("per_page", str(PAGE))])
            notes = [n for n in (got if isinstance(got, list) else []) if isinstance(n, Mapping)]
            counts = {s: 0 for s in STATUSES}
            # Oldest first, so the cursor only moves past what was read.
            notes.sort(key=lambda n: str(n.get("updated_at") or ""))
            newest = since
            for note in notes:
                _one(gh, conn, note, counts)
                updated = note.get("updated_at")
                if isinstance(updated, str) and updated > newest:
                    newest = updated
            if newest != since:
                _set_cursor(conn, "notifications", newest)
            starred = stars(gh, conn)
        finally:
            conn.close()
    return {"read": len(notes), "new": {s: n for s, n in counts.items() if n}, "stars": starred, "cursor": newest}


def recent(limit: int = 20) -> Json:
    conn = db()
    try:
        rows = conn.execute("SELECT key, repo, number, kind, author, status, note, at FROM gh_notifications"
                            " ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return {"notifications": [dict(r) for r in rows]}
