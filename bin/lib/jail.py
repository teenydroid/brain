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
  the network      left open unless the caller names `tcp_connect`, the
                   TCP ports it may connect to (ABI 4). Then Landlock
                   refuses every other TCP connect and every bind, and the
                   filter allows a socket only for a plain TCP stream
                   (AF_INET or AF_INET6, protocol TCP): no UDP, ICMP, raw,
                   netlink, packet, MPTCP or SCTP socket, which Landlock
                   does not see, and no unix one, which Landlock lets
                   connect to any path (a stream socketpair stays; a datagram one
                   can sendto a path, so it goes). It also refuses
                   MSG_FASTOPEN sends, a TCP connect Landlock never sees, and
                   listen (on an unbound socket it binds a port Landlock
                   never sees) and io_uring, which can make socket calls
                   the filter never sees. A Landlock port rule is a port,
                   not an address: a process may connect to that port on
                   any host
  fail closed      no Landlock, or one too old to stop a truncate (ABI < 3)
                   or, with `tcp_connect`, to rule the network (ABI < 4), a
                   machine the filter has no table for, or a grant that
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
_RULE_PATH_BENEATH, _RULE_NET_PORT = 1, 2
_PR_SET_NO_NEW_PRIVS = 38
MIN_ABI = 3
#: The first Landlock with network rules.
NET_ABI = 4

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REMOVE_DIR, REMOVE_FILE, MAKE_CHAR, MAKE_DIR = 1 << 4, 1 << 5, 1 << 6, 1 << 7
MAKE_REG, MAKE_SOCK, MAKE_FIFO, MAKE_BLOCK, MAKE_SYM = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
_SCOPE_ABSTRACT_UNIX_SOCKET, _SCOPE_SIGNAL = 1 << 0, 1 << 1
#: LANDLOCK_ACCESS_NET_BIND_TCP and _CONNECT_TCP (linux/landlock.h).
_NET_BIND_TCP, _NET_CONNECT_TCP = 1 << 0, 1 << 1

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


def available(network: bool = False) -> bool:
    """Whether this kernel can hold the jail; with NETWORK, the jail that
    also rules the network (`enter(tcp_connect=...)`)."""
    return abi() >= (NET_ABI if network else MIN_ABI)


# --- the syscall filter -------------------------------------------------------------------

_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER = 22, 2
_RET_ALLOW, _RET_ERRNO, _RET_KILL = 0x7FFF0000, 0x00050000, 0x80000000
_LD_ABS, _JEQ, _JGE, _AND, _RET = 0x20, 0x15, 0x35, 0x54, 0x06
#: Per machine: the audit arch, utimensat (allowed with no path: futimens),
#: and the calls that change a file's mode, owner, times or attributes by
#: its path. fchmodat2, setxattrat and removexattrat share one number.
_SYSCALLS = {
    "x86_64": (0xC000003E, 280, (90, 268, 452, 92, 94, 260, 132, 235, 261, 188, 189, 197, 198, 463, 466)),
    "aarch64": (0xC00000B7, 88, (53, 452, 54, 5, 6, 14, 15, 463, 466)),
}
#: Per machine, for the network rule: socket, then the calls refused
#: outright: listen, io_uring_setup, io_uring_enter, io_uring_register
#: (asm/unistd_64.h and asm-generic/unistd.h).
_NET_SYSCALLS = {
    "x86_64": (41, (50, 425, 426, 427)),
    "aarch64": (198, (201, 425, 426, 427)),
}
#: Per machine, for the network rule: socketpair, then sendto, sendmsg and
#: sendmmsg. The last three open a TCP connection under MSG_FASTOPEN with no
#: connect(2), which Landlock's port rule never sees.
_NET_PAIR_SYSCALLS = {
    "x86_64": (53, 44, 46, 307),
    "aarch64": (199, 206, 211, 269),
}
_AF_UNIX, _SOCK_DGRAM = 1, 2
_MSG_FASTOPEN = 0x20000000
_JSET = 0x45
_AF_INET, _AF_INET6 = 2, 10
_SOCK_STREAM, _SOCK_TYPE_MASK = 1, 0xF
_IPPROTO_TCP = 6


