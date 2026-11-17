#!/usr/bin/env python3
"""The offline evolution gate (docs/evolve.md, "The offline evolution gate").

    bin/evolve-gate [--root DIR] BASE [HEAD]

Refuses a candidate brain that breaks any of the eight saved rules in the
Slop registry (https://slop.cash/api/v1/projects/teeny-s-brain, revision 6),
the only approved task for this project. BASE..HEAD is diffed from their
merge base, as a pull request shows it; without HEAD, the working tree
(staged, unstaged and untracked) is checked against BASE. Prints one JSON
object on stdout:

    {"ok": bool, "base": SHA, "root": DIR,
     "refused": [{"rule": ..., "detail": ..., "paths": [...]}],
     "skipped": [{"check": ..., "detail": ...}],
     "sandbox": "bwrap" | "none",
     "lines": {"total": N, "soul_context": M}}

Exit 0 when nothing is refused, 1 naming every refused rule, 2 for a usage
or git error: the check fails closed.

The eight rules (registry `approvedScope`):

1. frozen-path   a change touches a frozen path. The allowlist is the part
                 of the brain Teeny may change; anything outside it is
                 refused, as is anything matching the frozen list
                 (evals/frozen.txt) as of BASE. Inside the allowed set,
                 brain/agents/act.toml, brain/agents/judge.toml, existing
                 files in hands/lib/teeny_hands/**, hands/guard and hands/x
                 stay frozen.
2. escape        getting out of that set some other way: a symlink anywhere
                 in the diff, a rename or copy into a frozen path, a deleted
                 frozen file, a file mode change, a binary file, or a path
                 containing `...`. Also closed here, as the existing hard
                 gate does (bin/lib/evolve.py, hard_gate): a file under
                 hands/lib/ beside teeny_hands/ would stand in for the
                 standard library under frozen code.
3. cron          bin/seed-cron --check fails on the candidate's brain/cron.toml.
4. doctor        bin/doctor --root CANDIDATE fails, run offline (no --live).
5. invariants    an invariant anchor stops matching (bin/eval invariants).
6. diff-size     over 400 changed lines in total, or over 60 in SOUL.md and
                 CONTEXT.md combined.
7. hand-contract a new hand has no test that parses, or breaks the contract
                 in hands/lib/teeny_hands/hand.py: one JSON object on
                 stdout, exit codes 0, 1 or 2.
8. checks        the hands' unit tests or shellcheck fail on the candidate.

How it stays honest:

- The base is read from git, never from the candidate's working tree: the
  frozen list, the pinned cron jobs and the invariant anchors all come from
  `git show BASE:...`. The checkers (bin/seed-cron, bin/doctor, bin/eval)
  are this checkout's, never the candidate's: bin/ is frozen, so a
  candidate that edits it is refused by rule 1 already, and its own scripts
  never run as checkers.
- It never writes to the candidate checkout. The dynamic checks run on a
  throwaway copy in a temporary directory (the working tree, or HEAD's tree
  when HEAD is given); the copy's directory modes are normalised so the
  doctor's keyring check does not read the copying umask.
- It never needs the network or a running core (OH_URL unset, no --live),
  and it adds no new dependencies: Python 3 standard library only, plus
  git, jq and shellcheck on PATH.
- It collects every violated rule instead of stopping at the first: a
  candidate that breaks three rules lists all three.
- The dynamic checks that execute candidate code (doctor, the hand probe,
  the hands' tests) run only when the diff touches no frozen path and holds
  no symlink: a candidate that rewrote frozen code could otherwise make the
  checker run its own code. When they are skipped the candidate is already
  refused, and the skip is reported, not hidden.
- A new hand's probe and tests run in bubblewrap or not at all: without a
  sandbox they are skipped and reported, never run unsandboxed. (The gate's
  sandbox omits --proc: this container cannot mount it, and nothing the gate
  runs inside needs it. `bin/lib/sandbox.py`'s probe still asks for --proc,
  so it reports no sandbox here.)

Python 3.11 standard library only. No sibling imports besides frozen.py,
which itself imports nothing of the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen  # noqa: E402  (bin/lib/frozen.py: the frozen-path check)

MAX_DIFF_LINES = 400
MAX_SOUL_CONTEXT_LINES = 60
SOUL_CONTEXT = ("brain/SOUL.md", "brain/CONTEXT.md")
MAX_CHECK_DETAIL = 600
HAND_TEST_SECS = 300
HAND_PROBE_SECS = 30
SHELLCHECK_SECS = 120
DOCTOR_SECS = 600
SEED_CRON_SECS = 60
INVARIANTS_SECS = 60

RULE_FROZEN = "frozen-path"
RULE_ESCAPE = "escape"
RULE_CRON = "cron"
RULE_DOCTOR = "doctor"
RULE_INVARIANTS = "invariants"
RULE_SIZE = "diff-size"
RULE_HAND = "hand-contract"
RULE_CHECKS = "checks"

#: The part of the brain Teeny may change (registry approvedScope), as exact
#: paths and directory prefixes. hands/ is new files only: an existing file
#: there is refused (see _allowed).
ALLOW_EXACT = frozenset({
    "brain/SOUL.md", "brain/CONTEXT.md", "brain/lessons.md", "brain/cron.toml",
})
ALLOW_PREFIX = ("brain/narrative/", "brain/skills/", "brain/playbooks/")
#: Inside the allowed set, these stay frozen (registry approvedScope).
FROZEN_INSIDE = ("brain/agents/act.toml", "brain/agents/judge.toml",
                  "hands/guard", "hands/x")


class GateError(Exception):
    """The check could not run: fail closed (exit 2)."""


@dataclass
class Change:
    """One path the candidate changes, from BASE to HEAD (or the working tree)."""
    status: str  # git's letter: A, M, D, T; "?" for untracked
    path: str
    old_path: str | None = None  # for a rename: the name it had
    old_mode: str | None = None
    new_mode: str | None = None
    added: int | None = None  # lines added (None: binary or unknown)
    removed: int | None = None
    blob: str | None = None  # the new blob id, for copy detection


@dataclass
class Refusal:
    rule: str
    detail: str
    paths: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        out = {"rule": self.rule, "detail": self.detail}
        if self.paths:
            out["paths"] = sorted(set(self.paths))
        return out


def _run(argv: list[str], *, env: dict | None = None, timeout: float,
         cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env=env, cwd=str(cwd) if cwd else None)
    except subprocess.TimeoutExpired:
        raise GateError(f"{' '.join(argv[:2])}: timed out after {timeout:g} s")
    except OSError as exc:
        raise GateError(f"cannot run {' '.join(argv[:2])}: {exc}")


def _git(root: str | Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GateError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _git_bytes(root: str | Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if proc.returncode != 0:
        raise GateError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def resolve_base(root: Path, base: str) -> str:
    """BASE as a commit id, or an error."""
    _git(root, "rev-parse", "--git-dir")
    out = _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").strip()
    if not out:
        raise GateError(f"no commit at {base}")
    return out


def base_files(root: Path, base: str) -> set[str]:
    """Every path git tracks at BASE."""
    out = _git(root, "ls-tree", "-r", "--name-only", "-z", base)
    return {p for p in out.split("\0") if p}


def base_blobs(root: Path, base: str) -> dict[str, str]:
    """Blob id -> one path holding it at BASE (for copy detection)."""
    blobs: dict[str, str] = {}
    out = _git(root, "ls-tree", "-r", "-z", base)
    for record in out.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            blobs.setdefault(parts[2], path)
    return blobs


def base_show(root: Path, base: str, path: str) -> str:
    """A file as of BASE (git objects only, never the working tree)."""
    try:
        return _git(root, "show", f"{base}:{path}")
    except GateError as exc:
        raise GateError(f"no {path} at {base} ({exc})") from None


def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def _count_lines(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _hand_name(path: str) -> str | None:
    """The hand PATH belongs to (bin/lib/evolve.py, _hand_name), or None."""
    parts = path.split("/")
    if len(parts) == 2 and parts[0] == "hands" and parts[1] not in ("lib", "tests"):
        return parts[1]
    if len(parts) >= 4 and parts[:3] == ["hands", "lib", "teeny_hands"]:
        return parts[3].removesuffix(".py") if len(parts) == 4 else parts[3]
    return None


def _test_hand(path: str) -> str | None:
    """The hand whose tests PATH is, or None."""
    parts = path.split("/")
    if parts[:2] != ["hands", "tests"] or len(parts) < 3:
        return None
    if len(parts) == 3:
        match = re.fullmatch(r"test_(\w+)\.py", parts[2])
        return match.group(1) if match else None
    return parts[2]


def _hand_test_args(copy: Path, name: str) -> list[str] | None:
    """`python -m unittest` arguments for hand NAME's tests in COPY, or None
    when it has none (bin/lib/evolve.py, _hand_tests)."""
    tests = copy / "hands" / "tests"
    if (tests / f"test_{name}.py").is_file():
        return ["discover", "-s", "hands/tests", "-t", "hands/tests", "-p", f"test_{name}.py"]
    if (tests / name / "__init__.py").is_file():
        return ["discover", "-s", f"hands/tests/{name}", "-t", "hands/tests"]
    return None


def _parse_name_status(out: str) -> tuple[dict[str, str], dict[str, str]]:
    """(path -> status letter, new-path -> old-path for renames),
    from NUL-separated `git diff --name-status -z`."""
    parts = out.split("\0")
    it = iter(parts)
    statuses: dict[str, str] = {}
    renames: dict[str, str] = {}
    for status in it:
        if not status:
            continue
        if status[0] == "R":
            old = next(it, "")
            new = next(it, "")
            statuses[new] = status
            renames[new] = old
        else:
            statuses[next(it, "")] = status[0]
    return statuses, renames



def _parse_raw(out: str) -> dict[str, tuple[str | None, str | None, str | None]]:
    """path -> (old_mode, new_mode, new_blob) from `git diff --raw -z`.
    With -z the metadata and the path are separate NUL-separated fields
    (":old new oldsha newsha STATUS", path, ...), not tab-separated."""
    result: dict[str, tuple[str | None, str | None, str | None]] = {}
    fields = out.split("\0")
    it = iter(fields)
    for meta in it:
        if not meta.startswith(":"):
            continue
        path = next(it, "")
        if not path:
            continue
        parts = meta.split()
        if len(parts) < 5:
            continue
        result[path] = (parts[0][1:], parts[1], parts[3])
    return result


def _changes_worktree(root: Path, base: str) -> list[Change]:
    """Staged, unstaged and untracked changes vs BASE."""
    changes: dict[str, Change] = {}
    statuses, _ = _parse_name_status(_git(root, "diff", "--name-status", "-z", "--no-renames", base, "--"))
    raw = _parse_raw(_git(root, "diff", "--raw", "--abbrev=40", "-z", "--no-renames", base, "--"))
    for path, status in statuses.items():
        old_mode, new_mode, blob = raw.get(path, (None, None, None))
        changes[path] = Change(status=status, path=path, old_mode=old_mode,
                               new_mode=new_mode, blob=blob)
    # Line counts (binary files get None for both).
    numstat = _git(root, "diff", "--numstat", "-z", "--no-renames", base, "--")
    for record in numstat.split("\0"):
        if not record:
            continue
        parts = record.split("\t")
        if len(parts) != 3 or parts[2] not in changes:
            continue
        added, removed, path = parts
        ch = changes[path]
        ch.added = None if added == "-" else int(added)
        ch.removed = None if removed == "-" else int(removed)
    # Renames, so a rename into a frozen path can be named as one.
    _, renames = _parse_name_status(_git(root, "diff", "--name-status", "-z", "-M", base, "--"))
    for new, old in renames.items():
        if new in changes:
            changes[new].old_path = old
    # Untracked files: new by definition, their content is on disk.
    for path in (p for p in _git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if p):
        full = root / path
        try:
            st = full.lstat()
        except OSError:
            continue
        if (st.st_mode & 0o170000) == 0o120000:
            changes[path] = Change(status="?", path=path, new_mode="120000", blob=None)
            continue
        data = full.read_bytes() if full.is_file() else b""
        mode = "100755" if (st.st_mode & 0o111) else "100644"
        changes[path] = Change(
            status="?", path=path, new_mode=mode,
            added=None if _is_binary(data) else _count_lines(data), removed=0,
            blob=_git(root, "hash-object", path).strip(),
        )
    return sorted(changes.values(), key=lambda c: c.path)


def _changes_commits(root: Path, base: str, head: str) -> list[Change]:
    """BASE..HEAD (merge base) as a pull request shows it."""
    rng = f"{base}...{head}"
    changes: dict[str, Change] = {}
    statuses, _ = _parse_name_status(_git(root, "diff", "--name-status", "-z", "--no-renames", rng, "--"))
    raw = _parse_raw(_git(root, "diff", "--raw", "--abbrev=40", "-z", "--no-renames", rng, "--"))
    for path, status in statuses.items():
        old_mode, new_mode, blob = raw.get(path, (None, None, None))
        changes[path] = Change(status=status, path=path, old_mode=old_mode,
                               new_mode=new_mode, blob=blob)
    _, renames = _parse_name_status(_git(root, "diff", "--name-status", "-z", "-M", rng, "--"))
    for new, old in renames.items():
        if new in changes:
            changes[new].old_path = old
    numstat = _git(root, "diff", "--numstat", "-z", "--no-renames", rng, "--")
    for record in numstat.split("\0"):
        if not record:
            continue
        parts = record.split("\t")
        if len(parts) != 3 or parts[2] not in changes:
            continue
        added, removed, path = parts
        ch = changes[path]
        ch.added = None if added == "-" else int(added)
        ch.removed = None if removed == "-" else int(removed)
    return sorted(changes.values(), key=lambda c: c.path)


def _frozen_patterns(root: Path, base: str) -> list[str]:
    """evals/frozen.txt as of BASE (git objects only)."""
    text = base_show(root, base, "evals/frozen.txt")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _matches_frozen(path: str, patterns: list[str]) -> str | None:
    """The frozen pattern PATH matches, or None (bin/lib/frozen.py)."""
    return frozen.frozen_pattern(path, patterns)


def _is_allowed(path: str, base_tree: set[str], patterns: list[str]) -> str | None:
    """None when PATH is inside the registry allowlist, else the reason."""
    if path in FROZEN_INSIDE or any(path.startswith(p + "/") for p in FROZEN_INSIDE):
        return "frozen inside the allowed set"
    if path in ALLOW_EXACT or any(path.startswith(p) for p in ALLOW_PREFIX):
        return None
    if path == "hands" or path.startswith("hands/"):
        # New hands only: anything that already exists at BASE is refused.
        if path in base_tree:
            return "existing file under hands/ (new files only)"
        return None
    return "outside the allowed set"


def rule_frozen(changes: list[Change], base_tree: set[str], patterns: list[str]) -> Refusal | None:
    """Rule 1: a change touches a frozen path."""
    bad: list[tuple[str, str]] = []
    for change in changes:
        pattern = _matches_frozen(change.path, patterns)
        if pattern:
            bad.append((change.path, f"matches the frozen list ({pattern})"))
            continue
        reason = _is_allowed(change.path, base_tree, patterns)
        if reason:
            bad.append((change.path, reason))
    if not bad:
        return None
    detail = "; ".join(f"{path}: {reason}" for path, reason in bad)
    return Refusal(RULE_FROZEN, detail[:MAX_CHECK_DETAIL], [p for p, _ in bad])


def rule_escape(changes: list[Change], base_tree: set[str], base_blobs_map: dict[str, str],
                patterns: list[str]) -> Refusal | None:
    """Rule 2: getting out of the allowed set some other way."""
    bad: list[tuple[str, str]] = []
    for change in changes:
        reasons: list[str] = []
        if "..." in change.path:
            reasons.append("path contains '...'")
        if change.new_mode == "120000" or change.old_mode == "120000":
            reasons.append("symlink")
        if change.status in ("M", "T") and change.old_mode and change.new_mode \
                and change.old_mode != change.new_mode \
                and "120000" not in (change.old_mode, change.new_mode):
            reasons.append(f"mode change {change.old_mode} -> {change.new_mode}")
        if change.status == "D" and (
                _matches_frozen(change.path, patterns)
                or _is_allowed(change.path, base_tree, patterns)):
            reasons.append("deleted frozen file")
        if change.old_path is not None and (
                _matches_frozen(change.path, patterns)
                or _is_allowed(change.path, base_tree, patterns)):
            reasons.append(f"rename {change.old_path} -> {change.path} into a frozen path")
        if change.status in ("A", "?") and change.blob and change.blob in base_blobs_map and (
                _matches_frozen(change.path, patterns)
                or _is_allowed(change.path, base_tree, patterns)):
            reasons.append(f"copy of {base_blobs_map[change.blob]} into a frozen path")
        if change.path.startswith("hands/lib/") and not change.path.startswith("hands/lib/teeny_hands/"):
            reasons.append("file under hands/lib/ beside teeny_hands/ (shadows the standard library)")
        if change.status != "D" and change.added is None:
            reasons.append("binary file")
        if reasons:
            bad.append((change.path, "; ".join(reasons)))
    if not bad:
        return None
    detail = "; ".join(f"{path}: {reason}" for path, reason in bad)
    return Refusal(RULE_ESCAPE, detail[:MAX_CHECK_DETAIL], [p for p, _ in bad])


def rule_size(changes: list[Change]) -> tuple[Refusal | None, int, int]:
    """Rule 6: over 400 changed lines, or over 60 in SOUL.md + CONTEXT.md."""
    total = sum((c.added or 0) + (c.removed or 0) for c in changes)
    soul = sum((c.added or 0) + (c.removed or 0) for c in changes if c.path in SOUL_CONTEXT)
    if total > MAX_DIFF_LINES:
        return (Refusal(RULE_SIZE, f"diff is {total} lines (over {MAX_DIFF_LINES})"),
                total, soul)
    if soul > MAX_SOUL_CONTEXT_LINES:
        return (Refusal(RULE_SIZE,
                        f"SOUL.md and CONTEXT.md changed by {soul} lines "
                        f"(over {MAX_SOUL_CONTEXT_LINES})"), total, soul)
    return None, total, soul


def make_copy(root: Path, head: str | None, scratch: Path) -> Path:
    """A throwaway copy of the candidate (never the candidate checkout).
    HEAD's tree when HEAD is given, else the working tree (tracked and
    untracked, never .git). Directory modes are normalised: git does not
    track them, and the doctor's keyring check reads them."""
    copy = scratch / "candidate"
    if head:
        archive = _git_bytes(root, "archive", head)
        copy.mkdir(parents=True)
        with tarfile.open(fileobj=BytesIO(archive)) as tf:
            try:
                tf.extractall(copy, filter="data")
            except TypeError:  # Python < 3.11.4 has no filter=; the archive is our own repo's
                tf.extractall(copy)
    else:
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git"), symlinks=True)
    for dirpath, _dirnames, _filenames in os.walk(copy):
        os.chmod(dirpath, 0o755)
    return copy


