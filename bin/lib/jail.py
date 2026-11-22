"""A files-only jail for one child process, with the kernel's Landlock.

The mind's shell runs in the core's sandbox. This is the same fence for code
the core does not start: bin/box runs a tool Teeny wrote (home/tools/) under
it, so that code merged on green CI alone never has secrets/, the live brain,
state/ or .git in reach (docs/mind.md, "Its own tools").

  what it closes   every file and folder not named in `ro` or `rw`: no read,
                   no write, no listing, no execute. With a kernel that has
                   it (ABI 6), also signals and abstract unix sockets to
                   anything outside the jail
  what it leaves   the network. A jailed process holds none of Teeny's
                   accounts, so it cannot speak as Teeny; it can still fetch
                   and send as an anonymous client, and reach whatever
                   listens on loopback without a password
  fail closed      no Landlock, or one too old to stop a truncate (ABI < 3),
                   is `Unavailable`: the caller runs nothing

`enter` is for a child between fork and exec (subprocess's `preexec_fn`): it
cannot be undone and it is inherited by everything the child starts.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
from pathlib import Path
from typing import Iterable

#: x86_64 and aarch64 share these numbers (the generic syscall table).
_CREATE_RULESET, _ADD_RULE, _RESTRICT_SELF = 444, 445, 446
_VERSION_FLAG = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
MIN_ABI = 3

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REMOVE_DIR, REMOVE_FILE, MAKE_CHAR, MAKE_DIR = 1 << 4, 1 << 5, 1 << 6, 1 << 7
MAKE_REG, MAKE_SOCK, MAKE_FIFO, MAKE_BLOCK, MAKE_SYM = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
_SCOPE_ABSTRACT_UNIX_SOCKET, _SCOPE_SIGNAL = 1 << 0, 1 << 1

_READ = EXECUTE | READ_FILE | READ_DIR
#: No device nodes inside the jail, whatever the folder: MAKE_CHAR and
#: MAKE_BLOCK are handled and never granted.
_WRITE = (_READ | WRITE_FILE | REMOVE_DIR | REMOVE_FILE | MAKE_DIR | MAKE_REG | MAKE_SOCK | MAKE_FIFO | MAKE_SYM
          | REFER | TRUNCATE)
#: What a rule on a file (not a folder) may carry.
_FILE_ONLY = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE | IOCTL_DEV

#: What a program needs to start: the system's libraries, binaries and the
#: resolver's and TLS's files. Nothing of Teeny's is under any of them.
SYSTEM_RO = ("/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin", "/etc", "/opt")
SYSTEM_RW = ("/dev/null",)
SYSTEM_READ_FILES = ("/dev/urandom", "/dev/random", "/dev/zero")


class Unavailable(RuntimeError):
    """This kernel cannot hold the jail; nothing may run in its place."""


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def abi() -> int:
    """The kernel's Landlock version, 0 when it has none (or it is off)."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        got = _libc().syscall(_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(_VERSION_FLAG))
    except (OSError, AttributeError):
        return 0
    return max(int(got), 0)


def available() -> bool:
    return abi() >= MIN_ABI


def _handled(version: int) -> int:
    mask = (1 << 13) - 1
    if version >= 2:
        mask |= REFER
    if version >= 3:
        mask |= TRUNCATE
    if version >= 5:
        mask |= IOCTL_DEV
    return mask


def _fail(what: str) -> Unavailable:
    return Unavailable(f"landlock: {what}: {os.strerror(ctypes.get_errno())}")


def _add(libc: ctypes.CDLL, ruleset: int, path: str, access: int, handled: int) -> None:
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return  # a path this box does not have (no /lib64) grants nothing
    try:
        import stat as stat_
        if not stat_.S_ISDIR(os.fstat(fd).st_mode):
            access &= _FILE_ONLY
        access &= handled
        if not access:
            return
        attr = struct.pack("=Qi", access, fd)
        if libc.syscall(_ADD_RULE, ctypes.c_int(ruleset), ctypes.c_int(_RULE_PATH_BENEATH), attr, ctypes.c_uint32(0)):
            raise _fail(f"cannot allow {path}")
    finally:
        os.close(fd)


def enter(ro: Iterable[str | Path] = (), rw: Iterable[str | Path] = (), *, system: bool = True) -> None:
    """Jails this process for good. `ro` may be read and run, `rw` also
    written; with `system`, the system's own folders are readable too."""
    version = abi()
    if version < MIN_ABI:
        raise Unavailable(f"landlock: this kernel has version {version}, the jail needs {MIN_ABI}")
    libc = _libc()
    handled = _handled(version)
    scoped = (_SCOPE_ABSTRACT_UNIX_SOCKET | _SCOPE_SIGNAL) if version >= 6 else 0
    # handled_access_fs, handled_access_net (none: the network stays), scoped.
    attr = struct.pack("=QQQ", handled, 0, scoped) if version >= 6 else struct.pack("=Q", handled)
    ruleset = libc.syscall(_CREATE_RULESET, attr, ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))
    if ruleset < 0:
        raise _fail("cannot make the ruleset")
    try:
        if system:
            for path in SYSTEM_RO:
                _add(libc, ruleset, path, _READ, handled)
            for path in SYSTEM_RW:
                _add(libc, ruleset, path, READ_FILE | WRITE_FILE, handled)
            for path in SYSTEM_READ_FILES:
                _add(libc, ruleset, path, READ_FILE, handled)
        for path in ro:
            _add(libc, ruleset, str(path), _READ, handled)
        for path in rw:
            _add(libc, ruleset, str(path), _WRITE, handled)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0):
            raise _fail("cannot set no_new_privs")
        if libc.syscall(_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0)):
            raise _fail("cannot enter the jail")
    finally:
        os.close(ruleset)