def _insn(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("=HBBI", code, jt, jf, k)


def _socket_rule(nr: int) -> list[bytes]:
    """socket(family, type, protocol): only a TCP stream over AF_INET or
    AF_INET6 (SOCK_NONBLOCK and SOCK_CLOEXEC masked off the type, the
    protocol 0 or IPPROTO_TCP); anything else EPERM, AF_UNIX too: Landlock
    never asks about connecting to a unix socket by its path, and through
    one (the docker daemon's, the system resolver's, D-Bus) a process
    leaves the jail. socketpair, a pair of its own, stays. Any other call
    skips the block. The arguments are ints: the kernel reads the low 32
    bits."""
    allow, eperm = _RET_ALLOW, _RET_ERRNO | errno.EPERM
    return [
        _insn(_JEQ, 0, 11, nr),               # 0: not socket: on past 11
        _insn(_LD_ABS, 0, 0, 16),             # 1: the family
        _insn(_JEQ, 1, 0, _AF_INET),          # 2: -> 4
        _insn(_JEQ, 0, 7, _AF_INET6),         # 3: else -> 11 refuse
        _insn(_LD_ABS, 0, 0, 24),             # 4: the type
        _insn(_AND, 0, 0, _SOCK_TYPE_MASK),   # 5
        _insn(_JEQ, 0, 4, _SOCK_STREAM),      # 6: else -> 11 refuse
        _insn(_LD_ABS, 0, 0, 32),             # 7: the protocol
        _insn(_JEQ, 1, 0, 0),                 # 8: -> 10 allow
        _insn(_JEQ, 0, 1, _IPPROTO_TCP),      # 9: else -> 11 refuse
        _insn(_RET, 0, 0, allow),             # 10
        _insn(_RET, 0, 0, eperm),             # 11
    ]


def _pair_rule(nr: int) -> list[bytes]:
    """socketpair(AF_UNIX, SOCK_DGRAM): EPERM. On a datagram pair, sendto
    with a pathname names a receiver outside the jail, whatever the pair's
    peer; a stream pair has nobody to name. Any other call skips the block."""
    return [
        _insn(_JEQ, 0, 7, nr),                  # 0: not socketpair: on past 7
        _insn(_LD_ABS, 0, 0, 16),               # 1: the family
        _insn(_JEQ, 0, 3, _AF_UNIX),            # 2: else -> 6 allow
        _insn(_LD_ABS, 0, 0, 24),               # 3: the type
        _insn(_AND, 0, 0, _SOCK_TYPE_MASK),     # 4
        _insn(_JEQ, 1, 0, _SOCK_DGRAM),         # 5: -> 7 refuse
        _insn(_RET, 0, 0, _RET_ALLOW),          # 6
        _insn(_RET, 0, 0, _RET_ERRNO | errno.EPERM),  # 7
    ]


def _fastopen_rule(nr: int, flags_offset: int) -> list[bytes]:
    """A send call with MSG_FASTOPEN in its flags: EPERM. Any other call, and
    any other flags, skip the block (the call is not otherwise refused)."""
    return [
        _insn(_JEQ, 0, 4, nr),                  # 0: not this call: on past 4
        _insn(_LD_ABS, 0, 0, flags_offset),     # 1
        _insn(_JSET, 1, 0, _MSG_FASTOPEN),      # 2: -> 4 refuse
        _insn(_RET, 0, 0, _RET_ALLOW),          # 3
        _insn(_RET, 0, 0, _RET_ERRNO | errno.EPERM),  # 4
    ]


def _filter(network: bool = False) -> bytes:
    """The syscall filter; with NETWORK, also the socket rule and the
    network calls refused outright (see the module's docstring)."""
    machine = platform.machine()
    try:
        arch, utimensat, denied = _SYSCALLS[machine]
        socket_nr, net_denied = _NET_SYSCALLS[machine]
        pair_nr, sendto_nr, sendmsg_nr, sendmmsg_nr = _NET_PAIR_SYSCALLS[machine]
    except KeyError:
        raise Unavailable(f"seccomp: no syscall table for a {machine} machine") from None
    insn = _insn
    eperm = _RET_ERRNO | errno.EPERM
    prog = [insn(_LD_ABS, 0, 0, 4), insn(_JEQ, 1, 0, arch), insn(_RET, 0, 0, _RET_KILL), insn(_LD_ABS, 0, 0, 0)]
    if machine == "x86_64":
        # The x32 numbers are another table for the same calls.
        prog += [insn(_JGE, 0, 1, 0x40000000), insn(_RET, 0, 0, _RET_ERRNO | errno.ENOSYS)]
    # utimensat(fd, NULL, ...) is futimens: allowed. With a path it is not.
    prog += [insn(_JEQ, 0, 6, utimensat), insn(_LD_ABS, 0, 0, 24), insn(_JEQ, 0, 3, 0), insn(_LD_ABS, 0, 0, 28),
             insn(_JEQ, 0, 1, 0), insn(_RET, 0, 0, _RET_ALLOW), insn(_RET, 0, 0, eperm)]
    if network:
        prog += _socket_rule(socket_nr) + _pair_rule(pair_nr)
        prog += _fastopen_rule(sendto_nr, 40) + _fastopen_rule(sendmsg_nr, 32) + _fastopen_rule(sendmmsg_nr, 40)
        denied = (*denied, *net_denied)
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


def _ports(tcp_connect: Iterable[int]) -> list[int]:
    ports = list(tcp_connect)
    for port in ports:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise Unavailable(f"jail: {port!r} is not a TCP port")
    return ports


def enter(ro: Iterable[str | Path] = (), rw: Iterable[str | Path] = (), *, system: bool = True,
          protect: Iterable[str | Path] = (), tcp_connect: Iterable[int] | None = None) -> None:
    """Jails this process for good. `ro` may be read and run, `rw` also
    written; with `system`, the system's own folders are readable too.
    `protect` names what no grant may hold. `tcp_connect`, when given, is
    the only TCP ports it may connect to (none: no TCP at all), and the
    rest of the network is closed with them; None leaves the network."""
    version = abi()
    if version < MIN_ABI:
        raise Unavailable(f"landlock: this kernel has version {version}, the jail needs {MIN_ABI}")
    ports = None if tcp_connect is None else _ports(tcp_connect)
    if ports is not None and version < NET_ABI:
        raise Unavailable(f"landlock: this kernel has version {version}, the network rule needs {NET_ABI}")
    ro, rw = list(ro), list(rw)
    check_grants(grants(ro, rw, system=system), protect)
    code = _filter(network=ports is not None)
    libc = _libc()
    handled = _handled(version)
    # Bind is handled and never granted: no rule ever allows it.
    net = 0 if ports is None else _NET_BIND_TCP | _NET_CONNECT_TCP
    scoped = (_SCOPE_ABSTRACT_UNIX_SOCKET | _SCOPE_SIGNAL) if version >= 6 else 0
    # struct landlock_ruleset_attr: handled_access_fs, handled_access_net
    # (ABI 4), scoped (ABI 6); an older kernel takes the shorter struct.
    if version >= 6:
        attr = struct.pack("=QQQ", handled, net, scoped)
    elif net:
        attr = struct.pack("=QQ", handled, net)
    else:
        attr = struct.pack("=Q", handled)
    ruleset = libc.syscall(_CREATE_RULESET, attr, ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))
    if ruleset < 0:
        raise _fail("cannot make the ruleset")
    try:
        for port in ports or ():
            # struct landlock_net_port_attr: allowed_access, port (host order).
            rule = struct.pack("=QQ", _NET_CONNECT_TCP, port)
            if libc.syscall(_ADD_RULE, ctypes.c_int(ruleset), ctypes.c_int(_RULE_NET_PORT), rule, ctypes.c_uint32(0)):
                raise _fail(f"cannot allow TCP port {port}")
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
