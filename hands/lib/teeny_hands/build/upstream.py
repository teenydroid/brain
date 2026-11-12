"""Upstream contributions (docs/roadmap.md, A8): one gap from docs/oh-gaps.md,
built in a fork of OpenHuman and opened as one disclosed pull request.

  state/workbench/upstream-openhuman/   the working tree, where the builder
                                        edits (inside its workbench)
  state/upstream/openhuman.git          its git directory, out of the
                                        builder's reach, as for Teeny's own
                                        clone (build/selfclone.py): nothing in
                                        the working tree can redirect a fetch
                                        or a push

The flow, every step through code that already holds its rule:

1. `propose` (hands/build upstream, stdin {gap, text}): the weekly picker
   playbook (brain/playbooks/upstream-pick.md) names one open gap and says
   how to close it. It becomes a build request from source `upstream`, and
   is picked at once unless another request is in flight (one at a time,
   hands/requests). Refused unless BUILD_UPSTREAM=1, for a gap that is
   solved, unknown, already in flight or shipped, or tried in the last
   RETRY_DAYS.
2. `start` (from hands/build next): the project's contributing policy
   (hands/gh contributing) is read first, and a project that forbids AI
   contributions is never worked on: the request is skipped with why. Then
   the clone fetches UPSTREAM's main and starts teeny/<slug> from it.
3. The builder works in the folder (an `upstream` job), as for any job.
4. `ship` (hands/build ship): the guard reads the title and body; the
   external rules run before anything is pushed (today's cap of pull
   requests to other projects, one open pull request there at a time, the
   policy again: hands/gh actions.check_external); Teeny's fork is made when
   missing; the branch is pushed to the fork (push_branch: own repository,
   teeny/ branch only, never forced, secret-scanned) and the pull request
   opened on UPSTREAM through hands/gh open-pr, which adds the disclosure
   footer and applies the same rules once more under its lock.

Under DRY_RUN nothing is forked, pushed or opened: the answer carries the
branch and the pull request's title and body, disclosure included.

What this never does: babysit the pull request it opened. Strangers' review
comments and CI logs reach only the tool-less mind (hands/gh ingest), never
the builder, which holds tools.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .. import git, hand
from ..gh import gitpush
from ..gh.client import split_repo, user

#: The project the gaps are about (docs/oh-gaps.md), and the only one this
#: build kind contributes to.
UPSTREAM_REPO = "tinyhumansai/openhuman"
BASE = "main"
BRANCH_PREFIX = "teeny/"
UPSTREAM_REF = f"refs/remotes/upstream/{BASE}"
#: The build_requests source of an upstream contribution.
SOURCE = "upstream"
#: The page the gaps are read from, in the operator's checkout.
GAPS_DOC = "docs/oh-gaps.md"
#: A gap whose last try failed or was skipped may be tried again after this.
RETRY_DAYS = 30
#: The switch: off unless secrets/teeny.env sets it.
FLAG = "BUILD_UPSTREAM"

Json = dict[str, Any]

_ROW = re.compile(r"^\|\s*(G\d+)\b(.*?)\|(.*?)\|(.*?)\|(.*?)\|\s*$")
_ISSUE = re.compile(r"\((https://github\.com/[^)\s]+/(?:issues|pull)/\d+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_TAGS = re.compile(r"\s+")


def name() -> str:
    return split_repo(UPSTREAM_REPO)[1]


def fork_repo() -> str:
    return f"{user()}/{name()}"


def worktree() -> Path:
    return hand.state_dir() / "workbench" / f"upstream-{name()}"


def gitdir() -> Path:
    return hand.state_dir() / "upstream" / f"{name()}.git"


def workdir_rel() -> str:
    return f"state/workbench/upstream-{name()}"


def enabled() -> bool:
    return hand.flag(FLAG, False)


# --- the gaps --------------------------------------------------------------------------------


def _plain(cell: str) -> str:
    return _TAGS.sub(" ", cell.replace("`", "").replace("**", "")).strip()


def gaps() -> list[Json]:
    """Every gap row of docs/oh-gaps.md: its id, title (the first bold
    phrase), the upstream issue it links, the change it proposes, and
    whether it is already solved there."""
    path = hand.root() / GAPS_DOC
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        found = _ROW.match(line.strip())
        if not found:
            continue
        gap_id, id_rest, gap, _workaround, change = found.groups()
        bold = _BOLD.search(gap)
        title = _plain(bold.group(1)) if bold else _plain(gap)[:120]
        solved = bool(re.search(r"\b(?:solved|resolved|landed)\b", id_rest + " " + title, re.IGNORECASE)) or \
            _plain(change).lower().startswith("done")
        issue = _ISSUE.search(id_rest)
        out.append({
            "id": gap_id,
            "title": title.rstrip("."),
            "issue": issue.group(1) if issue else None,
            "gap": _plain(gap)[:1500],
            "change": _plain(change)[:1500],
            "solved": solved,
        })
    return out


def _attempts(conn: Any, gap_id: str) -> list[Any]:
    return conn.execute(
        "SELECT id, status, created_at, note FROM build_requests WHERE source = ? AND source_id LIKE ? ORDER BY id",
        (SOURCE, f"{gap_id}:%"),
    ).fetchall()


def _why_not(conn: Any, gap: Json) -> str | None:
    """Why this gap can't be proposed now; None when it can."""
    if gap["solved"]:
        return "solved upstream"
    for row in reversed(_attempts(conn, gap["id"])):
        if row["status"] in ("pending", "picked"):
            return f"in flight (request {row['id']})"
        if row["status"] == "shipped":
            return f"already opened ({row['note'] or 'request ' + str(row['id'])})"
        if hand.now() - float(row["created_at"]) < RETRY_DAYS * 86400:
            return f"tried within {RETRY_DAYS} days (request {row['id']}, {row['status']})"
    return None


