"""The lab spine: a second OpenHuman core over a candidate's worktree, for
bin/evolve (docs/evolve.md, "Phase C").

A lab core is `bin/spine` run with its own everything:

  TEENY_ROOT, OPENHUMAN_WORKSPACE   the worktree and its brain/
  TEENY_STATE, TEENY_SECRETS        LAB/state and LAB/secrets, never the live ones
  TEENY_STORAGE                     LAB/storage.db, a fresh store
  OPENHUMAN_CORE_PORT               a port of its own (EVOLVE_LAB_PORT and up)
  DRY_RUN=1                         forced, and LAB/secrets/teeny.env says so too
  SPINE_LAB=1                       bin/spine seeds no cron, never falls back
                                    to state/last-good, and refuses DRY_RUN=0
  SPINE_LAB_EDIT=1                  (the evolver's lab only) the agents' file
                                    tools may write the worktree

LAB/secrets holds only what a boot needs: a fresh core token and master key,
`teeny.env` with DRY_RUN=1 and nothing else (no hand has a key, so nothing can
post, pay or push even if DRY_RUN were lost), and the model lines of the live
`spine.env` (LADDER_URL, LADDER_API_KEY, SPINE_MODEL and the two tuning
knobs). The memory store (CORTEX_*) is left out: a lab never writes Teeny's
long-term memory.

`start` waits for /health and returns a `Lab`; `stop` sends TERM to the
spine's process group and KILL after a grace period. The command is
EVOLVE_LAB_SPINE (default: this checkout's bin/spine), which the tests swap
for a stand-in that answers /health.
"""

from __future__ import annotations

import os
import secrets as _secrets
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]

#: The lines of the live spine.env a lab core needs: the model and nothing else.
SPINE_KEYS = ("LADDER_URL", "LADDER_API_KEY", "SPINE_MODEL", "SPINE_NATIVE_TOOLS", "SPINE_CONTEXT_WINDOW")
#: What a lab spine must never inherit from the process that starts it.
DROP_PREFIXES = ("OPENHUMAN_", "TEENY_", "CORTEX_", "GITHUB_", "X_", "MOLTBOOK_", "WALLET_", "TELEGRAM_",
                 "AGENTMAIL_", "RECEIPTS_", "DIARY_", "SPINE_", "LADDER_", "GH_")
#: OPENHUMAN_CORE_BIN is the one exception: the image sets it, and bin/spine
#: needs it to find the core.
KEEP = ("OPENHUMAN_CORE_BIN",)


class LabError(Exception):
    pass


@dataclass
class Lab:
    name: str
    tree: Path
    folder: Path
    port: int
    token: str
    proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def log(self) -> Path:
        return self.folder / "spine.log"

    def env(self) -> dict[str, str]:
        """What a client of this lab core needs: its URL and its token."""
        return {"OPENHUMAN_CORE_URL": self.url, "OPENHUMAN_CORE_TOKEN": self.token}


def _read_env(path: Path) -> list[tuple[str, str]]:
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        out.append((key.strip().removeprefix("export ").strip(), value.strip()))
    return out


def _private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


def prepare(folder: Path, live_secrets: Path) -> str:
    """LAB/secrets and LAB/state; returns the lab core's token. The token
    and the master key are minted once per folder and kept: the core's
    keyring sits in the worktree's brain, under that key, and one
    candidate's lab boots twice (the evolver's, then the evaluation's)."""
    secrets = folder / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    os.chmod(secrets, 0o700)
    (folder / "state").mkdir(parents=True, exist_ok=True)
    for name in ("core.token", "openhuman.key"):
        path = secrets / name
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            _private(path, _secrets.token_hex(32) + "\n")
    token = (secrets / "core.token").read_text(encoding="utf-8").strip()
    _private(secrets / "teeny.env", "# A lab core: nothing it runs may act for real.\nDRY_RUN=1\n")
    model = [f"{k}={v}" for k, v in _read_env(live_secrets / "spine.env") if k in SPINE_KEYS]
    _private(secrets / "spine.env", "\n".join(model) + ("\n" if model else ""))
    return token


def spine_command() -> list[str]:
    given = os.environ.get("EVOLVE_LAB_SPINE")
    return [given] if given else [str(CODE / "bin" / "spine")]


def lab_env(lab: Lab, edit: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in KEEP or not k.startswith(DROP_PREFIXES)}
    env.pop("DRY_RUN", None)
    env.update(
        TEENY_ROOT=str(lab.tree),
        OPENHUMAN_WORKSPACE=str(lab.tree / "brain"),
        TEENY_STATE=str(lab.folder / "state"),
        TEENY_SECRETS=str(lab.folder / "secrets"),
        TEENY_STORAGE=str(lab.folder / "storage.db"),
        OPENHUMAN_CORE_HOST="127.0.0.1",
        OPENHUMAN_CORE_PORT=str(lab.port),
        DRY_RUN="1",
        SPINE_LAB="1",
    )
    if edit:
        env["SPINE_LAB_EDIT"] = "1"
    return env


def healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout) as resp:  # noqa: S310 - loopback only
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def start(name: str, tree: Path, folder: Path, port: int, live_secrets: Path, boot_secs: float,
          edit: bool = False) -> Lab:
    """Boots a lab spine over TREE and waits for it to answer. Raises
    LabError (after stopping it) when it exits or does not answer in time."""
    if healthy(f"http://127.0.0.1:{port}", 0.5):
        raise LabError(f"port {port} already answers /health: another core is there")
    folder.mkdir(parents=True, exist_ok=True)
    lab = Lab(name=name, tree=tree, folder=folder, port=port, token=prepare(folder, live_secrets))
    # Every boot starts from an empty store: nothing one turn left (the
    # evolver's transcript) reaches the next (the evaluation's drafts).
    for leftover in folder.glob("storage.db*"):
        leftover.unlink()
    log = lab.log.open("ab")
    try:
        lab.proc = subprocess.Popen(spine_command(), cwd=str(tree), env=lab_env(lab, edit), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        raise LabError(f"the lab spine did not start: {exc}") from None
    finally:
        log.close()
    deadline = time.monotonic() + boot_secs
    while time.monotonic() < deadline:
        if lab.proc.poll() is not None:
            tail = _tail(lab.log)
            stop(lab)
            raise LabError(f"the lab spine exited with {lab.proc.returncode} before it answered: {tail}")
        if healthy(lab.url):
            return lab
        time.sleep(0.5)
    stop(lab)
    raise LabError(f"the lab spine did not answer /health in {int(boot_secs)} s: {_tail(lab.log)}")


def stop(lab: Lab, grace: float = 30.0) -> None:
    """TERM to the spine's process group (bin/spine forwards it to the
    core), then KILL what is left after GRACE seconds."""
    proc = lab.proc
    if proc is None:
        return
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
        if proc.poll() is not None:
            break
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            continue
    # Anything the core started in its group (a shell job) goes with it.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _tail(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return " ".join(lines[-3:])[-300:]
