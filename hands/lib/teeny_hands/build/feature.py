"""The feature rubric: what a self job that builds a capability (a `want`,
build/jobs.py `propose_self`) must hold before the desk accepts "ok".

A feature is a new hand that something uses. Until the clone has all of it,
`rubric` says what is missing, and `jobs.done` sends the report back with
that text, so the builder finishes the job instead of shipping half of it:

  - a new hand: an executable `hands/<name>`, whose package
    `hands/lib/teeny_hands/<name>/` runs through `hand.run` (the hand
    contract: one JSON line, exit codes)
  - tests: `hands/tests/test_<name>.py`, which parses, and which pass when
    the desk runs them itself in the Landlock jail (build/verify.py)
  - a playbook that calls it (an input `run = "hands/<name> ..."` or an
    output `hand:<name> <verb>`) that brain/cron.toml runs as `bin/act <book>`
  - a `## `<name>`` section in docs/hands.md

Pure reads of the clone, plus the one jailed test run; nothing is written.
The same ideas as evolve's gate (bin/lib/evolve.py: new hands have tests, are
called, are documented), which the hand lib does not import: this is the
operator's copy, for lane 2.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import tomllib
from pathlib import Path
from typing import Any, Callable

from . import verify

Json = dict[str, Any]

#: A hand's name: its entry point is hands/<name>, its package teeny_hands/<name>.
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
#: The most of a failing test run quoted in a missing-message.
_TAIL_CHARS = 400
_NOT_RUN: Json = {"ok": False, "ran": 0, "jailed": False, "output": ""}


def new_hands(changed: list[tuple[str, str]]) -> list[str]:
    """The hands the change adds: `hands/<name>` (one level, an entry point)
    added or untracked. CHANGED is the clone's changes against origin/main
    (selfclone.changed_paths), so an added path is one main does not have."""
    names = set()
    for status, path in changed:
        parts = path.split("/")
        if status in ("A", "??") and len(parts) == 2 and parts[0] == "hands" and _NAME.match(parts[1]):
            names.add(parts[1])
    return sorted(names)


def _front_matter(path: Path) -> Json:
    """A playbook's TOML front matter (between the `+++` lines), or {}."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("+++\n"):
        return {}
    head, sep, _ = text[4:].partition("\n+++")
    if not sep:
        return {}
    try:
        return tomllib.loads(head)
    except tomllib.TOMLDecodeError:
        return {}


def _argv(command: Any) -> list[str]:
    try:
        return shlex.split(command) if isinstance(command, str) else []
    except ValueError:
        return []


def callers(tree: Path, name: str) -> list[str]:
    """The playbooks (brain/playbooks/<book>.md) whose front matter calls the
    hand: an input whose `run` starts with `hands/<name>`, or an `output`
    that is `hand:<name> <verb>`. Prose that mentions the hand does not
    count: a playbook calls what its inputs run and its output does."""
    out = []
    for path in sorted((tree / "brain" / "playbooks").glob("*.md")):
        meta = _front_matter(path)
        inputs = meta.get("inputs")
        runs = [_argv(i.get("run")) for i in inputs if isinstance(i, dict)] if isinstance(inputs, list) else []
        output = meta.get("output")
        if any(argv[:1] == [f"hands/{name}"] for argv in runs) or (
                isinstance(output, str) and output.startswith(f"hand:{name} ")):
            out.append(path.stem)
    return out


def scheduled(tree: Path, books: list[str]) -> list[str]:
    """Of BOOKS, the playbooks brain/cron.toml runs: an enabled job whose
    command is `bin/act <book>`."""
    try:
        jobs = tomllib.loads((tree / "brain" / "cron.toml").read_text(encoding="utf-8")).get("jobs") or {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    runs = set()
    for job in jobs.values() if isinstance(jobs, dict) else []:
        argv = _argv(job.get("command")) if isinstance(job, dict) else []
        if job.get("enabled", True) is not False and len(argv) >= 2 and argv[0] == "bin/act":
            runs.add(argv[1])
    return [book for book in books if book in runs]


def _has_section(text: str, name: str) -> bool:
    """Hand NAME has a `## ` heading naming it in docs/hands.md (the rule of
    evolve's `hand_section`: the heading line holds `name` in backticks)."""
    return any(line.startswith("## ") and f"`{name}`" in line for line in text.splitlines())


def _runs_through_hand_run(package: Path) -> bool:
    for path in sorted(package.rglob("*.py")) if package.is_dir() else []:
        try:
            if "hand.run(" in path.read_text(encoding="utf-8"):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def _parses(path: Path) -> bool:
    try:
        ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    return True


def _check(tree: Path, name: str, run_tests: Callable[..., Json]) -> tuple[Json, list[str]]:
    """One new hand against the rubric: what was found, and what is missing."""
    missing: list[str] = []
    entry = tree / "hands" / name
    is_entry = entry.is_file() and os.access(entry, os.X_OK)
    if not is_entry:
        missing.append(f"hands/{name} is not an executable entry point")
    contract = _runs_through_hand_run(tree / "hands" / "lib" / "teeny_hands" / name)
    if not contract:
        missing.append(f"hands/lib/teeny_hands/{name}/ does not run through hand.run (the hand contract)")
    test_file = tree / "hands" / "tests" / f"test_{name}.py"
    tests = test_file.is_file() and _parses(test_file)
    ran = dict(_NOT_RUN)
    if not tests:
        missing.append(f"no hands/tests/test_{name}.py that parses")
    else:
        result = run_tests(tree, start="hands/tests", top="hands/tests", pattern=f"test_{name}.py")
        ran = {"ok": bool(result.get("ok")), "ran": int(result.get("ran") or 0),
               "jailed": bool(result.get("jailed")), "output": str(result.get("output") or "")}
        if not ran["ok"]:
            missing.append(f"the tests of hands/{name} did not pass when the desk ran them: "
                           f"{ran['output'].strip()[-_TAIL_CHARS:]}")
    books = callers(tree, name)
    runs = scheduled(tree, books)
    if not books:
        missing.append(f"no playbook calls hands/{name} (an input `run = \"hands/{name} ...\"` or "
                       f"`output = \"hand:{name} <verb>\"`)")
    elif not runs:
        missing.append(f"no playbook that calls hands/{name} is scheduled in brain/cron.toml")
    try:
        docs = _has_section((tree / "docs" / "hands.md").read_text(encoding="utf-8"), name)
    except (OSError, UnicodeDecodeError):
        docs = False
    if not docs:
        missing.append(f"docs/hands.md has no `## `{name}`` section")
    return {"name": name, "entry": is_entry, "contract": contract, "tests": tests, "ran": ran,
            "callers": books, "scheduled": runs, "docs": docs}, missing


def rubric(tree: Path, changed: list[tuple[str, str]], run_tests: Callable[..., Json] | None = None) -> Json:
    """The feature in TREE (the clone), given its CHANGED paths against
    origin/main, held to the rubric: `{ok, missing, hands}`. `run_tests` is
    `verify.run` unless a test hands in another."""
    run_tests = run_tests or verify.run
    names = new_hands(changed)
    if not names:
        return {"ok": False, "hands": [],
                "missing": ["no new hand: a feature adds hands/<name>, hands/lib/teeny_hands/<name>/ and "
                            "hands/tests/test_<name>.py"]}
    hands: list[Json] = []
    missing: list[str] = []
    for name in names:
        found, gaps = _check(tree, name, run_tests)
        hands.append(found)
        missing += gaps
    return {"ok": not missing, "missing": missing, "hands": hands}
