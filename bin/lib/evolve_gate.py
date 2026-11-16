#!/usr/bin/env python3
"""The standalone evolution gate (docs/evolve.md, "The gate").

    bin/evolve gate CANDIDATE [--base REF]

Checks a proposed new version of Teeny -- a checkout at CANDIDATE, against
the git ref REF (default main) -- and decides, with plain code and no AI
model, whether it is safe to test. It prints one JSON result and exits 0 if
the candidate passes, 2 if it breaks any rule (listing every broken rule),
or 1 if the gate itself fails to run.

The rules are the registered project's saved task (slop.cash, Teeny's
brain): refuse a candidate when any of these is true:

  1. it touches a frozen path (``evals/frozen.txt`` as of the base, read from
     git, never from the candidate), or anything outside the allowlist --
     ``brain/SOUL.md``, ``brain/CONTEXT.md``, ``brain/narrative/**``,
     ``brain/skills/**``, ``brain/playbooks/**``, ``brain/lessons.md``,
     ``brain/cron.toml``, and new files only under ``hands/``,
     ``hands/lib/teeny_hands/`` and ``hands/tests/test_*.py``;
  2. it gets out of that set some other way: a symlink anywhere in the diff,
     a rename or copy into a frozen path, a deleted frozen file, a file mode
     change, or a binary file;
  3. ``bin/seed-cron --check`` fails on the candidate's ``brain/cron.toml``;
  4. ``bin/doctor --root CANDIDATE`` fails on a check about the candidate's
     own files (brain files, cron, playbooks, agents, the browser lists,
     invariants). The doctor's environment checks -- disk space, directory
     modes, git identity, keys and caps that live outside the diff -- are the
     machine's, not the candidate's, so they never refuse one; a doctor run
     that produces no report at all is a gate failure instead. A candidate
     that touches a frozen path or an existing hand's entry point never has
     the doctor run on it at all, as in the loop's gate;
  5. an invariant anchor stops matching (``bin/eval invariants``);
  6. the diff is too big: over 400 changed lines in total, or over 60 in
     ``brain/SOUL.md`` and ``brain/CONTEXT.md`` combined;
  7. a new hand has no test, or breaks the hand contract in
     ``hands/lib/teeny_hands/hand.py``: one JSON object on stdout, exit
     codes 0, 1 or 2. The new hand's own test file is run too: a failing
     test refuses the candidate, as a failing unit test would;
  8. shellcheck fails on a changed shell file. The hands' full unit suite
     runs in CI, not here: a gate that ran it per candidate could not run
     per candidate.

The gate also meets these conditions:

- it never writes to the candidate checkout and never needs the network or
  a running core: it works with OH_URL unset. (One exception, as in the
  loop's gate: the doctor writes its report to the candidate's gitignored
  ``state/doctor.json``; the candidate's tracked files are untouched.)
- it reads the base from git, never from the candidate's working tree, so a
  version of Teeny can't redefine what it is compared against. The frozen
  list, the pinned cron jobs and every base file come from the base ref;
  the checks themselves run from this checkout's frozen scripts, never the
  candidate's.
- a new hand is run only to prove its contract: no arguments, stdin closed,
  a bare environment, no secrets, a short timeout, and never when the hand
  itself is a symlink.

Python 3.11 standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "bin" / "lib"))

import frozen  # noqa: E402

MAX_DIFF_LINES = 400
MAX_SOUL_CONTEXT_LINES = 60
SOUL_CONTEXT = ("brain/SOUL.md", "brain/CONTEXT.md")

#: What Teeny may change, besides the frozen paths below (rule 1). New files
#: only under hands/, hands/lib/teeny_hands/ and hands/tests/test_*.py.
ALLOW_EXACT = ("brain/SOUL.md", "brain/CONTEXT.md", "brain/lessons.md", "brain/cron.toml")
ALLOW_PREFIXES = ("brain/narrative/", "brain/skills/", "brain/playbooks/")

HAND_ENTRY = re.compile(r"hands/[^/]+$")
HAND_TEST = re.compile(r"hands/tests/test_[^/]+\.py$")
#: A hand a playbook's ``run`` or ``output`` line names: ``hands/<name> ...``
#: or ``hand:<name> ...``. The slash after ``hands`` matters: without it the
#: pattern never matches a ``run`` line at all.
HAND_REF = re.compile(r"(?:hands/([A-Za-z0-9_.-]+)|hand:\s*([A-Za-z0-9_.-]+))")

#: Doctor checks about the candidate's own files. The rest -- disk, directory
#: modes, git identity, keys, caps, storage -- are the machine's.
DOCTOR_CONTENT = ("brain/", "cron.toml", "playbook ", "agent ", "browser-", "invariants")


class GateError(Exception):
    """The gate itself failed to run: exit 1."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"ok": False, "error": f"usage: {message}",
                          "refused": [], "checks": []}))
        raise SystemExit(1)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise GateError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _write_bytes(path: Path, data: bytes, what: str) -> None:
    """Write a scratch file, retrying a transient I/O failure a few times
    before calling it a gate failure: a scratch write that flakes must not
    refuse a candidate that is otherwise fine."""
    last: OSError | None = None
    for attempt in range(5):
        try:
            path.write_bytes(data)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise GateError(f"cannot write {what}: {last}") from None


