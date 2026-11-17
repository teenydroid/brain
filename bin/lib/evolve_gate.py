"""The evolve gate: refuse a candidate before anything of it runs.

    python3 bin/lib/evolve_gate.py [--root DIR] [--list FILE] [--max-lines N]
                                   [--max-soul-lines N] BASE [HEAD]

Prints one JSON document: {"ok", "base", "head", "changed", "lines",
"soul_lines", "refused": [{rule, path, detail}]}. Exit 0 when nothing is
refused, 1 when at least one rule refuses the candidate, 2 when the check
cannot run (a bad ref, no frozen list): the gate fails closed.

The rules, each with the id `refused[].rule` carries (docs/evolve.md, "What
Teeny may change"; DIRECTIVE.md, "The fences that never move"):

    frozen-path        a diff that edits, adds, deletes or renames a path in
                       evals/frozen.txt, read as of BASE. bin/lib/frozen.py
                       does the matching; this module reports every hit.
    symlink            any changed path that is a symlink at HEAD: a link is
                       how a candidate reaches a path it may not write.
    mode-change        a changed file whose mode differs between BASE and HEAD
                       (a chmod +x on a playbook is a change to how it runs).
    binary-file        a changed file git reports as binary.
    path-escape        a changed path with a `..` segment or an absolute path.
    diff-size          more than --max-lines (400) added and removed lines.
    soul-size          more than --max-soul-lines (60) changed lines across
                       brain/SOUL.md and brain/CONTEXT.md.
    cron-policy        brain/cron.toml that bin/seed-cron --check refuses
                       (a job more often than every 5 minutes, an unknown
                       playbook, a catch_up on a posting playbook, ...).
    soul-anchor        a SOUL.md that dropped the anchor: the sentence saying
                       $TINY is never sold.
    hand-without-test  a new hand with no hands/tests/test_<name>.py and no
                       hands/tests/<name>/ that parses.
    hand-contract      a new hand whose own run does not print exactly one
                       JSON object on stdout (the hand contract,
                       docs/architecture.md §3), run with DRY_RUN=1, a 20 s
                       timeout and no inherited environment.

Every rule is reported: a candidate that breaks three is refused with three
entries, so its builder reads them all in one round. Python 3.11 standard
library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import frozen

#: The caps this gate holds candidates to. They are the published ones
#: (docs/evolve.md), not a share of the repository: 400 changed lines in all,
#: 60 across SOUL.md and CONTEXT.md, which is what a candidate may spend.
MAX_CHANGE_LINES = 400
MAX_SOUL_LINES = 60

#: The files the SOUL cap counts (the prompt's own two documents).
SOUL_CONTEXT = ("brain/SOUL.md", "brain/CONTEXT.md")

#: The SOUL anchor: the sentence that says $TINY is never sold. It is written
#: in SOUL.md and anchored in evals/invariants.md (frozen); a candidate that
#: removes it is refused rather than reviewed.
ANCHOR = "you never sell it"

#: A new hand is run once, under these, to see what it prints.
HAND_TIMEOUT = 20.0

#: git's modes for a symlink and for the two file kinds a diff may show.
SYMLINK_MODE = "120000"
REGULAR_MODES = ("100644", "100755")


class GateError(Exception):
    """The check could not run: the gate refuses (exit 2)."""


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="surrogateescape",
    )
    if done.returncode != 0:
        raise GateError(f"git {' '.join(args)}: {done.stderr.strip()[:400]}")
    return done.stdout


def _exists(root: Path, rev: str, path: str) -> bool:
    done = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{rev}:{path}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return done.returncode == 0


def _modes_at(root: Path, rev: str) -> dict[str, str]:
    """Every path at REV and the mode git records for it."""
    modes: dict[str, str] = {}
    for line in _git(root, "ls-tree", "-r", "-z", rev).split("\0"):
        if not line:
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 3:
            modes[path] = parts[0]
    return modes


def _numstat(root: Path, base: str, head: str) -> list[tuple[int | None, int | None, str]]:
    out: list[tuple[int | None, int | None, str]] = []
    for line in _git(root, "diff", "--numstat", "-M", base, head).splitlines():
        if not line.strip():
            continue
        added, removed, path = line.split("\t", 2)
        out.append((
            None if added == "-" else int(added),
            None if removed == "-" else int(removed),
            path,
        ))
    return out


def _raw_diffs(root: Path, base: str, head: str) -> list[tuple[str, str, str, str, str]]:
    """(old mode, new mode, status, path, path) from `git diff --raw -M`."""
    rows: list[tuple[str, str, str, str, str]] = []
    for line in _git(root, "diff", "--raw", "-M", base, head).splitlines():
        if not line.startswith(":"):
            continue
        meta, _, paths = line.partition("\t")
        old, new, _sha_old, _sha_new, status = meta[1:].split(" ") if meta.count(" ") >= 4 else (None,) * 5
        if old is None:
            continue
        # A rename or copy lists the old path and the new one, tab separated.
        old_path, _, new_path = paths.partition("\t")
        rows.append((old, new, status[0], old_path, new_path or old_path))
    return rows


def _lines_at(root: Path, rev: str, path: str) -> str:
    return _git(root, "show", f"{rev}:{path}")


def _normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _hand_name(path: str) -> str | None:
    """The hand a path belongs to, as a bare name: hands/<name>, or a file in
    hands/lib/teeny_hands/<name>/ or hands/lib/teeny_hands/<name>.py."""
    parts = path.split("/")
    if len(parts) == 2 and parts[0] == "hands" and parts[1]:
        return parts[1]
    if len(parts) >= 4 and parts[:2] == ["hands", "lib"] and parts[2] == "teeny_hands":
        return parts[3].removesuffix(".py")
    return None


def _json_values(stdout: str) -> int:
    """How many JSON values `stdout` holds, one after another."""
    decoder = json.JSONDecoder()
    count = 0
    index = 0
    while True:
        while index < len(stdout) and stdout[index] in " \t\r\n":
            index += 1
        if index >= len(stdout):
            return count
        try:
            _, index = decoder.raw_decode(stdout, index)
        except ValueError:
            return count
        count += 1


def _run_hand(root: Path, name: str) -> tuple[bool, str]:
    """Run a new hand once: one JSON object out, exit 0 or 2. Not a harness for
    the hand's work: DRY_RUN=1, no inherited environment, a short timeout."""
    executable = root / "hands" / name
    if not executable.exists():
        return False, f"hands/{name} is missing at HEAD"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "DRY_RUN": "1",
        "TZ": "UTC",
        "LANG": "C.UTF-8",
    }
    try:
        done = subprocess.run(
            [str(executable)],
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="surrogateescape",
            timeout=HAND_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"hands/{name} did not run: {error}"
    if done.returncode not in (0, 2):
        return False, f"hands/{name} exited {done.returncode}, not 0 or 2: {done.stderr.strip()[:200]}"
    values = _json_values(done.stdout)
    if values != 1:
        return False, f"hands/{name} printed {values} JSON value(s) on stdout, not exactly one"
    return True, "one JSON object out"


def refusals(root: Path, base: str, head: str, patterns: list[str] | None,
             max_lines: int = MAX_CHANGE_LINES, max_soul_lines: int = MAX_SOUL_LINES) -> list[dict]:
    """Every rule the candidate breaks, one entry each: {rule, path, detail}."""
    out: list[dict] = []

    def refuse(rule: str, path: str, detail: str) -> None:
        out.append({"rule": rule, "path": path, "detail": detail})

    # The frozen list is the base's, never the candidate's (frozen.base_patterns).
    try:
        hits = frozen.check(root, base, head, patterns)
    except frozen.FrozenError as error:
        raise GateError(f"the frozen check could not run: {error}") from error
    for hit in hits:
        refuse("frozen-path", hit.path, f"{hit.status} {hit.path} is frozen (evals/frozen.txt)")

    modes_head = _modes_at(root, head)
    diffs = _raw_diffs(root, base, head)
    stats = _numstat(root, base, head)

    for old, new, _status, old_path, new_path in diffs:
        if modes_head.get(new_path) == SYMLINK_MODE:
            refuse("symlink", new_path, f"{new_path} is a symlink at HEAD: a link reaches a path the diff does not show")
        if old in REGULAR_MODES and new in REGULAR_MODES and old != new:
            refuse("mode-change", new_path, f"{new_path} goes from {old} to {new}")

    for added, removed, path in stats:
        if added is None or removed is None:
            refuse("binary-file", path, f"{path} is binary: a diff cannot be reviewed for it")

    for _old, _new, _status, old_path, new_path in diffs:
        for path in {old_path, new_path}:
            if path.startswith("/") or ".." in path.split("/"):
                refuse("path-escape", path, f"{path} leaves the repository: a path that contains `..` or is absolute")

    lines = sum((added or 0) + (removed or 0) for added, removed, _ in stats)
    if lines > max_lines:
        refuse("diff-size", "", f"{lines} added and removed lines, over the {max_lines} a candidate may spend")

    soul = sum((added or 0) + (removed or 0) for added, removed, path in stats if path in SOUL_CONTEXT)
    if soul > max_soul_lines:
        refuse("soul-size", ", ".join(SOUL_CONTEXT),
               f"{soul} changed lines across SOUL.md and CONTEXT.md, over {max_soul_lines}")

    # brain/cron.toml, read by the referee (bin/seed-cron), not by this module.
    cron = root / "brain" / "cron.toml"
    if cron.exists():
        referee = Path(__file__).resolve().parents[2]
        done = subprocess.run(
            ["bash", str(referee / "bin" / "seed-cron"), "--check",
             "--file", str(cron), "--pinned", str(referee / "evals" / "cron.pinned.toml")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="surrogateescape",
        )
        if done.returncode != 0:
            detail = (done.stdout + done.stderr).strip().splitlines()
            refuse("cron-policy", "brain/cron.toml",
                   detail[-1][:400] if detail else "bin/seed-cron --check refused brain/cron.toml")

    # The SOUL anchor, from the base's SOUL.md into the candidate's.
    if _exists(root, base, "brain/SOUL.md"):
        before = _normalise_text(_lines_at(root, base, "brain/SOUL.md"))
        if ANCHOR in before:
            after = _normalise_text(_lines_at(root, head, "brain/SOUL.md")) if _exists(root, head, "brain/SOUL.md") else ""
            if ANCHOR not in after:
                refuse("soul-anchor", "brain/SOUL.md", f"brain/SOUL.md no longer says \"{ANCHOR}\": the anchor stays")

    # New hands: a test, and the hand contract.
    added_paths = [path for status, path in frozen.changed_paths(root, base, head) if status.startswith("A")]
    new_hands = sorted({
        name for path in added_paths
        if (name := _hand_name(path))
        and not _exists(root, base, f"hands/{name}")
        and not _exists(root, base, f"hands/lib/teeny_hands/{name}")
        and not _exists(root, base, f"hands/lib/teeny_hands/{name}.py")
    })
    for name in new_hands:
        test_file = root / "hands" / "tests" / f"test_{name}.py"
        test_dir = root / "hands" / "tests" / name
        if not test_file.exists() and not test_dir.is_dir():
            refuse("hand-without-test", f"hands/{name}",
                   f"hands/{name} adds a hand with no hands/tests/test_{name}.py and no hands/tests/{name}/")
            continue
        ok, detail = _run_hand(root, name)
        if not ok:
            refuse("hand-contract", f"hands/{name}", detail)

    return out


def evaluate(root: Path, base: str, head: str, patterns: list[str] | None = None,
             max_lines: int = MAX_CHANGE_LINES, max_soul_lines: int = MAX_SOUL_LINES) -> dict:
    """The gate's answer: the refusals and the numbers they were judged on."""
    stats = _numstat(root, base, head)
    refused = refusals(root, base, head, patterns, max_lines, max_soul_lines)
    return {
        "ok": not refused,
        "base": base,
        "head": head,
        "changed": len(stats),
        "lines": sum((added or 0) + (removed or 0) for added, removed, _ in stats),
        "soul_lines": sum((added or 0) + (removed or 0) for added, removed, path in stats if path in SOUL_CONTEXT),
        "refused": refused,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bin/evolve-gate",
        description="Refuse a candidate the evolve loop may not ship (docs/evolve.md).",
    )
    parser.add_argument("base")
    parser.add_argument("head", nargs="?", default="HEAD")
    parser.add_argument("--root", default=".")
    parser.add_argument("--list", dest="list_path", default=None,
                        help="read the frozen patterns from this file instead of BASE:evals/frozen.txt")
    parser.add_argument("--max-lines", type=int, default=MAX_CHANGE_LINES)
    parser.add_argument("--max-soul-lines", type=int, default=MAX_SOUL_LINES)
    opts = parser.parse_args(argv)

    root = Path(opts.root).resolve()
    try:
        patterns = frozen.parse_patterns(Path(opts.list_path).read_text(encoding="utf-8")) if opts.list_path else None
        answer = evaluate(root, opts.base, opts.head, patterns, opts.max_lines, opts.max_soul_lines)
    except (GateError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error), "refused": []}))
        print(f"[evolve-gate] {error}", file=sys.stderr)
        return 2

    print(json.dumps(answer, indent=2, sort_keys=True))
    for item in answer["refused"]:
        print(f"[evolve-gate] {item['rule']}: {item['detail']}", file=sys.stderr)
    if answer["refused"]:
        print(f"[evolve-gate] refused: {len(answer['refused'])} rule(s), "
              f"{answer['lines']} changed lines", file=sys.stderr)
        return 1
    print(f"[evolve-gate] accepted: {answer['lines']} changed lines", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