def listing() -> Json:
    """`hands/build gaps`: the gaps, each with whether it can be proposed."""
    from . import jobs

    conn = jobs.db()
    try:
        rows = []
        for gap in gaps():
            why = _why_not(conn, gap)
            rows.append({**gap, "open": why is None, **({"why_not": why} if why else {})})
    finally:
        conn.close()
    return {"repo": UPSTREAM_REPO, "enabled": enabled(), "gaps": rows}


def propose(body: Any) -> Json:
    """One gap, and how Teeny would close it: a build request from source
    `upstream`, picked at once when nothing else is in flight."""
    from collections.abc import Mapping

    from .. import requests as build_requests
    from . import jobs

    if not enabled():
        raise hand.Refused(f"upstream contributions are off ({FLAG} in secrets/teeny.env)", rule="off")
    if not isinstance(body, Mapping):
        raise hand.HandError("stdin must be a JSON object with gap and text")
    unknown = sorted(set(body) - {"gap", "text"})
    if unknown:
        raise hand.HandError(f"unknown field(s) in the body: {', '.join(unknown)}")
    gap_id = body.get("gap")
    if not isinstance(gap_id, str) or not re.fullmatch(r"G\d+", gap_id.strip()):
        raise hand.HandError('"gap" must be a gap id from docs/oh-gaps.md, like "G13"')
    gap_id = gap_id.strip()
    text = jobs._text(body, "text")
    found = {g["id"]: g for g in gaps()}.get(gap_id)
    if found is None:
        raise hand.Refused(f"there is no gap {gap_id} in {GAPS_DOC}", rule="unknown_gap")
    conn = jobs.db()
    try:
        why = _why_not(conn, found)
        if why:
            raise hand.Refused(f"gap {gap_id} can't be proposed: {why}", rule="gap", gap=gap_id)
        with hand.transaction(conn):
            source_id = f"{gap_id}:{hand.now():.6f}"
            brief = (f"Contribute to {UPSTREAM_REPO}: close gap {gap_id} ({found['title']}). "
                     f"The gap: {found['gap']} The change asked for: {found['change']} How: {text}")
            build_requests.record(SOURCE, source_id, user(), brief, conn=conn)
            row = conn.execute("SELECT id FROM build_requests WHERE source = ? AND source_id = ?",
                               (SOURCE, source_id)).fetchone()
    finally:
        conn.close()
    out: Json = {"id": row["id"], "gap": gap_id, "status": "pending"}
    try:
        build_requests.pick({"id": row["id"], "reason": f"upstream gap {gap_id}"})
        out["status"] = "picked"
    except hand.Refused as exc:
        out["waiting"] = exc.reason  # another request is in flight; pick-request picks it later
    return out


