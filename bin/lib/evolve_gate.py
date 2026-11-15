"""`bin/evolve gate CANDIDATE [--base REF] [--git-dir DIR]`: is a proposed
version of Teeny safe to test? Plain code, no model (docs/evolve.md, "The
standalone gate").

CANDIDATE is a checkout or worktree. Its working tree as it is now (untracked
and ignored files included, `.git` left out) is compared with REF (default
`main`), a commit read from git: by default the candidate's own repository,
or --git-dir DIR. Nothing the candidate's files say moves what it is compared
against: the allowlist is the constants below, and the frozen list, the
pinned jobs, the invariants and every operator script a check runs are the
base commit's.

The gate never writes to the candidate and never runs git in it. Everything
happens in a scratch directory: the base is exported there with `git archive`
and committed in a repository of its own, the candidate is copied over it and
staged, and the diff (`--raw -z` with renames and copies, `--numstat -z`) is
read there. The checks that run code run on a second copy, in a scrubbed
environment: no core, no token, no OH_URL and no TEENY_*. Network access is
NOT blocked (the standard library has no sandbox); the base's checks make no
calls, and a new hand or test that reaches out is not stopped here.

The rules, each a stable id in `refused[].rule`:

  frozen-path        a changed path outside the allowlist (ALLOWED_FILES,
                     ALLOWED_TREES, new hands), one of FROZEN_INSIDE, an
                     existing hand's file, a path the base's evals/frozen.txt
                     freezes, or a moved submodule pin
  path-escape        a symlink, a submodule or nested `.git`, a rename or copy
                     out of the allowlist, a deletion outside it, a mode change,
                     an executable that is not a new hand's entry point, a
                     binary file, a special file, a git metadata file, a path
                     with an empty, `.` or `..` segment, a leading `/`, a
                     backslash or a control character, or one that folds onto
                     a different base path on a case-insensitive disk
  seed-cron          the base's `bin/seed-cron --check` refuses the candidate's
                     brain/cron.toml (with the base's pinned jobs)
  doctor             the base's `bin/doctor --root` (offline) fails on the copy
  invariants         the base's `bin/eval invariants --root` finds an anchor gone
  diff-size          more than MAX_DIFF_LINES changed lines (renames count their
                     edits, copies their whole text)
  soul-context-size  more than MAX_SOUL_CONTEXT_LINES in SOUL.md and CONTEXT.md
  hand-test          a new hand without a hands/tests/test_<name>.py that compiles
                     and defines a test
  hand-contract      a new hands/<name>, run with no verb and `{}` on stdin, does
                     not print exactly one JSON object or exits other than 0, 1, 2
  hand-unit-tests    a new hand's own tests (hands/tests/test_<name>.py) fail,
                     run with `python3 -m unittest` from hands/tests in the
                     second copy; not the whole suite. Every existing file under
                     hands/ is frozen (frozen-path), a new teeny_hands/<name>
                     cannot shadow an existing module (frozen-path, the base has
                     the name), and no candidate code runs once either refuses,
                     so no existing test's result can depend on the diff's
                     hands/ part. (Existing tests that read brain/ files Teeny
                     may edit are not this rule's: it never ran on a brain-only
                     diff; the doctor and the invariants check brain/.)
  shellcheck         `shellcheck -x --norc` fails on a changed shell script

Every check runs, except that no candidate code (the doctor, the hand runs,
the unit tests) runs once frozen-path or path-escape has refused; those are
reported as skipped (`ok: null`). An empty diff passes.

Prints one JSON object: {ok, base, candidate, refused: [{rule, detail,
paths}], checks: [{rule, ok, detail}], lines, soul_context_lines, files}.
Exits 0 when nothing is refused, 2 when anything is, 1 with {ok: false,
error} when the gate itself cannot run (a bad ref, a missing tool, a check
that crashes).

Frozen with bin/. Python 3.11 standard library only, and no sibling imports:
it loads the base's bin/lib/frozen.py, never this checkout's.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

# The gate may be run on the checkout it lives in: it leaves no __pycache__.
sys.dont_write_bytecode = True

Json = dict[str, Any]

MAX_DIFF_LINES = 400
MAX_SOUL_CONTEXT_LINES = 60
SOUL_CONTEXT = ("brain/SOUL.md", "brain/CONTEXT.md")

#: What Teeny may change. Anything else is frozen.
ALLOWED_FILES = frozenset({"brain/SOUL.md", "brain/CONTEXT.md", "brain/lessons.md", "brain/cron.toml"})
ALLOWED_TREES = ("brain/narrative/", "brain/skills/", "brain/playbooks/")
#: New hands, added only: hands/<name>, hands/lib/teeny_hands/<name>/...,
#: hands/tests/test_<name>.py, for a <name> the base has in none of its forms.
#: No hyphen: hands/tests/test_<name>.py must be a module unittest discovers,
#: and teeny_hands/<name> a package Python imports. No existing hand has one.
HAND_NAME = re.compile(r"[a-z0-9][a-z0-9_]*")
#: Frozen although the allowlist would reach them (existing files under
#: hands/lib/teeny_hands/ are frozen too: only a new name is Teeny's).
FROZEN_INSIDE = frozenset({"brain/agents/act.toml", "brain/agents/judge.toml", "hands/guard", "hands/x"})
GIT_METADATA = frozenset({".git", ".gitignore", ".gitattributes", ".gitmodules"})

RULES = ("frozen-path", "path-escape", "seed-cron", "doctor", "invariants", "diff-size", "soul-context-size",
         "hand-test", "hand-contract", "hand-unit-tests", "shellcheck")
#: The rules after which no candidate code runs.
UNSAFE = ("frozen-path", "path-escape")
MAX_DETAIL = 600
HAND_TIMEOUT = 20
CHECK_TIMEOUT = 600
UNIT_TEST_TIMEOUT = 1200
SHELL_SHEBANG = re.compile(rb"#!\s*(?:/usr/bin/env\s+(?:-\S+\s+)*)?(?:\S*/)?(?:ba|da|k)?sh(?:\s|$)")
#: What a check's environment keeps from the gate's: nothing that names a
#: core, a token or Teeny's own root.
KEEP_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")


class GateError(Exception):
    """The gate could not run: exit 1."""


def clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_DETAIL else text[: MAX_DETAIL - 3] + "..."


# --- git, in the scratch repository or read-only on the base's ------------------------------------------------


def git_env(home: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
               GIT_AUTHOR_NAME="gate", GIT_AUTHOR_EMAIL="gate@localhost", GIT_COMMITTER_NAME="gate",
               GIT_COMMITTER_EMAIL="gate@localhost")
    return env


def git(args: list[str], env: dict[str, str], stdout: Any = subprocess.PIPE, cwd: Path | None = None) -> str:
    try:
        done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *args],
                              stdout=stdout, stderr=subprocess.PIPE, env=env, cwd=cwd, stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise GateError(f"git: {exc}") from None
    if done.returncode != 0:
        raise GateError(clip(f"git {' '.join(args[:3])}: {done.stderr.decode(errors='replace').strip()}"))
    return done.stdout.decode("utf-8", errors="surrogateescape") if done.stdout is not None else ""


def candidate_git_dir(candidate: Path) -> Path:
    """The candidate's repository, read from its `.git` (a directory, or a
    worktree's `gitdir:` file) without running git in the candidate."""
    dotgit = candidate / ".git"
    if dotgit.is_dir() and not dotgit.is_symlink():
        return dotgit
    if dotgit.is_file() and not dotgit.is_symlink():
        match = re.match(r"gitdir:\s*(.+)", dotgit.read_text(encoding="utf-8", errors="replace").strip())
        if match:
            return (candidate / match.group(1).strip()).resolve()
    raise GateError(f"{candidate} has no .git to read the base from: pass --git-dir")


