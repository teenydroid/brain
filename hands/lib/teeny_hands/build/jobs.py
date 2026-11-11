"""The builder's jobs, in `build_jobs`:

  build_jobs(id, kind, request_id, slug, repo, text, workdir, status,
             claimed_at, finished_at, summary, tests, ship_attempts,
             ship_error, shipped_at, repo_url, commit_url)

  kind     request (a build request someone made, `build_requests`, picked
           by somebody else), daily (Teeny's own small thing, at most one
           a UTC day, made only when no request is waiting), or self (a
           change to Teeny's own repository: a request Teeny proposed with
           `propose_self`, built in its clone and shipped as a pull request;
           build/selfclone.py)
  status   claimed  the builder has it
           ok       built and tested, waiting for `ship`
           failed   the builder gave up, or shipping failed MAX_SHIP_ATTEMPTS times
           shipped  pushed to GitHub as GITHUB_USER/<repo> (a self job: its
                    branch pushed and a pull request opened, `pr_url`)

A job's folder is state/workbench/<slug>/; the builder works only there, and
`ship` pushes only that folder (hands/gh refuses any other). A self job's
folder is the clone, state/workbench/brain/, on the branch teeny/<slug>.

This hand reads `build_requests` and writes back only to a request it
built: `shipped` with the repository, or `failed` with the reason.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .. import desk as _desk
from .. import hand
from .. import requests as build_requests
from . import selfclone

KINDS = ("request", "daily", "self")
STATUSES = ("claimed", "ok", "failed", "shipped")
#: A claim older than this is abandoned: the builder run that took it ended
#: without reporting (a crash, a timeout). It is failed and the queue moves on.
STALE_CLAIM_SECONDS = 6 * 3600
#: Shipping is retried on the next bin/build-ship run; after this many
#: refusals or errors the job is failed instead of tried forever.
MAX_SHIP_ATTEMPTS = 3
_TEXT_CHARS = 2000
_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BRANCH = "main"

#: What a daily job asks for when nobody asked for anything.
DAILY_BRIEF = (
    "Nobody has asked for anything today. Build one small, self-contained, useful thing of your own "
    "choosing: a command-line tool, a library function or a tiny game. Python standard library only, "
    "a README that says what it does and how to run it, and tests that pass."
)

#: Handed to the builder with every job, so the rules travel with the work.
RULES = (
    "Work only inside workdir. Write small, working code with tests and run them. Never read or "
    "write anything outside workdir, never open secrets/, and never push: shipping happens later "
    "and is not yours to do. When finished, write your report to report_to, once."
)

#: The build_requests source of a change Teeny proposes to itself.
SELF_SOURCE = "self"
#: Teeny's own instructions for a self job (its to edit, like every agent
#: prompt), handed to the builder with the job.
SELF_INSTRUCTIONS = "brain/agents/builder-self.md"
#: The rules that travel with a self job.
SELF_RULES = (
    "workdir is a clone of your own repository, on the branch named in branch; its brain/ is the clone's, yours to "
    "edit there. Change only what docs/evolve.md lets you change: a frozen path (evals/frozen.txt) is refused when "
    "it ships, and the whole change with it. Run the tests that need no network from inside workdir. Never commit, "
    "push or switch branches: shipping commits as you and opens a pull request the operator reviews. When finished, "
    "write your report to report_to, once."
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS build_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    request_id    INTEGER UNIQUE,
    slug          TEXT NOT NULL UNIQUE,
    repo          TEXT NOT NULL,
    text          TEXT NOT NULL,
    workdir       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'claimed',
    claimed_at    REAL NOT NULL,
    finished_at   REAL,
    summary       TEXT,
    tests         TEXT,
    ship_attempts INTEGER NOT NULL DEFAULT 0,
    ship_error    TEXT,
    shipped_at    REAL,
    repo_url      TEXT,
    commit_url    TEXT,
    pr_url        TEXT
);
CREATE INDEX IF NOT EXISTS build_jobs_status ON build_jobs (status, claimed_at);
"""