def gap_of(request_text: str) -> str | None:
    found = re.search(r"close gap (G\d+)", request_text or "")
    return found.group(1) if found else None


# --- the clone ---------------------------------------------------------------------------------


def _url() -> str:
    owner, repo_name = split_repo(UPSTREAM_REPO)
    return gitpush.remote_url(owner, repo_name)


def _git(*args: str, check: bool = True, token: str | None = None):
    return git.run(worktree(), "--git-dir", str(gitdir()), "--work-tree", str(worktree()), *args,
                   check=check, token=token)


def _out(*args: str) -> str:
    return _git(*args).stdout.decode("utf-8", "replace").strip()


def current_branch() -> str | None:
    done = _git("symbolic-ref", "--short", "-q", "HEAD", check=False)
    return done.stdout.decode().strip() or None


def dirty() -> bool:
    return bool(_out("status", "--porcelain", "--untracked-files=all"))


def has_changes() -> bool:
    """The checked-out branch differs from the project's main: edits, or
    commits."""
    return dirty() or int(_out("rev-list", "--count", f"{UPSTREAM_REF}..HEAD")) > 0


def fetch() -> None:
    """The project's main, read-only. A token, when there is one, goes only
    to this command's askpass."""
    _git("fetch", "-q", _url(), f"+refs/heads/{BASE}:{UPSTREAM_REF}", token=hand.setting("GITHUB_TOKEN"))


def _ensure() -> None:
    tree = worktree()
    if gitdir().exists():
        tree.mkdir(parents=True, exist_ok=True)
        return
    if tree.exists() and any(tree.iterdir()):
        raise hand.Refused(f"{tree.relative_to(hand.root())} is there but is not the upstream clone; it was left alone",
                           rule="not_the_clone")
    gitdir().parent.mkdir(parents=True, exist_ok=True)
    tree.mkdir(parents=True, exist_ok=True)
    git.run(hand.state_dir(), "init", "-q", "-b", BASE, "--separate-git-dir", str(gitdir()), str(tree))
    (tree / ".git").unlink(missing_ok=True)  # every run names the git directory


def start(branch: str, finished: Any) -> str:
    """Fetches the project's main and starts BRANCH from it. `finished(b)`
    says whether the job on branch b is over; a branch still in progress
    keeps the clone (refused, `busy`). What a finished branch left
    uncommitted is committed to it first, never thrown away."""
    from ..gh import reads

    if not branch.startswith(BRANCH_PREFIX):
        raise hand.HandError(f"an upstream branch is {BRANCH_PREFIX}<slug>")
    owner, repo_name = split_repo(UPSTREAM_REPO)
    verdict = reads.contributing(owner, repo_name)
    if verdict.get("forbids_ai"):
        raise hand.Refused(f"{UPSTREAM_REPO}'s contributing policy does not welcome AI contributions",
                           rule="contributing")
    with hand.lock("upstream-clone"):
        _ensure()
        fetch()
        on = current_branch()
        if on and on.startswith(BRANCH_PREFIX) and _git("rev-parse", "-q", "--verify", "HEAD", check=False).returncode == 0:
            if not finished(on):
                raise hand.Refused(f"the upstream clone is busy on {on}", rule="busy")
            if dirty():
                _git("add", "-A")
                _git("commit", "-q", "-m", f"teeny: keep what {on} left uncommitted")
        elif dirty() and _git("rev-parse", "-q", "--verify", "HEAD", check=False).returncode == 0:
            raise hand.Refused("the upstream clone has uncommitted edits off any branch; left alone", rule="dirty")
        _git("checkout", "-q", "-f" if on == BASE and not _has_head() else "-q", "-b", branch, UPSTREAM_REF)
        return _out("rev-parse", UPSTREAM_REF)


