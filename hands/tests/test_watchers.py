"""Watchers Teeny can't switch off (docs/roadmap.md, G7; DIRECTIVE.md, "The
fences that never move": "it can't switch off its watchers").

The watchers are the guard, the doctor, the frozen-path check (in code and
in CI), evolve-watch and the health check, and the pinned cron jobs that run
them. Teeny changes itself through what it may edit (every path
evals/frozen.txt does not freeze: brain/, new hands) and through nothing
else: a cron shell job gets HOME, PATH and LANG only, and its settings live
in secrets/, which no agent's file tools reach. So a switch is reachable from
the brain when:

1. a watcher's own code or list is a path Teeny may edit;
2. a watcher reads a switch from the process environment (a setting belongs
   in secrets/teeny.env or secrets/evolve.env, read by hand.setting or
   evolve.load_config, which the environment cannot override);
3. brain/cron.toml, Teeny's own, can redefine, disable or replace the pinned
   job that runs a watcher.

Each test enumerates one of these and fails when a new one appears: a new
environment read in a watcher fails until it is added to ENVIRONMENT here
with the reason it cannot turn a watcher off, in an operator's PR.

Not here yet: the weekly audit (G6), which does not exist. Add its paths to
WATCHERS when it lands.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import cron_policy  # noqa: E402
import frozen  # noqa: E402
from teeny_hands import hand  # noqa: E402

#: Every watcher and the files it is made of (directories: every file in them).
WATCHERS = {
    "guard": ["hands/guard", "hands/lib/teeny_hands/guard", "hands/x", "hands/lib/teeny_hands/x"],
    "doctor": ["bin/doctor", "bin/lib/agent_check.py", "bin/lib/cron_policy.py", "bin/spine"],
    "frozen check": ["bin/frozen-check", "bin/lib/frozen.py", "evals/frozen.txt", ".github/workflows/check.yml"],
    "evolve-watch": ["bin/evolve", "bin/lib/evolve.py", "bin/lib/lab.py", "bin/lib/probes.py", "bin/lib/last_good.py",
                     "evals/invariants.md", "evals/judge.md"],
    "health": ["bin/health", "bin/lib/health.py"],
    "cron pins": ["evals/cron.pinned.toml", "bin/seed-cron", "bin/lib/teeny.sh"],
}

#: The pinned jobs that run a watcher. brain/cron.toml may not touch them.
PINNED_WATCHERS = {"health": "bin/health", "health-daily": "bin/health daily", "evolve-watch": "bin/evolve watch --detach",
                   "goals-check": "bin/goals-check"}

#: Every name a watcher reads from the process environment, and why it
#: cannot switch a watcher off. A name that is not here fails the test.
ENVIRONMENT = {
    "BASH_SOURCE": "bash's own: where the script is",
    "PATH": "where programs are found; cron gives the box's own",
    "TEENY_ROOT": "the checkout to run over (tests); cron jobs never get TEENY_* (docs/architecture.md, Hands)",
    "TEENY_STATE": "where state/ is (tests)",
    "TEENY_SECRETS": "where secrets/ is (tests)",
    "TEENY_STORAGE": "where the core's storage.db is (tests); read-only",
    "TEENY_HANDS": "where the hands are (tests)",
    "TEENY_OH": "the RPC client to call (tests)",
    "OPENHUMAN_WORKSPACE": "the core's workspace; the spine sets it, cron jobs never get OPENHUMAN_*",
    "OPENHUMAN_CORE_URL": "where the core answers",
    "OPENHUMAN_CORE_PORT": "where the core answers",
    "OPENHUMAN_CORE_HOST": "where the core listens",
    "OPENHUMAN_CORE_TOKEN": "the RPC bearer; no token is an error, never an open call",
    "OPENHUMAN_CORE_BIN": "the spine's binary (the image sets it)",
    "CALENDAR_TZ": "the default time zone of the schedules; changes when, never whether",
    "TEENY_AGENT_TURN_SECS": "how long an agent turn may run",
    "DRY_RUN": "can only force a rehearsal on (hand.dry_run, evolve), never off",
    "EVOLVE_NOW": "a pinned clock for the tests",
    "EVOLVE_BASE": "the base ref a generation starts from (tests)",
    "EVOLVE_GIT_DIR": "Teeny's clone's git dir (tests)",
    "EVOLVE_LAB_SPINE": "the lab spine's command (tests)",
    "EVAL_HELDOUT_DIR": "where the held-out tasks are (tests); under secrets/ by default",
    "SPINE_LAB": "marks a lab spine's own boot, which never runs the production jobs",
    "SPINE_LAST_GOOD_TRIED": "set by bin/spine for its own retry from state/last-good",
    "SPINE_BOOT_SECS": "how long the spine waits for the core to answer",
    "LADDER_URL": "the model endpoint, from secrets/spine.env in a subshell",
    "LADDER_API_KEY": "the model key, from secrets/spine.env in a subshell",
    "SPINE_MODEL": "the model id, from secrets/spine.env",
    "SPINE_CONTEXT_WINDOW": "the model's window, from secrets/spine.env",
    "SPINE_NATIVE_TOOLS": "the tool protocol, from secrets/spine.env",
    "CORTEX_API_URL": "the memory engine, from secrets/spine.env",
    "CORTEX_API_KEY": "the memory engine's key, from secrets/spine.env",
}

_ENV_READ = re.compile(
    r'os\.environ(?:\.get)?\s*[\[(]\s*"([A-Z_][A-Z_0-9]*)"'
    r'|os\.getenv\(\s*"([A-Z_][A-Z_0-9]*)"'
    r'|environ\.setdefault\(\s*"([A-Z_][A-Z_0-9]*)"'
    r'|\$\{([A-Z_][A-Z_0-9]*)'
)


def _files(path: str) -> list[Path]:
    full = REPO / path
    if full.is_dir():
        return sorted(p for p in full.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return [full]


def _patterns() -> list[str]:
    return frozen.parse_patterns((REPO / "evals" / "frozen.txt").read_text())


class WatcherTest(unittest.TestCase):
    def test_every_watcher_is_a_frozen_path(self):
        patterns = _patterns()
        for watcher, paths in WATCHERS.items():
            for path in paths:
                files = _files(path)
                self.assertTrue(files and all(f.exists() for f in files), f"{watcher}: {path} is missing")
                for f in files:
                    rel = f.relative_to(REPO).as_posix()
                    self.assertIsNotNone(frozen.frozen_pattern(rel, patterns),
                                         f"{watcher}: {rel} is a path Teeny may edit; freeze it in evals/frozen.txt")

    def test_watchers_read_no_switch_from_the_environment(self):
        seen: dict[str, set[str]] = {}
        for paths in WATCHERS.values():
            for path in paths:
                for f in _files(path):
                    if f.suffix in (".md", ".txt", ".toml", ".yml") or f.name == "askpass":
                        continue
                    for match in _ENV_READ.finditer(f.read_text(encoding="utf-8", errors="replace")):
                        name = next(g for g in match.groups() if g)
                        seen.setdefault(name, set()).add(f.relative_to(REPO).as_posix())
        unknown = {name: sorted(where) for name, where in seen.items() if name not in ENVIRONMENT}
        self.assertEqual(unknown, {}, "a watcher reads these from the environment; a switch belongs in secrets/ "
                                      "(hand.setting), or add the name to ENVIRONMENT with why it cannot turn a watcher off")
        # And the list stays the list of what is read, not a place to park names.
        self.assertEqual(sorted(set(ENVIRONMENT) - set(seen)), [])

    def test_a_setting_is_read_from_secrets_never_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "secrets").mkdir()
            (root / "secrets" / "teeny.env").write_text("SAFETY_JUDGE=1\nDRY_RUN=1\n")
            saved = dict(os.environ)
            try:
                os.environ.update(TEENY_ROOT=str(root), SAFETY_JUDGE="0", DRY_RUN="0", GH_INBOUND="1")
                hand._ENV_CACHE = None
                self.assertEqual(hand.setting("SAFETY_JUDGE"), "1")
                self.assertTrue(hand.dry_run())
                self.assertIsNone(hand.setting("GH_INBOUND"))
            finally:
                os.environ.clear()
                os.environ.update(saved)
                hand._ENV_CACHE = None

    def test_the_evolve_halts_cannot_be_loosened_from_the_environment(self):
        import evolve

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evolve.env"
            path.write_text("EVOLVE=0\nEVOLVE_AUTO_PROMOTE=0\n")
            saved = dict(os.environ)
            try:
                os.environ.update(EVOLVE="1", EVOLVE_AUTO_PROMOTE="1")
                cfg = evolve.load_config(path)
            finally:
                os.environ.clear()
                os.environ.update(saved)
        self.assertFalse(cfg["EVOLVE"])
        self.assertFalse(cfg["EVOLVE_AUTO_PROMOTE"])

    def test_the_pinned_watcher_jobs_are_pinned(self):
        pinned = tomllib.loads((REPO / "evals" / "cron.pinned.toml").read_text())["jobs"]
        for name, command in PINNED_WATCHERS.items():
            self.assertIn(name, pinned)
            self.assertEqual(pinned[name].get("command"), command)
            self.assertNotEqual(pinned[name].get("enabled"), False, f"{name} is pinned off")

    def test_the_brain_cannot_redefine_disable_or_replace_a_watcher_job(self):
        pinned = REPO / "evals" / "cron.pinned.toml"
        attempts = [f'[jobs.{name}]\nschedule = "0 0 1 1 *"\ncommand = "{command}"\nenabled = false\n'
                    for name, command in PINNED_WATCHERS.items()]
        # A job of its own that runs a watcher (with flags it chose), or the doctor.
        attempts += [f'[jobs.mine]\nschedule = "*/30 * * * *"\ncommand = "{command}"\n'
                     for command in ("bin/health", "bin/evolve resume", "bin/doctor", "bin/frozen-check", "hands/guard holds list")]
        with tempfile.TemporaryDirectory() as tmp:
            for text in attempts:
                cron = Path(tmp) / "cron.toml"
                cron.write_text(text)
                with self.assertRaises(cron_policy.PolicyError, msg=text):
                    cron_policy.load(cron, pinned)

    def test_ci_runs_the_frozen_check_on_teenys_branches_by_name_too(self):
        """G5's code half: the CI frozen job runs on a pull request by
        teenydroid, and on any branch named teeny/... (a generation's
        teeny/gen-<n>, a revert's teeny/revert-gen-<n>, a self job's
        teeny/<slug>), whoever opened it."""
        workflow = (REPO / ".github" / "workflows" / "check.yml").read_text()
        job = workflow.split("\n  frozen:\n", 1)[1].split("\n  web:\n", 1)[0]
        condition = job.split("if:", 1)[1].split("runs-on:", 1)[0]
        self.assertIn("github.event.pull_request.user.login == 'teenydroid'", condition)
        self.assertIn("startsWith(github.head_ref, 'teeny/')", condition)
        import evolve
        sys.path.insert(0, str(HERE.parent / "lib"))
        from teeny_hands.build import selfclone

        for prefix in (evolve.BRANCH_PREFIX, evolve.REVERT_PREFIX, selfclone.BRANCH_PREFIX):
            self.assertTrue(prefix.startswith("teeny/"), prefix)


if __name__ == "__main__":
    unittest.main()
