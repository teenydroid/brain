"""Teeny's own clone: a working copy of GITHUB_USER/brain that the builder
may change in a `self` job (docs/evolve.md, "Teeny's own clone").

  state/workbench/brain/   the working tree, where the builder edits (its
                           file tools reach state/workbench/ and no further)
  state/self/brain.git     its git directory, kept outside the workbench. Git
                           reads its config from there, and a config the
                           builder could write could send a push (and its
                           token) somewhere else, or run a program when the
                           ship commits. Every git run here names both paths,
                           so nothing in the working tree can redirect it.
  state/self/sync.json     the last sync: when, origin/main's commit, the
                           clone's branch and state

`sync` runs from cron (bin/build-ship, every 30 minutes), outside the
builder's sandbox, which has no network. It fetches origin's main and
teeny/* branches, moves the local main ref to origin/main whenever that is a
fast-forward, and then leaves the working tree in one of these states:

  cloned    there was no clone, so one is made at origin/main
  synced    main was checked out with nothing uncommitted, and now matches
            origin/main. A teeny/<slug> branch whose self job shipped or
            failed is parked first: anything left uncommitted on it is
            committed to it (as Teeny), and main is checked out (`parked`)
  dirty     main has uncommitted edits: left as they are
  diverged  main has commits origin/main lacks: left as they are
  busy      another branch is checked out (a teeny/<slug> with unshipped
            work): left as it is, and its distance from main is reported

It never resets, never forces and never deletes a branch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import git, hand
from ..gh import gitpush
from ..gh.client import split_repo, user

REPO_NAME = "brain"
BASE = "main"
BRANCH_PREFIX = "teeny/"
ORIGIN_BASE = f"refs/remotes/origin/{BASE}"

Json = dict[str, Any]


def worktree() -> Path:
    return hand.state_dir() / "workbench" / REPO_NAME


def gitdir() -> Path:
    return hand.state_dir() / "self" / f"{REPO_NAME}.git"


def record_path() -> Path:
    return hand.state_dir() / "self" / "sync.json"


def repo() -> str:
    return f"{user()}/{REPO_NAME}"


def _url() -> str:
    owner, name = split_repo(repo())
    return gitpush.remote_url(owner, name)


def _git(*args: str, check: bool = True, token: str | None = None):
    return git.run(worktree(), "--git-dir", str(gitdir()), "--work-tree", str(worktree()), *args,
                   check=check, token=token)


def _out(*args: str) -> str:
    return _git(*args).stdout.decode("utf-8", "replace").strip()


def _ok(*args: str) -> bool:
    return _git(*args, check=False).returncode == 0


def _rev(ref: str) -> str | None:
    done = _git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    if done.returncode != 0:
        return None
    return done.stdout.decode().strip() or None


def _is_ancestor(older: str, newer: str) -> bool:
    return _ok("merge-base", "--is-ancestor", older, newer)


def _clone() -> None:
    tree = worktree()
    if tree.exists() and any(tree.iterdir()):
        raise hand.Refused(f"{tree.relative_to(hand.root())} is there but is not Teeny's clone; it was left alone",
                           rule="not_the_clone")
    gitdir().parent.mkdir(parents=True, exist_ok=True)
    git.run(hand.state_dir(), "clone", "-q", "--branch", BASE, "--separate-git-dir", str(gitdir()), _url(), str(tree),
            token=hand.setting("GITHUB_TOKEN"))
    # The pointer git leaves in the working tree is not needed (every run
    # names the git directory) and would only lead somewhere else if edited.
    (tree / ".git").unlink(missing_ok=True)


def fetch() -> None:
    """origin's main and its teeny/* branches. A token, when there is one,
    goes only to this command's askpass (a private repository needs it)."""
    _git("fetch", "-q", "--prune", _url(), f"+refs/heads/{BASE}:{ORIGIN_BASE}",
         f"+refs/heads/{BRANCH_PREFIX}*:refs/remotes/origin/{BRANCH_PREFIX}*", token=hand.setting("GITHUB_TOKEN"))