def _has_head() -> bool:
    return _git("rev-parse", "-q", "--verify", "HEAD", check=False).returncode == 0


# --- shipping ----------------------------------------------------------------------------------


def _tests_lines(tests: Any) -> list[str]:
    if isinstance(tests, list):
        return [str(t) for t in tests]
    return [str(tests)] if tests else []


def pr_text(job: Json) -> tuple[str, str]:
    """The pull request's title (the summary's first line) and body. The
    disclosure footer is hands/gh's to add (actions.DISCLOSURE)."""
    summary = (job.get("summary") or job["slug"]).strip()
    title = summary.splitlines()[0].strip()[:100] or job["slug"]
    gap = {g["id"]: g for g in gaps()}.get(gap_of(job.get("text", "")) or "")
    lines = [summary, ""]
    if gap and gap.get("issue"):
        lines += [f"Refs {gap['issue']}", ""]
    lines += ["**Tests** (run by the builder in its clone; this project's own CI is the check that counts)", ""]
    lines += [f"- {t}" for t in _tests_lines(job.get("tests"))] or ["- none reported"]
    return title, "\n".join(lines)


def ship(job: Json, gh: Any = None) -> Json:
    """Ships an upstream job: guard, the external rules, the fork, the push
    to the fork and the pull request on UPSTREAM_REPO. Never merges, never
    forces. Under DRY_RUN nothing is written anywhere; the answer has the
    branch and the pull request it would open."""
    from ..gh import actions
    from ..gh.client import GitHubApi

    owner, repo_name = split_repo(UPSTREAM_REPO)
    branch = job["branch"]
    with hand.lock("upstream-clone"):
        fetch()
        on = current_branch()
        if on != branch:
            raise hand.HandError(f"the upstream clone is on {on}, not {branch}")
        title, body = pr_text(job)
        fields = actions.guarded({"title": title, "body": body}, "pr", {"repo": UPSTREAM_REPO, "head": branch})
        if dirty():
            _git("add", "-A")
            _git("commit", "-q", "-m", fields["title"])
        if not has_changes():
            raise hand.Refused("the branch changes nothing", rule="empty")
        # The rules for somebody else's project read GitHub, never write it,
        # so they run under DRY_RUN too, and before anything is pushed.
        gh = gh or GitHubApi.from_settings()
        actions.check_external(gh, owner, repo_name)
        fork = gh.repo(user(), repo_name)
        if fork is not None and not fork.get("fork"):
            raise hand.Refused(f"{fork_repo()} exists and is not a fork of {UPSTREAM_REPO}", rule="not_a_fork")
        would_fork = fork is None
        if would_fork and not hand.dry_run():
            actions.fork({"repo": UPSTREAM_REPO}, gh=gh)
        pushed = actions.push_branch(gitdir(), fork_repo(), branch, UPSTREAM_REF, gh=gh)
        opened = actions.open_pr({"repo": UPSTREAM_REPO, "head": branch, "base": BASE, "title": fields["title"],
                                  "body": fields["body"]}, gh=gh)
    if pushed.get("dry_run"):
        return {"job_id": job["id"], "dry_run": True, "branch": branch, "would_fork": would_fork,
                "would_push": pushed["would_push"], "would_open_pr": opened.get("would_open_pr"),
                "head": opened.get("head"), "title": opened.get("title"), "body": opened.get("body")}
    full = fork_repo()
    return {"job_id": job["id"], "repo": UPSTREAM_REPO, "branch": branch, "commit": pushed["commit"],
            "pr_url": opened.get("html_url"), "number": opened.get("number"), "forked": would_fork,
            "repo_url": f"https://github.com/{full}", "commit_url": f"https://github.com/{full}/commit/{pushed['commit']}"}


def _unused() -> float:  # pragma: no cover - keeps `time` for callers that patch it
    return time.time()