Json = dict[str, Any]


def db() -> sqlite3.Connection:
    """hands.db with both tables in place: ours and the requests we read,
    and the columns added since a table was first made."""
    conn = hand.open_db(SCHEMA + build_requests.SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(build_jobs)")}
    if "pr_url" not in have:
        try:
            conn.execute("ALTER TABLE build_jobs ADD COLUMN pr_url TEXT")
            conn.commit()
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc):
                raise
    return conn


def workbench() -> Path:
    return hand.state_dir() / "workbench"


def _row(row: sqlite3.Row | None) -> Json | None:
    if row is None:
        return None
    out = {k: row[k] for k in row.keys() if row[k] is not None}
    if "tests" in out:
        try:
            out["tests"] = json.loads(out["tests"])
        except ValueError:
            pass
    return out


def _slugify(text: str, limit: int = 40) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = ""
    for word in words:
        candidate = f"{slug}-{word}" if slug else word
        if len(candidate) > limit:
            break
        slug = candidate
    return slug or "thing"


def _today() -> str:
    return _dt.datetime.fromtimestamp(hand.now(), _dt.timezone.utc).strftime("%Y-%m-%d")


def _offer(job: Json, resumed: bool = False) -> Json:
    """What the builder gets for a job: what to build, where, and the rules."""
    path = hand.root() / job["workdir"]
    path.mkdir(parents=True, exist_ok=True)
    offer = {
        "job_id": job["id"],
        "kind": job["kind"],
        "request": job["text"],
        "workdir": job["workdir"],
        "workdir_path": str(path),
        "repo": job["repo"],
        "resumed": resumed,
    }
    if job["kind"] != "self":
        return {"job": offer, "rules": RULES}
    branch = self_branch(job)
    instructions = hand.root() / SELF_INSTRUCTIONS
    offer.update(branch=branch, base=selfclone.base_of(branch),
                 instructions=instructions.read_text() if instructions.is_file() else None)
    return {"job": offer, "rules": SELF_RULES}


def self_branch(job: Mapping[str, Any]) -> str:
    return selfclone.BRANCH_PREFIX + job["slug"]


def self_status(branch: str) -> str | None:
    """The status of the self job that owns BRANCH, or None."""
    if not branch.startswith(selfclone.BRANCH_PREFIX):
        return None
    conn = db()
    try:
        row = conn.execute("SELECT status FROM build_jobs WHERE kind = 'self' AND slug = ?",
                           (branch[len(selfclone.BRANCH_PREFIX):],)).fetchone()
    finally:
        conn.close()
    return None if row is None else row["status"]


def propose_self(body: Any) -> Json:
    """A change Teeny wants made to itself: a pending build request from
    itself, picked (hands/requests pick) like any other."""
    if not isinstance(body, Mapping):
        raise hand.HandError("stdin must be a JSON object with text")
    unknown = sorted(set(body) - {"text"})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    text = _text(body, "text")
    from ..gh.client import user

    conn = db()
    try:
        with hand.transaction(conn):
            source_id = f"{hand.now():.6f}"
            build_requests.record(SELF_SOURCE, source_id, user(), text, conn=conn)
            row = conn.execute("SELECT id FROM build_requests WHERE source = ? AND source_id = ?",
                               (SELF_SOURCE, source_id)).fetchone()
    finally:
        conn.close()
    return {"id": row["id"], "status": "pending"}


def _abandon_stale(conn: sqlite3.Connection) -> list[int]:
    cutoff = hand.now() - STALE_CLAIM_SECONDS
    rows = conn.execute(
        "SELECT id, request_id FROM build_jobs WHERE status = 'claimed' AND claimed_at < ?", (cutoff,)
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE build_jobs SET status = 'failed', finished_at = ?, summary = ? WHERE id = ?",
            (hand.now(), "abandoned: the builder never reported back", row["id"]),
        )
        if row["request_id"] is not None:
            _mark_request(conn, row["request_id"], "failed", "the build was abandoned")
    return [row["id"] for row in rows]


