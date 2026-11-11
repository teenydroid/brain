"""One agent definition's posture, for bin/doctor.

    python3 bin/lib/agent_check.py AGENT_TOML

Prints "id <id>" and exits 0 when the definition is what Teeny's design
needs; exits non-zero with the reason otherwise:

- every agent has an id and a when_to_use;
- the drafting agents (NO_TOOLS) have no tools at all and never act on a
  schedule: they read text strangers wrote;
- only the agents written to act from cron (SCHEDULED) may set
  `scheduled_actions`, and those must set it, name their tools explicitly
  (never the wildcard), and leave out the tools their role must not have;
- each scheduled agent has the tools its desk needs (it reads its work from
  state/workbench/desk/ and writes its report there) and its role's own.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

NO_TOOLS = ("act", "judge", "teeny-mentions", "teeny-mail")
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
