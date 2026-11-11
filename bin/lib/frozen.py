"""The evolve gate's frozen-path check (docs/evolve.md, "What Teeny may change").

    python3 bin/lib/frozen.py [--root DIR] [--list FILE] BASE [HEAD]
    python3 bin/lib/frozen.py [--list FILE] --files PATH...

Lists the paths changed between BASE and HEAD (from their merge base, as a
pull request shows them) and refuses every one that matches a pattern in the
frozen list (evals/frozen.txt). Renames count as a deletion and an addition,
so a frozen path is refused whether it is edited, added, deleted, or renamed
in or out. Without HEAD it checks the working tree of --root against BASE:
staged, unstaged and untracked changes, which is what bin/evolve checks before
it commits a generation. --files checks a list of paths with no git at all.

The list is read as of BASE, never from the candidate, so a candidate cannot
loosen the list in the same diff that crosses it. --list overrides that; with
--files the default is this checkout's evals/frozen.txt.

Exit 0 when nothing frozen is touched, 1 naming every frozen path touched
(stderr, one per line), 2 for a usage or git error: the check fails closed.

bin/evolve calls check() (or violations()) directly. Python 3.11 standard
library only, and no sibling imports: CI runs the base's copy on its own.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, NamedTuple

LIST_PATH = "evals/frozen.txt"


class FrozenError(Exception):
    """The check could not run (a bad ref, no list): treat it as a refusal."""


class Hit(NamedTuple):
    status: str  # git's letter: A, M, D, T; "?" for untracked, "-" for --files
    path: str
    pattern: str


def parse_patterns(text: str) -> list[str]:
    """The frozen list's patterns: one per line, `#` comments and blanks skipped."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _regex(pattern: str) -> re.Pattern[str]:
    # `**` crosses `/`, `*` and `?` do not; a trailing `/` is the directory.
    # Whatever the pattern matches, everything beneath it matches too.
    pattern = pattern.strip("/")
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + "(?:/.*)?")


def normalise(path: str) -> str:
    """A repository-relative POSIX path: `./bin/x` and `brain/../bin/x` are `bin/x`."""
    path = posixpath.normpath(path.replace("\\", "/")).lstrip("/")
    return "" if path == "." else path


def frozen_pattern(path: str, patterns: Iterable[str]) -> str | None:
    """The first pattern that freezes PATH, or None when Teeny may change it."""
    path = normalise(path)
    for pattern in patterns:
        if _regex(pattern).fullmatch(path):
            return pattern
    return None


def violations(changes: Iterable[tuple[str, str]], patterns: list[str]) -> list[Hit]:
    """Every (status, path) change that touches a frozen path."""
    hits = []
    for status, path in changes:
        pattern = frozen_pattern(path, patterns)
        if pattern is not None:
            hits.append(Hit(status, normalise(path), pattern))
    return hits


def _git(root: str | Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise FrozenError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _name_status(out: str) -> list[tuple[str, str]]:
    # `--name-status -z --no-renames`: STATUS NUL PATH NUL, repeated.
    fields = out.split("\0")
    return [(fields[i][:1], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]


def changed_paths(root: str | Path, base: str, head: str | None = None) -> list[tuple[str, str]]:
    """(status, path) for every path changed from BASE to HEAD, or to the working tree."""
    if head is not None:
        out = _git(root, "diff", "--name-status", "-z", "--no-renames", f"{base}...{head}", "--")
        return _name_status(out)
    _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    changes = _name_status(_git(root, "diff", "--name-status", "-z", "--no-renames", base, "--"))
    untracked = _git(root, "ls-files", "-z", "--others", "--exclude-standard")
    changes += [("?", p) for p in untracked.split("\0") if p]
    return changes


def base_patterns(root: str | Path, base: str) -> list[str]:
    """The frozen list as of BASE."""
    try:
        text = _git(root, "show", f"{base}:{LIST_PATH}")
    except FrozenError as err:
        raise FrozenError(f"no frozen list at {base}:{LIST_PATH} ({err})") from None
    patterns = parse_patterns(text)
    if not patterns:
        raise FrozenError(f"the frozen list at {base}:{LIST_PATH} is empty")
    return patterns


def check(
    root: str | Path, base: str, head: str | None = None, patterns: list[str] | None = None
) -> list[Hit]:
    """The gate: every frozen path touched from BASE to HEAD (or the working tree).

    An empty list means the candidate may go on. Raises FrozenError when the
    check cannot run, which a caller must treat as a refusal.
    """
    if patterns is None:
        patterns = base_patterns(root, base)
    return violations(changed_paths(root, base, head), patterns)


def _read_list(path: str) -> list[str]:
    try:
        patterns = parse_patterns(Path(path).read_text())
    except OSError as err:
        raise FrozenError(f"cannot read the frozen list: {err}") from None
    if not patterns:
        raise FrozenError(f"the frozen list {path} is empty")
    return patterns


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bin/frozen-check",
        description="Refuse any change to a path frozen to Teeny (evals/frozen.txt).",
    )
    parser.add_argument("--root", default=".", help="the git checkout (default: .)")
    parser.add_argument("--list", help=f"the frozen list (default: {LIST_PATH} as of BASE)")
    parser.add_argument("--files", nargs="+", metavar="PATH", help="check these paths, no git")
    parser.add_argument("refs", nargs="*", metavar="BASE [HEAD]")
    args = parser.parse_args(argv)

    def say(msg: str) -> None:
        print(f"[frozen-check] {msg}", file=sys.stderr)

    try:
        if args.files:
            if args.refs:
                parser.error("--files takes no refs")
            patterns = _read_list(args.list or str(Path(__file__).resolve().parents[2] / LIST_PATH))
            hits = violations([("-", p) for p in args.files], patterns)
        else:
            if len(args.refs) not in (1, 2):
                parser.error("give BASE [HEAD], or --files PATH...")
            base, head = args.refs[0], (args.refs[1] if len(args.refs) == 2 else None)
            patterns = _read_list(args.list) if args.list else None
            hits = check(args.root, base, head, patterns)
    except FrozenError as err:
        say(f"error: {err}")
        return 2

    if hits:
        for hit in hits:
            print(f"frozen {hit.status} {hit.path} (matches {hit.pattern})", file=sys.stderr)
        say(f"refused: {len(hits)} frozen path(s) touched (evals/frozen.txt; docs/evolve.md)")
        return 1
    say("no frozen path touched")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