@dataclass
class BaseRules:
    frozen: list[str]
    pinned: Path  # evals/cron.pinned.toml as of BASE
    evals: Path  # an evals/ holding invariants.md as of BASE


def base_rules(root: Path, base: str, scratch: Path) -> BaseRules:
    """The base's rule data, read from git (never the candidate's tree)."""
    frozen = _frozen_patterns(root, base)
    folder = scratch / "base-rules"
    folder.mkdir(parents=True)
    pinned = folder / "cron.pinned.toml"
    pinned.write_text(base_show(root, base, "evals/cron.pinned.toml"))
    evals = folder / "evals"
    evals.mkdir()
    (evals / "invariants.md").write_text(base_show(root, base, "evals/invariants.md"))
    return BaseRules(frozen, pinned, evals)


SANDBOX_ROOT = "/work"
SANDBOX_SYSTEM = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc")


def _clean_env() -> dict[str, str]:
    """The environment the gate's own checkers get: nothing of this
    process's, no core token, no TEENY_ROOT (the checkers derive theirs
    from their own script's location, which is this checkout, never the
    candidate's copy)."""
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1"}


def _sandbox_env() -> dict[str, str]:
    """The environment inside bwrap (bin/lib/sandbox.py, sandbox_env)."""
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8",
            "TMPDIR": "/tmp", "TEENY_ROOT": SANDBOX_ROOT, "TEENY_STATE": f"{SANDBOX_ROOT}/state",
            "TEENY_SECRETS": f"{SANDBOX_ROOT}/secrets", "TEENY_HANDS": f"{SANDBOX_ROOT}/hands",
            "OPENHUMAN_WORKSPACE": f"{SANDBOX_ROOT}/brain", "DRY_RUN": "1",
            "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null"}