def _git_bytes(root: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise GateError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def _bare_env(scratch: Path) -> dict[str, str]:
    """No core, no secrets, no network configuration: the gate's children
    run offline, as the candidate's own scripts would on a bare machine."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(scratch), "TMPDIR": str(scratch)}
    return env


def _run(argv: list[str], env: Mapping[str, str], timeout: int,
         cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=dict(env),
                              cwd=str(cwd) if cwd else None)
    except OSError as exc:
        raise GateError(f"cannot run {' '.join(argv)}: {exc}") from None
    except subprocess.TimeoutExpired:
        raise GateError(f"{' '.join(argv)} timed out after {timeout} s") from None


def _allowed(path: str, status: str) -> bool:
    """Rule 1's allowlist. A path Teeny may change; anything else is refused
    unless a more specific rule names it first."""
    if path in ALLOW_EXACT or any(path.startswith(p) for p in ALLOW_PREFIXES):
        return True
    if status in ("A", "?"):
        if HAND_ENTRY.fullmatch(path):
            return True
        if path.startswith("hands/lib/teeny_hands/"):
            return True
        if HAND_TEST.fullmatch(path):
            return True
    return False


def _hand_name(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) == 2 and parts[0] == "hands" and parts[1] not in ("lib", "tests"):
        return parts[1]
    return None


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _diff(root: Path, base: str) -> tuple[list[dict[str, Any]], dict[str, tuple[int | None, int | None]]]:
    """The candidate's diff against BASE: one entry per changed path, plus
    numstat (added, removed) lines by path. Untracked files count too."""
    raw = _git(root, "diff", "--raw", "-z", "--find-renames", base, "--")
    fields = [f for f in raw.split("\0") if f != ""]
    changes: list[dict[str, Any]] = []
    i = 0
    while i < len(fields):
        head = fields[i]
        if not head.startswith(":"):
            raise GateError(f"cannot parse git diff --raw: {head!r}")
        parts = head[1:].split(" ")
        old_mode, new_mode, _old_sha, _new_sha, status = parts[0], parts[1], parts[2], parts[3], parts[4]
        kind = status[0]
        if kind in ("R", "C"):
            old_path, new_path = fields[i + 1], fields[i + 2]
            changes.append({"status": kind, "path": new_path, "old_path": old_path,
                            "old_mode": old_mode, "new_mode": new_mode})
            i += 3
        else:
            changes.append({"status": kind, "path": fields[i + 1], "old_path": None,
                            "old_mode": old_mode, "new_mode": new_mode})
            i += 2
    numstat: dict[str, tuple[int | None, int | None]] = {}
    # ``git diff --numstat -z`` prints a rename or copy as a record with an
    # empty path, followed by the old and new paths as two bare fields:
    # ``0\t0\t\0old\0new\0``. The counts belong to the new path.
    pending: tuple[int | None, int | None] | None = None
    pending_paths: list[str] = []
    for record in _git(root, "diff", "--numstat", "-z", base, "--").split("\0"):
        if not record:
            continue
        parts = record.split("\t")
        if len(parts) == 3:
            added, removed, path = parts
            entry = (None if added == "-" else int(added),
                     None if removed == "-" else int(removed))
            if path:
                numstat[path] = entry
                pending = None
            else:
                pending, pending_paths = entry, []
        elif len(parts) == 1 and pending is not None:
            pending_paths.append(record)
            if len(pending_paths) == 2:
                numstat[pending_paths[1]] = pending
                pending = None
        # Any other shape is not a numstat record git emits; ignoring it
        # keeps an odd line from failing the whole gate.
    for path in _git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0"):
        if not path:
            continue
        full = root / path
        changes.append({"status": "?", "path": path, "old_path": None,
                        "old_mode": "000000", "new_mode": "100644",
                        "untracked": True, "islink": full.is_symlink()})
    return changes, numstat


def _untracked_lines(root: Path, change: dict[str, Any]) -> tuple[int, bool]:
    """(changed lines, is binary) for an untracked file, read from the tree.
    A symlink counts as no lines: its target is not the candidate's content,
    and following the link would count another file's lines as this diff's."""
    full = root / change["path"]
    if full.is_symlink():
        return 0, False
    try:
        data = full.read_bytes()
    except OSError as exc:
        raise GateError(f"cannot read {change['path']}: {exc}") from None
    if _is_binary(data):
        return 0, True
    return data.decode("utf-8", "replace").count("\n") + (1 if data and not data.endswith(b"\n") else 0), False


def _front_matter_hands(text: str) -> list[str]:
    """The hands a playbook's front matter names (``run`` and ``output``),
    ignoring comments. The prompt body may quote hands; only the front
    matter binds the playbook to one."""
    if not text.startswith("+++\n"):
        return []
    end = text.find("\n+++\n", 4)
    head = text[4:end if end != -1 else len(text)]
    names = []
    for line in head.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for match in HAND_REF.finditer(line):
            names.append(match.group(1) or match.group(2))
    return sorted(set(names))


def _exists_at(root: Path, base: str, path: str) -> bool:
    """The path exists in the candidate's worktree or at BASE."""
    if (root / path).exists():
        return True
    proc = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{base}:{path}"],
                          capture_output=True, timeout=60)
    return proc.returncode == 0


