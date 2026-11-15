"""The hand-test sandbox (docs/evolve.md, "The hand-test sandbox"): where a
candidate's hand tests, code a model wrote, run. Never on the box as they
are. Two ways, tried in order by bin/lib/evolve.py:

  bwrap     bubblewrap on this host, every namespace unshared, the system
            read-only, only a throwaway copy of the candidate writable.
            Needs user namespaces, which the spine's container (Docker's
            default seccomp profile) does not have.
  sidecar   the `sandbox` compose service (deploy/compose.yaml): the same
            image with no network, a read-only root, no capabilities, and
            nothing of the checkout mounted but state/sandbox/, where
            bin/evolve leaves a job (a copy of the candidate and the test
            arguments) and bin/sandbox-runner leaves its result.

Neither available: nothing runs, and the gate says the tests could not be
run (a finding).

    python3 bin/lib/sandbox.py status JOBS_DIR   which one works here
"""

from __future__ import annotations

import contextlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

#: How much of a run's output is kept; past it the run is killed.
MAX_OUTPUT_BYTES = 256 * 1024
#: A result file bigger than this is not read.
MAX_RESULT_BYTES = MAX_OUTPUT_BYTES + 16 * 1024
#: The runner touches JOBS_DIR/ready this often; older than READY_SECS, it
#: is taken for gone.
HEARTBEAT_SECS = 10
READY_SECS = 60
#: How long a client waits past the job's own limit for the runner to pick
#: it up and answer.
PICKUP_SECS = 120
BWRAP = "bwrap"
_BWRAP_PROBE: dict[str, bool] = {}


def run_contained(argv: list[str], timeout: float, max_bytes: int = MAX_OUTPUT_BYTES,
                  env: dict[str, str] | None = None, cwd: str | None = None) -> tuple[int | None, str, str | None]:
    """ARGV in a session of its own, stdout and stderr together. At TIMEOUT,
    or once it has written more than MAX_BYTES, its whole process group is
    killed. (returncode or None, the output it wrote (cut at MAX_BYTES),
    None or "timeout" or "output")."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True, env=env, cwd=cwd)
    assert proc.stdout is not None
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    chunks: list[bytes] = []
    size, why = 0, None
    deadline = time.monotonic() + timeout
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                why = "timeout"
                break
            if not sel.select(timeout=min(left, 1.0)):
                if proc.poll() is not None:
                    break
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                why = "output"
                break
    finally:
        sel.close()
        if why:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        proc.stdout.close()
        code = proc.wait()
    out = b"".join(chunks)[:max_bytes].decode("utf-8", "replace")
    return (None if why else code), out, why


def sandbox_env(root: str) -> dict[str, str]:
    """The environment a candidate's tests get: nothing of the caller's."""
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "TMPDIR": "/tmp",
            "TEENY_ROOT": root, "TEENY_STATE": f"{root}/state", "TEENY_SECRETS": f"{root}/secrets",
            "TEENY_HANDS": f"{root}/hands", "OPENHUMAN_WORKSPACE": f"{root}/brain", "DRY_RUN": "1",
            "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}


def bwrap_ready(binary: str | None = None) -> tuple[bool, str]:
    """Whether bwrap is here and can make its namespaces (once per process)."""
    binary = binary or shutil.which(BWRAP)
    if not binary:
        return False, f"no {BWRAP}"
    if binary not in _BWRAP_PROBE:
        try:
            done = subprocess.run([binary, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/",
                                   "--dev", "/dev", "--proc", "/proc", "--", "/bin/true"],
                                  capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
            _BWRAP_PROBE[binary] = done.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _BWRAP_PROBE[binary] = False
    return (True, "") if _BWRAP_PROBE[binary] else (False, f"{BWRAP} cannot make a namespace here")


# --- the sidecar: a job folder in, a result file out ------------------------------------------------


def sidecar_ready(jobs: Path) -> tuple[bool, str]:
    """Whether the runner is there: JOBS/ready, a regular file, touched in
    the last READY_SECS."""
    try:
        st = (jobs / "ready").lstat()
    except OSError:
        return False, "no sandbox runner (state/sandbox/ready)"
    if not (st.st_mode & 0o170000) == 0o100000:
        return False, "state/sandbox/ready is not a file"
    if time.time() - st.st_mtime > READY_SECS:
        return False, f"the sandbox runner has not answered for {int(time.time() - st.st_mtime)} s"
    return True, ""


def _read_capped(path: Path, cap: int) -> bytes | None:
    """PATH's bytes, never through a link and never more than CAP; None when
    it is not a regular file of at most CAP bytes."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if (st.st_mode & 0o170000) != 0o100000 or st.st_size > cap:
            return None
        return os.read(fd, cap + 1)[:cap]
    finally:
        os.close(fd)


