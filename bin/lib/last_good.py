"""The last-good boot (docs/evolve.md, "Phase C", restart): when the doctor
or the boot fails on a checkout bin/evolve just promoted, bin/spine boots the
commit that ran before it.

    python3 bin/lib/last_good.py record ROOT STATE COMMIT [GEN]
    python3 bin/lib/last_good.py fallback ROOT STATE REASON
    python3 bin/lib/last_good.py status STATE

`record` writes STATE/last-good ({commit, gen, at}): bin/evolve calls it
with the outgoing commit just before it restarts the core on a promotion.

`fallback` is bin/spine's: it checks out STATE/last-good's commit, detached,
in ROOT and records STATE/spine-fallback.json ({from, to, reason, at}). It
exits 0 when it switched (bin/spine then boots again, once), and 1 when there
is nothing to fall back to: no last-good, the checkout already at it, a
fallback already done for it, tracked edits in the checkout (never thrown
away), or a lab spine (SPINE_LAB=1). Only a promotion writes last-good, and
promotion is off unless EVOLVE_AUTO_PROMOTE=1, so a checkout that never
promoted never falls back.

While STATE/spine-fallback.json exists, bin/evolve starts no generation and
evolve-watch opens the revert pull request. The operator clears it (deletes
the file) once main is good again.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


def iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
                          capture_output=True, text=True)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def record(root: Path, state: Path, commit: str, gen: str | None = None) -> dict:
    full = _git(root, "rev-parse", "--verify", f"{commit}^{{commit}}")
    if full.returncode != 0:
        raise SystemExit(f"last-good: {commit} is not a commit in {root}")
    value = {"commit": full.stdout.strip(), "gen": gen, "at": iso()}
    _write(state / "last-good", value)
    return value


def fallback(root: Path, state: Path, reason: str) -> tuple[bool, str]:
    if os.environ.get("SPINE_LAB") == "1":
        return False, "a lab spine never falls back"
    good = _read(state / "last-good")
    if not good or not isinstance(good.get("commit"), str):
        return False, "no state/last-good"
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        return False, f"{root} is not a git checkout"
    head_sha = head.stdout.strip()
    if head_sha == good["commit"]:
        return False, "the checkout is already at last-good"
    done = _read(state / "spine-fallback.json")
    if done and done.get("to") == good["commit"]:
        return False, "already fell back to last-good once"
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0 or dirty.stdout.strip():
        return False, "the checkout has tracked edits; not throwing them away"
    switched = _git(root, "checkout", "-q", "--detach", good["commit"])
    if switched.returncode != 0:
        return False, f"git checkout failed: {switched.stderr.strip()[-200:]}"
    _write(state / "spine-fallback.json", {"from": head_sha, "to": good["commit"], "gen": good.get("gen"),
                                          "reason": reason[:300], "at": iso()})
    return True, f"checked out last-good {good['commit'][:12]} (was {head_sha[:12]})"


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "record":
        print(json.dumps(record(Path(argv[1]), Path(argv[2]), argv[3], argv[4] if len(argv) > 4 else None)))
        return 0
    if len(argv) == 4 and argv[0] == "fallback":
        ok, detail = fallback(Path(argv[1]), Path(argv[2]), argv[3])
        print(detail, file=sys.stderr)
        return 0 if ok else 1
    if len(argv) == 2 and argv[0] == "status":
        state = Path(argv[1])
        print(json.dumps({"last_good": _read(state / "last-good"), "fallback": _read(state / "spine-fallback.json")}))
        return 0
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
