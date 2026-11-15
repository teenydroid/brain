"""Deploy on merge: after the babysitter self-merges Teeny's evolve pull
request (actions.merge), the live checkout (TEENY_ROOT, the one bin/spine
runs) is brought to the merge. Queued by the merge in
state/deploy-pending.json and run by the sweep, last, every 20 minutes
until it is done. Off with BABYSIT_SELF_MERGE.

The steps, in bin/lib/live_checkout.py's order (the one bin/evolve's
LiveRestarter takes on a promotion):

  1 wait     while a generation, evolve-watch or an eval holds its run
             lock (state/locks/{evolve,evolve-watch,eval}.lock): a restart
             would end the container it runs in. Nothing moves; the next
             sweep tries again
  2 prepare  tracked edits, a fetch that fails, or a history that cannot
             fast-forward refuse, and say why; the pending deploy stays for
             the operator to clear the way (nothing is reset or forced)
  3 last-good  the outgoing commit (state/last-good), when a restart follows,
             so a boot that fails on the merge falls back (bin/spine)
  4 fast-forward  `git merge --ff-only` to the merge
  5 submodules  `git submodule update --init --recursive` for a gitlink the
             merge moved
  6 seed-cron  `bin/seed-cron` when brain/cron.toml or evals/cron.pinned.toml
             changed and no restart follows (bin/spine reseeds at boot)
  7 restart  TERM to bin/spine (state/spine.pid) when the merge touched
             something read at boot (`plan`); compose starts it again and
             the doctor gates the boot

Under DRY_RUN nothing moves: the plan is recorded and the deploy stays
pending. Every step that happens is a `babysit_actions` row (`deploy_*`),
with commits, counts and rule names, never a path or a word from the pull
request; a wait or a refusal is recorded once per reason, not every sweep.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import hand
from . import ledger, policy

Json = dict[str, Any]

PENDING = "deploy-pending.json"
#: The runs a restart must never cut off: a generation (bin/evolve run),
#: evolve-watch, and the targeted eval of an evolve PR.
BUSY_LOCKS = ("evolve", "evolve-watch", "eval")
SEED_TIMEOUT = 240

#: Read once, at boot, by bin/spine or the core it starts: a change restarts.
#: bin/lib/prompts.py builds the speaking agents' prompts from the first six
#: and brain/agents/; the core loads its agents and skills at boot;
#: bin/lib/inbound_config.py writes the mentions and mail playbooks into the
#: core's config (`boot_playbooks`); bin/lib/core_config.py reads deploy/
#: and hands/lib/teeny_hands/websites.py; bin/ and spine/ are the spine itself.
BOOT = (
    "brain/SOUL.md", "brain/CONTEXT.md", "brain/DRIVE.md", "brain/CHARTER.md",
    "brain/narrative/LORE.md", "brain/narrative/ARCS.md",
    "brain/agents/**", "brain/skills/**",
    "hands/lib/teeny_hands/websites.py",
    "bin/**", "deploy/**", "spine/**", "vendor/**", ".gitmodules",
)
#: Read fresh by every run that uses them: bin/act reads its playbook each
#: run (bin/lib/playbook.py), every hand is a process of its own, bin/eval
#: and bin/seed-cron read evals/ when they run. Anything in neither list
#: restarts, so a path nobody thought about is never left half-deployed.
PER_RUN = ("brain/playbooks/**", "brain/narrative/chronicle/**", "brain/cron.toml", "hands/**", "evals/**",
           "docs/**", "README.md")
#: What bin/seed-cron makes the core's cron match.
RESEED = ("brain/cron.toml", "evals/cron.pinned.toml")


def _lib(name: str):
    """A module from this checkout's bin/lib (the code, not TEENY_ROOT)."""
    path = policy.CHECKOUT / "bin" / "lib" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"teeny_babysit_{name}", path)
    if spec is None or spec.loader is None:
        raise hand.HandError(f"bin/lib/{name}.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def _live():
    return _lib("live_checkout")


@lru_cache(maxsize=None)
def _last_good():
    return _lib("last_good")


@lru_cache(maxsize=None)
def boot_playbooks() -> tuple[str, ...]:
    """The playbooks bin/lib/inbound_config.py writes into the core's
    config at boot, from its own table so the two never disagree."""
    return tuple(f"brain/playbooks/{source[2]}.md" for source in _lib("inbound_config").SOURCES)


def plan(paths: list[str]) -> Json:
    """What a change to PATHS needs: a reseed, a restart, and which of
    BOOT's rules (or `unlisted`) make the restart."""
    because: set[str] = set()
    for path in paths:
        hit = next((p for p in (*boot_playbooks(), *BOOT) if policy._match(path, p)), None)
        if hit is None and not any(policy._match(path, p) for p in PER_RUN):
            hit = "unlisted"
        if hit is not None:
            because.add(hit)
    reseed = any(policy._match(path, p) for path in paths for p in RESEED)
    return {"restart": bool(because), "because": sorted(because), "reseed": reseed}


# --- the pending deploy ----------------------------------------------------------------------


def _path() -> Path:
    return hand.state_dir() / PENDING


def _read() -> Json | None:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("sha"), str) else None


