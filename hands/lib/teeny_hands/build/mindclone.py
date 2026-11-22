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
clean tree (ignored files do not count, and go with it) is replaced by a
fresh checkout of origin/main, made in state/mind/ (where the mind cannot
race it) and renamed into place; the tree it replaces is renamed aside
first, which never follows a link. A tree with edits is left as it is. Git
never writes into the mind's tree, and the mind cannot run git on it.

`ship(slug, title, body)` refuses, in order: a bad slug, title (1..120) or
body (1..8000); a teeny/mind-<slug> already on origin (one pull request per
branch); a title or body the guard holds (actions.guarded, surface github,
as selfclone.ship asks it); a `.git` anywhere in the tree; nothing changed
against origin/main; over MAX_FILES files or MAX_LINES lines; any path that
is not a plain file of the tree's own (a link, wherever it points, a nested
repository, a hard link, a file over 2 MB, or bytes that differ from what
git staged, read here without following a link); a frozen path, the list
read from origin/main (selfclone's frozen check); anything the secret scan
finds (gitpush.scan_branch). It commits as Teeny with commit-tree (no
hooks, HEAD and the tree untouched), pushes teeny/mind-<slug> (hands/gh
push_branch, which scans again) and opens a ready pull request into main
(hands/gh open_pr, which guards again) whose body ends with DISCLOSURE.
After a push the tree is checked out afresh at origin/main, so the next
change starts clean; the change itself is on the branch. Under DRY_RUN every
rule runs and nothing is pushed or opened. Never merges.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import stat
from pathlib import Path
from typing import Any

from .. import git, hand
from ..gh import gitpush
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
    if done.returncode != 0:
        return None
    return done.stdout.decode().strip() or None


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


def _aside() -> Path | None:
    """Renames whatever is at the tree's path into state/mind/: a link is
    renamed, never followed. Gone already is fine (None); else where it went."""
    moved = scratch() / f"old-{secrets.token_hex(6)}"
    try:
        os.rename(worktree(), moved)
    except FileNotFoundError:
        return None
    return moved


def _sweep() -> None:
    """Removes the trees moved aside and any checkout a crash left; rmtree
    never follows a link (shutil.rmtree.avoids_symlink_attacks). A tree kept
    for its edits (`kept-*`) stays."""
    for entry in scratch().iterdir():
        if entry.name.startswith(("old-", "fresh-")):
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)


def _differs(displaced: Path, snapshot: str | None) -> bool:
    """Whether a tree moved aside holds anything but SNAPSHOT (a commit) and
    ignored files: read with a throwaway index, so the clone's own is not
    touched. When that cannot be told, it differs: a copy is cheaper than a
    loss."""
    if snapshot is None or not displaced.is_dir() or displaced.is_symlink():
        return displaced.exists() or displaced.is_symlink()
    index = scratch() / f"fresh-index-{secrets.token_hex(6)}"
    try:
        for args in (("read-tree", snapshot),):
            if git.run(scratch(), "--git-dir", str(gitdir()), "--work-tree", str(displaced), *args,
                       check=False, index_file=index).returncode != 0:
                return True
        for args in (("diff-files", "--name-only"), ("ls-files", "--others", "--exclude-standard")):
            done = git.run(scratch(), "--git-dir", str(gitdir()), "--work-tree", str(displaced), *args,
                           check=False, index_file=index)
            if done.returncode != 0 or done.stdout.strip():
                return True
        return False
    finally:
        index.unlink(missing_ok=True)


def _keep(displaced: Path) -> str:
    """A displaced tree with edits in it, renamed so the sweep leaves it:
    state/mind/kept-<name>. Returns where it is."""
    kept = scratch() / f"kept-{secrets.token_hex(6)}"
    os.rename(displaced, kept)
    return str(kept)


