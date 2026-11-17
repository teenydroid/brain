"""One whole generation of bin/evolve, end to end, before it reaches the box.

On 2026-11-17 gen 14 died in its first second: observe() handed
evolve_evidence.outcomes() a list of failed cron runs where failing() wanted
a mapping of counts (fixed by #346). Each module's own tests passed; nothing
ran the two together on a state dir that had any failed cron run in it.

This runs `bin/evolve run` as the cron does (a subprocess, no mocks between
evolve.py and evolve_evidence.py) under DRY_RUN, with a stand-in proposer
(--proposals) and no lab (EVOLVE_EVALUATOR=none), against a state dir shaped
like the box's: state/hands.db made by the hands' own schemas with a week of
posts, comments, holds and build ideas in it; the core's storage.db with cron
jobs and their runs, some failed; bin/act run logs with failures; and a
MEMORY.md with open learnings. It fails when observe() raises."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "hands" / "lib"))

from test_evolve import REPO, T0, DAY, EvolveCase  # noqa: E402

import evolve  # noqa: E402

PAGE = """# MEMORY.md

## people

- [m1] **ana** (x): builds a cli for crabs.

## what i have decided

- [m36] before retrying anything, check whether it already went through.
- [m44] **no likes on X, ever**: X's rules don't allow automated likes.
"""


def ledgers(state: Path) -> None:
    """state/hands.db, made by the hands' own schemas (so a renamed column
    shows up here as it would on the box), with a week of Teeny's work."""
    os.environ["TEENY_ROOT"] = str(state.parent)
    from teeny_hands import requests as build_requests
    from teeny_hands import stimuli
    from teeny_hands.build import ideas, jobs
    from teeny_hands.guard import holds
    from teeny_hands.listen import ledger
    from teeny_hands.moltbook import rules as moltbook
    from teeny_hands.stats import collect
    from teeny_hands.x import mentions, metrics  # mentions' makes x_posts too

    for make in (build_requests._db, stimuli.db, ideas.db, jobs.db, ledger.db, moltbook.db, collect.db, mentions._db,
                 metrics.db):
        make().close()
    with holds._store():
        pass
    db = sqlite3.connect(state / "hands.db")
    try:
        at = T0 - DAY
        db.execute("insert into x_posts (id, kind, text, created_at) values ('101', 'post', 'shipped a chart', ?)", (at,))
        db.execute("insert into x_posts (id, kind, text, in_reply_to, created_at) values ('102', 'reply', 'thanks', '9', ?)",
                   (at,))
        db.execute("insert into x_mentions (id, author_id, conversation_id, text, post, status, seen_at, updated_at) "
                   "values ('9', 'a1', '101', 'nice chart', '{}', 'answered', ?, ?)", (at, at))
        db.execute("insert into moltbook_posts (post_id, at, submolt, title, status) values ('p1', ?, 'agents', "
                   "'what i shipped', 'posted')", (at,))
        db.execute("insert into moltbook_comments (comment_id, post_id, at, status) values ('c1', 'p1', ?, 'posted')",
                   (at,))
        db.execute("insert into stats (date, source, metric, value, at) values (?, 'x', 'followers', 40, ?)",
                   (evolve.day(T0 - 3 * DAY), at))
        db.execute("insert into stats (date, source, metric, value, at) values (?, 'x', 'followers', 44, ?)",
                   (evolve.day(at), at))
        db.execute("insert into guard_holds (at, direction, kind, context, category, reason, held_by, text) values "
                   "(?, 'outbound', 'post', 'x', 'unknown', 'the judge could not be reached: 502', 'judge', 'held text')",
                   (evolve.iso(at),))
        db.execute("insert into build_ideas (day, title, who, source, pitch, created_at) values "
                   "(?, 'a tide clock', 'ana', 'x', 'a clock for crabs', ?)", (evolve.day(at), at))
        db.execute("insert into build_requests (source, source_id, text, created_at, status) values "
                   "('x', '9', 'build me a clock', ?, 'pending')", (at,))
        db.commit()
    finally:
        db.close()