def submit(jobs: Path, fill: Callable[[Path], None], args: list[str], timeout: float,
           wait: Callable[[float], None] = time.sleep) -> tuple[int | None, str, str | None]:
    """One job for the runner: FILL writes the candidate's copy into the
    job's tree/, then job.json says what to run (unittest ARGS) and for how
    long. Waits for done/<id>.json. (returncode or None, output, why)."""
    job_id = uuid.uuid4().hex
    folder = jobs / "jobs" / job_id
    (jobs / "done").mkdir(parents=True, exist_ok=True)
    folder.mkdir(parents=True)
    result = jobs / "done" / f"{job_id}.json"
    try:
        fill(folder / "tree")
        tmp = folder / "job.json.tmp"
        tmp.write_text(json.dumps({"id": job_id, "args": list(args), "timeout": timeout}), encoding="utf-8")
        os.replace(tmp, folder / "job.json")
        deadline = time.monotonic() + timeout + PICKUP_SECS
        while time.monotonic() < deadline:
            raw = _read_capped(result, MAX_RESULT_BYTES)
            if raw is not None:
                try:
                    got = json.loads(raw)
                except ValueError:
                    return None, "", "the sandbox runner's answer did not parse"
                if not isinstance(got, dict) or got.get("id") != job_id:
                    return None, "", "the sandbox runner answered another job"
                code = got.get("code")
                return (code if isinstance(code, int) and not isinstance(code, bool) else None,
                        str(got.get("output") or "")[:MAX_OUTPUT_BYTES], got.get("why") if got.get("why") else None)
            wait(0.5)
        return None, "", "the sandbox runner never answered"
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        with contextlib.suppress(OSError):
            result.unlink()


def serve_one(jobs: Path, work: Path, reap: bool = False) -> bool:
    """The runner's step: the oldest waiting job, run on its own copy under
    WORK, every process it left killed (REAP: all of this user's but the
    runner, in the sidecar), then its result. False when there was none."""
    waiting = sorted((p for p in (jobs / "jobs").glob("*/job.json") if p.is_file() and not p.is_symlink()),
                     key=lambda p: p.stat().st_mtime)
    if not waiting:
        return False
    folder = waiting[0].parent
    try:
        job = json.loads(_read_capped(folder / "job.json", 64 * 1024) or b"{}")
    except ValueError:
        job = {}
    job_id = str(job.get("id") or "")
    if job_id != folder.name or not isinstance(job.get("args"), list):
        shutil.rmtree(folder, ignore_errors=True)
        return True
    copy = work / job_id
    shutil.rmtree(copy, ignore_errors=True)
    code, out, why = None, "", None
    try:
        shutil.copytree(folder / "tree", copy, symlinks=True)
        timeout = float(job.get("timeout") or 300)
        code, out, why = run_contained([sys.executable, "-m", "unittest", *[str(a) for a in job["args"]]],
                                       min(timeout, 1800), env=sandbox_env(str(copy)), cwd=str(copy))
    except OSError as exc:
        why = f"the runner could not run it: {exc}"[:300]
    finally:
        if reap and os.getpid() != 1:
            # Anything the tests left behind (a daemon, a setsid child) goes
            # before the result is written, so nothing can write over it.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(-1, signal.SIGKILL)
        shutil.rmtree(copy, ignore_errors=True)
    done = jobs / "done"
    done.mkdir(parents=True, exist_ok=True)
    tmp = done / f".{job_id}.{uuid.uuid4().hex}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"id": job_id, "code": code, "output": out, "why": why}, fh)
    os.replace(tmp, done / f"{job_id}.json")
    shutil.rmtree(folder, ignore_errors=True)
    return True


def serve(jobs: Path, work: Path, reap: bool = True) -> None:
    """The runner: a heartbeat in JOBS/ready, and every job in turn."""
    (jobs / "jobs").mkdir(parents=True, exist_ok=True)
    beat = 0.0
    while True:
        if time.monotonic() - beat >= HEARTBEAT_SECS:
            (jobs / "ready").unlink(missing_ok=True)
            (jobs / "ready").write_text(str(int(time.time())) + "\n", encoding="utf-8")
            beat = time.monotonic()
        if not serve_one(jobs, work, reap=reap):
            time.sleep(1)


def status(jobs: Path) -> tuple[bool, str]:
    """Which sandbox works here, for bin/doctor."""
    ok, why = bwrap_ready()
    if ok:
        return True, "bwrap: hand tests run in a namespace sandbox here"
    side, side_why = sidecar_ready(jobs)
    if side:
        return True, "sidecar: hand tests run in the sandbox service"
    return False, (f"none ({why}; {side_why}): a candidate that changes hand code is sent back, never shipped "
                   "from here")


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "status":
        ok, detail = status(Path(argv[1]))
        print(detail)
        return 0 if ok else 1
    print("usage: sandbox.py status JOBS_DIR", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
