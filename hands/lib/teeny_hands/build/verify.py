"""The ship step's own test run (docs/roadmap.md, A6): `hands/build ship`
trusts this and never the builder's report.

The builder says "ok" and "3 passed"; that is a claim. Before a job is
pushed, `run(workdir)` runs the job's tests itself, from cron, outside any
model turn, and only a pass ships. A failure is recorded on the job with
the tail of the output, and the job fails.

The tests are code the builder wrote, so they run in a jail no looser than
the builder's own shell, and tighter where the kernel allows:

- Landlock (Linux 5.13+): the process may read only the system's own
  directories (READ_ROOTS) and the job's folder, and write only the job's
  folder. secrets/, state/ (hands.db), brain/ and the rest of the checkout
  are out of reach. With Landlock ABI 4 or later, TCP connect and bind are
  refused too, so the run has no network (the builder's shell has the
  host's; this is stricter).
- no_new_privs, a bare environment (PATH, HOME and TMPDIR inside the job's
  folder, LANG), stdin closed, its own session, a time limit.

No Landlock, no run: `run` answers `{"ok": false, "jailed": false}` and the
job is not shipped. Nothing here ever runs the code unjailed.

What is run: Python's unittest discovery over the folder (`python3 -I -m
unittest discover`), the only kind of test the builder is asked for
(build/jobs.py DAILY_BRIEF: standard library only). A folder in which it
finds no tests is a failure: "tested" must mean a test ran.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

#: How long a job's tests may run.
TIMEOUT_SECS = 300
#: The most of the output kept on the job and told to the mind.
OUTPUT_CHARS = 3000
#: What the jailed process may read (and run) besides the job's folder.
READ_ROOTS = ("/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin", "/etc", "/dev/null", "/dev/urandom",
              "/proc/self", "/sys/devices/system/cpu")

# --- Landlock, through the raw system calls (linux/landlock.h) ---------------------------------

_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_RULE_NET_PORT = 2
_PR_SET_NO_NEW_PRIVS = 38

_FS_EXECUTE = 1 << 0
_FS_WRITE_FILE = 1 << 1
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_CHAR = 1 << 6
_FS_MAKE_DIR = 1 << 7
_FS_MAKE_REG = 1 << 8
_FS_MAKE_SOCK = 1 << 9
_FS_MAKE_FIFO = 1 << 10
_FS_MAKE_BLOCK = 1 << 11
_FS_MAKE_SYM = 1 << 12
_FS_REFER = 1 << 13  # ABI 2
_FS_TRUNCATE = 1 << 14  # ABI 3
_NET_BIND_TCP = 1 << 0  # ABI 4
_NET_CONNECT_TCP = 1 << 1  # ABI 4

_READ = _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR
_WRITE_V1 = (_FS_WRITE_FILE | _FS_REMOVE_DIR | _FS_REMOVE_FILE | _FS_MAKE_CHAR | _FS_MAKE_DIR | _FS_MAKE_REG
             | _FS_MAKE_SOCK | _FS_MAKE_FIFO | _FS_MAKE_BLOCK | _FS_MAKE_SYM)


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _libc() -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    """The kernel's Landlock ABI version; 0 when there is none."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        got = _libc().syscall(_SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(_CREATE_RULESET_VERSION))
    except (OSError, AttributeError):
        return 0
    return int(got) if got > 0 else 0


def _jail(workdir: str, abi: int) -> None:
    """Runs in the child, after fork and before exec: no new privileges,
    then a Landlock domain that reads READ_ROOTS and the folder, writes
    the folder, and (ABI 4+) makes no TCP connection. Any failure kills the
    child before the tests start."""
    try:
        libc = _libc()
        handled = _READ | _WRITE_V1 | (_FS_REFER if abi >= 2 else 0) | (_FS_TRUNCATE if abi >= 3 else 0)
        handled_net = (_NET_BIND_TCP | _NET_CONNECT_TCP) if abi >= 4 else 0
        attr = _RulesetAttr(handled, handled_net)
        size = ctypes.sizeof(_RulesetAttr) if abi >= 4 else ctypes.sizeof(ctypes.c_uint64)
        ruleset = libc.syscall(_SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
        if ruleset < 0:
            raise OSError(ctypes.get_errno(), "landlock_create_ruleset")

        def allow(path: str, access: int) -> None:
            try:
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                return  # a root this system does not have
            try:
                is_dir = os.path.isdir(path)
                rule = _PathBeneath(access if is_dir else access & ~(_FS_READ_DIR | _FS_REMOVE_DIR | _FS_MAKE_CHAR
                                                                     | _FS_MAKE_DIR | _FS_MAKE_REG | _FS_MAKE_SOCK
                                                                     | _FS_MAKE_FIFO | _FS_MAKE_BLOCK | _FS_MAKE_SYM
                                                                     | _FS_REFER), fd)
                if libc.syscall(_SYS_ADD_RULE, ctypes.c_int(ruleset), ctypes.c_int(_RULE_PATH_BENEATH),
                                ctypes.byref(rule), ctypes.c_uint32(0)) < 0:
                    raise OSError(ctypes.get_errno(), f"landlock_add_rule {path}")
            finally:
                os.close(fd)

        for root in READ_ROOTS:
            allow(root, _READ)
        allow("/dev/null", _READ | _FS_WRITE_FILE)
        allow(workdir, handled)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS)")
        if libc.syscall(_SYS_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0)) < 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
        os.close(ruleset)
    except BaseException:  # noqa: BLE001 - never exec the tests outside the jail
        os._exit(97)


# --- the run ----------------------------------------------------------------------------------

_SUMMARY = re.compile(r"^Ran (\d+) tests? in", re.MULTILINE)


def _tail(text: str) -> str:
    text = text.strip()
    return text if len(text) <= OUTPUT_CHARS else "...\n" + text[-OUTPUT_CHARS:]


def run(workdir: Path, timeout: float = TIMEOUT_SECS) -> dict[str, Any]:
    """The job's tests, jailed: `{ok, jailed, ran, command, output}`. `ok`
    only when the jail held, at least one test ran and every test passed."""
    command = [sys.executable, "-I", "-B", "-m", "unittest", "discover", "-s", ".", "-t", "."]
    out: dict[str, Any] = {"ok": False, "jailed": False, "ran": 0, "command": "python3 -m unittest discover"}
    folder = Path(workdir).resolve()
    if not folder.is_dir():
        return {**out, "output": "the job's folder is missing"}
    abi = landlock_abi()
    if abi < 1:
        return {**out, "output": "no Landlock on this kernel: the ship step never runs a job's tests unjailed"}
    tmp = folder / ".tmp-tests"
    tmp.mkdir(exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(folder), "TMPDIR": str(tmp), "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(command, cwd=folder, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=timeout, preexec_fn=lambda: _jail(str(folder), abi), start_new_session=True)
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) else ""
        return {**out, "jailed": True, "output": _tail(f"{text}\nstopped after {int(timeout)} s")}
    finally:
        try:
            for leftover in sorted(tmp.rglob("*"), reverse=True):
                leftover.rmdir() if leftover.is_dir() else leftover.unlink()
            tmp.rmdir()
        except OSError:
            pass
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 97:
        return {**out, "output": "the jail could not be set up; nothing ran"}
    found = _SUMMARY.findall(text)
    ran = int(found[-1]) if found else 0
    ok = proc.returncode == 0 and ran > 0
    output = _tail(text) if ran or proc.returncode else "no tests were found to run"
    return {**out, "ok": ok, "jailed": True, "ran": ran, "exit": proc.returncode, "abi": abi, "output": output}
