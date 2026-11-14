"""The scheduled agents' settings (docs/architecture.md, "Scheduled agents"):
how long a cron shell job may run, the autonomy policy and what the agents'
file tools may reach, the browser, the websites it may open, and the
linked module.

  [cron]         shell_timeout_secs: bin/ jobs that draft several turns
                 take minutes, not the core's default 120 s
  [autonomy]     on, `full` tier, workspace_only: file tools reach the brain
                 (minus Teeny's own files, below) and state/workbench/ only.
                 On the evolver's lab spine (SPINE_LAB=1 and SPINE_LAB_EDIT=1,
                 bin/lib/lab.py) the root is a candidate's worktree, and the
                 file tools may write it, except its git link, secrets, the
                 core's config and the core's own files in the brain
                 `full` keeps cron shell jobs (bin/<job>) running as before:
                 the tier gates what an agent may do, the hands keep the rules
  [browser]      TinyComputer, headless, a persistent profile in
                 state/browser/profile/ (cookies and sign-ins survive a
                 restart), downloads under state/browser/, the actions an unattended
                 run may take, and bin/keychain-fill as the password source
                 for the operator's {{keychain:<site>}} placeholder. It
                 opens any public https page (bin/spine sets
                 OPENHUMAN_BROWSER_ALLOW_ALL=1); what it
                 never opens is deploy/browser-deny.txt (hands/tasks)
  [http_request] allowed_domains: deploy/browser-allow.txt, for the raw
                 http_request tool
  [modules]      tinywallet from the spine's own executable (spine/Cargo.toml),
                 every other module, TinyComputer included, from its release

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
#: irreversible (submitting a form); the deny list bounds where, and a
#: payment checkpoint still stops it whatever this says.
UNATTENDED_ACTIONS = ("click", "fill", "type", "press", "select", "check", "task_step")

#: OpenHuman's own default forbidden paths, kept.
DEFAULT_FORBIDDEN = (
    "/etc", "/root", "/home", "/usr", "/bin", "/sbin", "/lib", "/opt", "/boot", "/dev", "/proc", "/sys",
    "/var", "/tmp", "~/.ssh", "~/.gnupg", "~/.aws", "~/.config",
)
#: Teeny's files in the brain (the core's workspace, which workspace_only
#: always allows): no agent's file tool reads or writes them (MEMORY.md, the
#: live page, only bin/reflect writes; MEMORY.seed.md is where it started).
#: SOUL.md and IDENTITY.md the core reserves itself. TinySecurity takes only
#: absolute forbidden paths, so these and `~` are resolved here.
BRAIN_FORBIDDEN = (
    "storage.db", "storage.db-wal", "storage.db-shm", "agents", "playbooks", "skills", "narrative", "assets",
    "cron.toml", "hooks.json", "calendar.json", "CONTEXT.md", "MEMORY.md",
    "MEMORY.seed.md", "agent",
)

#: What the evolver's file tools may not touch even in its own worktree: the
#: core's store and keyring, the prompts it builds, the git link.
LAB_EDIT_FORBIDDEN = (
    "storage.db", "storage.db-wal", "storage.db-shm", "agent", "keyring", "dev-keychain.json", "state",
)

CHROME_CANDIDATES = (
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

#: Modules served from the spine's own executable. Not TinyComputer: the
#: browser loads it with a host callback (the consequence gate on browser
#: egress), and TinyBus admits a callback-bound load only from the pinned
#: release or a local file, so linked every browser call fails with "module
#: 'tinycomputer' callback source cannot preserve admitted identity".
LINKED_MODULES = ("tinywallet",)


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

    workbench = root / "state" / "workbench"
    downloads = root / "state" / "browser" / "downloads"
    profile = root / "state" / "browser" / "profile"
    for folder in (workbench, downloads, root / "state" / "media", profile):
        folder.mkdir(parents=True, exist_ok=True)
    # The profile holds cookies, which are sign-ins: the owner's alone.
    profile.chmod(0o700)

    brain = Path(os.environ.get("OPENHUMAN_WORKSPACE") or root / "brain")
    lab_edit = os.environ.get("SPINE_LAB") == "1" and os.environ.get("SPINE_LAB_EDIT") == "1"
    forbidden = [
        *(os.path.expanduser(p) for p in DEFAULT_FORBIDDEN),
        str(root / "secrets"),
        str(root / ".openhuman"),
    ]
    if lab_edit:
        forbidden += [str(root / ".git"), str(root / "state"), *(str(brain / name) for name in LAB_EDIT_FORBIDDEN)]
        trusted = root
    else:
        forbidden += [str(brain / name) for name in BRAIN_FORBIDDEN]
        trusted = workbench
    autonomy = (
        "enabled = true\n"
        'level = "full"\n'
        "workspace_only = true\n"
        f"forbidden_paths = {_list(forbidden)}\n"
        f'trusted_roots = [{{ path = {_s(str(trusted))}, access = "readwrite" }}]\n'
    )

    # One persistent profile, so cookies and sign-ins survive a restart. The
    # core hands it to TinyComputer as the host's own profile
    # (browser.profile, tinyhumansai/tinycomputer#175), the only folder its
    # egress-enforced launcher lets a session name, launched with extensions
    # off so nothing stored in it can route around the egress proxy. When a
    # second browser runs while the first holds it, that one starts fresh.
    browser = (
        "enabled = true\n"
        "headless = true\n"
        'profile_mode = "persistent"\n'
        f"profile_path = {_s(str(profile))}\n"
        f"download_dir = {_s(str(downloads))}\n"
        f"unattended_actions = {_list(UNATTENDED_ACTIONS)}\n"
        # The browser's password source for the operator's {{keychain:<site>}}
        # placeholder (hands/keychain): the core runs it only after it checked
        # that the page is on <site>, and the password never reaches a model.
        f"keychain_command = {_s(str(root / 'bin' / 'keychain-fill'))}\n"
    )
    chrome = chrome_path()
    if chrome:
        browser += f"chrome_path = {_s(chrome)}\n"

    overrides = ", ".join(f'{{ id = {_s(m)}, source = "linked" }}' for m in LINKED_MODULES)

    return {
        "[cron]": f"shell_timeout_secs = {SHELL_TIMEOUT_SECS}\n",
        "[autonomy]": autonomy,
        "[browser]": browser,
        "[http_request]": f"allowed_domains = {_list(websites.load(root))}\n",
        "[modules]": f"overrides = [{overrides}]\n",
    }