def current_branch() -> str | None:
    done = _git("symbolic-ref", "--short", "-q", "HEAD", check=False)
    return done.stdout.decode().strip() or None


def dirty() -> bool:
    return bool(_out("status", "--porcelain", "--untracked-files=all"))


def _branches() -> list[Json]:
    """Every local teeny/* branch: commits ahead of and behind origin/main,
    and whether origin has exactly it."""
    names = _out("for-each-ref", "--format=%(refname:short)", f"refs/heads/{BRANCH_PREFIX}").splitlines()
    out = []
    for name in sorted(n for n in names if n):
        head = _rev(f"refs/heads/{name}")
        out.append({
            "branch": name,
            "ahead": int(_out("rev-list", "--count", f"{ORIGIN_BASE}..refs/heads/{name}")),
            "behind": int(_out("rev-list", "--count", f"refs/heads/{name}..{ORIGIN_BASE}")),
            "pushed": head is not None and _rev(f"refs/remotes/origin/{name}") == head,
        })
    return out


def _finished(branch: str) -> bool:
    """The branch's self job shipped or failed: nothing on it is in progress."""
    from . import jobs

    return jobs.self_status(branch) in ("shipped", "failed")


def _park(branch: str) -> None:
    if dirty():
        _git("add", "-A")
        _git("commit", "-q", "-m", f"teeny: keep what {branch} left uncommitted")
    _git("checkout", "-q", BASE)


def _write_record(record: Json) -> None:
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _sync_locked() -> Json:
    if not gitdir().exists():
        _clone()
        state = "cloned"
    else:
        worktree().mkdir(parents=True, exist_ok=True)
        fetch()
        state = None
    origin = _rev(ORIGIN_BASE)
    if origin is None:
        raise hand.HandError(f"origin has no {BASE} branch")
    branch = current_branch()
    if branch != BASE:
        local = _rev(f"refs/heads/{BASE}")
        if local is None or (local != origin and _is_ancestor(local, origin)):
            _git("update-ref", f"refs/heads/{BASE}", origin, *([local] if local else []))
    parked = None
    if state is None and branch and branch.startswith(BRANCH_PREFIX) and _finished(branch):
        _park(branch)
        parked, branch = branch, BASE
    if state is None:
        if branch != BASE:
            state = "busy"
        elif dirty():
            state = "dirty"
        elif _is_ancestor("HEAD", origin):
            _git("merge", "-q", "--ff-only", origin)
            state = "synced"
        else:
            state = "diverged"
    record = {
        "at": hand.now(),
        "repo": repo(),
        "commit": origin,
        "head": _rev("HEAD"),
        "branch": current_branch(),
        "state": state,
        "branches": _branches(),
        **({"parked": parked} if parked else {}),
    }
    _write_record(record)
    return record


def sync() -> Json:
    """Fetches, fast-forwards what may be fast-forwarded, records it."""
    with hand.lock("self-clone"):
        return _sync_locked()


def start(branch: str) -> str:
    """Syncs, then starts BRANCH from the fresh origin/main for a self job.
    Refused while the clone is not free: anything but a clean main."""
    with hand.lock("self-clone"):
        record = _sync_locked()
        if record["state"] not in ("cloned", "synced"):
            raise hand.Refused(f"the clone is not free ({record['state']} on {record['branch']})", rule="busy")
        _git("checkout", "-q", "-b", branch, ORIGIN_BASE)
        return record["commit"]


def base_of(branch: str) -> str | None:
    """Where BRANCH left origin/main."""
    done = _git("merge-base", ORIGIN_BASE, f"refs/heads/{branch}", check=False)
    if done.returncode != 0:
        return None
    return done.stdout.decode().strip() or None


def has_changes() -> bool:
    """The checked-out branch differs from origin/main: edits, or commits."""
    return dirty() or int(_out("rev-list", "--count", f"{ORIGIN_BASE}..HEAD")) > 0
