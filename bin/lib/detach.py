"""Starts a long cron job in the background, so its cron call returns at once.

    python3 bin/lib/detach.py --log tick/beat.log [--lock tick] -- COMMAND...

The core runs each cron shell job as one TinyBox call and gives up on that
call after 300 s (MODULE_CALL_TIMEOUT in the core's TinyBox adapter), whatever
`[cron] shell_timeout_secs` says (docs/oh-gaps.md, G12). A job that can run
longer is marked failed and retried while it is still running, and its
outcome never reaches cron history. So `bin/tick --detach` and `bin/reflect
--detach` start their work here instead:

- COMMAND runs in its own session (outside TinyBox's process group, so the
  call's cleanup does not take it down), with stdin from /dev/null and
  stdout and stderr appended to state/<--log>. A log past LOG_MAX_BYTES is
  first moved to <log>.1 (one generation kept).
- It prints one JSON line, `{"ok": true, "kind": "started", "pid": ...,
  "log": ...}`, and exits 0.
- With --lock NAME, a run still holding state/locks/NAME.lock means nothing
  starts: it prints `{"ok": true, "kind": "busy", ...}`. COMMAND must take
  that lock itself (bin/lib/tick.py and bin/lib/reflect.py do), so one
  started in the moment before the other took it still finds it held.

The job's own record (state/tick/runs.jsonl for a beat, the log for the
rest) is where its outcome lands.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("TEENY_ROOT") or CODE)
sys.path.insert(0, str(CODE / "hands" / "lib"))

from teeny_hands import hand  # noqa: E402

#: A log past this many bytes is moved to <log>.1 before the next run.
LOG_MAX_BYTES = 1_000_000


def _held(name: str) -> bool:
    try:
        with hand.lock(name, timeout=0):
            return False
    except hand.HandError as exc:
        if "lock" in str(exc):
            return True
        raise


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except FileNotFoundError:
        pass
    return open(path, "a", encoding="utf-8")


def start(log: str, command: list[str], lock: str | None = None) -> dict:
    state = hand.state_dir()
    path = state / log
    shown = str(path.relative_to(state.parent)) if path.is_relative_to(state.parent) else str(path)
    if lock and _held(lock):
        return {"ok": True, "kind": "busy", "skipped": f"the last {lock} is still running", "log": shown}
    with _open_log(path) as fh:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fh.write(f"--- {stamp} {' '.join(command)}\n")
        fh.flush()
        proc = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True)
    return {"ok": True, "kind": "started", "pid": proc.pid, "log": shown}


def main(argv: list[str]) -> int:
    log, lock = None, None
    while argv and argv[0] != "--":
        flag, value = argv[0], argv[1] if len(argv) > 1 else None
        if flag == "--log" and value:
            log = value
        elif flag == "--lock" and value:
            lock = value
        else:
            break
        argv = argv[2:]
    if not log or argv[:1] != ["--"] or len(argv) < 2:
        print("usage: detach.py --log STATE_RELATIVE_PATH [--lock NAME] -- COMMAND...", file=sys.stderr)
        return 64
    try:
        result = start(log, argv[1:], lock)
    except (OSError, hand.HandError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
