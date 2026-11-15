"""Moving the live checkout (the one bin/spine runs) forward, and asking
whether it is safe to: the steps bin/evolve's `LiveRestarter` takes on a
promotion and hands/babysit's deploy takes after a self-merge, in one place
so the two never drift (docs/evolve.md, restart; docs/hands.md, "babysit").

The order, which both follow:

  1 `prepare`       tracked edits refuse; SHA is fetched from origin when it
                    is not here; a checkout that cannot fast-forward to SHA
                    refuses; a detached HEAD (a last-good boot) goes back
                    onto main first
  2 last_good.record  the outgoing commit, when a restart follows
  3 `fast_forward`  `git merge --ff-only SHA`, never anything else
  4 `term_spine`    TERM to state/spine.pid; compose starts bin/spine again,
                    the doctor gates the boot, and last-good catches a bad one

`lock_held` asks whether a run holds state/locks/NAME.lock (bin/lib/detach.py
--lock NAME, bin/evolve's run lock) without waiting for it.

Nothing here throws a change away, resets, or forces.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
from pathlib import Path


def git(checkout: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(checkout), "-c", "core.hooksPath=/dev/null", *args],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def prepare(checkout: Path, sha: str) -> tuple[str | None, str | None]:
    """(the checkout's HEAD, None) when it can fast-forward to SHA, else
    (None, why). May fetch origin and leave a detached HEAD on main."""
    head = git(checkout, "rev-parse", "HEAD").stdout.strip()
    if git(checkout, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return None, "the live checkout has tracked edits; restart it by hand"
    if git(checkout, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        fetched = git(checkout, "fetch", "-q", "origin")
        if fetched.returncode != 0:
            return None, f"git fetch failed: {fetched.stderr.strip()[-200:]}"
    if git(checkout, "merge-base", "--is-ancestor", head, sha).returncode != 0:
        return None, f"the live checkout ({head[:12]}) cannot fast-forward to {sha[:12]}"
    if git(checkout, "symbolic-ref", "-q", "HEAD").returncode != 0:
        # A last-good boot left it detached: back onto main first.
        if git(checkout, "checkout", "-q", "main").returncode != 0:
            return None, "the live checkout is detached and main cannot be checked out"
        head = git(checkout, "rev-parse", "HEAD").stdout.strip()
    return head, None


def fast_forward(checkout: Path, sha: str) -> str | None:
    """None once the checkout is at SHA, else why not."""
    merged = git(checkout, "merge", "-q", "--ff-only", sha)
    if merged.returncode != 0:
        return f"git merge --ff-only failed: {merged.stderr.strip()[-200:]}"
    return None


def term_spine(state: Path) -> str | None:
    """None once bin/spine (state/spine.pid) has TERM, else why not."""
    try:
        pid = int((state / "spine.pid").read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError) as exc:
        return f"no spine to TERM (state/spine.pid): {exc}"
    return None


def lock_held(state: Path, name: str) -> bool:
    """Whether a run holds state/locks/NAME.lock now."""
    path = state / "locks" / f"{name}.lock"
    if not path.exists():
        return False
    with open(path, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False
