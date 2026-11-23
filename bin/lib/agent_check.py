"""One agent definition's posture, for bin/doctor.

    python3 bin/lib/agent_check.py AGENT_TOML

Prints "id <id>" and exits 0 when the definition is what Teeny's design
needs; exits non-zero with the reason otherwise:

- every agent has an id and a when_to_use;
- the drafting agents (NO_TOOLS) have no tools at all and never act on a
  schedule: they read text strangers wrote;
- the drafting agents run read_only, at exactly their count in
  MAX_ITERATIONS, else NO_TOOLS_MAX_ITERATIONS (see there);
- the judge, reviewer and querier (MEMORYLESS) load no memory and no identity;
- only the agents written to act from cron (SCHEDULED) may set
  `scheduled_actions`, and those must set it, name their tools explicitly
  (never the wildcard), and leave out the tools their role must not have;
- the mind and its worker (VIEW_ONLY) set `browser_view_only`: their browser
  opens and reads pages on a throwaway profile and never acts as Teeny;
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

NO_TOOLS = ("teeny", "act", "judge", "reviewer", "querier", "teeny-mentions", "teeny-mail", "writer", "editor")
#: The tool-less agents that must also load no memory and no identity: they
#: are not Teeny, and are shown only what their message carries (the
#: querier's queries leave the box; the studio's editor judges drafts blind).
MEMORYLESS = ("judge", "reviewer", "querier", "editor")
#: A drafting agent's turn is one model call: with no tools it cannot loop.
#: But the vendored harness pauses a session turn when it reaches
#: max_iterations even after a final answer (the CapPauser in
#: tinyagents-runtime turn_driver/driver.rs, drained as Paused by
#: settle_natural_finish in tinyagents-harness agent_loop/turn_control.rs),
#: so at 1 every turn is recorded as failed though its answer comes back.
#: The second iteration only absorbs that pause (or an output-retry nudge).
#: Drop to 1 once the harness stops pausing a finished turn.
NO_TOOLS_MAX_ITERATIONS = 2
#: The drafting agents whose first call has been seen to die: it reasons to
#: its output cap and comes back empty or truncated, so the harness retries
#: it as a second call with reasoning off (RunPolicy's call after a dead one,
#: tinyagents-harness agent_loop/run_loop.rs). At 2 the cap pause then lands
#: on that retry and the turn fails though it answered, and the beat is lost
#: (12 of the mind's turns and 1 of act's in 12 hours on the box,
#: 2026-11-21). The third iteration absorbs the pause after the retry. Each
#: count is exact, like NO_TOOLS_MAX_ITERATIONS: a tool-less turn still
#: cannot loop, and an agent not named here stays at 2.
MAX_ITERATIONS = {"teeny": 3, "act": 3}
#: Never in a scheduled agent: money, connectors, and any MCP server.
NEVER = ("wallet", "web3", "x402", "composio", "mcp")
#: agent -> (tools it must have, tool name prefixes it must not have)
SCHEDULED = {
    "builder": (("shell", "file_read", "file_write"), ("browser", *NEVER)),
    "operator": (("browser", "file_read", "file_write"), ("shell", "edit", "apply_patch", *NEVER)),
    # The mind works in its own home with a shell and no key (docs/mind.md).
    # Nothing that schedules (a cron shell job runs outside any jail), spawns
    # or installs into the core: each is a way past the jail or the broker.
    # Its one browser tool is view-only (VIEW_ONLY).
    "mind": (("shell", "file_read", "file_write", "memory"),
             ("browser", "cron", "schedule", "spawn", "create_skill", "install", "skill_registry", "run_workflow",
              *NEVER)),
}
#: agent -> the only agents its [subagents] allowlist may name, all of them.
#: An agent listed here has `spawn_subagent` (the one exception to a banned
#: "spawn" prefix) and that exact list; no other agent has either.
SUBAGENTS = {"mind": ("worker",)}
SPAWN_TOOL = "spawn_subagent"
#: The helpers: agent -> (tools it must have, prefixes it must not have). A
#: helper is never a cron job. The core runs it in its parent's workspace
#: with its parent's file tools, and lets it act only because its own
#: definition opts in (scheduled_actions), is sandboxed and names its tools.
#: It gets nothing its parent lacks, and no memory or helpers of its own.
HELPERS = {
    "worker": (("shell", "file_read", "file_write"),
               ("browser", "cron", "schedule", "spawn", "create_skill", "install", "skill_registry", "run_workflow",
                "memory", *NEVER)),
}
#: The agents whose browser is for reading only. The core has one browser
#: profile, signed in as Teeny, and one list of clicks and typing it allows
#: unattended (the operator's), so a browser in any other agent's hands could
#: speak as Teeny past the guard. `browser_view_only` gives these agents a
#: throwaway profile and only the actions that open and read a page. They
#: must set it whether or not they list the tool, so that adding the tool
#: later is never the one step that opens the signed-in profile; `browser`
#: is the only browser tool they may name.
VIEW_ONLY = ("mind", "worker")
BROWSER_TOOL = "browser"
#: The scheduled agents whose shell must stay jailed to files in their workspace.
JAILED = ("builder", "mind", "worker")

#: agent -> the only tools it may have. The evolver edits a lab worktree
#: and answers JSON; bin/evolve gates what it wrote.
EDITORS = {"evolver": ("file_read", "file_write", "edit", "glob", "grep", "list")}

#: agent -> the root its cron runs work in (OpenHuman's `scheduled_workspace`,
#: relative to the checkout). Unlisted agents must not set one.
WORKSPACE = {"builder": "state/workbench", "mind": "state/home"}

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
    helpers = doc.get("subagents")
    if agent in SUBAGENTS:
        allowed = list(SUBAGENTS[agent])
        if not isinstance(helpers, dict) or helpers.get("allowlist") != allowed or set(helpers) != {"allowlist"}:
            raise SystemExit(f"the {agent} agent must set [subagents] allowlist = {allowed!r} and nothing else there")
        if not isinstance(named, list) or SPAWN_TOOL not in named:
            raise SystemExit(f"the {agent} agent lists helpers, so it needs {SPAWN_TOOL}")
        if doc.get("agent_tier") not in ("chat", "reasoning"):
            raise SystemExit(f"the {agent} agent lists helpers, so it must set agent_tier = \"reasoning\": the core "
                             "defaults to a leaf worker tier and rejects the whole agent registry over a leaf with subagents")
    elif helpers is not None:
        raise SystemExit(f"only {', '.join(SUBAGENTS)} may have [subagents], not {agent}")
    root = doc.get("scheduled_workspace")
    if root != WORKSPACE.get(agent):
        if agent in WORKSPACE:
            raise SystemExit(f"the {agent} agent must set scheduled_workspace = {WORKSPACE[agent]!r} "
                             "(its sandboxed shell is granted that folder, never the checkout)")
        raise SystemExit(f"only {', '.join(WORKSPACE)} may set scheduled_workspace, not {agent}")
    if agent not in VIEW_ONLY and "browser_view_only" in doc:
        raise SystemExit(f"only {', '.join(VIEW_ONLY)} set browser_view_only, not {agent}")
    if agent in NO_TOOLS:
        if named != []:
            raise SystemExit(f"the {agent} agent must have no tools ([tools] named = [])")
        if scheduled:
            raise SystemExit(f"the {agent} agent must not set scheduled_actions")
        if doc.get("sandbox_mode") != "read_only":
            raise SystemExit(f'the {agent} agent must set sandbox_mode = "read_only"')
        iterations = MAX_ITERATIONS.get(agent, NO_TOOLS_MAX_ITERATIONS)
        if doc.get("max_iterations") != iterations:
            raise SystemExit(f"the {agent} agent must set max_iterations = {iterations} "
                             "(fewer trips the harness's cap pause on a turn that answered; "
                             "more is a loop it has no tools for)")
        if agent in MEMORYLESS and not (doc.get("omit_memory_context") is True and doc.get("omit_identity") is True):
            raise SystemExit(f"the {agent} agent must set omit_memory_context = true and omit_identity = true")
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
    if agent not in SCHEDULED and agent not in HELPERS:
        if scheduled:
            raise SystemExit(f"only {', '.join((*SCHEDULED, *HELPERS))} may set scheduled_actions, not {agent}")
        return agent
    if scheduled is not True:
        raise SystemExit(f"the {agent} agent acts unattended and must set scheduled_actions = true")
    if not isinstance(named, list) or not named or not all(isinstance(t, str) and t for t in named) or "*" in named:
        raise SystemExit(f"the {agent} agent must name its tools explicitly ([tools] named = [...], no wildcard)")
    needs, banned = SCHEDULED[agent] if agent in SCHEDULED else HELPERS[agent]
    for tool in named:
        if tool == SPAWN_TOOL and agent in SUBAGENTS:
            continue
        if tool == BROWSER_TOOL and agent in VIEW_ONLY:
            continue
        if any(tool == b or tool.startswith(b) for b in banned):
            raise SystemExit(f"the {agent} agent must not have {tool!r}")
    parents = [p for p, kids in SUBAGENTS.items() if agent in kids]
    if parents:
        parent_file = path.parent / f"{parents[0]}.toml"
        try:
            with open(parent_file, "rb") as fh:
                theirs = (tomllib.load(fh).get("tools") or {}).get("named")
        except (OSError, tomllib.TOMLDecodeError):
            theirs = None
        if not isinstance(theirs, list):
            raise SystemExit(f"the {agent} agent is checked against {parents[0]}.toml's tools, which must sit beside it")
        extra = [t for t in named if t not in theirs or t in (SPAWN_TOOL, "memory")]
        if extra:
            raise SystemExit(f"the {agent} agent may have only tools the {parents[0]} has (and never {SPAWN_TOOL} "
                             f"or memory), not {', '.join(extra)}")
    if agent in HELPERS and doc.get("omit_memory_context") is not True:
        raise SystemExit(f"the {agent} agent must set omit_memory_context = true "
                         "(the core adds its parent's memory to a helper's message otherwise)")
    missing = [t for t in needs if t not in named]
    if missing:
        raise SystemExit(f"the {agent} agent needs {', '.join(missing)}")
    if agent in JAILED and not (doc.get("sandbox_mode") == "sandboxed" and doc.get("sandbox_filesystem_only") is True):
        raise SystemExit(f'the {agent} agent must set sandbox_mode = "sandboxed" and sandbox_filesystem_only = true '
                         "(its shell is jailed to its workspace)")
    if agent in VIEW_ONLY and doc.get("browser_view_only") is not True:
        raise SystemExit(f"the {agent} agent must set browser_view_only = true (the core's browser is signed in as "
                         "Teeny; this agent's is for reading, on a throwaway profile)")
    if agent not in NOTHING_TO_DO:
        return agent
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
