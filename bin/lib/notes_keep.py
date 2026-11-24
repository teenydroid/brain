"""Snapshots of the agentic mind's notes, so a file it destroyed can be read back.

The mind remembers nothing between wakes but state/home/notes/, and its one
writing tool, `file_write`, replaces a file whole: on 2026-11-24 at 02:36Z a
journal entry written with it replaced about 19 KB of JOURNAL.md, and on
2026-11-23 several notes files vanished and were rebuilt from memory. The
append verbs in the client (`teeny journal`, `teeny note`) make the right
write easy; this keeps a history for when the wrong one happens anyway.

The broker calls `snapshot()` every EVERY_SECS while it serves (the mind's
wakes are agent cron jobs with no start hook, so a short fixed schedule in the
process that already writes into state/home is the hook), and `bin/broker
keep` takes one by hand. Each snapshot is the notes markdown (the fixed files
below plus any other plain *.md directly in notes/, at most MAX_FILE each)
copied to notes/.keep/<UTC stamp>/, skipped when nothing changed since the
last one. Pruning keeps every snapshot of the last RECENT_SECS, the newest of
each UTC hour for HOURLY_SECS and the newest of each UTC day for DAILY_SECS:
with a change every ten minutes, at most about 36 + 42 + 12 folders.

A destructive rewrite is noticed cheaply as the snapshot is taken: JOURNAL.md
more than SHRINK_LINES lines shorter than in the last snapshot, or a fixed
notes file gone. Nothing is restored. The alert names the file, the line
counts and the last full copy, goes to the mind's inbox
(notes/inbox/keep-<file>.md) and to `teeny pulse` (`notes_shrunk`), and pins
that copy against pruning until the file is back to its old length or
DAILY_SECS pass.

The broker runs outside the jail, as the spine's user, with secrets/ in reach,
and the mind can leave anything in notes/: a link to a secret, a link in
place of .keep/ or of a snapshot folder. So nothing here opens a path by
name. Notes are read through a descriptor on notes/ with O_NOFOLLOW, only
regular files with one link; writes go through mind_home, which replaces a
link instead of following it; pruning unlinks a link and never descends into
one. Nothing outside state/home/notes/ is read or written.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB.parents[1] / "hands" / "lib"))
sys.path.insert(0, str(LIB))

from teeny_hands import hand  # noqa: E402

import mind_home  # noqa: E402

Json = dict[str, Any]

NOTES = "notes"
KEEP = ".keep"
#: The notes files the mind keeps (docs/mind.md, "Notes"), always copied first.
FIXED = ("JOURNAL.md", "PROJECTS.md", "TASKS.md", "CHECKS.md", "LEARNINGS.md", "MEMORY.md", "SELF.md")
#: The largest notes file copied, and the most other *.md files a snapshot takes.
MAX_FILE = 256 * 1024
MAX_EXTRA = 40
#: How often the broker takes a snapshot: wakes come every 30 minutes at most
#: (the pinned base wake), so each one sees at least one taken before it.
EVERY_SECS = 600
RECENT_SECS = 6 * 3600
HOURLY_SECS = 48 * 3600
DAILY_SECS = 14 * 86400
#: How many lines JOURNAL.md may lose between snapshots before it is reported:
#: the mind tidying a line or two is not a destroyed history.
SHRINK_LINES = 3
#: Watched for shrinking (JOURNAL.md by lines, the fixed files by vanishing).
JOURNAL = "JOURNAL.md"
ALERTS = "alerts.json"
STAMP = re.compile(r"\d{8}T\d{6}Z")
PARTIAL = ".partial-"


def stamp_of(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _time_of(name: str) -> float:
    return dt.datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lines(text: str) -> int:
    return len(text.splitlines())


# --- reading without following a link ---------------------------------------------------


def _read_file(folder: int, name: str) -> str | None:
    """NAME in FOLDER as text when it is a regular file with one link and at
    most MAX_FILE bytes; None otherwise. Never through a link, never blocks
    on a fifo, and waits briefly for an append in progress (`teeny journal`
    holds an exclusive lock while it writes)."""
    try:
        found = os.stat(name, dir_fd=folder, follow_symlinks=False)
        if not stat.S_ISREG(found.st_mode) or found.st_nlink != 1 or found.st_size > MAX_FILE:
            return None
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=folder)
    except OSError:
        return None
    try:
        opened = os.fstat(fd)
        if (opened.st_ino, opened.st_dev) != (found.st_ino, found.st_dev) or not stat.S_ISREG(opened.st_mode):
            return None
        for _ in range(20):
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.05)
        data = os.read(fd, MAX_FILE + 1)
        return None if len(data) > MAX_FILE else data.decode("utf-8", errors="replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def _open_dir(parent: int, name: str) -> int | None:
    """A descriptor on the real folder NAME in PARENT; None for anything else."""
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    except OSError:
        return None


def _read_folder(folder: int) -> tuple[dict[str, str], list[str]]:
    """The notes markdown in FOLDER (the fixed files first, then up to
    MAX_EXTRA others), and the names of the *.md files left out."""
    names = sorted(os.listdir(folder))
    wanted = [n for n in FIXED if n in names]
    wanted += [n for n in names if n.endswith(".md") and not n.startswith(".") and n not in FIXED]
    files: dict[str, str] = {}
    skipped: list[str] = []
    for name in wanted:
        extra = name not in FIXED
        if extra and sum(1 for n in files if n not in FIXED) >= MAX_EXTRA:
            skipped.append(name)
            continue
        text = _read_file(folder, name)
        if text is None:
            skipped.append(name)
        else:
            files[name] = text
    return files, skipped


def _notes_fd() -> int:
    """notes/, every step a real folder (mind_home replaces a link on the way)."""
    return mind_home._open((NOTES,))


def _keep_fd() -> int:
    return mind_home._open((NOTES, KEEP))


def _is_stamp(name: str) -> bool:
    """A snapshot folder's name: stamp-shaped and a real UTC time (a folder
    named 20261301T000000Z is left alone, never parsed again)."""
    if not STAMP.fullmatch(name):
        return False
    try:
        _time_of(name)
    except ValueError:
        return False
    return True


def _stamps(keep: int) -> list[str]:
    return sorted(n for n in os.listdir(keep) if _is_stamp(n))


# --- removing without following a link ---------------------------------------------------


def _remove(parent: int, name: str, depth: int = 0) -> None:
    """Removes NAME from PARENT: a link or file is unlinked, a real folder is
    emptied first, never through a link. Bounded depth: a snapshot is flat."""
    try:
        found = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(found.st_mode):
        os.unlink(name, dir_fd=parent)
        return
    fd = _open_dir(parent, name)
    if fd is None:
        return
    try:
        if depth < 4:
            for child in os.listdir(fd):
                _remove(fd, child, depth + 1)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)


# --- the alerts --------------------------------------------------------------------------


def _load_alerts(keep: int) -> list[Json]:
    text = _read_file(keep, ALERTS)
    try:
        value = json.loads(text) if text else []
    except ValueError:
        return []
    return [a for a in value if isinstance(a, dict) and isinstance(a.get("file"), str)
            and isinstance(a.get("stamp"), str) and _is_stamp(a["stamp"])] if isinstance(value, list) else []


def _save_alerts(alerts: list[Json]) -> None:
    mind_home.write((NOTES, KEEP), ALERTS, json.dumps(alerts, indent=2) + "\n", 0o644)


def alerts() -> list[Json]:
    """The open alerts: which notes file shrank, by how much, and where its
    last full copy is. For `teeny pulse`. Never raises, and never makes or
    changes a folder: a read."""
    try:
        state = os.open(hand.state_dir(), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return []
    try:
        home = _open_dir(state, mind_home.HOME)
    finally:
        os.close(state)
    if home is None:
        return []
    try:
        notes = _open_dir(home, NOTES)
    finally:
        os.close(home)
    if notes is None:
        return []
    try:
        keep = _open_dir(notes, KEEP)
        if keep is None:
            return []
        try:
            return _load_alerts(keep)
        finally:
            os.close(keep)
    finally:
        os.close(notes)


def _inbox(alert: Json) -> None:
    gone = alert["lines_now"] == 0 and alert.get("vanished")
    what = (f"notes/{alert['file']} is gone" if gone
            else f"notes/{alert['file']} shrank from {alert['lines_before']} to {alert['lines_now']} lines")
    text = (f"# {alert['file']} lost its history\n\n"
            f"{what} between {alert['before_at']} and {alert['seen_at']} (UTC). Nothing was restored.\n\n"
            f"The last full copy is `{alert['last_full_copy']}` ({alert['lines_before']} lines). Read it, put back "
            f"what was lost, and add to the journal with `teeny journal \"...\"`, which appends; `file_write` "
            f"replaces the whole file.\n")
    mind_home.write((NOTES, "inbox"), f"keep-{alert['file']}", text, 0o644)


def _shrunk(before: dict[str, str], now: dict[str, str], excluded: list[str], prev: str, t: float) -> list[Json]:
    """The fixed files lost since snapshot PREV: JOURNAL.md shorter by more
    than SHRINK_LINES, or any of them absent from notes/. A file still there
    but left out of the copy (EXCLUDED: over MAX_FILE, a link, a hard link)
    is no loss and raises nothing; snapshot() lists it in skipped_files."""
    found = []
    for name in FIXED:
        if name not in before or name in excluded:
            continue
        was, cur = _lines(before[name]), _lines(now.get(name, ""))
        vanished = name not in now
        if vanished or (name == JOURNAL and was - cur > SHRINK_LINES):
            found.append({"file": name, "lines_before": was, "lines_now": cur, "vanished": vanished,
                          "before_at": _iso(_time_of(prev)), "seen_at": _iso(t), "stamp": prev,
                          "last_full_copy": f"{NOTES}/{KEEP}/{prev}/{name}"})
    return found


# --- pruning -----------------------------------------------------------------------------


def keep_set(stamps: list[str], now: float, pinned: set[str]) -> set[str]:
    """Which snapshots stay: every one of the last RECENT_SECS, the newest of
    each UTC hour inside HOURLY_SECS, of each UTC day inside DAILY_SECS, the
    latest, and every one an open alert names."""
    kept = set(pinned) | ({stamps[-1]} if stamps else set())
    hours: dict[str, str] = {}
    days: dict[str, str] = {}
    for s in stamps:
        age = now - _time_of(s)
        if age <= RECENT_SECS:
            kept.add(s)
        if age <= HOURLY_SECS:
            hours[s[:11]] = max(hours.get(s[:11], s), s)
        if age <= DAILY_SECS:
            days[s[:8]] = max(days.get(s[:8], s), s)
    return kept | set(hours.values()) | set(days.values())


# --- the snapshot ------------------------------------------------------------------------


def _seen(alert: Json) -> float:
    """When an alert was raised; its lifetime counts from then, not from the
    copy it names, which may be far older after a quiet spell."""
    try:
        return dt.datetime.strptime(str(alert.get("seen_at")), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return _time_of(alert["stamp"])


def snapshot() -> Json:
    """Takes one snapshot of notes/ when anything changed, reports a shrunken
    journal or a vanished notes file, and prunes. Answers what it did.

    One run at a time: an exclusive lock on the .keep/ folder spans the whole
    of it (the broker's timer and `bin/broker keep` may overlap). A run that
    finds it held does nothing and answers `busy`. An alert is saved before
    the shortened snapshot becomes the newest, and its inbox note is retried
    at every run until it is written."""
    t = hand.now()
    keep = _keep_fd()
    out: Json = {"taken": None, "shrunk": [], "pruned": [], "skipped_files": [], "carried": [], "busy": False}
    try:
        try:
            fcntl.flock(keep, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            out["busy"] = True
            return out
        # Read under the lock: a reading taken before it could predate
        # another run's snapshot and close that run's alert.
        notes = _notes_fd()
        try:
            current, skipped = _read_folder(notes)
        finally:
            os.close(notes)
        out["skipped_files"] = skipped
        for name in os.listdir(keep):
            if name.startswith(PARTIAL):
                _remove(keep, name)  # an interrupted snapshot: no other run holds the lock
        stamps = _stamps(keep)
        prev, before = None, {}
        for s in reversed(stamps):
            fd = _open_dir(keep, s)
            if fd is not None:
                try:
                    prev, before = s, _read_folder(fd)[0]
                finally:
                    os.close(fd)
                break
        # A fixed file still in notes/ but left out of the copy (it grew past
        # MAX_FILE, or became a link) keeps its last full copy in every later
        # snapshot: a later loss is then seen against that copy, and an alert
        # pins it.
        for name in FIXED:
            if name in skipped and name in before:
                current[name] = before[name]
                out["carried"].append(name)
        saved = _load_alerts(keep)
        open_alerts = [dict(a) for a in saved]
        if prev is not None and before == current:
            out["unchanged_since"] = f"{NOTES}/{KEEP}/{prev}"
        elif prev is not None:
            have = {a["file"] for a in open_alerts}
            for alert in _shrunk(before, current, skipped, prev, t):
                if alert["file"] in have:  # the first full copy is the one to keep
                    continue
                alert["notified"] = False
                open_alerts.append(alert)
                out["shrunk"].append(alert)
        # An alert closes when its file is back to its old length, or DAILY_SECS after it was raised.
        open_alerts = [a for a in open_alerts
                       if _lines(current.get(a["file"], "")) < a.get("lines_before", 0) and t - _seen(a) <= DAILY_SECS]
        if open_alerts != saved:
            _save_alerts(open_alerts)
            saved = [dict(a) for a in open_alerts]
        if current and out.get("unchanged_since") is None:
            name = stamp_of(t)
            if name not in stamps:
                partial = PARTIAL + name
                for file, text in current.items():
                    mind_home.write((NOTES, KEEP, partial), file, text, 0o644)
                os.rename(partial, name, src_dir_fd=keep, dst_dir_fd=keep)
                stamps.append(name)
                stamps.sort()
                out["taken"] = f"{NOTES}/{KEEP}/{name}"
        for alert in open_alerts:
            if not alert.get("notified", True):
                _inbox(alert)
                alert["notified"] = True
        if open_alerts != saved:
            _save_alerts(open_alerts)
        kept = keep_set(stamps, t, {a["stamp"] for a in open_alerts})
        for s in stamps:
            if s not in kept:
                _remove(keep, s)
                out["pruned"].append(s)
    finally:
        os.close(keep)  # and the lock with it
    return out