def base_tree(git_dir: Path, sha: str, env: dict[str, str]) -> dict[str, str]:
    """Every path at SHA and its mode."""
    out = git(["--git-dir", str(git_dir), "ls-tree", "-r", "-z", "--full-tree", sha], env)
    tree = {}
    for entry in out.split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            tree[path] = meta.split()[0]
    return tree


def export(git_dir: Path, sha: str, into: Path, env: dict[str, str]) -> None:
    into.mkdir(parents=True)
    archive = subprocess.Popen(["git", "--git-dir", str(git_dir), "archive", "--format=tar", sha],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, stdin=subprocess.DEVNULL)
    untar = subprocess.run(["tar", "-x", "-f", "-", "-C", str(into)], stdin=archive.stdout, capture_output=True)
    assert archive.stdout is not None and archive.stderr is not None
    archive.stdout.close()
    err = archive.stderr.read().decode(errors="replace")
    if archive.wait() != 0 or untar.returncode != 0:
        raise GateError(clip(f"cannot export {sha}: {err or untar.stderr.decode(errors='replace')}"))


def submodule_pin(path: Path, env: dict[str, str]) -> str | None:
    """The commit a checked-out submodule's HEAD names (read-only), or None
    when it is not checked out, which git counts as unchanged."""
    dotgit = path / ".git"
    if not (dotgit.exists() or dotgit.is_symlink()):
        return None
    try:
        sub_git = candidate_git_dir(path)
        return git(["--git-dir", str(sub_git), "rev-parse", "--verify", "HEAD^{commit}"], env).strip()
    except GateError as exc:
        return f"unreadable ({exc})"