def storage(path: Path) -> None:
    """The core's storage.db as bin/crons reads it: cron jobs, and their
    runs over the last day, two of them failed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.execute("create table docs (scope text default '', coll text, id text, created_ms integer default 0,"
                   " updated_ms integer default 0, deleted integer default 0, body text)")
        for job_id, name, command in (("j1", "teeny.mentions", "bin/mentions"), ("j2", "teeny.health", "bin/health"),
                                      ("j3", "teeny.moltbook-follow", "bin/act moltbook-follow")):
            db.execute("insert into docs (coll, id, body) values ('cron.jobs', ?, ?)",
                       (job_id, json.dumps({"name": name, "command": command, "enabled": True})))
        runs = [("j1", "error", "core unreachable"), ("j1", "ok", ""), ("j3", "error", "playbook said no"),
                ("j2", "ok", "")]
        for k, (job_id, status, output) in enumerate(runs):
            finished = int((T0 - 3600 * (k + 1)) * 1000)
            db.execute("insert into docs (coll, id, body) values ('cron.runs', ?, ?)",
                       (f"r{k}", json.dumps({"job_id": job_id, "status": status, "finished_ms": finished,
                                             "output": output})))
        db.commit()
    finally:
        db.close()


def act_runs(state: Path) -> None:
    """bin/act's run logs: one playbook failing, one fine."""
    for book, runs in (("moltbook-follow", [{"at": T0 - 7200, "ok": False, "error": "the draft was held"},
                                            {"at": T0 - 3600, "ok": True, "actions": []}]),
                       ("mentions", [{"at": T0 - 3600, "ok": True,
                                      "actions": [{"action": "reply", "status": "error"}]}])):
        folder = state / "act" / book
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs))


class WholeGeneration(EvolveCase):
    def setUp(self):
        super().setUp()
        os.environ["DRY_RUN"] = "1"
        state = self.root / "state"
        ledgers(state)
        storage(self.root / "brain" / "storage.db")
        act_runs(state)
        shutil.copyfile(REPO / "brain" / "cron.toml", self.root / "brain" / "cron.toml")
        self.page(PAGE)
        self.propose(1, [self.playbook_edit("Open with what shipped.")])
        self.propose(2, [self.playbook_edit("Name who shipped it.")])

    def run_cli(self, *args):
        return subprocess.run([str(REPO / "bin" / "evolve"), *args], capture_output=True, text=True,
                              env=dict(os.environ), timeout=600, stdin=subprocess.DEVNULL)

    def test_a_dry_run_generation_on_a_lived_in_state_dir_runs_through_observe(self):
        done = self.run_cli("run", "--candidates", "2", "--proposals", str(self.props))
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        out = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(out["gen"], 1, out)
        self.assertEqual([c["verdict"] for c in out["candidates"]], ["dry-run", "dry-run"])
        evidence = json.loads((evolve.gen_dir(1) / "evidence.json").read_text())
        outcomes = evidence["outcomes"]
        # The failed cron runs and playbook runs reached the evidence as one
        # list of failing jobs, from both sources.
        failing = {row["job"]: row for row in outcomes["failing"]}
        self.assertEqual(failing["teeny.mentions"]["failed_runs"], 1)
        self.assertEqual(failing["teeny.moltbook-follow"]["failed_runs"], 1)
        self.assertEqual(failing["bin/act moltbook-follow"]["failed_runs"], 1)
        self.assertEqual(failing["bin/act mentions"]["failed_runs"], 1)
        # And the ledgers were read, not found empty.
        for section in ("x", "moltbook", "followers", "held_drafts", "builds"):
            self.assertIn(section, outcomes, outcomes.get("nothing_recorded"))
        self.assertFalse((evolve.evolve_dir() / "last-crash.json").exists())


if __name__ == "__main__":
    unittest.main()