def _write(value: Json) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _clear() -> None:
    try:
        _path().unlink()
    except FileNotFoundError:
        pass


def request(repo: str, number: int, sha: str) -> Json:
    """Queue a deploy of SHA, the merge of NUMBER. A newer merge replaces
    an older one still pending: fast-forwarding to it brings both."""
    old = _read() or {}
    value = {"repo": repo, "number": number, "sha": sha, "since": old.get("since", hand.now()),
             "numbers": sorted({*old.get("numbers", []), number})}
    _write(value)
    return value


def _once(pending: Json, verb: str, reason: str, **detail: Any) -> None:
    """A wait or refusal row, only when the reason is new for this deploy."""
    if pending.get("last") == [verb, reason]:
        return
    pending["last"] = [verb, reason]
    _write(pending)
    ledger.record_action(verb, pending["repo"], int(pending["number"]), pending["sha"], reason=reason, **detail)


# --- the deploy -----------------------------------------------------------------------------


def run() -> Json | None:
    """The pending deploy, as far as it can go now; None with none pending."""
    pending = _read()
    if pending is None:
        return None
    sha, repo, number = pending["sha"], pending["repo"], int(pending["number"])
    out: Json = {"sha": sha, "number": number}
    if not policy.self_merge_on():
        return {**out, "skipped": "BABYSIT_SELF_MERGE is off; the deploy waits"}
    live, state, checkout = _live(), hand.state_dir(), hand.root()
    if not policy.is_brain(repo):
        _clear()
        return {**out, "skipped": "only teenydroid/brain is the live checkout"}

    if hand.dry_run():
        have = live.git(checkout, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
        steps = plan(_changed(checkout, "HEAD", sha)) if have else None
        _once(pending, "deploy_dry_run", "dry_run", plan=steps)
        return {**out, "dry_run": True, "would_fetch": not have, "plan": steps}

    busy = [name for name in BUSY_LOCKS if live.lock_held(state, name)]
    if busy:
        _once(pending, "deploy_wait", "busy:" + ",".join(busy))
        return {**out, "waiting": busy}

    if live.git(checkout, "merge-base", "--is-ancestor", sha, "HEAD").returncode == 0:
        _clear()
        return {**out, "deployed": sha, "already": True}
    head, error = live.prepare(checkout, sha)
    if error:
        _once(pending, "deploy_refused", error)
        return {**out, "refused": error}

    paths = _changed(checkout, head, sha)
    steps = plan(paths)
    if steps["restart"]:
        _last_good().record(checkout, state, head, f"pr-{number}")
    error = live.fast_forward(checkout, sha)
    if error:
        _once(pending, "deploy_refused", error)
        return {**out, "refused": error}
    ledger.record_action("deploy_ff", repo, number, sha, **{"from": head, "files": len(paths),
                                                           "numbers": pending.get("numbers", [number])})
    out.update(deployed=sha, restart=steps["restart"], because=steps["because"], reseed=False)

    modules = _gitlinks(checkout, head, sha)
    if modules:
        done = live.git(checkout, "submodule", "update", "--init", "--recursive")
        ledger.record_action("deploy_submodules", repo, number, sha, ok=done.returncode == 0, count=len(modules))
        if done.returncode != 0:
            # The checkout is at the merge with stale submodules: the boot
            # would run the old code, so no restart until a person looks.
            _once(pending, "deploy_refused", "git submodule update failed")
            return {**out, "refused": "git submodule update failed"}

    if steps["reseed"] and not steps["restart"]:
        ok, detail = _seed_cron(checkout)
        ledger.record_action("deploy_seed_cron", repo, number, sha, ok=ok, detail=detail)
        out["reseed"] = True
        if not ok:
            out["reseed_error"] = detail

    _clear()
    if steps["restart"]:
        # Recorded before the TERM: the restart ends this process's container.
        ledger.record_action("deploy_restart", repo, number, sha, because=steps["because"])
        error = live.term_spine(state)
        if error:
            ledger.record_action("deploy_restart_failed", repo, number, sha, reason=error)
            out["restart_error"] = error
    return out


def _changed(checkout: Path, old: str, new: str) -> list[str]:
    got = _live().git(checkout, "diff", "--name-only", "--no-renames", old, new)
    return [line for line in got.stdout.splitlines() if line]


def _gitlinks(checkout: Path, old: str, new: str) -> list[str]:
    """The submodules the merge moved (mode 160000 on either side)."""
    got = _live().git(checkout, "diff", "--raw", "--no-renames", old, new)
    out = []
    for line in got.stdout.splitlines():
        meta, _, path = line.partition("\t")
        modes = meta.split()[:2]
        if any(m.lstrip(":") == "160000" for m in modes):
            out.append(path)
    return out


def _seed_cron(checkout: Path) -> tuple[bool, str]:
    try:
        done = subprocess.run([str(checkout / "bin" / "seed-cron")], cwd=checkout, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=SEED_TIMEOUT,
                              env={**os.environ, "TEENY_ROOT": str(checkout)})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__
    return done.returncode == 0, f"exit {done.returncode}"
