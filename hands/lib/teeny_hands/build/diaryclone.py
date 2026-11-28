"""The agentic mind's copy of my diary (teenydroid/diary), and the one way the
mind writes to it (docs/mind.md, "Its diary"; bin/broker's /diary and
/diary-commit).

  state/home/diary/          the working tree, which the mind's shell and
                             file tools reach: hostile input, like repo/
  state/mind-diary/repo.git  its git directory, outside the jail

The diary is mostly written by code (bin/receipts: receipts, stats, wallet,
world, memory/, days/; bin/lib/diary_self.py: self/). The mind owns one
folder, `mind/`, and nothing else: a project log, a write-up, a page people
can read.

`prepare()` fetches the diary's main and puts a fresh checkout in the tree
when the tree is missing or clean, with mindclone's own primitives (made in
state/mind-diary/, renamed into place, never following a link). A tree with
edits is left as it is.

`commit(message)` refuses, in order: a message that is not 1..200
characters; no copy (run `teeny diary` first); a `.git` anywhere in the
tree; nothing changed; a changed path outside `mind/`; a link, a hard link
or a file that is not what git read; more than MAX_FILES files or MAX_LINES
lines; a file that is not UTF-8 text; what the secret scan finds; what the
guard holds or refuses, on the message and on every file it changes
(surface github, like the memory page). Then, holding the receipts' lock so
bin/receipts never races it, it lays those `mind/` changes on the diary's
main as it is now (receipts push often, and the mind's copy is usually
behind), commits as Teeny with commit-tree (no hooks) and pushes with git's
lease on that head, so the push is always a fast-forward and never
overwrites anyone. The tree is then checked out afresh. Under DRY_RUN every
rule runs and nothing is pushed. The key never enters the jail: the push
uses the receipts' token, read here.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from .. import git, hand
from ..gh import secretscan
from . import mindclone

Json = dict[str, Any]

DIARY = mindclone.Place("home/diary", "mind-diary")
BRANCH = "main"
ORIGIN = f"refs/remotes/origin/{BRANCH}"
#: The one folder of the diary the mind writes.
MIND_DIR = "mind/"
MAX_MESSAGE = 200
MAX_FILES = 20
MAX_LINES = 2000
DEFAULT_REPO = "teenydroid/diary"
FRESH_COPY = "copy what you want to keep into work/, then rm -rf diary and run bin/teeny diary for a fresh copy"


def repo() -> str:
    return hand.setting("RECEIPTS_REPO") or hand.setting("DIARY_REPO") or DEFAULT_REPO


def _url() -> str:
    # RECEIPTS_REMOTE (a full URL or a path) is for tests and mirrors, as in bin/lib/receipts.py.
    return hand.setting("RECEIPTS_REMOTE") or hand.setting("DIARY_REMOTE") or f"https://github.com/{repo()}.git"


def _token() -> str | None:
    return hand.setting("RECEIPTS_GITHUB_TOKEN") or hand.setting("DIARY_GITHUB_TOKEN") or hand.setting("GITHUB_TOKEN")


def _fetch_origin() -> str:
    mindclone._ensure_gitdir()
    mindclone._bare("fetch", "-q", "--prune", _url(), f"+refs/heads/{BRANCH}:{ORIGIN}", token=_token())
    origin = mindclone._rev(ORIGIN)
    if origin is None:
        raise hand.HandError(f"{repo()} has no {BRANCH} branch")
    return origin


def _settle(origin: str) -> Json:
    kind = mindclone._tree_kind()
    head = mindclone._rev("HEAD")
    kept: list[str] = []
    if kind == "missing" or head is None:
        kept = mindclone._fresh(origin, None if kind == "tree" else head)
        state = "cloned"
    elif mindclone._dirty():
        state = "dirty"
    elif head != origin:
        kept = mindclone._fresh(origin, head)
        state = "synced"
    else:
        state = "current"
    out: Json = {"path": str(mindclone.worktree()), "repo": repo(), "commit": origin, "head": mindclone._rev("HEAD"),
                 "state": state, "yours": MIND_DIR}
    if state == "dirty":
        out["note"] = f"your copy holds edits, so it was left as it is. To start again, {FRESH_COPY}"
    if kept:
        out["kept"] = kept
        out["note"] = ("edits made while the tree was refreshed are in " + ", ".join(kept)
                       + " (outside your reach); copy what is wanted back")
    return out


def prepare() -> Json:
    """state/home/diary/ as a checkout of the diary's main, unless it holds edits."""
    with mindclone.at(DIARY), hand.lock("mind-diary"):
        return _settle(_fetch_origin())