def _mark_request(conn: sqlite3.Connection, request_id: int, status: str, note: str) -> None:
    """Writes back to a request this hand built, and only while it is still
    picked (nobody else changed their mind about it meanwhile)."""
    conn.execute(
        "UPDATE build_requests SET status = ?, note = ? WHERE id = ? AND status = 'picked'",
        (status, note[:500], request_id),
    )


def next_job() -> Json:
    """Claims work for the builder, in this order: its own open claim (a run
    that ended mid-job resumes it), the oldest picked build request, then a
    daily job if none was made today. Nothing to do is an answer, not an error.

    A picked request Teeny made of itself (source `self`) becomes a self job:
    the clone is synced and a teeny/<slug> branch started from the fresh
    origin/main. While the clone is not free (unshipped work on it, or no
    network) it waits, named in `waiting`, and the queue moves on."""
    waiting: str | None = None
    with hand.lock("build-queue"):
        conn = db()
        try:
            with hand.transaction(conn):
                abandoned = _abandon_stale(conn)
                open_claim = conn.execute(
                    "SELECT * FROM build_jobs WHERE status = 'claimed' ORDER BY id LIMIT 1"
                ).fetchone()
                if open_claim is not None:
                    return {**_offer(_row(open_claim), resumed=True), "abandoned": abandoned}
                request = conn.execute(
                    "SELECT r.* FROM build_requests r LEFT JOIN build_jobs j ON j.request_id = r.id"
                    " WHERE r.status = 'picked' AND j.id IS NULL ORDER BY r.created_at, r.id LIMIT 1"
                ).fetchone()
            if request is not None and request["source"] == SELF_SOURCE:
                # Outside the transaction: this fetches from GitHub.
                slug = f"self-{request['id']}-{_slugify(request['text'])}"
                try:
                    selfclone.start(selfclone.BRANCH_PREFIX + slug)
                except (hand.Refused, hand.HandError) as exc:
                    waiting = f"self request {request['id']}: {exc.reason if isinstance(exc, hand.Refused) else exc}"
                    request = None
            with hand.transaction(conn):
                if request is not None and conn.execute(
                    "SELECT 1 FROM build_requests WHERE id = ? AND status = 'picked'", (request["id"],)
                ).fetchone() is None:
                    request = None  # unpicked meanwhile
                if request is not None and request["source"] == SELF_SOURCE:
                    kind, request_id, text = "self", request["id"], request["text"][:_TEXT_CHARS]
                    repo, workdir = selfclone.REPO_NAME, f"state/workbench/{selfclone.REPO_NAME}"
                elif request is not None:
                    slug = f"req-{request['id']}-{_slugify(request['text'])}"
                    kind, request_id, text = "request", request["id"], request["text"][:_TEXT_CHARS]
                    repo, workdir = slug, f"state/workbench/{slug}"
                else:
                    today = _today()
                    if conn.execute("SELECT 1 FROM build_jobs WHERE slug = ?", (f"daily-{today}",)).fetchone():
                        return {"job": None, "reason": "nothing to build: no picked request, and today's daily job exists",
                                "abandoned": abandoned, **({"waiting": waiting} if waiting else {})}
                    slug, kind, request_id, text = f"daily-{today}", "daily", None, DAILY_BRIEF
                    repo, workdir = slug, f"state/workbench/{slug}"
                cursor = conn.execute(
                    "INSERT INTO build_jobs (kind, request_id, slug, repo, text, workdir, claimed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (kind, request_id, slug, repo, text, workdir, hand.now()),
                )
                job = _row(conn.execute("SELECT * FROM build_jobs WHERE id = ?", (cursor.lastrowid,)).fetchone())
        finally:
            conn.close()
    assert job is not None
    return {**_offer(job), "abandoned": abandoned, **({"waiting": waiting} if waiting else {})}