def _fresh(commit: str, snapshot: str | None = None) -> list[str]:
    """The tree replaced by a checkout of COMMIT on main, made in state/mind/
    and renamed into place. The tree it replaces is read once it is out of the
    mind's reach: if it differs from SNAPSHOT (the commit it was a clean copy
    of; none keeps any tree there was), it is kept in state/mind/kept-* and
    its path returned, so a write made after the clean-tree check is not lost."""
    _bare("update-ref", f"refs/heads/{BASE}", commit)
    _bare("symbolic-ref", "HEAD", f"refs/heads/{BASE}")
    fresh = scratch() / f"fresh-{secrets.token_hex(6)}"
    fresh.mkdir(mode=0o755)
    (gitdir() / "index").unlink(missing_ok=True)
    _git("reset", "-q", "--hard", f"refs/heads/{BASE}", tree=fresh)
    worktree().parent.mkdir(parents=True, exist_ok=True)
    kept: list[str] = []
    for _ in range(PLACE_TRIES):
        moved = _aside()
        if moved is not None and _differs(moved, snapshot):
            kept.append(_keep(moved))
        try:
            os.rename(fresh, worktree())
            break
        except OSError:
            continue  # something was put there again: move it aside and retry
    else:
        raise hand.HandError("could not put a fresh checkout at state/home/repo: something keeps replacing it")
    _sweep()
    return kept


def _dirty() -> bool:
    return bool(_out("status", "--porcelain", "--untracked-files=all"))


def _prepare_locked() -> Json:
    _ensure_gitdir()
    _fetch()
    origin = _rev(ORIGIN_BASE)
    if origin is None:
        raise hand.HandError(f"origin has no {BASE} branch")
    kept: list[str] = []
    head = _rev("HEAD")
    if _tree_kind() == "missing" or head is None:
        # Whatever is there is not a copy of anything: keep a tree with files in it.
        kept = _fresh(origin, None if _tree_kind() == "tree" else head)
        state = "cloned"
    elif _dirty():
        state = "dirty"
    elif head != origin:
        kept = _fresh(origin, head)
        state = "synced"
    else:
        state = "current"
    out: Json = {"path": str(worktree()), "repo": selfclone.repo(), "commit": origin, "head": _rev("HEAD"),
                 "state": state}
    if kept:
        out["kept"] = kept
        out["note"] = ("edits made while the tree was refreshed are in " + ", ".join(kept)
                       + " (outside the mind's reach); copy what is wanted back")
    return out


def prepare() -> Json:
    """Makes sure state/home/repo/ holds a checkout of origin/main, unless it
    holds edits: fetches, then checks out afresh what is missing or clean."""
    with hand.lock("mind-clone"):
        return _prepare_locked()


# --- shipping -----------------------------------------------------------------------------------

#: What the pushed branch is called: teeny/mind-<slug>, a teeny/* branch as
#: hands/gh push_branch requires, apart from the builder's teeny/<slug>.
MIND_BRANCH_PREFIX = selfclone.BRANCH_PREFIX + "mind-"
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
MAX_TITLE = 120
MAX_BODY = 8000
#: The babysitter's limits on a pull request it would merge.
MAX_FILES = 100
MAX_LINES = 3000
#: The disclosure every pull request from the mind's clone ends with.
DISCLOSURE = "—\nWritten by Teeny's agentic mind, an autonomous AI agent (github.com/teenydroid), from its own clone."


def _text(value: Any, name: str, most: int) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(text) <= most:
        raise hand.Refused(f"the {name} is 1 to {most} characters", rule=name)
    return text


def _branch(slug: Any) -> str:
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise hand.Refused("the branch is 1 to 41 lowercase letters, digits and dashes, starting with a letter or "
                           "digit", rule="branch")
    return MIND_BRANCH_PREFIX + slug


def _commit(message: str) -> str:
    """The tree as it is now, committed on HEAD as Teeny, without moving HEAD
    or touching the tree: staged in the clone's index, written with
    write-tree and commit-tree (which run no hooks), the index put back."""
    try:
        _git("add", "-A")
        tree = _bare("write-tree").stdout.decode().strip()
    finally:
        _git("reset", "-q", check=False)
    return _bare("commit-tree", tree, "-p", "HEAD", "-m", message).stdout.decode().strip()


def _changed(base: str, head: str) -> list[str]:
    raw = _bare("diff", "--name-only", "-z", "--no-renames", base, head).stdout.decode("utf-8", "replace")
    return sorted(p for p in raw.split("\0") if p)


