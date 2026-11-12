"""A data job (docs/roadmap.md, A17): a small script Teeny wrote, run on a
schedule, in a jail, with its output as a playbook's input.

    python3 bin/lib/datajob.py BRAIN NAME < inputs.json

The script is `brain/datajobs/NAME.py` (Teeny's, like its playbooks). A
playbook names it as an input, after the hand reads it works on:

    [[inputs]]
    name = "trend"
    run = "datajob:follower-trend"

bin/act runs it when the playbook runs, so it runs on the schedule of that
playbook's cron job, and only then. What it gets and what it may do:

- stdin: the playbook's inputs gathered before it, as one JSON object
  (what its hand reads returned). That is its only data. At most
  MAX_STDIN bytes.
- stdout: one JSON value, at most MAX_STDOUT bytes, which becomes the
  input. Anything else (not JSON, too long, a non-zero exit) fails the run,
  and bin/act fails the playbook as it does for a hand that fails.
- The jail, set in the child before the script starts, failing closed
  (exit 97, nothing ran) if any step fails:
  - Landlock (teeny_hands.build.verify.confine): it reads the system's
    own directories and a scratch folder holding a copy of the script, and
    writes only that folder, which is deleted after. secrets/, state/,
    brain/ and the rest of the checkout are out of reach, so it can write
    nothing that lasts. ABI 4+ also refuses TCP connect and bind.
  - seccomp: creating an IPv4, IPv6 or packet socket kills the process at
    once (SIGSYS). A job that tries the network is killed, not refused.
  - no_new_privs, a bare environment, its own session, MAX_SECS.
- No Landlock or no seccomp: nothing runs.

Every run is recorded in state/datajobs/runs.jsonl: when, the name, ok,
the exit, whether it was killed for the network, and how long it took.
Never its input or its output.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "hands" / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.build import verify  # noqa: E402

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
FOLDER = "datajobs"
MAX_SCRIPT = 64 * 1024
MAX_STDIN = 256 * 1024
MAX_STDOUT = 20_000
MAX_SECS = 60
#: The exit a jail that could not be set up leaves.
JAIL_FAILED = 97

Json = dict[str, Any]

# --- seccomp: an inet socket kills the process ---------------------------------------------------

_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_RET_ALLOW = 0x7FFF0000
_RET_KILL_PROCESS = 0x80000000
#: (AUDIT_ARCH_*, the socket syscall's number) per machine.
_ARCH = {"x86_64": (0xC000003E, 41), "aarch64": (0xC00000B7, 198)}
#: The families that reach a network: AF_INET, AF_INET6, AF_PACKET.
_NET_FAMILIES = (2, 10, 17)
_LD_W_ABS, _JEQ_K, _JGE_K, _RET_K = 0x20, 0x15, 0x35, 0x06


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]


def _filter() -> bytes:
    """The BPF program, by index: 0 load arch; 1 a foreign arch is killed;
    2 load nr; 3 an x32 call is killed; 4 not socket() is allowed; 5 load
    the family; 6-8 a network family is killed; 9 allow; 10 kill."""
    arch, socket_nr = _ARCH[platform.machine()]
    program = [
        (_LD_W_ABS, 0, 0, 4),
        (_JEQ_K, 0, 8, arch),
        (_LD_W_ABS, 0, 0, 0),
        (_JGE_K, 6, 0, 0x40000000),
        (_JEQ_K, 0, 4, socket_nr),
        (_LD_W_ABS, 0, 0, 16),
        (_JEQ_K, 3, 0, _NET_FAMILIES[0]),
        (_JEQ_K, 2, 0, _NET_FAMILIES[1]),
        (_JEQ_K, 1, 0, _NET_FAMILIES[2]),
        (_RET_K, 0, 0, _RET_ALLOW),
        (_RET_K, 0, 0, _RET_KILL_PROCESS),
    ]
    return b"".join(struct.pack("HBBI", *ins) for ins in program)


def seccomp_supported() -> bool:
    return sys.platform.startswith("linux") and platform.machine() in _ARCH


def _no_network() -> None:
    """Installs the filter in this process (after no_new_privs). Raises on
    failure."""
    code = _filter()
    buf = ctypes.create_string_buffer(code, len(code))
    prog = _SockFprog(len(code) // 8, ctypes.cast(buf, ctypes.c_void_p))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_SECCOMP)")


def _jail(scratch: str, abi: int) -> None:
    try:
        verify.confine((), (scratch,), abi)
        _no_network()
    except BaseException:  # noqa: BLE001 - never run the script outside the jail
        os._exit(JAIL_FAILED)


# --- the run -------------------------------------------------------------------------------------


def _state() -> Path:
    """bin/lib/teeny.sh's TEENY_STATE when bin/act runs this, else hands'."""
    env = os.environ.get("TEENY_STATE")
    return Path(env) if env else hand.state_dir()


def _record(row: Json) -> None:
    folder = _state() / "datajobs"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def script_path(brain: Path, name: str) -> Path:
    return brain / FOLDER / f"{name}.py"


def run(brain: Path, name: str, stdin: str, timeout: float = MAX_SECS) -> Json:
    """`{ok, output?, error?, killed?, exit?}`. `output` is the script's JSON."""
    if not NAME.fullmatch(name):
        return {"ok": False, "error": f"bad data job name {name!r}"}
    script = script_path(brain, name)
    if not script.is_file():
        return {"ok": False, "error": f"no data job {FOLDER}/{name}.py"}
    if script.stat().st_size > MAX_SCRIPT:
        return {"ok": False, "error": f"{FOLDER}/{name}.py is over {MAX_SCRIPT // 1024} KB"}
    if len(stdin.encode("utf-8")) > MAX_STDIN:
        return {"ok": False, "error": f"its input is over {MAX_STDIN // 1024} KB"}
    abi = verify.landlock_abi()
    if abi < 1 or not seccomp_supported():
        return {"ok": False, "error": "no Landlock or seccomp here: a data job never runs unjailed"}
    started = time.monotonic()
    scratch = Path(tempfile.mkdtemp(prefix=f"datajob-{name}-", dir=_state()))
    out: Json = {"ok": False}
    try:
        shutil.copyfile(script, scratch / "job.py")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(scratch), "TMPDIR": str(scratch), "LANG": "C.UTF-8"}
        try:
            proc = subprocess.run([sys.executable, "-I", "-B", "job.py"], cwd=scratch, env=env, input=stdin,
                                  capture_output=True, text=True, timeout=timeout,
                                  preexec_fn=lambda: _jail(str(scratch), abi), start_new_session=True)
        except subprocess.TimeoutExpired:
            out["error"] = f"stopped after {int(timeout)} s"
        else:
            out["exit"] = proc.returncode
            if proc.returncode == JAIL_FAILED:
                out["error"] = "the jail could not be set up; nothing ran"
            elif proc.returncode == -31:  # SIGSYS: the seccomp filter
                out.update(killed=True, error="killed: it tried to open a network socket")
            elif proc.returncode != 0:
                out["error"] = f"exited {proc.returncode}: {proc.stderr.strip()[-300:]}"
            elif len(proc.stdout) > MAX_STDOUT:
                out["error"] = f"its output is over {MAX_STDOUT} characters"
            else:
                try:
                    out.update(ok=True, output=json.loads(proc.stdout))
                except ValueError:
                    out["error"] = "its output is not JSON"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    _record({"at": hand.now(), "name": name, "ok": out["ok"], "exit": out.get("exit"),
             "killed": bool(out.get("killed")), "secs": round(time.monotonic() - started, 2)})
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: datajob.py BRAIN NAME < inputs.json"}))
        return 2
    got = run(Path(argv[0]), argv[1], sys.stdin.read())
    if got["ok"]:
        print(json.dumps(got["output"], ensure_ascii=False))
        return 0
    print(json.dumps({k: v for k, v in got.items() if k != "output"}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