def _bwrap_argv(copy: Path, command: list[str]) -> list[str]:
    """bwrap's command line (bin/lib/evolve.py, sandbox_argv), minus --proc:
    this container cannot mount /proc, and nothing the gate runs inside
    needs it. COPY at SANDBOX_ROOT read-write, the system read-only, its own
    /dev and /tmp, every namespace unshared, a clean environment."""
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    for key, value in sorted(_sandbox_env().items()):
        argv += ["--setenv", key, value]
    for path in SANDBOX_SYSTEM:
        argv += ["--ro-bind-try", path, path]
    python = Path(sys.executable).resolve()
    if not str(python).startswith(("/usr/", "/bin/")):
        base = str(Path(sys.base_prefix).resolve())
        argv += ["--ro-bind", base, base]
    argv += ["--dev", "/dev", "--tmpfs", "/tmp", "--bind", str(copy), SANDBOX_ROOT,
             "--remount-ro", "/", "--chdir", SANDBOX_ROOT, "--", *command]
    return argv


def _bwrap_ready(copy: Path) -> bool:
    """Whether bwrap is here and can make its namespaces, with the argv the
    gate actually runs (the loop's probe in bin/lib/sandbox.py asks for
    --proc too, which this container cannot mount, so it says no here)."""
    if not shutil.which("bwrap"):
        return False
    try:
        proc = subprocess.run(
            _bwrap_argv(copy, [str(Path(sys.executable).resolve()), "-c", "pass"]),
            capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_sandboxed(copy: Path, command: list[str], timeout: float,
                    sandbox: str) -> tuple[int | None, str, str, str | None]:
    """COMMAND on COPY, in bwrap when SANDBOX says so, else not at all:
    candidate code is never run unsandboxed. (returncode or None, stdout,
    stderr, None or "timeout" or "no-sandbox")."""
    if sandbox != "bwrap":
        return None, "", "", "no-sandbox"
    try:
        proc = subprocess.run(_bwrap_argv(copy, command), capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
        return proc.returncode, proc.stdout, proc.stderr, None
    except subprocess.TimeoutExpired:
        return None, "", "", "timeout"
    except OSError as exc:
        raise GateError(f"cannot run {' '.join(command[:2])} in bwrap: {exc}") from None


def check_cron(gate_root: Path, copy: Path, rules: BaseRules) -> Refusal | None:
    """Rule 3: bin/seed-cron --check fails on the candidate's brain/cron.toml
    (the pinned jobs come from BASE, never the candidate)."""
    proc = _run([str(gate_root / "bin" / "seed-cron"), "--check",
                 "--file", str(copy / "brain" / "cron.toml"),
                 "--pinned", str(rules.pinned)],
                env=_clean_env(), timeout=SEED_CRON_SECS)
    if proc.returncode == 0:
        return None
    detail = (proc.stderr.strip() or proc.stdout.strip()).splitlines()
    return Refusal(RULE_CRON, ("bin/seed-cron --check failed: " + " ".join(detail))[:MAX_CHECK_DETAIL])


def check_invariants(gate_root: Path, copy: Path, rules: BaseRules) -> Refusal | None:
    """Rule 5: an invariant anchor stops matching (bin/eval invariants,
    anchors from BASE, brain from the candidate's copy)."""
    proc = _run([sys.executable, str(gate_root / "bin" / "lib" / "evals.py"), "invariants",
                 str(rules.evals), str(copy / "brain")],
                env=_clean_env(), timeout=INVARIANTS_SECS)
    if proc.returncode == 0:
        return None
    try:
        missing = json.loads(proc.stdout).get("missing") or []
    except (json.JSONDecodeError, AttributeError):
        missing = []
    detail = "bin/eval invariants failed"
    if missing:
        detail += ": missing " + ", ".join(str(m) for m in missing)
    return Refusal(RULE_INVARIANTS, detail[:MAX_CHECK_DETAIL])


def _shell_files(copy: Path, changes: list[Change]) -> list[str]:
    """Changed shell scripts in COPY: a .sh name, or a shell shebang."""
    out: list[str] = []
    for change in changes:
        if change.status == "D":
            continue
        path = copy / change.path
        if not path.is_file() or path.is_symlink():
            continue
        if change.path.endswith(".sh"):
            out.append(change.path)
            continue
        try:
            first = path.read_bytes()[:1024].split(b"\n", 1)[0].decode("utf-8", "replace")
        except OSError:
            continue
        match = re.match(r"^#!\s*(\S+)(.*)$", first)
        if not match:
            continue
        interp, args = match.group(1), match.group(2)
        if interp.endswith(("sh", "bash")) or (
                interp.endswith("env") and re.search(r"\b(sh|bash)\b", args)):
            out.append(change.path)
    return out


def check_shellcheck(copy: Path, changes: list[Change]) -> Refusal | None:
    """Rule 8, the shellcheck half: shellcheck fails on a changed script.
    A missing shellcheck fails closed, but only when there is a script to
    check at all."""
    files = _shell_files(copy, changes)
    if not files:
        return None
    binary = shutil.which("shellcheck")
    if not binary:
        return Refusal(RULE_CHECKS, "shellcheck is not available here, and the candidate "
                                    "changes shell scripts: " + ", ".join(files))
    proc = _run([binary, "-x", *files], env=_clean_env(), timeout=SHELLCHECK_SECS, cwd=copy)
    if proc.returncode == 0:
        return None
    detail = "shellcheck failed on " + ", ".join(files) + ": " + " ".join(
        (proc.stdout.strip() or proc.stderr.strip()).splitlines()[:3])
    return Refusal(RULE_CHECKS, detail[:MAX_CHECK_DETAIL])


def check_doctor(gate_root: Path, copy: Path) -> Refusal | None:
    """Rule 4: bin/doctor --root CANDIDATE fails, offline (no --live, so no
    core is called). The doctor is this checkout's; the candidate's copy is
    only its root."""
    env = _clean_env()
    env["HOME"] = str(copy / "state" / "home")
    proc = _run([str(gate_root / "bin" / "doctor"), "--root", str(copy)],
                env=env, timeout=DOCTOR_SECS)
    if proc.returncode == 0:
        return None
    detail = f"bin/doctor --root CANDIDATE failed (exit {proc.returncode})"
    try:
        failed = [c.get("check") for c in json.loads(proc.stdout).get("checks", [])
                  if c.get("status") == "fail"]
        if failed:
            detail += ": " + ", ".join(str(f) for f in failed)
    except (json.JSONDecodeError, AttributeError):
        pass
    return Refusal(RULE_DOCTOR, detail[:MAX_CHECK_DETAIL])


def _hand_exists_at_base(base_tree: set[str], name: str) -> bool:
    if f"hands/{name}" in base_tree or f"hands/lib/teeny_hands/{name}" in base_tree \
            or f"hands/lib/teeny_hands/{name}.py" in base_tree:
        return True
    return any(p.startswith(f"hands/lib/teeny_hands/{name}/") for p in base_tree)


def _names_in_diff(changes: list[Change]) -> tuple[list[str], list[str]]:
    """(hands the diff names, hands whose tests the diff names)."""
    hands: list[str] = []
    tested: list[str] = []
    for change in changes:
        name = _hand_name(change.path)
        if name and name not in hands:
            hands.append(name)
        name = _test_hand(change.path)
        if name and name not in tested:
            tested.append(name)
    return hands, tested


def _test_file(copy: Path, name: str) -> Path | None:
    """Hand NAME's test file in COPY, or None when it has none."""
    tests = copy / "hands" / "tests"
    for candidate in (tests / f"test_{name}.py", tests / name / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def check_hands(copy: Path, changes: list[Change], base_tree: set[str],
                sandbox: str) -> tuple[list[Refusal], list[dict]]:
    """Rules 7 and 8, the hand half: a new hand has no test that parses, or
    breaks the hand contract (one JSON object on stdout, exit 0/1/2); the
    hands' unit tests fail. The probe and the tests run candidate code, so
    they run in bwrap or not at all: without a sandbox they are skipped and
    reported, never run unsandboxed."""
    refused: list[Refusal] = []
    skipped: list[dict] = []
    hands, tested = _names_in_diff(changes)
    new = [name for name in hands if not _hand_exists_at_base(base_tree, name)]
    if not new and not tested:
        return refused, skipped

    # Rule 7, the static half: every new hand has a test that parses.
    for name in new:
        test = _test_file(copy, name)
        if test is None:
            refused.append(Refusal(RULE_HAND, f"new hand {name} has no test"))
            continue
        try:
            py_compile.compile(str(test), doraise=True)
        except py_compile.PyCompileError as exc:
            refused.append(Refusal(RULE_HAND, f"new hand {name}'s test does not parse: {exc}"))

    # Rules 7 (probe) and 8 (tests): candidate code, bwrap or nothing.
    if sandbox != "bwrap":
        skipped.append({"check": "hand-contract probe",
                        "detail": "no sandbox here (bwrap); a candidate that changes hand code "
                                  "is sent back, never shipped from here"})
        skipped.append({"check": "hands tests",
                        "detail": "no sandbox here (bwrap); a candidate that changes hand code "
                                  "is sent back, never shipped from here"})
        return refused, skipped

    for name in new:
        entry = copy / "hands" / name
        if not entry.is_file() or entry.is_symlink():
            continue  # a library-only hand: its unit tests cover it
        # The contract is about stdout: one JSON object there. Stderr may
        # carry argparse's usage (no verb given), which is not a violation.
        code, stdout, _stderr, why = _run_sandboxed(copy, [f"{SANDBOX_ROOT}/hands/{name}"],
                                                    HAND_PROBE_SECS, sandbox)
        if why == "timeout":
            refused.append(Refusal(RULE_HAND, f"new hand {name} did not answer in "
                                              f"{HAND_PROBE_SECS:g} s"))
            continue
        if code not in (0, 1, 2):
            refused.append(Refusal(RULE_HAND,
                                   f"new hand {name} exited {code} (the contract allows 0, 1, 2)"))
            continue
        try:
            parsed = json.loads(stdout.strip())
            if not isinstance(parsed, dict):
                raise ValueError("not an object")
        except (json.JSONDecodeError, ValueError):
            refused.append(Refusal(RULE_HAND,
                                   f"new hand {name} did not print exactly one JSON object"))
            continue

    for name in sorted(set(new) | set(tested)):
        args = _hand_test_args(copy, name)
        if args is None:
            continue
        code, stdout, stderr, why = _run_sandboxed(
            copy, [str(Path(sys.executable).resolve()), "-m", "unittest", *args],
            HAND_TEST_SECS, sandbox)
        if why == "timeout":
            refused.append(Refusal(RULE_CHECKS,
                                   f"hand {name}'s tests did not finish in {HAND_TEST_SECS:g} s"))
            continue
        if code != 0:
            lines = [line for line in (stdout + stderr).splitlines()
                     if line.startswith(("FAIL:", "ERROR:"))][:3]
            detail = f"hand {name}'s tests failed"
            if lines:
                detail += ": " + "; ".join(lines)
            refused.append(Refusal(RULE_CHECKS, detail[:MAX_CHECK_DETAIL]))
    return refused, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bin/evolve-gate",
        description="Refuse a candidate brain that breaks any of the eight saved "
                    "evolution rules (docs/evolve.md, \"The offline evolution gate\").")
    parser.add_argument("--root", default=".", help="the git checkout holding the candidate")
    parser.add_argument("base", help="the base ref, read from git")
    parser.add_argument("head", nargs="?", help="the candidate ref (default: the working tree)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    gate_root = Path(__file__).resolve().parents[2]

    def fail(message: str) -> int:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 2

    try:
        base = resolve_base(root, args.base)
        head = None
        if args.head:
            head = _git(root, "rev-parse", "--verify", "--quiet",
                        f"{args.head}^{{commit}}").strip()
            if not head:
                raise GateError(f"no commit at {args.head}")
        base_tree = base_files(root, base)
        blobs = base_blobs(root, base)
        changes = _changes_commits(root, base, head) if head else _changes_worktree(root, base)
        with tempfile.TemporaryDirectory(prefix="evolve-gate-") as scratch:
            scratch_path = Path(scratch)
            rules = base_rules(root, base, scratch_path)
            # Phase A: static (no candidate code runs, ever).
            refused: list[Refusal] = []
            frozen_refusal = rule_frozen(changes, base_tree, rules.frozen)
            if frozen_refusal:
                refused.append(frozen_refusal)
            escape_refusal = rule_escape(changes, base_tree, blobs, rules.frozen)
            if escape_refusal:
                refused.append(escape_refusal)
            size_refusal, total, soul = rule_size(changes)
            if size_refusal:
                refused.append(size_refusal)
            copy = make_copy(root, head, scratch_path)
            sandbox = "bwrap" if _bwrap_ready(copy) else "none"
            # Phase B: pure dynamic (only the gate's own code runs).
            for refusal in (check_cron(gate_root, copy, rules),
                            check_invariants(gate_root, copy, rules),
                            check_shellcheck(copy, changes)):
                if refusal:
                    refused.append(refusal)
            # Phase C: executing dynamic (candidate code runs). Only when the
            # diff touches no frozen path and holds no symlink: a candidate
            # that rewrote frozen code could make the checker run its own.
            skipped: list[dict] = []
            has_symlink = any(c.new_mode == "120000" or c.old_mode == "120000" for c in changes)
            if frozen_refusal is None and not has_symlink:
                doctor_refusal = check_doctor(gate_root, copy)
                if doctor_refusal:
                    refused.append(doctor_refusal)
                hand_refused, hand_skipped = check_hands(copy, changes, base_tree, sandbox)
                refused.extend(hand_refused)
                skipped.extend(hand_skipped)
            else:
                why = ("the candidate touches a frozen path" if frozen_refusal is not None
                       else "the diff holds a symlink")
                skipped.append({"check": "doctor", "detail": f"not run: {why}"})
                hands, tested = _names_in_diff(changes)
                if hands or tested:
                    skipped.append({"check": "hand-contract probe", "detail": f"not run: {why}"})
                    skipped.append({"check": "hands tests", "detail": f"not run: {why}"})
            result = {"ok": not refused, "base": base, "root": str(root),
                      "refused": [r.as_json() for r in refused], "skipped": skipped,
                      "sandbox": sandbox, "lines": {"total": total, "soul_context": soul}}
            print(json.dumps(result, ensure_ascii=False))
            return 0 if not refused else 1
    except GateError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
