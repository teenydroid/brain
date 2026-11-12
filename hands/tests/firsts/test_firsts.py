"""hands/firsts (docs/roadmap.md, P3): a first is offered only when bin/proofs
says its proof is met, a proof that lives outside the ledgers is never
offered, announce checks again and refuses a number the ledger doesn't hold,
and only a post that went out is recorded. hands/x is a stand-in that
records what it was given."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.firsts import calendar  # noqa: E402
from teeny_hands.x import posting_rules  # noqa: E402

HAND = HERE.parents[1] / "firsts"
REPO_FIRSTS = HERE.parents[2] / "brain" / "narrative" / "FIRSTS.md"
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
DAY = 86400.0

CALENDAR = """# firsts

## fifty-replies

- first: fifty real replies to people on X
- proof: L2.x_replies
- verify: the replies on my X account

## upstream-merge

- first: a merged pull request upstream
- proof: L3.upstream
- verify: GitHub

## first-self-promoted

- first: the first generation I promoted myself
- proof: L5.promoted
- verify: the PR

## someday

- first: something nothing counts
- proof: none
- verify: later

## typo

- first: a proof that does not exist
- proof: L9.nope
- verify: nowhere
"""

FAKE_X = """#!/usr/bin/env python3
import json, os, sys
root = os.environ["TEENY_ROOT"]
body = sys.stdin.read()
with open(os.path.join(root, "x-calls.jsonl"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "body": json.loads(body)}) + "\\n")
mode = open(os.path.join(root, "x-mode")).read().strip()
if mode == "dry":
    print(json.dumps({"ok": True, "dry_run": True, "would_post": json.loads(body)["text"]}))
elif mode == "refuse":
    print(json.dumps({"ok": False, "refused": "held", "category": "c"})); sys.exit(2)
else:
    print(json.dumps({"ok": True, "id": "9001", "kind": "post"}))
