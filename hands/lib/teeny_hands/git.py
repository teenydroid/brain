"""How every git command Teeny runs is set up (docs/hands.md, "git").

The spine container runs as the checkout owner's uid, which has no passwd
entry, no name, no email and a throwaway HOME (/tmp). Out of the box `git
commit` there fails with "unable to auto-detect email address". And the box's
or the operator's git config (hooks, commit signing, credential helpers) has
no business in what Teeny commits or pushes. So every git run in the hands
goes through `run`:

- **One identity**: Teeny <teeny@agentmail.to> (its own mailbox), or
  `GIT_NAME` / `GIT_EMAIL` from secrets/teeny.env. The same for the diary
  and every push. Set as GIT_AUTHOR_* and GIT_COMMITTER_*, so no config file
  and no passwd lookup is ever consulted.
- **No outside config**: no system or global file, HOME under state/, no
  credential helper, no hooks, no fsmonitor, no prompts.
- **A token only when asked**, through GIT_ASKPASS (`gh/askpass`, which holds
  no secret) and the environment of that one command: never in a URL, a
  config file or a log, and redacted from any error.

`hands/git check` proves it on the box: it makes a commit in a scratch
repository under state/ and reports git's version and who it committed as.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import hand

DEFAULT_NAME = "Teeny"
DEFAULT_EMAIL = "teeny@agentmail.to"
ASKPASS = Path(__file__).resolve().parent / "gh" / "askpass"
TIMEOUT_SECONDS = 120.0


def identity() -> tuple[str, str]:
    """Who Teeny commits as. secrets/teeny.env wins; the environment never
    does (hand.setting), so a job cannot commit as somebody else."""
    return hand.setting("GIT_NAME") or DEFAULT_NAME, hand.setting("GIT_EMAIL") or DEFAULT_EMAIL


def env(token: str | None = None, user: str | None = None) -> dict[str, str]:
    """The whole environment of a git run: nothing inherited but PATH."""
    name, email = identity()
    out = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(hand.state_dir()),
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    if token is not None:
        out.update({"GIT_ASKPASS": str(ASKPASS), "TEENY_GH_ASKPASS_TOKEN": token,
                    "TEENY_GH_ASKPASS_USER": user or "x-access-token"})
    return out


def run(cwd: Path | None, *args: str, token: str | None = None, user: str | None = None,
        check: bool = True, timeout: float = TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """One git command, sealed off from the box. Raises HandError (token
    redacted) when it fails and `check` is set."""
    argv = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.fsmonitor=false", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args]
    try:
        done = subprocess.run(argv, cwd=cwd, env=env(token, user), capture_output=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise hand.HandError("git is not installed") from None
    if check and done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        message = detail[0][:300]
        if token:
            message = message.replace(token, "***")
        raise hand.HandError(f"git {args[0]} failed: {message}")
    return done


def text(cwd: Path | None, *args: str, **kw) -> str:
    return run(cwd, *args, **kw).stdout.decode("utf-8", "replace")


def check() -> dict:
    """git works here: installed, and able to commit as Teeny with nothing
    but what `run` gives it. Leaves nothing behind."""
    if shutil.which("git") is None:
        raise hand.HandError("git is not installed")
    version = text(None, "--version").strip().removeprefix("git version ").strip()
    scratch = Path(tempfile.mkdtemp(prefix="git-check-", dir=hand.state_dir()))
    try:
        run(scratch, "init", "-q", "-b", "main")
        (scratch / "check.txt").write_text("teeny\n")
        run(scratch, "add", "-A")
        run(scratch, "commit", "-q", "-m", "check")
        who = text(scratch, "log", "-1", "--format=%an%x00%ae").strip().split("\0")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"version": version, "name": who[0], "email": who[1]}
