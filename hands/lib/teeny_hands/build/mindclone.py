"""The agentic mind's own clone: a working copy of GITHUB_USER/brain that the
jailed mind edits, and the one way that copy becomes a pull request
(docs/mind.md; bin/broker's /repo and /pr).

  state/home/repo/        the working tree. The mind's shell and file tools
                          reach it, so everything in it is hostile input: a
                          `.git`, links, hook-looking files, a tree swapped
                          for a link while the broker works
  state/mind/repo.git     its git directory, outside the jail. Git reads its
                          config and hooks from there, and every git run here
                          names it and the tree, so nothing the mind writes
                          can redirect git, run a program or reach the token
  state/mind/fresh-*, old-*
                          a checkout being made, and a tree moved aside; the
                          mind cannot reach either

`prepare` fetches origin's main and teeny/* branches. A missing, empty or
clean tree is replaced by a fresh checkout of origin/main, made in
state/mind/ (where the mind cannot race it) and renamed into place; the tree
it replaces is renamed aside first, which never follows a link. A tree with
edits is left as it is. Git never writes into the mind's tree.

`ship` stages the tree, refuses what the rules refuse (below), commits as
Teeny with commit-tree (no hooks, no checkout), checks the commit with the
frozen-path check and the secret scan exactly as a self job's ship does
(selfclone.ship: the frozen list read from origin/main, gitpush.scan_branch),
pushes it as teeny/mind-<slug> (hands/gh push_branch) and opens a ready pull
request into main (hands/gh open_pr). After a push the tree is checked out
afresh at origin/main, so the next change starts clean; the change itself is
on the branch. Never merges.
"""

from __future__ import annotations

import os
import secrets
import shutil
import stat
from pathlib import Path
from typing import Any

from .. import git, hand
from . import selfclone

Json = dict[str, Any]

BASE = selfclone.BASE
ORIGIN_BASE = selfclone.ORIGIN_BASE
#: Tries at renaming a fresh checkout into place while something keeps
#: putting a folder there.
PLACE_TRIES = 3


def worktree() -> Path:
    return hand.state_dir() / "home" / "repo"


def scratch() -> Path:
    return hand.state_dir() / "mind"


def gitdir() -> Path:
    return scratch() / "repo.git"


def _git(*args: str, check: bool = True, tree: Path | None = None):
    """git on the clone, naming the git directory and the working tree, so
    nothing in the tree is ever read as config. Run from state/mind/."""
    return git.run(scratch(), "--git-dir", str(gitdir()), "--work-tree", str(tree or worktree()), *args, check=check)


def _bare(*args: str, check: bool = True, token: str | None = None):
    """git on the git directory alone: no working tree at all."""
    return git.run(scratch(), "--git-dir", str(gitdir()), *args, check=check, token=token)


def _out(*args: str) -> str:
    return _git(*args).stdout.decode("utf-8", "replace").strip()


def _rev(ref: str) -> str | None:
    done = _bare("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    return done.stdout.decode().strip() or None if done.returncode == 0 else None


def _fetch() -> None:
    _bare("fetch", "-q", "--prune", selfclone._url(), f"+refs/heads/{BASE}:{ORIGIN_BASE}",
          f"+refs/heads/{selfclone.BRANCH_PREFIX}*:refs/remotes/origin/{selfclone.BRANCH_PREFIX}*",
          token=hand.setting("GITHUB_TOKEN"))


def _ensure_gitdir() -> None:
    if (gitdir() / "HEAD").is_file():
        return
    scratch().mkdir(mode=0o700, parents=True, exist_ok=True)
    git.run(scratch(), "init", "-q", "--bare", str(gitdir()))
    _bare("symbolic-ref", "HEAD", f"refs/heads/{BASE}")


def _tree_kind() -> str:
    """`missing` (nothing, an empty folder, or anything but a real folder),
    or `tree`. Read with lstat: a link is never a tree."""
    try:
        found = os.lstat(worktree())
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISDIR(found.st_mode):
        return "missing"
    try:
        with os.scandir(worktree()) as entries:
            return "tree" if any(True for _ in entries) else "missing"
    except OSError:
        return "missing"


def _aside() -> None:
    """Renames whatever is at the tree's path into state/mind/: a link is
    renamed, never followed. Gone already is fine."""
    try:
        os.rename(worktree(), scratch() / f"old-{secrets.token_hex(6)}")
    except FileNotFoundError:
        pass


def _sweep() -> None:
    """Removes the trees moved aside and any checkout a crash left; rmtree
    never follows a link (shutil.rmtree.avoids_symlink_attacks)."""
    for entry in scratch().iterdir():
        if entry.name.startswith(("old-", "fresh-")):
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)


def _fresh(commit: str) -> None:
    """The tree replaced by a checkout of COMMIT on main, made in state/mind/
    and renamed into state/home/."""
    _bare("update-ref", f"refs/heads/{BASE}", commit)
    _bare("symbolic-ref", "HEAD", f"refs/heads/{BASE}")
    fresh = scratch() / f"fresh-{secrets.token_hex(6)}"
    fresh.mkdir(mode=0o755)
    (gitdir() / "index").unlink(missing_ok=True)
    _git("reset", "-q", "--hard", f"refs/heads/{BASE}", tree=fresh)
    worktree().parent.mkdir(parents=True, exist_ok=True)
    for _ in range(PLACE_TRIES):
        _aside()
        try:
            os.rename(fresh, worktree())
            break
        except OSError:
            continue  # something was put there again: move it aside and retry
    else:
        raise hand.HandError("could not put a fresh checkout at state/home/repo: something keeps replacing it")
    _sweep()


def _dirty() -> bool:
    return bool(_out("status", "--porcelain", "--untracked-files=all"))


def _prepare_locked() -> Json:
    _ensure_gitdir()
    _fetch()
    origin = _rev(ORIGIN_BASE)
    if origin is None:
        raise hand.HandError(f"origin has no {BASE} branch")
    if _tree_kind() == "missing" or _rev("HEAD") is None:
        _fresh(origin)
        state = "cloned"
    elif _dirty():
        state = "dirty"
    elif _rev("HEAD") != origin:
        _fresh(origin)
        state = "synced"
    else:
        state = "current"
    return {"path": str(worktree()), "repo": selfclone.repo(), "commit": origin, "head": _rev("HEAD"), "state": state}


def prepare() -> Json:
    """Makes sure state/home/repo/ holds a checkout of origin/main, unless it
    holds edits: fetches, then checks out afresh what is missing or clean."""
    with hand.lock("mind-clone"):
        return _prepare_locked()
