"""The scheduled agents' settings (docs/architecture.md, "Scheduled agents"):
how long a cron shell job may run, the autonomy policy and what the agents'
file tools may reach, the browser, the websites it may open, the two linked
modules, and the hands' MCP server.

  [cron]         shell_timeout_secs: bin/ jobs that draft several turns
                 take minutes, not the core's default 120 s
  [autonomy]     on, `full` tier, workspace_only: file tools reach the brain
                 (minus Teeny's own files, below) and state/workbench/ only.
                 `full` keeps cron shell jobs (bin/<job>) running as before:
                 the tier gates what an agent may do, the hands keep the rules
  [browser]      TinyComputer, headless, a persistent profile under
                 state/browser/, and the actions an unattended run may take
  [http_request] allowed_domains: deploy/browser-allow.txt, which the
                 browser shares
  [modules]      TinyComputer and tinywallet from the spine's own executable
                 (spine/Cargo.toml), every other module from its release
  [mcp_client]   hands/mcp, the fixed catalog the builder and the operator
                 claim and finish work through

The wallet's `[wallet_policy]` belongs in its own module beside this one.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: A cron shell job's wall-clock limit (OpenHuman `[cron] shell_timeout_secs`).
SHELL_TIMEOUT_SECS = 1800

#: What a browser in an unattended (cron) run may do without a person's
#: approval. `task_step` lets a browser task take a step it marks
#: irreversible (submitting a form); the allowed websites bound where, and a
#: payment checkpoint still stops it whatever this says.
UNATTENDED_ACTIONS = ("click", "fill", "type", "press", "select", "check", "task_step")

#: OpenHuman's own default forbidden paths, kept.
DEFAULT_FORBIDDEN = (
    "/etc", "/root", "/home", "/usr", "/bin", "/sbin", "/lib", "/opt", "/boot", "/dev", "/proc", "/sys",
    "/var", "/tmp", "~/.ssh", "~/.gnupg", "~/.aws", "~/.config",
)
#: Teeny's files in the brain (the core's workspace, which workspace_only
#: always allows): no agent's file tool reads or writes them. Relative
#: entries are the brain's own; SOUL.md and IDENTITY.md the core reserves
#: itself.
BRAIN_FORBIDDEN = (
    "storage.db", "storage.db-wal", "storage.db-shm", "agents", "playbooks", "skills", "narrative", "assets",
    "cron.toml", "hooks.json", "calendar.json", "CONTEXT.md",
)

CHROME_CANDIDATES = (
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

LINKED_MODULES = ("tinycomputer", "tinywallet")


def _hands_lib(root: Path) -> None:
    lib = str(root / "hands" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)


def _s(value: str) -> str:
    # A JSON string is a valid TOML basic string for anything json.dumps emits.
    return json.dumps(value, ensure_ascii=False)


def _list(values) -> str:
    return "[" + ", ".join(_s(v) for v in values) + "]"


def chrome_path() -> str | None:
    """TEENY_CHROME_PATH, else the first browser found on this machine."""
    given = os.environ.get("TEENY_CHROME_PATH", "").strip()
    if given:
        return given
    return next((c for c in CHROME_CANDIDATES if Path(c).is_file()), None)


def tables(root: Path) -> dict[str, str]:
    _hands_lib(root)
    from teeny_hands import websites
    from teeny_hands.mcp.server import TOOLS, agent_tool_name

    workbench = root / "state" / "workbench"
    profile = root / "state" / "browser" / "profile"
    downloads = root / "state" / "browser" / "downloads"
    for folder in (workbench, profile, downloads, root / "state" / "media"):
        folder.mkdir(parents=True, exist_ok=True)

    forbidden = [*DEFAULT_FORBIDDEN, str(root / "secrets"), str(root / ".openhuman"), *BRAIN_FORBIDDEN]
    autonomy = (
        "enabled = true\n"
        'level = "full"\n'
        "workspace_only = true\n"
        f"forbidden_paths = {_list(forbidden)}\n"
        f'trusted_roots = [{{ path = {_s(str(workbench))}, access = "readwrite" }}]\n'
    )

    browser = (
        "enabled = true\n"
        "headless = true\n"
        'profile_mode = "persistent"\n'
        f"profile_path = {_s(str(profile))}\n"
        f"download_dir = {_s(str(downloads))}\n"
        f"unattended_actions = {_list(UNATTENDED_ACTIONS)}\n"
    )
    chrome = chrome_path()
    if chrome:
        browser += f"chrome_path = {_s(chrome)}\n"

    overrides = ", ".join(f'{{ id = {_s(m)}, source = "linked" }}' for m in LINKED_MODULES)

    tools = list(TOOLS)
    server = (
        "{ "
        f'name = "hands", command = {_s(str(root / "hands" / "mcp"))}, args = [], cwd = {_s(str(root))}, '
        'description = "Teeny\'s work queues: claim a build job or a browser task, and report it done", '
        f"operator_trusted = true, expose = \"direct\", allowed_tools = {_list(tools)}, timeout_secs = 60"
        " }"
    )
    # The names the agents use must be the ones the core will register.
    assert all(agent_tool_name(t).startswith("mcp_hands_") for t in tools)

    return {
        "[cron]": f"shell_timeout_secs = {SHELL_TIMEOUT_SECS}\n",
        "[autonomy]": autonomy,
        "[browser]": browser,
        "[http_request]": f"allowed_domains = {_list(websites.load(root))}\n",
        "[modules]": f"overrides = [{overrides}]\n",
        "[mcp_client]": f"enabled = true\nservers = [{server}]\n",
    }