def _message(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(text) <= MAX_MESSAGE or "\n" in text:
        raise hand.Refused(f"the message is one line of 1 to {MAX_MESSAGE} characters", rule="message")
    return text


def _outside(paths: list[str]) -> list[str]:
    return [p for p in paths if not p.startswith(MIND_DIR) or ".." in p.split("/")]


def _texts(base: str, head: str) -> dict[str, str]:
    """Each file HEAD adds or changes against BASE, as UTF-8 text."""
    out: dict[str, str] = {}
    for mode, blob, path in mindclone._entries(base, head):
        if mode == "000000":
            continue
        data = mindclone._bare("cat-file", "blob", blob).stdout
        try:
            out[path] = data.decode("utf-8")
        except UnicodeDecodeError:
            raise hand.Refused(f"{path} is not UTF-8 text: the diary takes text", rule="not_text", path=path) from None
    return out


def _lines(base: str, head: str) -> int:
    total = 0
    for row in mindclone._bare("diff", "--numstat", "--no-renames", base, head).stdout.decode("utf-8", "replace").splitlines():
        total += sum(int(n) for n in row.split("\t")[:2] if n.isdigit())
    return total


def _screen(fields: Json) -> Json:
    """The guard on each field (surface github): its text when allowed; a
    hold or a refusal names the field."""
    from ..guard.gate import check_fields

    out: Json = {}
    for name, text in fields.items():
        verdict = check_fields({"body": text}, surface="github", kind="diary", context={"repo": repo(), "path": name})
        if verdict.kind != "allow":
            why = verdict.category if verdict.kind == "hold" else "refused"
            raise hand.Refused(f"the guard {'held' if verdict.kind == 'hold' else 'refused'} {name}: {why}",
                               rule="guard", path=name)
        body = verdict.fields.get("body") if isinstance(verdict.fields, dict) else None
        # The guard's text when it changed a word; the file's own bytes when
        # it only trimmed the whitespace around them.
        out[name] = body if isinstance(body, str) and body.strip() != text.strip() else text
    return out


def _blob(text: str) -> str:
    """TEXT written into the clone's git directory; its blob id. Written
    through a file of our own in state/mind-diary/, out of the mind's reach."""
    staged = mindclone.scratch() / f"blob-{secrets.token_hex(6)}"
    try:
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        return mindclone._bare("hash-object", "-w", "--no-filters", str(staged)).stdout.decode().strip()
    finally:
        staged.unlink(missing_ok=True)


def _onto(origin: str, base: str, head: str, texts: dict[str, str]) -> str:
    """The tree of ORIGIN with the mind/ changes HEAD makes against BASE
    laid on it (the guard's text for each file), in a throwaway index."""
    index = mindclone.scratch() / "onto-index"
    where = ("--git-dir", str(mindclone.gitdir()))
    try:
        git.run(mindclone.scratch(), *where, "read-tree", origin, index_file=index)
        for mode, _blob_id, path in mindclone._entries(base, head):
            if mode == "000000":
                git.run(mindclone.scratch(), *where, "update-index", "--force-remove", "--", path, index_file=index)
            else:
                blob = _blob(texts[path])
                git.run(mindclone.scratch(), *where, "update-index", "--add", "--cacheinfo",
                        f"{mode},{blob},{path}", index_file=index)
        return git.text(mindclone.scratch(), *where, "write-tree", index_file=index).strip()
    finally:
        index.unlink(missing_ok=True)


def commit(message: Any) -> Json:
    """The mind's edits under mind/ as one commit on the diary's main."""
    text = _message(message)
    with mindclone.at(DIARY), hand.lock("mind-diary"):
        if mindclone._tree_kind() == "missing" or mindclone._rev("HEAD") is None:
            raise hand.Refused("you have no copy of your diary yet: run bin/teeny diary first", rule="no_copy")
        mindclone._no_git_inside()
        base = mindclone._rev("HEAD")
        head = mindclone._commit(text)
        changed = mindclone._changed(base, head)
        if not changed:
            raise hand.Refused(f"nothing changed in your diary: write under {MIND_DIR} first", rule="nothing")
        outside = _outside(changed)
        if outside:
            raise hand.Refused(f"you write only under {MIND_DIR} in your diary; take these out: {', '.join(outside[:10])}"
                               " (code writes the rest)", rule="path", paths=outside)
        mindclone._only_plain_files(head, base)
        if len(changed) > MAX_FILES:
            raise hand.Refused(f"the change touches {len(changed)} files; the most is {MAX_FILES}", rule="too_big")
        lines = _lines(base, head)
        if lines > MAX_LINES:
            raise hand.Refused(f"the change is {lines} lines; the most is {MAX_LINES}", rule="too_big")
        texts = _texts(base, head)
        findings = secretscan.scan(changed, texts)
        if findings:
            named = ", ".join(f"{f.as_dict()['path']} ({f.as_dict()['rule']})" for f in findings)
            raise hand.Refused(f"it would publish what looks like a secret: {named}", rule="secrets")
        screened = _screen({"message": text, **texts})
        message_text = screened.pop("message")
        out: Json = {"repo": repo(), "files": changed, "lines": lines}
        if hand.dry_run():
            return {**out, "dry_run": True, "note": "DRY_RUN: every rule passed and nothing was pushed"}
        token = _token()
        if not token:
            raise hand.HandError("no token for the diary: RECEIPTS_GITHUB_TOKEN (or GITHUB_TOKEN) in secrets/teeny.env")
        with hand.lock("receipts", timeout=60):
            origin = _fetch_origin()
            tree = _onto(origin, base, head, screened)
            pushed = mindclone._bare("commit-tree", tree, "-p", origin, "-m", message_text).stdout.decode().strip()
            done = mindclone._bare("push", "-q", f"--force-with-lease=refs/heads/{BRANCH}:{origin}", _url(),
                                   f"{pushed}:refs/heads/{BRANCH}", check=False, token=token)
            if done.returncode != 0:
                raise hand.Refused("the diary moved while it was pushed; nothing was pushed, try again", rule="moved")
            mindclone._bare("update-ref", ORIGIN, pushed)
        mindclone._fresh(pushed, head)
        return {**out, "commit": pushed, "url": f"https://github.com/{repo()}/commit/{pushed}"}
