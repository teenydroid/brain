"""One agent definition's posture, for bin/doctor.

    python3 bin/lib/agent_check.py AGENT_TOML

Prints "id <id>" and exits 0 when the definition is what Teeny's design
needs; exits non-zero with the reason otherwise:

- every agent has an id and a when_to_use;
- the drafting agents (NO_TOOLS) have no tools at all and never act on a
  schedule: they read text strangers wrote;
- the drafting agents run at exactly NO_TOOLS_MAX_ITERATIONS (see there);
- only the agents written to act from cron (SCHEDULED) may set
  `scheduled_actions`, and those must set it, name their tools explicitly
  (never the wildcard), and leave out the tools their role must not have;
- each scheduled agent has the tools its desk needs (it reads its work from
  state/workbench/desk/ and writes its report there) and its role's own;
- each scheduled agent's prompt says how a run with nothing queued ends:
  no report, and its NOTHING_TO_DO line as the final message (an empty
  final message is a failed run to the harness, every time the cron fires);
- the evolver (EDITORS) names its tools, only file tools, and never acts
  on a schedule: it runs on bin/evolve's lab spine over a worktree;
- the builder's runs are rooted at the workbench (`scheduled_workspace`),
  so its sandboxed shell is granted that folder and never the checkout
  that holds brain/ and secrets/; no other agent picks a root;
- an agent whose prompt is a file has its instructions beside it
  (brain/agents/<id>.md), which bin/lib/prompts.py builds the file from.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

NO_TOOLS = ("teeny", "act", "judge", "teeny-mentions", "teeny-mail")
#: A drafting agent's turn is one model call: with no tools it cannot loop.
#: But the vendored harness pauses a session turn when it reaches
#: max_iterations even after a final answer (the CapPauser in
#: tinyagents-runtime turn_driver/driver.rs, drained as Paused by
#: settle_natural_finish in tinyagents-harness agent_loop/turn_control.rs),
#: so at 1 every turn is recorded as failed though its answer comes back.
#: The second iteration only absorbs that pause (or an output-retry nudge).
#: Drop to 1 once the harness stops pausing a finished turn.
NO_TOOLS_MAX_ITERATIONS = 2
#: Never in a scheduled agent: money, connectors, and any MCP server.
NEVER = ("wallet", "web3", "x402", "composio", "mcp")
#: agent -> (tools it must have, tool name prefixes it must not have)
SCHEDULED = {
    "builder": (("shell", "file_read", "file_write"), ("browser", *NEVER)),
    "operator": (("browser", "file_read", "file_write"), ("shell", "edit", "apply_patch", *NEVER)),
}

#: agent -> the only tools it may have. The evolver edits a lab worktree
#: and answers JSON; bin/evolve gates what it wrote.
EDITORS = {"evolver": ("file_read", "file_write", "edit", "glob", "grep", "list")}

#: agent -> the root its cron runs work in (OpenHuman's `scheduled_workspace`,
#: relative to the checkout). Unlisted agents must not set one.
WORKSPACE = {"builder": "state/workbench"}

#: agent -> the exact final message of a run with nothing queued.
NOTHING_TO_DO = {
    "builder": "nothing to do: no job waiting",
    "operator": "nothing to do: no task waiting",
}


def check(path: Path) -> str:
    with open(path, "rb") as fh:
        doc = tomllib.load(fh)
    for key in ("id", "when_to_use"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            raise SystemExit(f"needs {key}")
    agent = doc["id"]
    named = (doc.get("tools") or {}).get("named")
    scheduled = doc.get("scheduled_actions", False)
    source = doc.get("system_prompt")
    if isinstance(source, dict) and "file" in source:
        instructions = path.parent / f"{agent}.md"
        if not instructions.is_file() or not instructions.read_text(encoding="utf-8").strip():
            raise SystemExit(f"the {agent} agent's prompt is a file; its instructions belong in {instructions.name}")
    root = doc.get("scheduled_workspace")
    if root != WORKSPACE.get(agent):
        if agent in WORKSPACE:
            raise SystemExit(f"the {agent} agent must set scheduled_workspace = {WORKSPACE[agent]!r} "
                             "(its sandboxed shell is granted that folder, never the checkout)")
        raise SystemExit(f"only {', '.join(WORKSPACE)} may set scheduled_workspace, not {agent}")
    if agent in NO_TOOLS:
        if named != []:
            raise SystemExit(f"the {agent} agent must have no tools ([tools] named = [])")
        if scheduled:
            raise SystemExit(f"the {agent} agent must not set scheduled_actions")
        if doc.get("max_iterations") != NO_TOOLS_MAX_ITERATIONS:
            raise SystemExit(f"the {agent} agent must set max_iterations = {NO_TOOLS_MAX_ITERATIONS} "
                             "(1 trips the harness's cap pause on every turn; more is a loop it has no tools for)")
        return agent
    if agent in EDITORS:
        if scheduled:
            raise SystemExit(f"the {agent} agent must not set scheduled_actions")
        if not isinstance(named, list) or not named or "*" in named:
            raise SystemExit(f"the {agent} agent must name its tools explicitly ([tools] named = [...], no wildcard)")
        extra = [t for t in named if t not in EDITORS[agent]]
        if extra:
            raise SystemExit(f"the {agent} agent may have only {', '.join(EDITORS[agent])}, not {', '.join(extra)}")
        return agent
    if agent not in SCHEDULED:
        if scheduled:
            raise SystemExit(f"only {', '.join(SCHEDULED)} may set scheduled_actions, not {agent}")
        return agent
    if scheduled is not True:
        raise SystemExit(f"the {agent} agent runs from cron and must set scheduled_actions = true")
    if not isinstance(named, list) or not named or not all(isinstance(t, str) and t for t in named) or "*" in named:
        raise SystemExit(f"the {agent} agent must name its tools explicitly ([tools] named = [...], no wildcard)")
    needs, banned = SCHEDULED[agent]
    for tool in named:
        if any(tool == b or tool.startswith(b) for b in banned):
            raise SystemExit(f"the {agent} agent must not have {tool!r}")
    missing = [t for t in needs if t not in named]
    if missing:
        raise SystemExit(f"the {agent} agent needs {', '.join(missing)}")
    prompt = source.get("inline", "") if isinstance(source, dict) else ""
    line = NOTHING_TO_DO[agent]
    if f"`{line}`" not in prompt or "write no report" not in prompt or "Never end a run with an empty message." not in prompt:
        raise SystemExit(f"the {agent} agent's prompt must end a run with nothing to do on `{line}`, write no report, "
                         "and never end a run with an empty message")
    return agent


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: agent_check.py AGENT_TOML")
    try:
        print("id " + check(Path(sys.argv[1])))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.exit(str(exc))