def _no_git_inside() -> None:
    """Refuses a tree with a `.git` anywhere in it (any case): a git
    directory, a `gitdir:` pointer or a nested repository the mind made.
    Git is never pointed at one (every run names the clone's own), and a
    nested one would be committed as a link to another repository."""
    for folder, dirs, names in os.walk(worktree()):  # never follows a link
        for name in (*dirs, *names):
            if name.lower() == ".git":
                rel = (Path(folder) / name).relative_to(worktree())
                raise hand.Refused(f"{rel} is in your tree: your clone's git lives outside your home, so delete it",
                                   rule="git_in_tree", path=str(rel))


def _entries(base: str, head: str) -> list[tuple[str, str, str]]:
    """(new mode, new blob, path) for every path HEAD changes against BASE,
    from `git diff --raw -z`; a deleted path has mode 000000."""
    raw = _bare("diff", "--raw", "-z", "--no-renames", "--no-abbrev", base, head).stdout.decode("utf-8", "replace")
    parts = raw.split("\0")
    out = []
    for meta, path in zip(parts[0::2], parts[1::2]):
        fields = meta.lstrip(":").split()
        if len(fields) >= 4:
            out.append((fields[1], fields[3], path))
    return out


def _within_limits(head: str, files: list[str]) -> None:
    if len(files) > MAX_FILES:
        raise hand.Refused(f"the change touches {len(files)} files; the most is {MAX_FILES}", rule="too_big")
    lines = 0
    for row in _bare("diff", "--numstat", "--no-renames", "HEAD", head).stdout.decode("utf-8", "replace").splitlines():
        added, deleted = row.split("\t")[:2]
        lines += sum(int(n) for n in (added, deleted) if n.isdigit())
    if lines > MAX_LINES:
        raise hand.Refused(f"the change is {lines} lines; the most is {MAX_LINES}", rule="too_big")


