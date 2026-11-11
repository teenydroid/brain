"""The one `git push` Teeny makes, from a folder under state/workbench/.

The token reaches git through the environment of that single command and an
askpass helper that holds no secret itself (`askpass`, beside this file):
never in the remote URL, never in a config file or a credential store, never
in a log. Every git run here ignores the box's and the user's git config, has
no credential helper and runs no hooks, so nothing in the workbench (which
Teeny's own code fills) can run on push or read the token.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .. import git, hand
from . import secretscan

#: Where pushes go. `GITHUB_GIT_BASE` moves it (the tests' local remotes).
GITHUB_GIT_BASE = "https://github.com"
#: Files larger than this are not read for secrets; they are refused instead,
#: because a push this hand cannot read is a push it cannot vouch for.
MAX_SCAN_BYTES = 2 * 1024 * 1024

_BRANCH = re.compile(r"^(?!-)(?!/)(?!.*\.\.)(?!.*//)(?!.*@\{)(?!.*\.lock$)(?!.*/$)[A-Za-z0-9._/-]{1,200}$")


def workbench() -> Path:
    return hand.state_dir() / "workbench"


def workdir(value: object) -> Path:
    """The folder to push from: an existing directory inside state/workbench/,
    after every symlink is resolved, so `../../secrets` and a link out both
    fail."""
    if not isinstance(value, str) or not value.strip():
        raise hand.HandError('"workdir" must be a folder under state/workbench/')
    path = Path(value)
    if not path.is_absolute():
        path = hand.root() / path
    resolved = path.resolve()
    base = workbench().resolve()
    if resolved == base or base not in resolved.parents:
        raise hand.Refused("pushes come only from a folder under state/workbench/", rule="workdir")
    if not resolved.is_dir():
        raise hand.HandError(f"no folder {value!r}")
    return resolved


def branch(value: object) -> str:
    if not isinstance(value, str) or not _BRANCH.match(value):
        raise hand.HandError('"branch" must be a plain branch name')
    return value


def _git(cwd: Path, *args: str, token: str | None = None, user: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    # Sealed off from the box, and commits as Teeny (teeny_hands.git).
    return git.run(cwd, *args, token=token, user=user, check=check)


def is_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _has_head(path: Path) -> bool:
    return _git(path, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def _files(path: Path) -> list[str]:
    """What `git add -A` would publish: tracked files and untracked ones not
    ignored. Outside a repository, every file."""
    if is_repo(path):
        out = _git(path, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
        return sorted({p for p in out.decode("utf-8", "replace").split("\0") if p})
    found = []
    for folder, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            found.append(str((Path(folder) / name).relative_to(path)))
    return sorted(found)


def scan(path: Path) -> list[dict[str, str]]:
    """Every finding in what a push from `path` would publish: the files git
    would add, and every commit already there (paths and patches)."""
    paths = _files(path)
    contents: dict[str, str] = {}
    findings: list[secretscan.Finding] = []
    for rel in paths:
        file = path / rel
        if file.is_symlink():
            findings.append(secretscan.Finding(rel, "a symlink"))
            continue
        try:
            size = file.stat().st_size
        except OSError:
            continue
        if size > MAX_SCAN_BYTES:
            findings.append(secretscan.Finding(rel, f"a file over {MAX_SCAN_BYTES // (1024 * 1024)} MB"))
            continue
        try:
            contents[rel] = file.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
    if is_repo(path) and _has_head(path):
        names = _git(path, "log", "--format=", "--name-only", "HEAD").stdout.decode("utf-8", "replace")
        paths = sorted(set(paths) | {n for n in names.splitlines() if n})
        contents["(history)"] = _git(path, "log", "-p", "--format=", "HEAD").stdout.decode("utf-8", "replace")
    findings.extend(secretscan.scan(paths, contents))
    unique: dict[str, secretscan.Finding] = {}
    for item in findings:
        unique.setdefault(item.path, item)
    return [item.as_dict() for item in unique.values()]


def remote_url(owner: str, name: str) -> str:
    base = hand.setting("GITHUB_GIT_BASE", GITHUB_GIT_BASE) or GITHUB_GIT_BASE
    return f"{base.rstrip('/')}/{owner}/{name}.git"


def commit_and_push(path: Path, owner: str, name: str, ref: str, message: str, token: str, user: str) -> str:
    """Commits whatever is there (as git.identity()) and
    pushes HEAD to `ref` on owner/name. Never forces. Returns the commit."""
    if not is_repo(path):
        _git(path, "init", "-q", "-b", ref)
    _git(path, "add", "-A")
    if _git(path, "status", "--porcelain").stdout.strip():
        _git(path, "commit", "-q", "-m", message)
    if not _has_head(path):
        raise hand.Refused("there is nothing to push: the folder is empty", rule="empty")
    sha = _git(path, "rev-parse", "HEAD").stdout.decode().strip()
    _git(path, "push", "-q", remote_url(owner, name), f"HEAD:refs/heads/{ref}", token=token, user=user)
    return sha


# --- a branch of a clone (Teeny's own, teeny_hands.build.selfclone) ------------------------


def _git_dir(git_dir: Path, *args: str, token: str | None = None, user: str | None = None,
             check: bool = True) -> subprocess.CompletedProcess:
    return git.run(git_dir, "--git-dir", str(git_dir), *args, token=token, user=user, check=check)


def head_of(git_dir: Path, ref: str) -> str:
    done = _git_dir(git_dir, "rev-parse", "--verify", "-q", f"refs/heads/{ref}^{{commit}}", check=False)
    if done.returncode != 0:
        raise hand.HandError(f"there is no branch {ref}")
    return done.stdout.decode().strip()


def _added_lines(patch: str) -> dict[str, str]:
    """The lines a patch adds, by the file they went into."""
    added: dict[str, list[str]] = {}
    current = "(branch)"
    for line in patch.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = target[2:] if target.startswith("b/") else target
        elif line.startswith("+"):
            added.setdefault(current, []).append(line[1:])
    return {path: "\n".join(lines) for path, lines in added.items()}


def scan_branch(git_dir: Path, since: str, ref: str) -> list[dict[str, str]]:
    """Every finding in what pushing REF adds to SINCE: the paths it adds or
    changes, and every line any of its commits adds (so a secret added and
    removed again is still found: the history is published too). What SINCE
    already has is not this push's to vouch for."""
    head = f"refs/heads/{ref}"
    names = _git_dir(git_dir, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=d",
                     f"{since}...{head}").stdout.decode("utf-8", "replace")
    patch = _git_dir(git_dir, "log", "-p", "--format=", "--unified=0", "--no-renames", f"{since}..{head}",
                     "--").stdout.decode("utf-8", "replace")
    findings = secretscan.scan(sorted({n for n in names.split("\0") if n}), _added_lines(patch))
    return [item.as_dict() for item in findings]


def push_ref(git_dir: Path, owner: str, name: str, ref: str, token: str, user: str) -> str:
    """Pushes the branch REF to the same branch on owner/name. Never forces.
    Returns the commit."""
    sha = head_of(git_dir, ref)
    _git_dir(git_dir, "push", "-q", remote_url(owner, name), f"refs/heads/{ref}:refs/heads/{ref}", token=token,
             user=user)
    return sha
