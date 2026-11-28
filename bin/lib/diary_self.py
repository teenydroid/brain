"""The files I rewrite myself, in public: my mind's notes and skills, and any
page OpenClaw keeps for an agent, published to the diary repository's
`self/` beside the receipts, so its history shows how I change.

bin/lib/receipts.py calls `add(root, live, check_secrets, repo, commit)` on
the checkout it is about to push (or on the preview folder). It mirrors:

    self/notes/<NAME>.md              state/home/notes/<NAME>.md, for NAME in
                                      NOTES (not the journal, the inbox,
                                      drafts, patches or backups)
    self/skills/INDEX.md              state/home/skills/INDEX.md
    self/skills/<name>/SKILL.md       state/home/skills/<name>/SKILL.md
    self/workspaces/<agent>/<FILE>    state/openclaw/workspaces/<agent>/<FILE>,
                                      for FILE in WORKSPACE_FILES: the pages
                                      OpenClaw lets an agent rewrite. Never
                                      AGENTS.md, which the core builds from
                                      brain/agents/<agent>.md (already in git)

and, live, commits what changed as one commit, `self: 2026-11-28 (+1 ~3 -0)`.
A run with nothing changed makes no commit. A source that is gone is
removed from self/.

Each file takes the memory page's road (bin/lib/diary_memory.py): code
redacts it (chat ids, email addresses, foreign wallet addresses, URL
queries, links off the guard's allowlist), the receipts' secret gate checks
it, and the guard screens it (surface github, the model judge included).
A file the gate or the guard stops is withheld: its last published copy
stays as it was, and it is tried again next run (the file is my own and
changes; nothing is remembered about it). Only plain files are read, never
through a link, each at most MAX_BYTES. Under DRY_RUN (the preview) the
files are redacted but not screened, marked `would_screen`. Off unless
DIARY_SELF=1 in secrets/teeny.env: it publishes in public.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable

import diary_memory
from teeny_hands import hand

Json = dict[str, Any]

FLAG = "DIARY_SELF"
SELF_DIR = "self"
#: The notes files my mind distils and keeps (brain/agents/mind.md).
NOTES = ("SELF", "MEMORY", "LEARNINGS", "CHECKS", "PROJECTS")
#: The pages OpenClaw lets an agent rewrite in its workspace.
WORKSPACE_FILES = ("SOUL.md", "MEMORY.md", "IDENTITY.md", "USER.md")
MAX_BYTES = 256 * 1024
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def enabled() -> bool:
    return hand.flag(FLAG, False)


def _read(path: Path) -> str | None:
    """PATH's text, opened without following a link; None for anything but
    a plain UTF-8 file of at most MAX_BYTES."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        found = os.fstat(fh.fileno())
        if not stat.S_ISREG(found.st_mode) or found.st_size > MAX_BYTES:
            return None
        try:
            return fh.read(MAX_BYTES + 1).decode("utf-8")
        except UnicodeDecodeError:
            return None


def _dirs(folder: Path) -> list[Path]:
    """FOLDER's own subfolders with a plain name, never through a link."""
    try:
        return sorted(p for p in folder.iterdir() if NAME.fullmatch(p.name) and p.is_dir() and not p.is_symlink())
    except OSError:
        return []


def sources() -> dict[str, Path]:
    """Where each file under self/ comes from."""
    state = hand.state_dir()
    home = state / "home"
    out: dict[str, Path] = {}
    for name in NOTES:
        out[f"{SELF_DIR}/notes/{name}.md"] = home / "notes" / f"{name}.md"
    out[f"{SELF_DIR}/skills/INDEX.md"] = home / "skills" / "INDEX.md"
    for skill in _dirs(home / "skills"):
        out[f"{SELF_DIR}/skills/{skill.name}/SKILL.md"] = skill / "SKILL.md"
    for agent in _dirs(state / "openclaw" / "workspaces"):
        for name in WORKSPACE_FILES:
            out[f"{SELF_DIR}/workspaces/{agent.name}/{name}"] = agent / name
    return out


def _screen(text: str, repo: str, path: str) -> Json:
    from teeny_hands.guard.gate import check_fields

    verdict = check_fields({"body": text}, surface="github", kind="self", context={"repo": repo, "page": path})
    if verdict.kind == "allow" and isinstance(verdict.fields, dict) and isinstance(verdict.fields.get("body"), str):
        body = verdict.fields["body"]
        return {"kind": "allow", "text": body if body.strip() != text.strip() else text}
    if verdict.kind == "hold" and verdict.judge_failed:
        return {"kind": "judge_unreachable"}
    if verdict.kind == "hold":
        return {"kind": "held", "category": verdict.category}
    return {"kind": "refused"}


def _published(root: Path) -> set[str]:
    folder = root / SELF_DIR
    if not folder.is_dir():
        return set()
    return {p.relative_to(root).as_posix() for p in folder.rglob("*") if p.is_file() and not p.is_symlink()}


def message(added: int, changed: int, removed: int) -> str:
    day = dt.datetime.fromtimestamp(hand.now(), dt.timezone.utc).strftime("%Y-%m-%d")
    return f"self: {day} (+{added} ~{changed} -{removed})"


def add(root: Path, live: bool, check_secrets: Callable[[str], None], repo: str = "teenydroid/diary",
        commit: Callable[[str], bool] | None = None) -> Json:
    """Mirrors my self-written files into ROOT/self/; when anything changed
    and COMMIT is given, commits it with `commit(message)`, which answers
    whether it committed. `live` False is the preview: redacted, unscreened."""
    if not enabled():
        return {"skipped": f"off ({FLAG} in secrets/teeny.env)"}
    out: Json = {"added": [], "changed": [], "removed": [], "withheld": []}
    if not live:
        out["would_screen"] = True
    wanted = sources()
    before = _published(root)
    kept: set[str] = set()
    for rel, source in sorted(wanted.items()):
        text = _read(source)
        if text is None or not text.strip():
            continue
        text = diary_memory.redact(text)
        try:
            check_secrets("\n".join("+" + line for line in text.splitlines()))
        except hand.Refused:
            out["withheld"].append({"path": rel, "why": "secret"})
            kept.add(rel)
            continue
        if live:
            screened = _screen(text, repo, rel)
            if screened["kind"] != "allow":
                out["withheld"].append({"path": rel, "why": screened["kind"],
                                        **({"category": screened["category"]} if screened.get("category") else {})})
                kept.add(rel)
                continue
            text = screened["text"]
        target = root / rel
        kept.add(rel)
        old = target.read_text(encoding="utf-8") if target.is_file() and not target.is_symlink() else None
        if old == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        out["added" if old is None else "changed"].append(rel)
    for rel in sorted(before - kept):
        (root / rel).unlink()
        out["removed"].append(rel)
    counts = (len(out["added"]), len(out["changed"]), len(out["removed"]))
    out["committed"] = bool(commit is not None and any(counts) and commit(message(*counts)))
    return out
