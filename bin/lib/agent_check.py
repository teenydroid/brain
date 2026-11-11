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
    if agent in NO_TOOLS:
        if named != []:
            raise SystemExit(f"the {agent} agent must have no tools ([tools] named = [])")
        if scheduled:
            raise SystemExit(f"the {agent} agent must not set scheduled_actions")
        if doc.get("max_iterations") != NO_TOOLS_MAX_ITERATIONS:
            raise SystemExit(f"the {agent} agent must set max_iterations = {NO_TOOLS_MAX_ITERATIONS} "
                             "(1 trips the harness's cap pause on every turn; more is a loop it has no tools for)")
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
    return agent


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: agent_check.py AGENT_TOML")
    try:
        print("id " + check(Path(sys.argv[1])))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.exit(str(exc))