def _exists_at_base(root: Path, base: str, path: str) -> bool:
    """The path exists at BASE (git only, never the worktree): a changed
    path always exists in the worktree, so that alone proves nothing."""
    proc = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{base}:{path}"],
                          capture_output=True, timeout=60)
    return proc.returncode == 0


def gate(candidate: str, base: str) -> dict[str, Any]:
    """Run every check. Raises GateError when the gate itself cannot run."""
    root = Path(candidate).resolve()
    if not root.is_dir():
        raise GateError(f"no checkout at {candidate}")
    try:
        _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    except GateError:
        raise GateError(f"no such base ref: {base}") from None
    try:
        patterns = frozen.base_patterns(root, base)
    except frozen.FrozenError as exc:
        raise GateError(str(exc)) from None
    scratch = Path(tempfile.mkdtemp(prefix="teeny-gate."))
    try:
        return _gate(root, base, scratch, patterns)
    finally:
        import shutil
        shutil.rmtree(scratch, ignore_errors=True)


def _gate(root: Path, base: str, scratch: Path, patterns: list[str]) -> dict[str, Any]:
    """Run every check. Raises GateError when the gate itself cannot run."""
    env = _bare_env(scratch)
    env["TEENY_ROOT"] = str(CODE)

    refused: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []

    def note(check: str, ok: bool, detail: str = "", rule: str | None = None) -> None:
        checks.append({"check": check, "ok": ok, "detail": detail})
        if not ok and rule:
            refused.append({"rule": rule, "detail": f"{check}: {detail}" if detail else check})

    changes, numstat = _diff(root, base)
    lines = 0
    soul_lines = 0
    binaries: list[str] = []
    for change in changes:
        path = change["path"]
        if change.get("untracked"):
            count, is_bin = _untracked_lines(root, change)
            lines += count
            if is_bin:
                binaries.append(path)
        else:
            added, removed = numstat.get(path, (0, 0))
            if added is None or removed is None:
                binaries.append(path)
            else:
                lines += added + removed
                if path in SOUL_CONTEXT:
                    soul_lines += added + removed

    # --- rules 1 and 2: one rule per path, the most specific first --------
    frozen_hits = False
    entry_touched = False
    for change in changes:
        path, status = change["path"], change["status"]
        old_path = change.get("old_path")
        if status == "R":
            if old_path and frozen.frozen_pattern(old_path, patterns):
                note("frozen paths", False, f"renamed out of frozen {old_path}",
                     rule="deleted-frozen")
                frozen_hits = True
                continue
            if frozen.frozen_pattern(path, patterns):
                note("frozen paths", False, f"renamed into frozen {path}",
                     rule="rename-into-frozen")
                frozen_hits = True
                continue
        if status == "D" and frozen.frozen_pattern(path, patterns):
            note("frozen paths", False, f"deleted frozen {path}", rule="deleted-frozen")
            frozen_hits = True
            continue
        if change["new_mode"] == "120000" or change.get("islink"):
            note("symlinks", False, f"symlink at {path}", rule="symlink")
            continue
        if frozen.frozen_pattern(path, patterns):
            note("frozen paths", False, f"{status} {path}", rule="frozen-path")
            frozen_hits = True
            continue
        if _hand_name(path) and status not in ("A", "?"):
            note("existing hand entry points", False, f"{status} {path}",
                 rule="hand-entry-point")
            entry_touched = True
            continue
        if status in ("M", "T") and change["old_mode"] != change["new_mode"]:
            note("file modes", False, f"{path}: {change['old_mode']} -> {change['new_mode']}",
                 rule="mode-change")
            continue
        if path in binaries:
            note("binary files", False, f"binary file {path}", rule="binary")
            continue
        if not _allowed(path, status):
            note("allowlist", False, f"{status} {path} is outside what Teeny may change",
                 rule="outside-allowlist")
            continue
    if not any(c["check"] in ("frozen paths", "symlinks", "existing hand entry points",
                              "file modes", "binary files", "allowlist") for c in checks):
        checks.append({"check": "frozen paths", "ok": True, "detail": "none touched"})
        checks.append({"check": "allowlist", "ok": True, "detail": "every change is allowed"})

    # --- rule 6: diff size --------------------------------------------------
    note("diff size", lines <= MAX_DIFF_LINES, f"{lines}/{MAX_DIFF_LINES} changed lines",
         rule=None if lines <= MAX_DIFF_LINES else "diff-size")
    note("SOUL+CONTEXT size", soul_lines <= MAX_SOUL_CONTEXT_LINES,
         f"{soul_lines}/{MAX_SOUL_CONTEXT_LINES} changed lines",
         rule=None if soul_lines <= MAX_SOUL_CONTEXT_LINES else "soul-size")

    # --- rule 7: new hands --------------------------------------------------
    new_hands = sorted({_hand_name(c["path"]) for c in changes
                        if c["status"] in ("A", "?") and _hand_name(c["path"])
                        and not _exists_at_base(root, base, f"hands/{_hand_name(c['path'])}")})
    for name in new_hands:
        test = root / "hands" / "tests" / f"test_{name}.py"
        try:
            compile(test.read_text(encoding="utf-8"), str(test), "exec")
            has_test = True
        except (OSError, SyntaxError):
            has_test = False
        note(f"new hand {name} has a test", has_test,
             f"no hands/tests/test_{name}.py that parses" if not has_test
             else f"hands/tests/test_{name}.py parses",
             rule=None if has_test else "new-hand-without-test")
        hand = root / "hands" / name
        if hand.is_symlink() or not hand.is_file():
            note(f"new hand {name} keeps the contract", False,
                 "not run: the hand is a symlink or not a file")
            continue
        # The hand runs with no arguments, no secrets, no state of its own:
        # TEENY_ROOT points at scratch, so nothing of any checkout is in reach.
        contract_env = _bare_env(scratch)
        contract_env["TEENY_ROOT"] = str(scratch)
        try:
            done = subprocess.run([str(hand)], stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=15, cwd=str(root), env=contract_env)
        except OSError as exc:
            note(f"new hand {name} keeps the contract", False, f"cannot run it: {exc}",
                 rule="new-hand-contract")
            continue
        except subprocess.TimeoutExpired:
            note(f"new hand {name} keeps the contract", False, "timed out with no arguments",
                 rule="new-hand-contract")
            continue
        try:
            parsed = json.loads(done.stdout.strip())
            one_json = isinstance(parsed, dict)
        except (json.JSONDecodeError, ValueError):
            one_json = False
        contract_ok = done.returncode in (0, 1, 2) and one_json
        note(f"new hand {name} keeps the contract", contract_ok,
             f"exit {done.returncode}, one JSON object on stdout" if contract_ok
             else f"exit {done.returncode}, stdout is not one JSON object",
             rule=None if contract_ok else "new-hand-contract")
        if has_test:
            test_env = dict(env)
            test_env["PYTHONPATH"] = str(root / "hands" / "lib")
            test_env["TEENY_ROOT"] = str(root)
            test_env["TEENY_SECRETS"] = str(scratch)
            test_env["TEENY_STATE"] = str(scratch)
            try:
                run = subprocess.run(
                    ["python3", "-m", "unittest", "discover", "-s",
                     str(root / "hands" / "tests"), "-p", f"test_{name}.py",
                     "-t", str(root / "hands" / "tests")],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=180, cwd=str(root), env=test_env)
                tests_ok = run.returncode == 0
                detail = (run.stderr.strip().splitlines() or [""])[-1][:300]
            except OSError as exc:
                tests_ok, detail = False, f"cannot run its tests: {exc}"
            except subprocess.TimeoutExpired:
                tests_ok, detail = False, "its tests timed out"
            note(f"new hand {name} tests pass", tests_ok, detail,
                 rule=None if tests_ok else "new-hand-tests")
    if not new_hands:
        checks.append({"check": "new hands", "ok": True, "detail": "no new hand"})

    # --- rule 5's neighbour: a playbook naming a hand that does not exist ---
    for change in changes:
        path = change["path"]
        if not (path.startswith("brain/playbooks/") and path.endswith(".md")):
            continue
        if change["status"] == "D":
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except OSError as exc:
            raise GateError(f"cannot read {path}: {exc}") from None
        missing = [n for n in _front_matter_hands(text)
                   if not _exists_at(root, base, f"hands/{n}")]
        if missing:
            note("playbooks name existing hands", False,
                 f"{path} names missing hands: {', '.join(missing)}",
                 rule="playbook-hand-missing")

    # --- rule 8: shellcheck on changed shell files --------------------------
    shell_files = []
    for change in changes:
        path = change["path"]
        if change["status"] == "D":
            continue
        full = root / path
        if full.is_symlink() or not full.is_file():
            continue
        try:
            first = full.open("rb").readline()
        except OSError:
            continue
        if path.endswith(".sh") or (first.startswith(b"#!") and b"sh" in first):
            shell_files.append(path)
    if shell_files:
        proc = _run(["shellcheck", "-x", "--", *shell_files], env, 120, cwd=root)
        ok = proc.returncode == 0
        detail = (proc.stdout.strip().splitlines() or proc.stderr.strip().splitlines() or [""])[-1][:300]
        note("shellcheck", ok, detail or "clean",
             rule=None if ok else "shellcheck")
    else:
        checks.append({"check": "shellcheck", "ok": True, "detail": "no shell files changed"})

    # --- rule 3: cron --------------------------------------------------------
    pinned = scratch / "cron.pinned.toml"
    _write_bytes(pinned, _git_bytes(root, "show", f"{base}:evals/cron.pinned.toml"),
                 "the base's pinned cron jobs")
    cron_file = root / "brain" / "cron.toml"
    if not cron_file.exists():
        note("seed-cron --check", False, "no brain/cron.toml in the candidate",
             rule="cron")
    else:
        proc = _run([str(CODE / "bin" / "seed-cron"), "--check", "--file", str(cron_file),
                     "--pinned", str(pinned)], env, 60)
        ok = proc.returncode == 0
        detail = (proc.stderr.strip().splitlines() or proc.stdout.strip().splitlines() or [""])[-1][:300]
        note("seed-cron --check", ok, detail or "valid",
             rule=None if ok else "cron")

    # --- rule 4: doctor, on content checks only ------------------------------
    if frozen_hits or entry_touched:
        note("doctor --root", False,
             "not run: the candidate touches a frozen path or a hand's entry point",
             rule="doctor")
    else:
        proc = _run([str(CODE / "bin" / "doctor"), "--root", str(root)], env, 300)
        report = None
        for line in proc.stdout.splitlines():
            if line.startswith('{"ok"'):
                try:
                    report = json.loads(line)
                except json.JSONDecodeError:
                    pass
        if report is None:
            raise GateError("bin/doctor produced no report; the gate cannot run")
        failed = [c for c in report.get("checks", [])
                  if c.get("status") != "ok"
                  and (c.get("check", "").startswith(DOCTOR_CONTENT))]
        ok = not failed
        detail = ("; ".join(f"{c['check']}: {c.get('detail', '')}" for c in failed)[:600]
                  if failed else "every content check passes")
        note("doctor --root", ok, detail, rule=None if ok else "doctor")

    # --- rule 5: invariants ---------------------------------------------------
    proc = _run([str(CODE / "bin" / "eval"), "invariants", "--root", str(root)], env, 60)
    try:
        inv = json.loads(proc.stdout.strip().splitlines()[-1])
        inv_ok = proc.returncode == 0 and inv.get("ok") is True
        if inv_ok:
            inv_detail = "every anchor holds"
        else:
            # ``missing`` is a list of objects ({id, file, pattern}), not
            # strings: joining it raw crashes the gate on the first failure.
            parts = []
            for m in inv.get("missing", []):
                if isinstance(m, dict):
                    parts.append(f"{m.get('id', '?')} "
                                 f"({m.get('file', '?')}: {m.get('pattern', '')})")
                else:
                    parts.append(str(m))
            inv_detail = (("missing anchors: " + ", ".join(parts))[:300]
                          if parts else "an anchor stopped matching")
    except (json.JSONDecodeError, IndexError, ValueError):
        inv_ok, inv_detail = False, "bin/eval invariants produced no result"
    note("eval invariants", inv_ok, inv_detail,
         rule=None if inv_ok else "invariant-anchor")

    return {"ok": not refused, "candidate": str(root), "base": base,
            "refused": refused, "checks": checks,
            "lines": lines, "soul_context_lines": soul_lines, "files": len(changes)}


def main(argv: list[str]) -> int:
    parser = _Parser(prog="bin/evolve gate",
                     description="Check a proposed new version of Teeny and decide, "
                                 "with plain code and no AI model, whether it is safe to test.")
    parser.add_argument("candidate", help="the checkout to check")
    parser.add_argument("--base", default="main", help="the git ref to compare against (default: main)")
    args = parser.parse_args(argv)
    try:
        result = gate(args.candidate, args.base)
    except GateError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "candidate": args.candidate,
                          "base": args.base, "refused": [], "checks": []}))
        return 1
    except Exception as exc:  # the gate never dies without one JSON result
        print(json.dumps({"ok": False, "error": f"gate failed: {exc}",
                          "candidate": args.candidate, "base": args.base,
                          "refused": [], "checks": []}), file=sys.stdout)
        print(f"evolve-gate: {exc!r}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
