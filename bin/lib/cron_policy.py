"""Validate brain/cron.toml (plus the pinned jobs) and flatten it to the JSON
bin/seed-cron reconciles. Run by bin/seed-cron and bin/doctor; never by a
model.

    python3 bin/lib/cron_policy.py CRON_TOML PINNED_TOML [DEFAULT_TZ]

Prints [{name, kind, expr, tz, command, agent, prompt, enabled}] on success
(kind "shell" with a command, or "agent" with an agent and a prompt); exits
non-zero with the reason on the first rule a file breaks.

Teeny owns cron.toml and may add, remove or reschedule any job in it, its
heartbeat included (docs/architecture.md, "Evolution"). What it may not do is
written here, in code it cannot edit:

- a brain job runs `bin/heartbeat` or `bin/act <playbook>`, and the playbook
  is a file in the brain's playbooks/; anything new it wants to do is a
  playbook over a hand, never a new script for cron to run;
- no job fires more often than every MIN_GAP_MINUTES;
- at most MAX_JOBS jobs in all, and at most MAX_ACT_RUNS_PER_DAY runs of
  bin/act a day across every job;
- the pinned jobs (PINNED_TOML, frozen with the evals) are always merged in,
  and a brain job may not take one of their names;
- an agent job (`agent = "..."` and `prompt = "..."` instead of `command`)
  runs one of AGENT_JOB_AGENTS, whose definition in the brain's agents/
  sets `scheduled_actions = true` and names its tools explicitly: OpenHuman
  lets such a run act without a person's approval (docs/architecture.md,
  "Scheduled agents"), so only the agents written for it may be scheduled,
  and at most MAX_AGENT_RUNS_PER_DAY agent runs a day across every job.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

MIN_GAP_MINUTES = 5
MAX_JOBS = 24
MAX_ACT_RUNS_PER_DAY = 400
BRAIN_COMMANDS = ("bin/heartbeat", "bin/act")
#: The agents a cron job may run. Each acts unattended, so each is written
#: for it (brain/agents/<id>.toml) and reviewed; a new one is added here.
AGENT_JOB_AGENTS = ("builder", "operator")
MAX_AGENT_RUNS_PER_DAY = 48
MAX_PROMPT_CHARS = 4000
PLAYBOOK_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


class PolicyError(Exception):
    pass


def _field(spec: str, low: int, high: int, where: str) -> list[int]:
    """One cron field (minute or hour) as the values it matches."""
    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) == 0:
                raise PolicyError(f"{where}: bad step in {spec!r}")
            step = int(step_s)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise PolicyError(f"{where}: bad range in {spec!r}")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = int(part)
            end = high if step > 1 else start
        else:
            raise PolicyError(f"{where}: unsupported field {spec!r} (use numbers, *, ranges, lists and steps)")
        if not (low <= start <= end <= high):
            raise PolicyError(f"{where}: {spec!r} is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    return sorted(values)


def fire_minutes(expr: str, where: str) -> list[int]:
    """The minutes of a day the expression fires at (day fields ignored, so
    this is the most it can fire on any one day)."""
    minute, hour = expr.split()[:2]
    return sorted(h * 60 + m for h in _field(hour, 0, 23, where) for m in _field(minute, 0, 59, where))


def min_gap(minutes: list[int]) -> int:
    if len(minutes) < 2:
        return 24 * 60
    gaps = [b - a for a, b in zip(minutes, minutes[1:])]
    gaps.append(minutes[0] + 24 * 60 - minutes[-1])
    return min(gaps)


def _command(job: dict, path: Path, pinned: bool, where: str) -> str:
    command = str(job.get("command", "")).strip()
    # Operator scripts only: what cron runs has no model in the loop, so
    # it must be something reviewed in this repository.
    if not re.fullmatch(r"bin/[A-Za-z0-9._/-]+( .*)?", command) or ".." in command.split()[0]:
        raise PolicyError(f"{where}: command must run a script under bin/, got {command!r}")
    argv = command.split()
    if not pinned:
        if argv[0] not in BRAIN_COMMANDS:
            raise PolicyError(f"{where}: a brain job runs {' or '.join(BRAIN_COMMANDS)}, got {argv[0]!r}")
        if argv[0] == "bin/act":
            if len(argv) != 2 or not PLAYBOOK_NAME.fullmatch(argv[1]):
                raise PolicyError(f"{where}: bin/act takes one playbook name, got {command!r}")
            if not (path.parent / "playbooks" / f"{argv[1]}.md").is_file():
                raise PolicyError(f"{where}: no playbook {argv[1]!r} in {path.parent / 'playbooks'}")
    return command


def _agent_job(job: dict, brain: Path, where: str) -> tuple[str, str]:
    """An agent job's agent and prompt, after the rules: an agent written to
    act unattended, defined in the brain the core boots, with a prompt."""
    agent = job.get("agent")
    prompt = job.get("prompt")
    if not isinstance(agent, str) or not isinstance(prompt, str):
        raise PolicyError(f"{where}: an agent job needs both agent and prompt, as strings")
    if agent not in AGENT_JOB_AGENTS:
        raise PolicyError(f"{where}: agent {agent!r} may not run from cron (only {', '.join(AGENT_JOB_AGENTS)})")
    prompt = prompt.strip()
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        raise PolicyError(f"{where}: prompt must be 1..{MAX_PROMPT_CHARS} characters")
    definition = brain / "agents" / f"{agent}.toml"
    try:
        with open(definition, "rb") as fh:
            doc = tomllib.load(fh)
    except FileNotFoundError:
        raise PolicyError(f"{where}: no agent {agent!r} in {brain / 'agents'}") from None
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"{where}: {definition} does not parse: {exc}") from None
    if doc.get("id") != agent:
        raise PolicyError(f"{where}: {definition} has id {doc.get('id')!r}, not {agent!r}")
    if doc.get("scheduled_actions") is not True:
        raise PolicyError(f"{where}: agent {agent!r} does not set scheduled_actions = true, so a cron run could not act")
    named = (doc.get("tools") or {}).get("named")
    if not isinstance(named, list) or not named or not all(isinstance(t, str) and t and t != "*" for t in named):
        raise PolicyError(f"{where}: agent {agent!r} must name its tools explicitly ([tools] named = [...])")
    return agent, prompt


def _jobs(path: Path, default_tz: str, pinned: bool, brain: Path | None = None) -> list[dict]:
    brain = brain or path.parent
    with open(path, "rb") as fh:
        doc = tomllib.load(fh)
    unknown = set(doc) - {"tz", "jobs"}
    if unknown:
        raise PolicyError(f"{path}: unknown top-level keys: {', '.join(sorted(unknown))}")
    tz = doc.get("tz") or default_tz
    out = []
    for name, job in sorted((doc.get("jobs") or {}).items()):
        where = f"{path}: jobs.{name}"
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise PolicyError(f"{where}: name must be lowercase letters, digits and dashes")
        extra = set(job) - {"schedule", "command", "agent", "prompt", "tz", "enabled"}
        if extra:
            raise PolicyError(f"{where}: unknown keys: {', '.join(sorted(extra))}")
        expr = str(job.get("schedule", "")).strip()
        if len(expr.split()) != 5:
            raise PolicyError(f"{where}: schedule must be a five-field cron expression, got {expr!r}")
        minutes = fire_minutes(expr, where)
        if min_gap(minutes) < MIN_GAP_MINUTES:
            raise PolicyError(f"{where}: {expr!r} fires more often than every {MIN_GAP_MINUTES} minutes")
        if "agent" in job or "prompt" in job:
            if "command" in job:
                raise PolicyError(f"{where}: a job runs a command or an agent, not both")
            agent, prompt = _agent_job(job, brain, where)
            command = None
        else:
            agent = prompt = None
            command = _command(job, path, pinned, where)
        enabled = job.get("enabled", True)
        if not isinstance(enabled, bool):
            raise PolicyError(f"{where}: enabled must be true or false")
        out.append({
            "name": f"teeny.{name}",
            "kind": "shell" if command is not None else "agent",
            "expr": expr,
            "tz": job.get("tz") or tz,
            "command": command,
            "agent": agent,
            "prompt": prompt,
            "enabled": enabled,
            "runs_per_day": len(minutes),
        })
    return out


def load(cron_toml: Path, pinned_toml: Path | None, default_tz: str = "") -> list[dict]:
    default_tz = default_tz or "UTC"
    jobs = _jobs(cron_toml, default_tz, pinned=False)
    pinned = (_jobs(pinned_toml, default_tz, pinned=True, brain=cron_toml.parent)
              if pinned_toml and pinned_toml.is_file() else [])
    taken = {j["name"] for j in pinned} & {j["name"] for j in jobs}
    if taken:
        raise PolicyError(f"{cron_toml}: {', '.join(sorted(taken))} is pinned in {pinned_toml} and cannot be redefined")
    jobs += pinned
    if len(jobs) > MAX_JOBS:
        raise PolicyError(f"{cron_toml}: {len(jobs)} jobs; the most is {MAX_JOBS}")
    act_runs = sum(j["runs_per_day"] for j in jobs
                   if j["enabled"] and j["kind"] == "shell" and j["command"].startswith("bin/act "))
    if act_runs > MAX_ACT_RUNS_PER_DAY:
        raise PolicyError(f"{cron_toml}: bin/act would run up to {act_runs} times a day; the most is {MAX_ACT_RUNS_PER_DAY}")
    agent_runs = sum(j["runs_per_day"] for j in jobs if j["enabled"] and j["kind"] == "agent")
    if agent_runs > MAX_AGENT_RUNS_PER_DAY:
        raise PolicyError(
            f"{cron_toml}: agent jobs would run up to {agent_runs} times a day; the most is {MAX_AGENT_RUNS_PER_DAY}")
    for j in jobs:
        del j["runs_per_day"]
    return jobs


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: cron_policy.py CRON_TOML PINNED_TOML [DEFAULT_TZ]", file=sys.stderr)
        return 2
    try:
        jobs = load(Path(argv[1]), Path(argv[2]), argv[3] if len(argv) == 4 else "")
    except (PolicyError, OSError, tomllib.TOMLDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(jobs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
