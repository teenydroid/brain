"""A files-only jail for one child process, with the kernel's Landlock.

The mind's shell runs in the core's sandbox. This is the same fence for code
the core does not start: bin/box runs a tool Teeny wrote (home/tools/) under
it, so that code merged on green CI alone never has secrets/, the live brain,
state/ or .git in reach (docs/mind.md, "Its own tools").

  what it closes   every file and folder not named in `ro` or `rw`: no read,
                   no write, no listing, no execute. With a kernel that has
                   it (ABI 6), also signals and abstract unix sockets to
                   anything outside the jail
  metadata         Landlock says nothing about chmod, chown, utime or
                   extended attributes, and the spine's user owns secrets/:
                   a jailed process could make teeny.env unreadable without
                   ever reading it. So a syscall filter (seccomp) refuses
                   those calls when they name a file by its path. Through a
                   file the process has open (one the jail let it open) they
                   still work: fchmod, fchown, futimens, fsetxattr
  what it leaves   the network. A jailed process holds none of Teeny's
                   accounts, so it cannot speak as Teeny; it can still fetch
                   and send as an anonymous client, and reach whatever
                   listens on loopback without a password
  fail closed      no Landlock, or one too old to stop a truncate (ABI < 3),
                   a machine the filter has no table for, or a grant that
                   holds something the caller named as protected, is
                   `Unavailable`: the caller runs nothing

`enter` is for a child between fork and exec (subprocess's `preexec_fn`): it
cannot be undone and it is inherited by everything the child starts.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
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


# --- the syscall filter -------------------------------------------------------------------

_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER = 22, 2
_RET_ALLOW, _RET_ERRNO, _RET_KILL = 0x7FFF0000, 0x00050000, 0x80000000
_LD_ABS, _JEQ, _JGE, _RET = 0x20, 0x15, 0x35, 0x06
#: Per machine: the audit arch, utimensat (allowed with no path: futimens),
#: and the calls that change a file's mode, owner, times or attributes by
#: its path. fchmodat2, setxattrat and removexattrat share one number.
_SYSCALLS = {
    "x86_64": (0xC000003E, 280, (90, 268, 452, 92, 94, 260, 132, 235, 261, 188, 189, 197, 198, 463, 466)),
    "aarch64": (0xC00000B7, 88, (53, 452, 54, 5, 6, 14, 15, 463, 466)),
}


def _filter() -> bytes:
    try:
        arch, utimensat, denied = _SYSCALLS[platform.machine()]
    except KeyError:
        raise Unavailable(f"seccomp: no syscall table for a {platform.machine()} machine") from None

    def insn(code: int, jt: int, jf: int, k: int) -> bytes:
        return struct.pack("=HBBI", code, jt, jf, k)

    eperm = _RET_ERRNO | errno.EPERM
    prog = [insn(_LD_ABS, 0, 0, 4), insn(_JEQ, 1, 0, arch), insn(_RET, 0, 0, _RET_KILL), insn(_LD_ABS, 0, 0, 0)]
    if platform.machine() == "x86_64":
        # The x32 numbers are another table for the same calls.
        prog += [insn(_JGE, 0, 1, 0x40000000), insn(_RET, 0, 0, _RET_ERRNO | errno.ENOSYS)]
    # utimensat(fd, NULL, ...) is futimens: allowed. With a path it is not.
    prog += [insn(_JEQ, 0, 6, utimensat), insn(_LD_ABS, 0, 0, 24), insn(_JEQ, 0, 3, 0), insn(_LD_ABS, 0, 0, 28),
             insn(_JEQ, 0, 1, 0), insn(_RET, 0, 0, _RET_ALLOW), insn(_RET, 0, 0, eperm)]
    prog += [insn(_JEQ, len(denied) - i, 0, nr) for i, nr in enumerate(denied)]
    prog += [insn(_RET, 0, 0, _RET_ALLOW), insn(_RET, 0, 0, eperm)]
    return b"".join(prog)


class _Prog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_char_p)]


def _seccomp(libc: ctypes.CDLL, code: bytes) -> None:
    prog = _Prog(len(code) // 8, code)
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0):
        raise _fail("cannot install the syscall filter")


def check_grants(grants: Iterable[str | Path], protect: Iterable[str | Path]) -> None:
    """Unavailable when a grant is, or holds, a protected path: a rule on a
    folder covers everything below it, so a checkout under /opt would hand a
    tool its secrets through the system's own grant."""
    for grant in grants:
        held = Path(os.path.realpath(grant))
        for path in protect:
            target = Path(os.path.realpath(path))
            if target == held or held in target.parents:
                raise Unavailable(f"the jail would open {grant}, which holds {path}")


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
    return Unavailable(f"jail: {what}: {os.strerror(ctypes.get_errno())}")


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


def grants(ro: Iterable[str | Path] = (), rw: Iterable[str | Path] = (), *, system: bool = True) -> list[str]:
    """Every path `enter` would open, for `check_grants`."""
    return [*(SYSTEM_RO + SYSTEM_RW + SYSTEM_READ_FILES if system else ()), *map(str, ro), *map(str, rw)]


def enter(ro: Iterable[str | Path] = (), rw: Iterable[str | Path] = (), *, system: bool = True,
          protect: Iterable[str | Path] = ()) -> None:
    """Jails this process for good. `ro` may be read and run, `rw` also
    written; with `system`, the system's own folders are readable too.
    `protect` names what no grant may hold."""
    version = abi()
    if version < MIN_ABI:
        raise Unavailable(f"landlock: this kernel has version {version}, the jail needs {MIN_ABI}")
    ro, rw = list(ro), list(rw)
    check_grants(grants(ro, rw, system=system), protect)
    code = _filter()
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
        _seccomp(libc, code)
    finally:
        os.close(ruleset)