def _text(body: Mapping[str, Any], key: str, required: bool = True) -> str | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise hand.HandError(f'"{key}" must be a non-empty string')
    return value.strip()[:_TEXT_CHARS]


def _tests(value: Any) -> str:
    """What the builder ran and what came of it: a sentence, or a list of
    them (one per command). Stored as JSON."""
    if isinstance(value, str) and value.strip():
        return json.dumps(value.strip()[:_TEXT_CHARS])
    if isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value):
        return json.dumps([v.strip()[:500] for v in value[:20]])
    raise hand.HandError('"tests" must be a non-empty string or a list of strings')


def done(body: Any) -> Json:
    """Finishes the builder's claim: ok (built and tested; `ship` will push
    it) or failed (with why)."""
    if not isinstance(body, Mapping):
        raise hand.HandError("stdin must be a JSON object with job_id, status, summary, tests")
    unknown = sorted(set(body) - {"job_id", "status", "summary", "tests", "repo"})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    job_id = body.get("job_id")
    if isinstance(job_id, bool) or not isinstance(job_id, int):
        raise hand.HandError('"job_id" must be the number build_next gave')
    status = body.get("status")
    if status not in ("ok", "failed"):
        raise hand.HandError('"status" must be "ok" or "failed"')
    summary = _text(body, "summary")
    tests = _tests(body.get("tests"))
    repo = body.get("repo")
    if repo is not None and (not isinstance(repo, str) or not _REPO_NAME.match(repo) or repo in (".", "..")
                             or repo.endswith(".git")):
        raise hand.HandError('"repo" must be a plain repository name')
    conn = db()
    try:
        with hand.transaction(conn):
            row = conn.execute("SELECT * FROM build_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise hand.Refused("unknown job", job_id=job_id)
            if row["status"] != "claimed":
                raise hand.Refused(f"job {job_id} is {row['status']}, not claimed", job_id=job_id, status=row["status"])
            folder = hand.root() / row["workdir"]
            if row["kind"] == "self":
                repo = None  # a self job ships to Teeny's own repository, whatever the report says
                if status == "ok" and not selfclone.has_changes():
                    raise hand.Refused("the clone has no change; an ok self job has something to ship", job_id=job_id)
            elif status == "ok" and not (folder.is_dir() and any(folder.iterdir())):
                raise hand.Refused("the workdir is empty; an ok job has something to ship", job_id=job_id)
            conn.execute(
                "UPDATE build_jobs SET status = ?, summary = ?, tests = ?, finished_at = ?, repo = COALESCE(?, repo)"
                " WHERE id = ?",
                (status, summary, tests, hand.now(), repo, job_id),
            )
            if status == "failed" and row["request_id"] is not None:
                _mark_request(conn, row["request_id"], "failed", summary or "the build failed")
    finally:
        conn.close()
    return {"job_id": job_id, "status": status}


#: What the builder writes to state/workbench/desk/build-done.json.
REPORT_SHAPE = (
    '{"job_id": <the job_id above>, "status": "ok" or "failed", "summary": "<one line first: what it is, or why it '
    'failed>", "tests": "<what you ran and what came of it>" (or a list of those), "repo": "<optional better name>"}'
)


def desk() -> Json:
    """The builder's desk (teeny_hands.desk): settle its report, then put
    its next job on state/workbench/desk/build.json."""
    return _desk.run("build", done, next_job, REPORT_SHAPE)


def listing(status: str | None = None, limit: int = 50) -> Json:
    if status is not None and status not in STATUSES:
        raise hand.HandError(f"status must be one of {', '.join(STATUSES)}")
    conn = db()
    try:
        query = "SELECT * FROM build_jobs"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        rows = conn.execute(query + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
    finally:
        conn.close()
    return {"jobs": [_row(r) for r in rows]}


# --- shipping -----------------------------------------------------------------------------

_GITIGNORE = "__pycache__/\n*.pyc\n.pytest_cache/\n.venv/\nnode_modules/\n"


def _ship_one(job: Json, gh: Any) -> Json:
    """Creates GITHUB_USER/<repo> when it does not exist and pushes the
    job's folder to it, through hands/gh's actions (guard, owner rule,
    secret scan, DRY_RUN)."""
    from ..gh import actions
    from ..gh.client import GitHubApi, user

    owner = user()
    full = f"{owner}/{job['repo']}"
    summary = (job.get("summary") or job["slug"]).strip()
    first_line = summary.splitlines()[0][:200] if summary else job["slug"]
    path = hand.root() / job["workdir"]
    if not (path / ".gitignore").exists():
        (path / ".gitignore").write_text(_GITIGNORE)
    push_body = {"workdir": job["workdir"], "repo": full, "branch": BRANCH, "message": first_line[:72]}
    if hand.dry_run():
        made = actions.create_repo({"name": job["repo"], "description": first_line})
        pushed = actions.push(push_body)
        return {"job_id": job["id"], "dry_run": True, "would_create": made.get("would_create"),
                "would_push": pushed.get("would_push"), "branch": BRANCH}
    gh = gh or GitHubApi.from_settings()
    created = False
    if gh.repo(owner, job["repo"]) is None:
        actions.create_repo({"name": job["repo"], "description": first_line}, gh=gh)
        created = True
    pushed = actions.push(push_body, gh=gh)
    return {"job_id": job["id"], "repo": full, "created": created, "commit": pushed["commit"],
            "repo_url": f"https://github.com/{full}", "commit_url": f"https://github.com/{full}/commit/{pushed['commit']}"}


def ship(gh: Any = None) -> Json:
    """Every ok job not yet shipped, oldest first. A refusal or an error is
    kept on the job and retried next run, up to MAX_SHIP_ATTEMPTS."""
    conn = db()
    try:
        jobs = [_row(r) for r in conn.execute("SELECT * FROM build_jobs WHERE status = 'ok' ORDER BY id").fetchall()]
    finally:
        conn.close()
    results: list[Json] = []
    with hand.lock("build-ship"):
        for job in jobs:
            assert job is not None
            try:
                result = _ship_one(job, gh)
            except (hand.Refused, hand.HandError) as exc:
                reason = exc.reason if isinstance(exc, hand.Refused) else str(exc)
                attempts = int(job.get("ship_attempts") or 0) + 1
                give_up = attempts >= MAX_SHIP_ATTEMPTS
                conn = db()
                try:
                    with hand.transaction(conn):
                        conn.execute(
                            "UPDATE build_jobs SET ship_attempts = ?, ship_error = ?, status = ? WHERE id = ?",
                            (attempts, reason[:500], "failed" if give_up else "ok", job["id"]),
                        )
                        if give_up and job.get("request_id") is not None:
                            _mark_request(conn, job["request_id"], "failed", f"could not ship: {reason}")
                finally:
                    conn.close()
                results.append({"job_id": job["id"], "error": reason, "attempts": attempts, "gave_up": give_up,
                                **({"detail": exc.detail} if isinstance(exc, hand.Refused) and exc.detail else {})})
                continue
            if not result.get("dry_run"):
                conn = db()
                try:
                    with hand.transaction(conn):
                        conn.execute(
                            "UPDATE build_jobs SET status = 'shipped', shipped_at = ?, repo_url = ?, commit_url = ?,"
                            " ship_error = NULL WHERE id = ?",
                            (hand.now(), result["repo_url"], result["commit_url"], job["id"]),
                        )
                        if job.get("request_id") is not None:
                            _mark_request(conn, job["request_id"], "shipped", result["repo_url"])
                finally:
                    conn.close()
            results.append(result)
    return {"shipped": [r for r in results if "commit" in r], "results": results,
            "dry_run": hand.dry_run()}