# --- the copy -------------------------------------------------------------------------------------------------


def copy_candidate(src: Path, dst: Path, skip: set[str], odd: list[tuple[str, str]]) -> None:
    """Copy SRC's tree into DST as git would see it: regular files with their
    executable bit, symlinks as links, directories. `.git` at the top and
    the SKIP paths (submodules, which keep only their directory) are left out;
    a special file is left out and noted in ODD. Nothing under a submodule
    path is read: a submodule's content can only change through its pin
    (submodule_pin). A directory holding a `.git` (a repository inside the
    tree) is noted in ODD and kept empty: none of its files is copied, so
    none is diffed, checked or run."""

    def walk(rel: str) -> None:
        here = src / rel if rel else src
        with os.scandir(here) as entries:
            names = sorted(e.name for e in entries)
        nested = [n for n in names if rel and n.casefold() == ".git"]
        if nested:
            odd.append(("nested-git", f"{rel}/{nested[0]}"))
            return
        for name in names:
            path = f"{rel}/{name}" if rel else name
            if not rel and name == ".git":
                continue
            st = os.lstat(src / path)
            if name.casefold() == ".git":
                odd.append(("nested-git", path))
                continue
            if stat.S_ISLNK(st.st_mode):
                os.symlink(os.readlink(src / path), dst / path)
            elif stat.S_ISDIR(st.st_mode):
                (dst / path).mkdir(mode=0o755)
                os.chmod(dst / path, 0o755)
                if path not in skip:
                    walk(path)
            elif stat.S_ISREG(st.st_mode):
                shutil.copyfile(src / path, dst / path, follow_symlinks=False)
                os.chmod(dst / path, 0o755 if st.st_mode & stat.S_IXUSR else 0o644)
            else:
                odd.append(("special", path))

    walk("")




# --- the diff -------------------------------------------------------------------------------------------------


class Change:
    """One entry of `git diff --raw`: status letter, modes, path, and the
    source of a rename or copy."""

    def __init__(self, status: str, old_mode: str, new_mode: str, path: str, src: str | None) -> None:
        self.status, self.old_mode, self.new_mode, self.path, self.src = status, old_mode, new_mode, path, src

    def paths(self) -> list[str]:
        return [p for p in (self.src, self.path) if p is not None]


def parse_raw(out: str) -> list[Change]:
    fields = out.split("\0")
    changes, i = [], 0
    while i < len(fields) and fields[i]:
        meta = fields[i].lstrip(":").split()
        old_mode, new_mode, status = meta[0], meta[1], meta[4][0]
        if status in "RC":
            changes.append(Change(status, old_mode, new_mode, fields[i + 2], fields[i + 1]))
            i += 3
        else:
            changes.append(Change(status, old_mode, new_mode, fields[i + 1], None))
            i += 2
    return changes


def parse_numstat(out: str) -> list[tuple[int | None, int | None, str]]:
    fields = out.split("\0")
    stats, i = [], 0
    while i < len(fields) and fields[i]:
        added, removed, path = fields[i].split("\t", 2)
        if path == "":  # a rename: the source and destination follow
            path, i = fields[i + 2], i + 3
        else:
            i += 1
        stats.append((None if added == "-" else int(added), None if removed == "-" else int(removed), path))
    return stats


# --- the allowlist --------------------------------------------------------------------------------------------


def hand_of(path: str) -> str | None:
    """The <name> of a path in one of a new hand's three places."""
    parts = path.split("/")
    name = None
    if len(parts) == 2 and parts[0] == "hands" and parts[1] not in ("lib", "tests"):
        name = parts[1]
    elif len(parts) >= 5 and parts[:3] == ["hands", "lib", "teeny_hands"]:
        name = parts[3]
    elif len(parts) == 3 and parts[:2] == ["hands", "tests"]:
        match = re.fullmatch(r"test_(.+)\.py", parts[2])
        name = match.group(1) if match else None
    return name if name and HAND_NAME.fullmatch(name) else None