"""


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        (self.root / "brain" / "narrative").mkdir(parents=True)
        (self.root / "brain" / "narrative" / "FIRSTS.md").write_text(CALENDAR)
        (self.root / "hands").mkdir()
        (self.root / "hands" / "x").write_text(FAKE_X)
        (self.root / "hands" / "x").chmod(0o755)
        self.mode("live")
        self._env = dict(os.environ)
        for name in ("TEENY_STATE", "OPENHUMAN_WORKSPACE", "DRY_RUN"):
            os.environ.pop(name, None)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def mode(self, mode: str) -> None:
        (self.root / "x-mode").write_text(mode)

    def x_calls(self) -> list[dict]:
        path = self.root / "x-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def replies(self, n: int) -> None:
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        for i in range(n):
            conn.execute("INSERT INTO x_posts (id, kind, text, created_at) VALUES (?, 'reply', 'hi', ?)", (str(i), T0 - DAY))
        conn.close()

    def stats(self, rows) -> None:
        conn = hand.db()
        conn.execute("CREATE TABLE IF NOT EXISTS stats (date TEXT, source TEXT, metric TEXT, value REAL, at REAL)")
        conn.executemany("INSERT INTO stats VALUES (?, ?, ?, ?, 0)", rows)
        conn.close()

    def refused(self, body) -> hand.Refused:
        with self.assertRaises(hand.Refused) as caught:
            calendar.announce(body)
        return caught.exception


class CalendarTest(Scratch):
    def test_the_repo_calendar_parses_and_names_only_real_proofs(self):
        sys.path.insert(0, str(HERE.parents[2] / "bin" / "lib"))
        import proofs

        known = {p[0] for p in proofs.PROOFS}
        firsts = calendar.parse(REPO_FIRSTS.read_text())
        self.assertGreaterEqual(len(firsts), 10)
        for item in firsts:
            self.assertNotIn("problem", item, item)
            self.assertTrue(item["proof"] == "none" or item["proof"] in known, item)

    def test_list_says_what_each_proof_has(self):
        self.replies(10)
        by = {f["id"]: f for f in calendar.listing()["firsts"]}
        self.assertEqual((by["fifty-replies"]["status"], by["fifty-replies"]["has"]), ("progress", 10))
        self.assertEqual(by["someday"]["status"], "uncounted")
        self.assertEqual(by["typo"]["status"], "invalid")
        self.assertIn("never due", by["upstream-merge"]["note"])

    def test_nothing_is_due_until_the_proof_is_met(self):
        self.replies(49)
        self.assertEqual(calendar.due()["firsts"], [])
        self.assertEqual(self.refused({"text": "fifty replies!", "first": "fifty-replies"}).detail["rule"], "unproven")
        self.assertEqual(self.x_calls(), [])

    def test_a_met_proof_is_due_and_posts_once(self):
        self.replies(50)
        due = calendar.due()["firsts"]
        self.assertEqual([d["first"] for d in due], ["fifty-replies"])
        self.assertEqual((due[0]["has"], due[0]["need"]), (50, 50))
        out = calendar.announce({"text": "50 real replies to people on x. thank you for talking to a tiny crab", "first": "fifty-replies"})
        self.assertEqual((out["recorded"], out["post"]["id"]), (True, "9001"))
        self.assertEqual(self.x_calls()[0]["argv"], ["post"])
        self.assertEqual(calendar.due()["firsts"], [])
        self.assertEqual(self.refused({"text": "fifty replies", "first": "fifty-replies"}).detail["rule"], "unproven")
        self.assertIn("announced", {f["id"]: f for f in calendar.listing()["firsts"]}["fifty-replies"])

    def test_a_number_the_ledger_does_not_hold_is_refused(self):
        self.replies(50)
        refused = self.refused({"text": "500 replies already", "first": "fifty-replies"})
        self.assertEqual(refused.detail["rule"], "number")
        self.assertEqual(self.x_calls(), [])

    def test_a_dry_run_or_a_refused_post_is_not_recorded(self):
        self.replies(50)
        self.mode("dry")
        out = calendar.announce({"text": "fifty real replies", "first": "fifty-replies"})
        self.assertFalse(out["recorded"])
        self.mode("refuse")
        self.assertIn("refused", self.refused({"text": "fifty real replies", "first": "fifty-replies"}).reason)
        self.assertEqual(len(calendar.due()["firsts"]), 1)

    def test_an_outside_proof_is_never_due(self):
        conn = hand.db()
        conn.execute("CREATE TABLE gh_actions (verb TEXT, repo TEXT, ref TEXT, external INTEGER, detail TEXT)")
        conn.execute("INSERT INTO gh_actions VALUES ('open-pr', 'tinyhumansai/openhuman', '1', 1, '{}')")
        conn.close()
        self.assertEqual(calendar.due()["firsts"], [])
        self.assertEqual(self.refused({"text": "merged upstream", "first": "upstream-merge"}).detail["rule"], "unproven")

    def test_a_round_number_crossed_this_week_is_due_once(self):
        self.stats([("2026-09-07", "x", "followers", 96), ("2026-09-14", "x", "followers", 104),
                    ("2026-09-07", "github", "stars", 120), ("2026-09-14", "github", "stars", 130),
                    ("2026-09-07", "x", "following", 90), ("2026-09-14", "x", "following", 110)])
        rounds = calendar.due()["rounds"]
        self.assertEqual([r["round"] for r in rounds], ["x.followers:100"])
        self.assertEqual(self.refused({"text": "1000 followers", "round": "x.followers:100"}).detail["rule"], "number")
        out = calendar.announce({"text": "100 of you on x now. thank you", "round": "x.followers:100"})
        self.assertTrue(out["recorded"])
        self.assertEqual(calendar.due()["rounds"], [])

    def test_a_body_names_exactly_one_thing(self):
        for body in ({"text": "x"}, {"text": "x", "first": "a", "round": "b"}, {"first": "a"}, "text"):
            with self.assertRaises(hand.HandError):
                calendar.announce(body)

    def test_the_cli(self):
        self.replies(50)
        env = dict(os.environ)
        done = subprocess.run([sys.executable, str(HAND), "due"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["firsts"][0]["first"], "fifty-replies")
        done = subprocess.run([sys.executable, str(HAND), "announce"], input='{"text": "a first", "first": "someday"}',
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual((done.returncode, json.loads(done.stdout)["rule"]), (2, "unproven"))


if __name__ == "__main__":
    unittest.main()