def _read_in_tree(rel: str) -> bytes:
    """The bytes of REL as the tree holds it, every step opened without
    following a link, so nothing outside the tree can be read through it."""
    parts = rel.split("/")
    fd = os.open(worktree(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in parts[:-1]:
            nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)
    with os.fdopen(leaf, "rb") as fh:
        found = os.fstat(fh.fileno())
        if not stat.S_ISREG(found.st_mode) or found.st_nlink != 1:
            # A hard link reads the same without following a link: it may
            # be a file from outside the tree.
            raise OSError(f"{rel} is not a file of the tree's own")
        if found.st_size > gitpush.MAX_SCAN_BYTES:
            raise hand.Refused(f"{rel} is over {gitpush.MAX_SCAN_BYTES // (1024 * 1024)} MB", rule="too_big", path=rel)
        return fh.read(gitpush.MAX_SCAN_BYTES + 1)


def _blob_id(data: bytes, like: str) -> str:
    algo = hashlib.sha1 if len(like) == 40 else hashlib.sha256
    return algo(b"blob %d\0" % len(data) + data).hexdigest()


def _only_plain_files(head: str) -> None:
    """Every path the commit adds or changes is a plain file whose blob is
    the bytes the tree holds, read here without following a link. Refuses a
    link (wherever it points: `brain/x -> ../secrets/teeny.env` stays in the
    tree and still reads the secret once merged), a nested repository, a
    hard link, and a file git read differently from what is there (swapped
    for a link while git read it, or converted by an attribute)."""
    for mode, blob, path in _entries("HEAD", head):
        if mode == "000000":
            continue
        if mode == "120000":
            raise hand.Refused(f"{path} is a link: a change from your clone carries plain files only", rule="link",
                               path=path)
        if mode not in ("100644", "100755"):
            raise hand.Refused(f"{path} is not a plain file (mode {mode})", rule="not_a_file", path=path)
        try:
            data = _read_in_tree(path)
        except OSError:
            data = None
        if data is None or _blob_id(data, blob) != blob:
            raise hand.Refused(f"{path} is not what git read from your tree (a link, or changed while it was read)",
                               rule="changed", path=path)


def _frozen_hits(branch: str) -> list[str]:
    """`path (pattern)` for every frozen path BRANCH touches against
    origin/main, the list read as of origin/main (selfclone's check)."""
    frozen = selfclone._frozen()
    try:
        hits = frozen.check(gitdir(), ORIGIN_BASE, f"refs/heads/{branch}")
    except frozen.FrozenError as exc:
        raise hand.Refused(f"the frozen-path check could not run: {exc}", rule="frozen_check") from None
    return sorted({f"{hit.path} ({hit.pattern})" for hit in hits})


def _guarded(fields: Json, branch: str) -> Json:
    """The guard's say on the title and body (surface github), as a self
    job's ship asks it; a hold or a refusal names the field and why."""
    from ..gh import actions

    try:
        return actions.guarded(fields, "pr", {"repo": selfclone.repo(), "head": branch})
    except hand.Refused as exc:
        field = exc.detail.get("field") or "title or body"
        why = exc.detail.get("category") if exc.reason == "held" else exc.reason
        verb = "held" if exc.reason == "held" else "refused"
        raise hand.Refused(f"the guard {verb} the {field}: {why}", **exc.detail) from None


def _check_branch(branch: str) -> None:
    """The frozen-path check and the secret scan on BRANCH against
    origin/main, as a self job's ship runs them; the list read as of
    origin/main, so the change cannot loosen what it is checked against."""
    hits = _frozen_hits(branch)
    if hits:
        raise hand.Refused(f"it touches frozen path(s): {', '.join(hits)} (evals/frozen.txt as of {BASE})",
                           rule="frozen", paths=hits)
    findings = gitpush.scan_branch(gitdir(), ORIGIN_BASE, branch)
    if findings:
        named = ", ".join(f"{f['path']} ({f['rule']})" for f in findings)
        raise hand.Refused(f"it would publish what looks like a secret: {named}", rule="secrets", findings=findings)


def ship(slug: Any, title: Any, body: Any, gh: Any = None) -> Json:
    """The mind's tree as a pull request from teeny/mind-<slug> into main,
    after every rule; under DRY_RUN every rule runs and nothing is sent."""
    from ..gh import actions
    from ..gh.client import GitHubApi

    branch = _branch(slug)
    title = _text(title, "title", MAX_TITLE)
    body = _text(body, "body", MAX_BODY)
    full = selfclone.repo()
    with hand.lock("mind-clone"):
        origin = _prepare_locked()["commit"]
        if _rev(f"refs/remotes/origin/{branch}") is not None:
            raise hand.Refused(f"{branch} is already on GitHub (one pull request per branch): pick another name",
                               rule="branch_taken")
        fields = _guarded({"title": title, "body": body}, branch)
        _no_git_inside()
        commit = _commit(fields["title"])
        files = _changed("HEAD", commit)
        if not files or not _changed(origin, commit):
            raise hand.Refused("nothing changed against origin/main", rule="empty")
        _within_limits(commit, files)
        _only_plain_files(commit)
        request = {"repo": full, "head": branch, "base": BASE, "title": fields["title"],
                   "body": fields["body"].rstrip() + "\n\n" + DISCLOSURE}
        _bare("update-ref", f"refs/heads/{branch}", commit)
        pushed: Json = {}
        try:
            _check_branch(branch)
            if gh is None and not hand.dry_run():
                gh = GitHubApi.from_settings()
            pushed = actions.push_branch(gitdir(), full, branch, ORIGIN_BASE, gh=gh)
            opened = actions.open_pr(request, gh=gh)
        finally:
            if pushed and not pushed.get("dry_run"):
                # Pushed: the change is on the branch, so the tree starts clean.
                _fresh(origin, commit)
            else:
                _bare("update-ref", "-d", f"refs/heads/{branch}", check=False)
    if pushed.get("dry_run"):
        would = {"repo": opened.get("would_open_pr"), **{k: opened.get(k) for k in ("head", "base", "title", "body")}}
        return {"dry_run": True, "branch": branch, "files": files, "would_open": would}
    return {"repo": full, "branch": branch, "commit": pushed["commit"], "files": files,
            "pr_url": opened.get("html_url"), "number": opened.get("number")}
