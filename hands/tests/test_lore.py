"""hands/lore events (docs/roadmap.md, A13): the proof events a lore
episode is written from, read from the evolve archive, the bounty ledger and
GitHub (hands/x's fake server). Nothing is written; nothing is made up."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from gh.support import T0, Scratch
from teeny_hands.lore import events

HANDS = Path(__file__).resolve().parents[1]
DAY = 86400.0


def iso(at: float) -> str:
    return events._iso(at)


class LoreScratch(Scratch):
    def setUp(self) -> None:
        super().setUp()
        os.environ.pop("OPENHUMAN_WORKSPACE", None)
        self.me()
        self.github(merged=[])

    def github(self, merged):
        self.gh.route("GET", "/search/issues", {"total_count": len(merged), "items": merged},
                      when=lambda seen: "is:merged" in seen.query.get("q", ""))
        self.gh.route("GET", "/search/issues", {"total_count": 0, "items": []},
                      when=lambda seen: "is:merged" not in seen.query.get("q", ""))
        self.gh.route("GET", "/search/commits", {"total_count": 0, "items": []})

    def archive(self, *records):
        path = self.root / "state" / "evolve" / "archive.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")

    def chronicle(self, gen, text):
        page = self.root / "brain" / "narrative" / "chronicle" / f"gen-{gen}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(text)


class EventsTest(LoreScratch):
    def test_no_event_is_only_an_empty_list_so_a_required_input_skips(self):
        self.assertEqual(events.recent(1), {"events": []})

    def test_a_generation_merged_today_comes_with_its_chronicle_and_who_merged_it(self):
        self.archive({"id": "g3c1", "gen": 3, "title": "open with what shipped", "component": "attention",
                      "pr_url": "https://github.com/teenydroid/brain/pull/5"},
                     {"id": "g3c1", "merged_at": iso(T0 - 3600)},
                     {"id": "g2c1", "gen": 2, "merged_at": iso(T0 - 3 * DAY)},   # too old for a day
                     {"id": "g4c1", "gen": 4, "verdict": "lost"})                 # never merged
        self.chronicle(3, "# gen-3\n\nthe night shift ran 2 candidates.\n")
        got = events.recent(1)["events"]
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["kind"], got[0]["gen"], got[0]["merged_by"]), ("generation", 3, "the operator"))
        self.assertIn("night shift", got[0]["chronicle"])
        self.assertEqual(got[0]["title"], "open with what shipped")

    def test_a_promoted_generation_was_merged_by_the_loop(self):
        self.archive({"id": "g1c1", "gen": 1, "merged_at": iso(T0 - 60), "promoted_at": iso(T0 - 60)})
        self.assertEqual(events.recent(1)["events"][0]["merged_by"], "the loop")

    def test_a_paid_bounty_and_a_pull_request_merged_elsewhere(self):
        db = self.root / "state" / "hands.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE bounties (id INTEGER PRIMARY KEY, title TEXT, status TEXT, paid_at REAL)")
        conn.execute("INSERT INTO bounties VALUES (1, 'best tiny game', 'paid', ?)", (T0 - 7200,))
        conn.execute("INSERT INTO bounties VALUES (2, 'old', 'paid', ?)", (T0 - 9 * DAY,))
        conn.execute("INSERT INTO bounties VALUES (3, 'open', 'open', NULL)")
        conn.commit()
        conn.close()
        self.github(merged=[
            {"repository_url": "https://api.github.com/repos/someone/tool", "number": 4, "title": "fix a typo",
             "html_url": "https://github.com/someone/tool/pull/4", "pull_request": {"merged_at": iso(T0 - 600)}},
            {"repository_url": "https://api.github.com/repos/teenydroid/brain", "number": 9, "title": "own",
             "pull_request": {"merged_at": iso(T0 - 600)}},
        ])
        got = events.recent(1)["events"]
        self.assertEqual([e["kind"] for e in got], ["merged_pr", "bounty_paid"])  # newest first
        self.assertEqual((got[0]["repo"], got[0]["number"]), ("someone/tool", 4))
        self.assertEqual(got[1]["title"], "best tiny game")

    def test_an_unreadable_source_is_named_and_the_others_still_answer(self):
        self.configure(GITHUB_TOKEN=None)
        self.archive({"id": "g1c1", "gen": 1, "merged_at": iso(T0 - 60)})
        out = events.recent(1)
        self.assertEqual([e["kind"] for e in out["events"]], ["generation"])
        self.assertEqual([u.split(":")[0] for u in out["unavailable"]], ["gh"])

    def test_the_cli_reads_and_bounds_its_days(self):
        env = {**os.environ}
        done = subprocess.run([str(HANDS / "lore"), "events", "--days", "2"], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["events"], [])
        done = subprocess.run([str(HANDS / "lore"), "events", "--days", "30"], capture_output=True, text=True, env=env)
        self.assertNotEqual(done.returncode, 0)
