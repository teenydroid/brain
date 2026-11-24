"""The one `git push` Teeny makes of a folder, from under state/workbench/
(where bin/broker stages a copy of a folder of the mind's home, docs/mind.md).

The token reaches git through the environment of that single command and an
askpass helper that holds no secret itself (`askpass`, beside this file):
never in the remote URL, never in a config file or a credential store, never
in a log. Every git run here ignores the box's and the user's git config, has
no credential helper and runs no hooks, so nothing in the workbench (which
Teeny's own code fills) can run on push or read the token.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
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


def remote_url(owner: str, name: str) -> str:
    base = hand.setting("GITHUB_GIT_BASE", GITHUB_GIT_BASE) or GITHUB_GIT_BASE
    return f"{base.rstrip('/')}/{owner}/{name}.git"


def remote_heads(owner: str, name: str, refs: tuple[str, ...], token: str | None = None,
                 user: str | None = None) -> dict[str, str] | None:
    """{branch: commit} for those of REFS owner/name has; None when the
    repository cannot be read (missing, or no access)."""
    done = git.run(None, "ls-remote", remote_url(owner, name), *(f"refs/heads/{r}" for r in refs),
                   token=token, user=user, check=False)
    if done.returncode != 0:
        return None
    heads: dict[str, str] = {}
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        sha, _, refname = line.partition("\t")
        if refname.startswith("refs/heads/"):
            heads[refname[len("refs/heads/"):]] = sha.strip()
    return heads


class Staged:
    """A folder's files as the next commit of a branch, in a git directory
    of the hand's own (under state/, never inside the folder), so nothing the
    folder holds (its own .git, its config, its hooks) is ever read: only its
    files, as `git add -A` sees them with its .gitignore.

    The commit builds on what the branch already has on GitHub (a push is
    never forced and never rewrites it), or, for a new branch, on BASE when
    given (a fork's main, for a pull request), or on nothing. Its secret scan
    reads only what the push changes on top of that: what the branch or the
    base already has is not this push's to vouch for. A rehearsal reads too
    (the branch's head is a read) and pushes nothing."""

    def __init__(self, path: Path, owner: str, name: str, ref: str, base: str | None,
                 token: str | None, user: str | None, rehearsal: bool, expect: str | None = None) -> None:
        self.path, self.owner, self.name, self.ref, self.expect = path, owner, name, ref, expect
        self.token, self.user = token, user
        self._tmp = Path(tempfile.mkdtemp(prefix="gh-push-", dir=hand.state_dir()))
        try:
            self.git_dir = self._tmp / "repo" / ".git"
            git.run(None, "init", "-q", str(self._tmp / "repo"))
            self.onto, self.parent, self.note = self._parent(base, rehearsal)
            self.changed, self.findings = self._stage()
        except BaseException:
            self.close()
            raise

    def _git(self, *args: str, token: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        return git.run(self.path, "--git-dir", str(self.git_dir), "--work-tree", str(self.path), *args,
                       token=self.token if token else None, user=self.user, check=check)

    def _parent(self, base: str | None, rehearsal: bool) -> tuple[str | None, str | None, str | None]:
        repo = f"{self.owner}/{self.name}"
        heads = remote_heads(self.owner, self.name, tuple(r for r in (self.ref, base) if r), self.token, self.user)
        if heads is None:
            if not rehearsal:
                raise hand.HandError(f"could not read {repo}: create it first (create-repo), or fork it")
            return None, None, f"{repo} could not be read (not created yet, in a rehearsal?): pushed as a first commit"
        onto = self.ref if self.ref in heads else base
        if onto is None:
            if self.expect is not None:
                raise hand.Refused(f"{repo} has no branch {self.ref} or base to hold parent {self.expect[:12]}: "
                                   "name the \"base\" the folder was cloned from", rule="moved")
            return None, None, None
        if onto not in heads:
            raise hand.Refused(f"{repo} has no branch {onto} to start {self.ref} from", rule="base")
        self._git("fetch", "-q", "--depth=1", "--no-tags", remote_url(self.owner, self.name), f"refs/heads/{onto}",
                  token=True)
        parent = self._git("rev-parse", "FETCH_HEAD").stdout.decode().strip()
        if self.expect is not None and parent != self.expect:
            # The folder was made from EXPECT; laid over a branch that moved
            # since, it would delete what came in between.
            raise hand.Refused(f"{onto} on {repo} is at {parent[:12]}, not {self.expect[:12]} the folder was made "
                               f"from: clone {onto} again, redo the change on it, and push with its new parent",
                               rule="moved")
        self._git("reset", "-q", parent)
        return onto, parent, None

    def _stage(self) -> tuple[int, list[dict[str, str]]]:
        self._git("add", "-A")
        if self.parent is not None:
            every = self._git("diff", "--cached", "--name-only", "-z", "--no-renames", self.parent).stdout
            kept = self._git("diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=d",
                             self.parent).stdout
        else:
            every = kept = self._git("ls-files", "-z", "--cached").stdout
        try:
            changed = sorted({p for p in every.decode("utf-8").split("\0") if p})
            paths = sorted({p for p in kept.decode("utf-8").split("\0") if p})
        except UnicodeDecodeError:
            # A name the scan could not open as it is, it could not vouch for.
            raise hand.Refused("a file name in the folder is not UTF-8: rename it", rule="name") from None
        if not changed:
            what = f"what {self.onto} already has" if self.onto else "an empty folder"
            raise hand.Refused(f"there is nothing to push: the folder holds {what}", rule="empty")
        return len(changed), scan(self.path, paths)

    def commit_and_push(self, message: str) -> str:
        """Commits the staged change (as git.identity()) and pushes it to the
        branch, with git's lease on what was read: the branch's head when the
        commit builds on it, else no branch at all, so a branch rewound or
        made in between fails the push and is never overwritten or restored.
        The commit descends from that head, so it is a fast-forward of it.
        Returns the commit."""
        self._git("commit", "-q", "-m", message)
        sha = self._git("rev-parse", "HEAD").stdout.decode().strip()
        held = self.parent if self.onto == self.ref else ""
        try:
            self._git("push", "-q", f"--force-with-lease=refs/heads/{self.ref}:{held}",
                      remote_url(self.owner, self.name), f"HEAD:refs/heads/{self.ref}", token=True)
        except hand.HandError as exc:
            if any(w in str(exc) for w in ("stale info", "rejected", "failed to push some refs")):
                raise hand.Refused(f"{self.ref} moved on GitHub since it was read; nothing was pushed: push again",
                                   rule="moved") from None
            raise
        return sha

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


def scan(path: Path, paths: list[str]) -> list[dict[str, str]]:
    """Every finding in PATHS (relative to the folder PATH), the files a
    push would add or change: the paths, links, files too large to read, and
    the contents."""
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
            data = file.read_bytes()
        except OSError:
            continue
        text = data.decode("utf-8", "replace")
        if b"\0" in data:
            # UTF-16 or -32 text: a NUL between the letters of a token.
            text += "\n" + data.replace(b"\0", b"").decode("utf-8", "replace")
        contents[rel] = text
    findings.extend(secretscan.scan(paths, contents))
    unique: dict[str, secretscan.Finding] = {}
    for item in findings:
        unique.setdefault(item.path, item)
    return [item.as_dict() for item in unique.values()]


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
    """The lines a patch adds, by the file they went into. A `+++ ` line is a
    file header only before a hunk's first `@@`: after it, every `+` line is
    content (an added line `++ x` reads `+++ x`), so none can hide as a header.
    Split on \\n only, as git does."""
    added: dict[str, list[str]] = {}
    current = "(branch)"
    in_header = False
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            in_header = True
            current = "(branch)"
        elif in_header and line.startswith("+++ "):
            target = line[4:]
            current = target[2:] if target.startswith("b/") else target
        elif line.startswith("@@"):
            in_header = False
        elif not in_header and line.startswith("+"):
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
    # A merge commit shows no patch in the log, so what its resolution added
    # is read from the branch's whole change.
    patch += "\n" + _git_dir(git_dir, "diff", "--unified=0", "--no-renames",
                             f"{since}...{head}").stdout.decode("utf-8", "replace")
    findings = secretscan.scan(sorted({n for n in names.split("\0") if n}), _added_lines(patch))
    return [item.as_dict() for item in findings]


def push_ref(git_dir: Path, owner: str, name: str, ref: str, token: str, user: str,
             expect: str | None = None) -> str:
    """Pushes the branch REF to the same branch on owner/name. Never forces.
    With EXPECT (a commit the pushed one descends from), the push lands only
    while the remote branch is still exactly EXPECT (git's lease): a branch
    rewound or moved since it was read is refused, never overwritten, and
    since the commit descends from EXPECT the push is always a fast-forward
    of it. Returns the commit."""
    sha = head_of(git_dir, ref)
    lease: tuple[str, ...] = ()
    if expect is not None:
        if _git_dir(git_dir, "merge-base", "--is-ancestor", expect, sha, check=False).returncode != 0:
            raise hand.HandError(f"{ref} does not descend from {expect}: that push would rewrite the branch")
        lease = (f"--force-with-lease=refs/heads/{ref}:{expect}",)
    try:
        _git_dir(git_dir, "push", "-q", *lease, remote_url(owner, name), f"refs/heads/{ref}:refs/heads/{ref}",
                 token=token, user=user)
    except hand.HandError as exc:
        # A refused ref (the lease, or a ref that moved) says "failed to push
        # some refs"; a network or auth failure says something else.
        if expect is not None and any(w in str(exc) for w in ("stale info", "rejected", "failed to push some refs")):
            raise hand.Refused(f"{ref} moved on GitHub since it was read; nothing was pushed", rule="moved") from None
        raise
    return sha