def touched_paths(ch: Change) -> list[tuple[str, bool]]:
    """The paths CH changes, each with whether it is a new file there. A
    copy's destination is a new file and its source is untouched; a rename
    deletes its source and moves into its destination, so neither is new."""
    if ch.status == "R":
        return [(ch.src or "", False), (ch.path, False)]
    return [(ch.path, ch.status in "AC")]


def fold(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def base_hand_names(base: dict[str, str]) -> set[str]:
    """Every hand name the base has, in any form, folded."""
    names = set()
    for path in base:
        parts = path.split("/")
        if len(parts) == 2 and parts[0] == "hands":
            names.add(parts[1])
        elif len(parts) >= 4 and parts[:3] == ["hands", "lib", "teeny_hands"]:
            names.add(parts[3][:-3] if len(parts) == 4 and parts[3].endswith(".py") else parts[3])
        elif len(parts) >= 3 and parts[:2] == ["hands", "tests"]:
            match = re.fullmatch(r"test_(.+)\.py", parts[2])
            names.add(match.group(1) if match else parts[2])
    return {fold(n) for n in names}


def allowed(path: str, added: bool, taken: set[str]) -> bool:
    """May Teeny change PATH? ADDED: it is a new file here (not modified,
    deleted or renamed). TAKEN: the base's hand names, folded."""
    if path in FROZEN_INSIDE:
        return False
    if path in ALLOWED_FILES or path.startswith(ALLOWED_TREES):
        return True
    name = hand_of(path)
    return bool(name and added and fold(name) not in taken)


def bad_path(path: str) -> str | None:
    if not path or path.startswith("/"):
        return "absolute or empty"
    if "\\" in path:
        return "a backslash"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return "a control character"
    segments = path.split("/")
    if any(s in ("", ".", "..") for s in segments):
        return "an empty, . or .. segment"
    if any(s.casefold() in GIT_METADATA for s in segments):
        return "a git metadata file"
    return None


def load_frozen(base_dir: Path) -> tuple[Any, list[str]]:
    """The base's frozen.py and its list, never this checkout's or the candidate's."""
    spec = importlib.util.spec_from_file_location("gate_base_frozen", base_dir / "bin" / "lib" / "frozen.py")
    if spec is None or spec.loader is None:
        raise GateError("the base has no bin/lib/frozen.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        patterns = module.parse_patterns((base_dir / module.LIST_PATH).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, AttributeError) as exc:
        raise GateError(f"the base's frozen list does not load: {exc}") from None
    if not patterns:
        raise GateError("the base's evals/frozen.txt is empty")
    return module, patterns


# --- running checks -------------------------------------------------------------------------------------------


class Runner:
    """Runs the base's scripts and the candidate copy's code in the scratch
    directory, with nothing of the operator's environment that names a core,
    a token or a root, and HOME and TMPDIR of their own."""

    def __init__(self, scratch: Path, base_dir: Path, run_dir: Path) -> None:
        self.scratch, self.base_dir, self.run_dir = scratch, base_dir, run_dir
        self.home = scratch / "home"
        self.tmp = scratch / "tmp"
        self.home.mkdir()
        self.tmp.mkdir()

    def env(self, **extra: str) -> dict[str, str]:
        """No TEENY_ROOT: the base's scripts find their own root, and the
        hands' tests theirs, as in CI."""
        env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
        env.update(HOME=str(self.home), TMPDIR=str(self.tmp), PYTHONDONTWRITEBYTECODE="1",
                   GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        env.update(extra)
        return env

    def scrub(self, text: str) -> str:
        for where, to in ((str(self.run_dir), "CANDIDATE"), (str(self.base_dir), "BASE"),
                          (str(self.scratch), "SCRATCH")):
            text = text.replace(where, to)
        return text

    def run(self, argv: list[str], timeout: float = CHECK_TIMEOUT, stdin: str | None = None,
            cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess | None:
        """The finished process, or None when it timed out. A program that
        cannot start is the gate's own failure."""
        try:
            return subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout,
                                  input=stdin if stdin is not None else "", cwd=cwd or self.run_dir,
                                  env=env or self.env())
        except subprocess.TimeoutExpired:
            return None
        except OSError as exc:
            raise GateError(f"{self.scrub(argv[0])}: {exc}") from None

    def verdict(self, done: subprocess.CompletedProcess | None, timeout: float = CHECK_TIMEOUT) -> tuple[bool, str]:
        """A check's verdict, detailed by its failing lines (the doctor's
        `FAIL ...`, unittest's `FAIL:`/`ERROR:`) before its last ones."""
        if done is None:
            return False, f"timed out after {int(timeout)} s"
        lines = (done.stderr.strip() or done.stdout.strip()).splitlines()
        failing = [line.strip() for line in lines if re.match(r"(FAIL|ERROR)\b", line)]
        if done.returncode != 0 and failing:
            detail = " | ".join(failing + lines[-1:])
            return False, clip(self.scrub(detail))
        detail = " ".join(lines[-3:]) or f"exit {done.returncode}"
        return done.returncode == 0, clip(self.scrub(detail)[-MAX_DETAIL:])


def one_object(stdout: str) -> str | None:
    """Why STDOUT is not exactly one JSON object, or None."""
    text = stdout.strip()
    if not text:
        return "printed nothing on stdout"
    try:
        obj, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return "stdout is not JSON"
    if text[end:].strip():
        return "printed more than one JSON value on stdout"
    if not isinstance(obj, dict):
        return "stdout is JSON but not an object"
    return None


def is_shell(path: Path) -> bool:
    if path.suffix == ".sh":
        return True
    try:
        with open(path, "rb") as handle:
            first = handle.readline(256)
    except OSError:
        return False
    return bool(SHELL_SHEBANG.match(first))


# --- the gate -------------------------------------------------------------------------------------------------


class Gate:
    def __init__(self, candidate: Path, base_ref: str, git_dir: Path | None, scratch: Path) -> None:
        self.candidate, self.base_ref, self.scratch = candidate, base_ref, scratch
        self.git_dir = git_dir or candidate_git_dir(candidate)
        self.genv = git_env(scratch)
        self.refused: list[Json] = []
        self.checks: dict[str, Json] = {}

    def refuse(self, rule: str, detail: str, paths: list[str] | None = None) -> None:
        self.refused.append({"rule": rule, "detail": clip(detail), "paths": sorted(set(paths or []))})

    def note(self, rule: str, ok: bool | None, detail: str) -> None:
        self.checks[rule] = {"rule": rule, "ok": ok, "detail": clip(detail)}

    def failed(self, rule: str) -> bool:
        return any(r["rule"] == rule for r in self.refused)

    def check(self, rule: str, ok: bool, detail: str, paths: list[str] | None = None) -> None:
        self.note(rule, ok, detail)
        if not ok:
            self.refuse(rule, detail, paths)

    # --- setting up ---

    def prepare(self) -> None:
        gd = str(self.git_dir)
        try:
            self.base = git(["--git-dir", gd, "rev-parse", "--verify", "--quiet", f"{self.base_ref}^{{commit}}"],
                            self.genv).strip()
        except GateError:
            raise GateError(f"no commit {self.base_ref!r} in {gd}") from None
        self.base_files = base_tree(self.git_dir, self.base, self.genv)
        self.submodules = {p for p, mode in self.base_files.items() if mode == "160000"}
        self.base_dir = self.scratch / "base"
        export(self.git_dir, self.base, self.base_dir, self.genv)
        self.frozen, self.patterns = load_frozen(self.base_dir)

        # The diff repository: the base committed, and checked to be the base.
        self.repo = self.scratch / "diff"
        export(self.git_dir, self.base, self.repo, self.genv)
        g = self.repo_git
        g(["init", "-q"])
        for key, value in (("core.fileMode", "true"), ("core.symlinks", "true"), ("core.ignoreCase", "false"),
                           ("core.autocrlf", "false"), ("core.precomposeUnicode", "false"),
                           ("core.safecrlf", "false"), ("commit.gpgSign", "false")):
            g(["config", key, value])
        # The bytes as they are: no candidate .gitattributes converts, filters
        # or hides a file from the diff.
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        (self.repo / ".git" / "info" / "attributes").write_text(
            "* !text !eol !filter !ident !working-tree-encoding !diff !merge !export-ignore\n", encoding="utf-8")
        g(["add", "-A", "-f"])
        for path in sorted(self.submodules):
            sha = git(["--git-dir", gd, "rev-parse", f"{self.base}:{path}"], self.genv).strip()
            g(["update-index", "--add", "--cacheinfo", f"160000,{sha},{path}"])
        g(["commit", "-q", "--no-verify", "--allow-empty", "-m", "base"])
        want = git(["--git-dir", gd, "rev-parse", f"{self.base}^{{tree}}"], self.genv).strip()
        if g(["rev-parse", "HEAD^{tree}"]).strip() != want:
            raise GateError("the exported base does not match its commit (export-ignore or export-subst?)")

        # The candidate over it.
        for entry in self.repo.iterdir():
            if entry.name != ".git":
                shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
        self.odd: list[tuple[str, str]] = []
        copy_candidate(self.candidate, self.repo, self.submodules, self.odd)
        g(["add", "-A", "-f"])
        self.changes = parse_raw(g(["diff", "--cached", "--raw", "-z", "--find-renames", "--find-copies",
                                    "--no-ext-diff", "HEAD"]))
        # Sizes count renames by their edits and copies by their whole text.
        self.stats = parse_numstat(g(["diff", "--cached", "--numstat", "-z", "--find-renames", "--no-ext-diff",
                                      "--no-textconv", "HEAD"]))
        # The copy the checks run in: a repository of its own whose HEAD is
        # the candidate, as CI checks out a pull request (the hands' tests
        # push HEAD of the checkout they run in).
        self.run_dir = self.scratch / "run"
        shutil.copytree(self.repo, self.run_dir, symlinks=True)
        git(["commit", "-q", "--no-verify", "--allow-empty", "-m", "candidate"], self.genv, cwd=self.run_dir)
        self.runner = Runner(self.scratch, self.base_dir, self.run_dir)

    def repo_git(self, args: list[str]) -> str:
        return git(args, self.genv, cwd=self.repo)

    # --- rules 1 and 2: the paths ---

    def paths_rules(self) -> None:
        taken = base_hand_names(self.base_files)
        frozen_hits: list[str] = []
        escapes: dict[str, list[str]] = {}

        def escape(kind: str, path: str) -> None:
            escapes.setdefault(kind, []).append(path)

        folded: dict[str, str] = {}
        for path in self.base_files:
            parts = path.split("/")
            for i in range(1, len(parts) + 1):
                prefix = "/".join(parts[:i])
                folded.setdefault(fold(prefix), prefix)

        for kind, path in self.odd:
            escape("a nested .git (a repository inside the tree)" if kind == "nested-git"
                   else "a special file (a fifo, socket or device)", path)
        for ch in self.changes:
            for path in ch.paths():
                why = bad_path(path)
                if why:
                    escape(f"a path with {why}", path)
                parts = path.split("/")
                for i in range(1, len(parts) + 1):
                    prefix = "/".join(parts[:i])
                    other = folded.get(fold(prefix))
                    if other is not None and other != prefix:
                        escape(f"a path that folds onto {other} on a case-insensitive disk", path)
                        break
            modes = {ch.old_mode, ch.new_mode} - {"000000"}
            if "120000" in modes:
                escape("a symlink", ch.path)
            if "160000" in modes:
                escape("a submodule", ch.path)
            if ch.status not in "AD" and ch.old_mode != ch.new_mode:
                escape("a mode change", ch.path)
            entry_point = (ch.status in "AC" and ch.path.startswith("hands/") and ch.path.count("/") == 1
                           and allowed(ch.path, True, taken))
            if ch.status != "D" and ch.new_mode == "100755" and not entry_point:
                escape("an executable that is not a new hand's entry point", ch.path)
            touched = [(p, ok and self.frozen.frozen_pattern(p, self.patterns) is None)
                       for p, ok in ((p, allowed(p, added, taken)) for p, added in touched_paths(ch))]
            frozen_hits += [p for p, ok in touched if not ok]
            if any(not ok for _, ok in touched):
                if ch.status == "D":
                    escape("a deleted frozen file", ch.path)
                elif ch.status == "R":
                    escape("a rename into or out of a frozen path", f"{ch.src} -> {ch.path}")
                elif ch.status == "C":
                    escape("a copy into a frozen path", f"{ch.src} -> {ch.path}")

        # A binary file: git's own test, and any NUL byte it did not look far enough to see.
        binary = [p for a, r, p in self.stats if a is None or r is None]
        for ch in self.changes:
            target = self.repo / ch.path
            if ch.status != "D" and ch.new_mode in ("100644", "100755") and ch.path not in binary:
                try:
                    if b"\0" in target.read_bytes():
                        binary.append(ch.path)
                except OSError:
                    pass
        for path in binary:
            escape("a binary file", path)

        for path in sorted(self.submodules):
            pin = submodule_pin(self.candidate / path, self.genv)
            if pin is not None and pin != git(["--git-dir", str(self.git_dir), "rev-parse",
                                               f"{self.base}:{path}"], self.genv).strip():
                frozen_hits.append(path)

        if frozen_hits:
            self.check("frozen-path", False, "outside what Teeny may change: " + ", ".join(sorted(set(frozen_hits))),
                       frozen_hits)
        else:
            self.note("frozen-path", True, f"{len(self.changes)} changed path(s), all Teeny's to change")
        for kind, paths in escapes.items():
            self.refuse("path-escape", f"{kind}: {', '.join(sorted(set(paths)))}", paths)
        self.note("path-escape", not escapes,
                  "; ".join(sorted(escapes)) if escapes else "no symlink, rename, deletion, mode, binary or path escape")

    # --- rule 6: size ---

    def size_rules(self) -> None:
        self.lines = sum((a or 0) + (r or 0) for a, r, _ in self.stats)
        self.soul = sum((a or 0) + (r or 0) for a, r, p in self.stats if p in SOUL_CONTEXT)
        self.check("diff-size", self.lines <= MAX_DIFF_LINES, f"{self.lines}/{MAX_DIFF_LINES} changed lines",
                   [p for _, _, p in self.stats])
        self.check("soul-context-size", self.soul <= MAX_SOUL_CONTEXT_LINES,
                   f"{self.soul}/{MAX_SOUL_CONTEXT_LINES} changed lines in SOUL.md and CONTEXT.md",
                   [p for _, _, p in self.stats if p in SOUL_CONTEXT])

    # --- rules 3, 4, 5: the base's own checks ---

    def base_checks(self, safe: bool) -> None:
        r, base = self.runner, self.base_dir
        cron = self.run_dir / "brain" / "cron.toml"
        ok, detail = r.verdict(r.run([str(base / "bin" / "seed-cron"), "--check", "--file", str(cron),
                                      "--pinned", str(base / "evals" / "cron.pinned.toml")]))
        self.check("seed-cron", ok, detail, ["brain/cron.toml"])

        done = r.run([str(base / "bin" / "eval"), "invariants", "--root", str(self.run_dir)])
        ok, detail = r.verdict(done)
        missing: list[Any] = []
        if done is not None:
            try:
                missing = list(json.loads(done.stdout).get("missing") or [])
            except (ValueError, AttributeError, TypeError):
                ok = False
        if missing:
            ok = False
            detail = "anchors gone: " + "; ".join(f"{m.get('id')}: {m.get('file')} /{m.get('pattern')}/"
                                                  for m in missing if isinstance(m, dict))
        elif ok:
            detail = "every anchor holds"
        self.check("invariants", ok, detail,
                   [f"brain/{m.get('file')}" for m in missing if isinstance(m, dict)])

        if not safe:
            self.note("doctor", None, "skipped: no candidate code runs once frozen-path or path-escape refused")
            return
        ok, detail = r.verdict(r.run([str(base / "bin" / "doctor"), "--root", str(self.run_dir)],
                                     env=r.env(DRY_RUN="1")))
        self.check("doctor", ok, detail)

    # --- rules 7 and 8: hands ---

    def hand_rules(self, safe: bool) -> None:
        taken = base_hand_names(self.base_files)
        new = sorted({n for ch in self.changes if ch.status in "AC" and (n := hand_of(ch.path))
                      and fold(n) not in taken})
        untested = []
        for name in new:
            test = self.run_dir / "hands" / "tests" / f"test_{name}.py"
            try:
                tree = ast.parse(test.read_bytes(), f"hands/tests/test_{name}.py")
                compile(tree, f"hands/tests/test_{name}.py", "exec", dont_inherit=True)
            except (OSError, SyntaxError, ValueError):
                untested.append(name)
                continue
            if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")
                       for node in ast.walk(tree)):
                untested.append(name)
        self.check("hand-test", not untested,
                   f"no hands/tests/test_<name>.py that compiles and defines a test for: {', '.join(untested)}"
                   if untested
                   else (f"{', '.join(new)} tested" if new else "no new hand"),
                   [f"hands/{n}" for n in untested])

        entries = [n for n in new if any(ch.path == f"hands/{n}" and ch.status in "AC" for ch in self.changes)]
        if not safe:
            self.note("hand-contract", None, "skipped: no candidate code runs once frozen-path or path-escape refused")
        else:
            broken = []
            for name in entries:
                why = self.contract(name)
                if why:
                    broken.append((name, why))
            self.check("hand-contract", not broken,
                       "; ".join(f"hands/{n}: {why}" for n, why in broken) if broken
                       else (f"{', '.join(entries)} keep the contract" if entries else "no new hand to run"),
                       [f"hands/{n}" for n, _ in broken])

        touches = any(p.startswith("hands/") for ch in self.changes for p in ch.paths())
        if not touches:
            self.note("hand-unit-tests", True, "not run: nothing under hands/ changed")
        elif not safe:
            self.note("hand-unit-tests", None, "skipped: no candidate code runs once frozen-path or path-escape refused")
        else:
            # Only the new hands' own tests, not `discover` over the suite: every
            # existing hands/ file is frozen, a new package can't shadow an
            # existing module, and neither refusal lets candidate code run, so no
            # existing test's result can depend on what the diff adds to hands/.
            tests = [f"test_{n}" for n in new if (self.run_dir / "hands" / "tests" / f"test_{n}.py").is_file()]
            if not tests:
                self.note("hand-unit-tests", True, "not run: no new hand's test file")
            else:
                r = self.runner
                done = r.run(["python3", "-m", "unittest", *tests], timeout=UNIT_TEST_TIMEOUT,
                             cwd=self.run_dir / "hands" / "tests")
                ok, detail = r.verdict(done, UNIT_TEST_TIMEOUT)
                self.check("hand-unit-tests", ok, detail, [f"hands/tests/{t}.py" for t in tests])

    def contract(self, name: str) -> str | None:
        """Why hands/NAME, run with no verb and `{}` on stdin, breaks the
        contract (one JSON object on stdout, exit 0, 1 or 2), or None."""
        entry = self.run_dir / "hands" / name
        if not (entry.is_file() and os.access(entry, os.X_OK)):
            return "not an executable file"
        home = self.scratch / f"hand-{name}"
        home.mkdir()
        r = self.runner
        done = r.run([str(entry)], timeout=HAND_TIMEOUT, stdin="{}", cwd=home,
                     env=r.env(TEENY_ROOT=str(home), HOME=str(home), DRY_RUN="1"))
        if done is None:
            return f"did not finish in {HAND_TIMEOUT} s"
        if done.returncode not in (0, 1, 2):
            return f"exit {done.returncode}, not 0, 1 or 2"
        return one_object(done.stdout)

    # --- rule 8: shellcheck ---

    def shellcheck(self) -> None:
        scripts = sorted({ch.path for ch in self.changes if ch.status != "D" and ch.new_mode in ("100644", "100755")
                          and is_shell(self.run_dir / ch.path)})
        if not scripts:
            self.note("shellcheck", True, "no shell script changed")
            return
        r = self.runner
        done = r.run(["shellcheck", "-x", "--norc", "--", *scripts])
        ok, detail = r.verdict(done)
        if not ok and done is not None:
            detail = clip(r.scrub(done.stdout.strip() or done.stderr.strip()))
        self.check("shellcheck", ok, detail if not ok else f"{len(scripts)} script(s) clean", scripts)

    def run(self) -> Json:
        self.prepare()
        self.paths_rules()
        safe = not any(self.failed(rule) for rule in UNSAFE)
        self.base_checks(safe)
        self.size_rules()
        self.hand_rules(safe)
        self.shellcheck()
        return {"ok": not self.refused, "base": self.base, "candidate": str(self.candidate),
                "refused": self.refused, "checks": [self.checks[rule] for rule in RULES],
                "lines": self.lines, "soul_context_lines": self.soul, "files": len(self.stats)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bin/evolve gate",
                                     description="Is a candidate version of Teeny safe to test? Plain code.")
    parser.add_argument("candidate", help="the candidate's checkout or worktree")
    parser.add_argument("--base", default="main", help="the ref to compare with (default: main)")
    parser.add_argument("--git-dir", help="the repository to read the base from (default: the candidate's)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        print(json.dumps({"ok": False, "error": "usage: bin/evolve gate CANDIDATE [--base REF] [--git-dir DIR]"}))
        return 1 if exc.code else 0

    def fail(error: str) -> int:
        print(json.dumps({"ok": False, "error": error}))
        return 1

    candidate = Path(args.candidate).resolve()
    if not candidate.is_dir():
        return fail(f"{args.candidate} is not a directory")
    for tool in ("git", "tar", "python3", "jq", "shellcheck"):
        if shutil.which(tool) is None:
            return fail(f"needs '{tool}' on PATH")
    git_dir = Path(args.git_dir).resolve() if args.git_dir else None
    # Resolved: a symlinked temp root (macOS /var) fails the doctor's keyring check.
    scratch = Path(tempfile.mkdtemp(prefix="teeny-gate.")).resolve()
    try:
        if scratch == candidate or candidate in scratch.parents:
            return fail("the scratch directory would be inside the candidate: set TMPDIR elsewhere")
        report = Gate(candidate, args.base, git_dir, scratch).run()
    except GateError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - a crash is the gate failing to run, still one JSON line
        return fail(clip(f"the gate crashed: {type(exc).__name__}: {exc}"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(json.dumps(report))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
