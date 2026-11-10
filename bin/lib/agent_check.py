"""One agent definition's posture, for bin/doctor.

    python3 bin/lib/agent_check.py AGENT_TOML HANDS_LIB

Prints "id <id>" and exits 0 when the definition is what Teeny's design
needs; exits non-zero with the reason otherwise:

- every agent has an id and a when_to_use;
- the drafting agents (NO_TOOLS) have no tools at all and never act on a
  schedule: they read text strangers wrote;
- only the agents written to act from cron (SCHEDULED) may set
  `scheduled_actions`, and those must set it, name their tools explicitly
  (never the wildcard), and leave out the tools their role must not have;
- an `mcp_hands_*` tool is one hands/mcp actually serves, spelled the way
  the core names it, and one that agent's role uses.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

NO_TOOLS = ("act", "judge", "teeny-mentions", "teeny-mail")
#: agent -> (hands/mcp tools it uses, tool name prefixes it must not have)
SCHEDULED = {
    "builder": (("build_next", "build_done"), ("browser", "wallet", "web3", "x402", "composio", "mcp_list", "mcp_call")),
    "operator": (("tasks_next", "tasks_done"),
                 ("shell", "file_write", "edit", "apply_patch", "wallet", "web3", "x402", "composio", "mcp_list", "mcp_call")),
}


def check(path: Path, hands_lib: Path) -> str:
    with open(path, "rb") as fh:
        doc = tomllib.load(fh)
    for key in ("id", "when_to_use"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            raise SystemExit(f"needs {key}")
    agent = doc["id"]
    named = (doc.get("tools") or {}).get("named")
    scheduled = doc.get("scheduled_actions", False)
    if agent in NO_TOOLS:
        if named != []:
            raise SystemExit(f"the {agent} agent must have no tools ([tools] named = [])")
        if scheduled:
            raise SystemExit(f"the {agent} agent must not set scheduled_actions")
        return agent
    if agent not in SCHEDULED:
        if scheduled:
            raise SystemExit(f"only {', '.join(SCHEDULED)} may set scheduled_actions, not {agent}")
        return agent
    if scheduled is not True:
        raise SystemExit(f"the {agent} agent runs from cron and must set scheduled_actions = true")
    if not isinstance(named, list) or not named or not all(isinstance(t, str) and t for t in named) or "*" in named:
        raise SystemExit(f"the {agent} agent must name its tools explicitly ([tools] named = [...], no wildcard)")
    uses, banned = SCHEDULED[agent]
    for tool in named:
        if any(tool == b or tool.startswith(b) for b in banned):
            raise SystemExit(f"the {agent} agent must not have {tool!r}")
    sys.path.insert(0, str(hands_lib))
    from teeny_hands.mcp.server import agent_tool_name

    wanted = {agent_tool_name(t) for t in uses}
    have = {t for t in named if t.startswith("mcp_hands_")}
    if have != wanted:
        raise SystemExit(
            f"the {agent} agent's hands tools are {sorted(have)}, not {sorted(wanted)} (hands/mcp --tool-names)")
    return agent


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: agent_check.py AGENT_TOML HANDS_LIB")
    try:
        print("id " + check(Path(sys.argv[1]), Path(sys.argv[2])))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.exit(str(exc))
