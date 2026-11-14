"""bin/dream: Teeny's twice-daily consolidation (docs/architecture.md, 4a,
"Dreaming"). It is reflect's item-by-item update of MEMORY.md, on a thread of
its own, with more to go on (what people said, the tasks, the learnings the
core's memory holds) and one more operation: `forget`, which takes a false or
superseded learning out of the core's memory, archived first and capped. Each
dream leaves a snapshot of the page for the public diary (bin/lib/diary_memory.py)."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))

from teeny_hands import hand, requests, stimuli  # noqa: E402

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z

FAKE_OH = r"""#!/usr/bin/env python3
import json, os, sys
method, params = sys.argv[1], json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")
row = {"method": method, "params": params}
if method == "memory_forget":
    # Whether every id was already in the archive when the core was asked.
    archive = os.path.join(os.environ["TEENY_ROOT"], "state", "dream", "forgotten.jsonl")
    kept = open(archive).read() if os.path.exists(archive) else ""
    row["archived_first"] = all(i in kept for i in params.get("ids", []))
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps(row) + "\n")
if method == "inference_agent_chat":
    with open(os.environ["FAKE_CALLS"]) as fh:
        n = sum(1 for line in fh if '"inference_agent_chat"' in line)
    path = os.environ["FAKE_ANSWER"]
    path = f"{path}.{n}" if os.path.exists(f"{path}.{n}") else path
    print(json.dumps({"response": open(path).read()}))
elif method == "memory_learn":
    print(json.dumps({"id": "l1"}))
elif method == "memory_items_list":
    items = json.loads(open(os.environ["FAKE_ITEMS"]).read()) if os.environ.get("FAKE_ITEMS") else []
    print(json.dumps({"items": items}))
elif method == "memory_forget":
    print(json.dumps({"forgotten": len(params.get("ids", []))}))
else:
    sys.exit(f"unexpected {method}")
"""

FAKE_WALLET = """#!/usr/bin/env python3
import json
print(json.dumps({"sol": {"display": "0.42 SOL"}, "usdc": {"display": "12.5 USDC"}, "tiny": {"display": "1000"},
                  "evm": {"networks": {"base": {"eth": {"display": "0.01 ETH"}, "usdc": {"display": "3 USDC"}}}}}))
"""

FAKE_X402 = """#!/usr/bin/env python3
import json
print(json.dumps({"ok": True, "base_usdc": "3.00"}))
"""

PAGE = "# MEMORY.md\n\nwhat i keep in mind.\n\n## people\n\n- [m1] ana (x): builds a cli for crabs.\n"

ITEMS = [
    {"id": "aaa111", "kind": "learning", "meta": {"observed_at": "2026-09-14T05:00:00Z"},
     "text": "journal, 2026-09-14 10:30: receipt delivered to nyna as promised"},
    {"id": "bbb222", "kind": "learning", "meta": {"observed_at": "2026-09-14T04:00:00Z"},
     "text": "[2026-09-14] about ana: ships friday"},
    {"id": "ccc333", "kind": "conversation", "meta": {"observed_at": "2026-09-14T04:00:00Z"},
     "text": "user: a whole batch"},
]


class DreamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scratch = Path(self.tmp.name)
        self.root = self.scratch / "root"
        brain = self.root / "brain"
        (brain / "agents").mkdir(parents=True)
        (brain / "narrative").mkdir()
        (self.root / "secrets").mkdir()
        (self.root / "bin").symlink_to(REPO / "bin")
        (brain / "agents" / "teeny.toml").write_text('id = "teeny"\n')
        (brain / "agents" / "teeny.md").write_text("You are Teeny.\n")
        (brain / "MEMORY.md").write_text(PAGE)
        self.brain = brain
        oh = self.scratch / "fake-oh"
        oh.write_text(FAKE_OH)
        oh.chmod(oh.stat().st_mode | stat.S_IEXEC)
        hands = self.scratch / "hands"
        hands.mkdir()
        for name, text in (("wallet", FAKE_WALLET), ("x402", FAKE_X402)):
            (hands / name).write_text(text)
            (hands / name).chmod(0o755)
        self.calls = self.scratch / "calls.log"
        self.answer_file = self.scratch / "answer.txt"
        self.items = self.scratch / "items.json"
        self.items.write_text(json.dumps(ITEMS))
        self._env = dict(os.environ)
        os.environ.update(
            TEENY_ROOT=str(self.root),
            OPENHUMAN_WORKSPACE=str(brain),
            TEENY_OH=str(oh),
            TEENY_HANDS=str(hands),
            OPENHUMAN_CORE_TOKEN="t",
            TEENY_NOW=str(T0),
            FAKE_CALLS=str(self.calls),
            FAKE_ANSWER=str(self.answer_file),
            FAKE_ITEMS=str(self.items),
            CALENDAR_TZ="UTC",
        )
        for key in ("TEENY_DREAM_THREAD", "TEENY_REFLECT_THREAD"):
            os.environ.pop(key, None)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def answer(self, *values):
        for n, value in enumerate(values, 1):
            Path(f"{self.answer_file}.{n}").write_text(value if isinstance(value, str) else json.dumps(value))
        self.answer_file.write_text(values[-1] if isinstance(values[-1], str) else json.dumps(values[-1]))

    def dream(self, *args) -> dict:
        proc = subprocess.run([str(REPO / "bin" / "dream"), *args], capture_output=True, text=True, env=os.environ)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def calls_of(self, method: str) -> list[dict]:
        if not self.calls.exists():
            return []
        return [c for c in map(json.loads, self.calls.read_text().splitlines()) if c["method"] == method]

    def message(self) -> str:
        return self.calls_of("inference_agent_chat")[0]["params"]["message"]

    def page(self) -> str:
        return (self.brain / "MEMORY.md").read_text()

    # --- what a dream goes over ----------------------------------------------

    def test_a_dream_runs_on_a_thread_of_its_own(self):
        self.answer({"ops": [], "diary": "a day."})
        got = self.dream()
        self.assertTrue(got["ok"], got)
        [chat] = [c["params"] for c in self.calls_of("inference_agent_chat")]
        self.assertEqual((chat["agent_id"], chat["thread_id"]), ("teeny", "teeny:dream"))
        self.assertIn('"forget"', chat["message"])  # the extra operation is in the shape

    def test_it_goes_over_what_people_said_and_not_its_own_moments(self):
        stimuli.add("x", "mention", "111", "can you build me a crab cli?", who="ana", at=T0 - 600)
        stimuli.add("self", "quiet", "q1", "a quiet moment to yourself", at=T0 - 300)
        self.answer({"ops": [], "diary": "d"})
        self.dream()
        message = self.message()
        self.assertIn("## what people said", message)
        self.assertIn("x · ana · 10 min ago: can you build me a crab cli?", message)
        self.assertNotIn("a quiet moment to yourself", message)

    def test_it_goes_over_hacker_news_and_what_it_read_of_the_world(self):
        stimuli.add("hn", "comment", "42", "your agent posted this? neat", who="pg", at=T0 - 1200)
        stimuli.add("world", "idle", "hn-top:1", "Hacker News is open:\n- A tiny agent that runs in public", at=T0 - 600)
        self.answer({"ops": [], "diary": "d"})
        self.dream()
        message = self.message()
        self.assertIn("hn · pg · 20 min ago: your agent posted this? neat", message)
        self.assertIn("world · what you read · 10 min ago: Hacker News is open: - A tiny agent that runs in public", message)

    def test_a_busy_day_still_fits_the_cores_prompt_screen(self):
        # 2026-11-13: sixty beats, eighty mentions and eighty learnings made a
        # 90,022-character message; the core holds anything over 64 KiB for
        # review ("Prompt flagged for security review"), so no dream ran.
        limit = None
        for line in (REPO / "bin" / "lib" / "reflect.py").read_text().splitlines():
            if line.startswith("MAX_PROMPT_BYTES"):
                limit = eval(line.split("=", 1)[1])  # noqa: S307 - a constant expression in our own file
        self.assertIsNotNone(limit, "bin/lib/reflect.py names no MAX_PROMPT_BYTES")
        self.assertLessEqual(limit, 62 * 1024)
        tick_log = self.root / "state" / "tick" / "runs.jsonl"
        tick_log.parent.mkdir(parents=True, exist_ok=True)
        with open(tick_log, "a") as fh:
            for n in range(200):
                at = f"2026-09-14T{3 + n // 60:02d}:{n % 60:02d}:00Z"
                fh.write(json.dumps({"at": at, "ok": True, "kind": "beat", "thought": f"beat {n} " + "thinking " * 40,
                                     "results": [{"do": "journal", "status": "done",
                                                  "intent": {"text": f"journal {n} " + "wrote " * 60}}]}) + "\n")
        for n in range(80):
            stimuli.add("x", "mention", f"m{n}", f"mention {n} " + "words " * 40, who="ana", at=T0 - 6000 + n)
        self.items.write_text(json.dumps([
            {"id": f"id{n:03d}", "kind": "learning", "meta": {"observed_at": f"2026-09-14T04:{n % 60:02d}:00Z"},
             "text": f"learning {n} " + "noted " * 60} for n in range(80)]))
        self.answer({"ops": [], "diary": "d"})
        got = self.dream()
        self.assertTrue(got["ok"], got)
        message = self.message()
        self.assertLessEqual(len(message.encode("utf-8")), limit)
        # What it cannot do without is all there: the page, the shape, the newest of everything.
        self.assertIn("- [m1] ana (x): builds a cli for crabs.", message)
        self.assertIn('"forget"', message)
        self.assertIn("beat 199 ", message)
        self.assertIn("mention 79 ", message)
        # What was cut is the oldest, and the message says so.
        self.assertNotIn("beat 0 ", message)
        self.assertRegex(message, r"the \d+ earliest beats are left out")
        self.assertRegex(message, r"\d+ older ones are left out")

    def test_both_dreams_see_the_scoreboard_the_lab_and_what_is_true_now(self):
        from teeny_hands.lab import ledger

        ledger.propose({"surface": "x", "hypothesis": "takes beat gm", "format": "take", "posts": 2, "deadline_days": 5})
        for light in (False, True):
            self.calls.unlink(missing_ok=True)
            self.answer({"ops": [], "diary": "d"})
            self.assertTrue(self.dream(*(["--light"] if light else []))["ok"])
            message = self.message()
            for heading in ("## the scoreboard", "## your experiments", "## what the lab has learned", "## true right now"):
                self.assertIn(heading, message)
            self.assertIn("#1 x `take` (result): 0 of 2 posts measured", message)
            self.assertIn("wallet on Solana: SOL 0.42 SOL, USDC 12.5 USDC, TINY 1000", message)
            self.assertIn("x402 rail (USDC on Base, what paid search spends): 3.00", message)
            # Only the night may run the lab.
            self.assertEqual('"op": "experiment"' in message, not light)

    def test_the_night_dream_concludes_and_proposes_through_hands_lab(self):
        from teeny_hands.lab import ledger

        ledger.propose({"surface": "x", "hypothesis": "gm posts work", "format": "gm", "posts": 2, "deadline_days": 5})
        self.answer({"ops": [
            {"op": "conclude", "id": 1, "verdict": "unclear", "lesson": "no gm went out this week"},
            {"op": "conclude", "id": 1, "verdict": "works", "lesson": "again"},
            {"op": "experiment", "surface": "moltbook", "hypothesis": "confessions get comments", "format": "confession",
             "posts": 3, "deadline_days": 7},
        ], "diary": "d"})
        got = self.dream()
        self.assertTrue(got["ok"], got)
        self.assertEqual([(r["op"], r["ok"]) for r in got["lab"]], [("conclude", True), ("conclude", False), ("experiment", True)])
        self.assertEqual(got["lab"][1]["rule"], "concluded")
        self.assertEqual([l["lesson"] for l in ledger.lessons()["lessons"]], ["no gm went out this week"])
        rows = ledger.listing("open")["experiments"]
        self.assertEqual([(r["surface"], r["format"]) for r in rows], [("moltbook", "confession")])
        self.assertEqual(ledger.db().execute("SELECT source FROM experiments WHERE id = 2").fetchone()[0], "dream")

    def test_more_lab_operations_than_a_dream_may_carry_change_nothing(self):
        from teeny_hands.lab import ledger

        many = [{"op": "experiment", "surface": "x", "hypothesis": "h", "format": f"f{n}", "posts": 1, "deadline_days": 2}
                for n in range(3)]
        self.answer({"ops": many, "diary": "d"}, {"ops": many, "diary": "d"})
        got = self.dream()
        self.assertFalse(got["ok"])
        self.assertEqual(ledger.listing(None)["experiments"], [])

    def test_a_midday_dream_cannot_run_the_lab(self):
        from teeny_hands.lab import ledger

        op = {"op": "experiment", "surface": "x", "hypothesis": "h", "format": "take", "posts": 1, "deadline_days": 2}
        self.answer({"ops": [op]}, {"ops": [op]})
        self.assertFalse(self.dream("--light")["ok"])
        self.assertEqual(ledger.listing(None)["experiments"], [])

    def test_a_day_that_fits_is_sent_whole(self):
        stimuli.add("x", "mention", "111", "can you build me a crab cli?", who="ana", at=T0 - 600)
        self.answer({"ops": [], "diary": "d"})
        self.dream()
        self.assertNotIn("left out", self.message())

    def test_it_goes_over_the_tasks(self):
        conn = hand.open_db(requests.SCHEMA)
        conn.execute("INSERT INTO build_requests (source, source_id, author, text, created_at, status)"
                     " VALUES ('x', '9', 'ana', 'a crab cli please', ?, 'pending')", (T0 - 3600,))
        conn.close()
        self.answer({"ops": [], "diary": "d"})
        self.dream()
        message = self.message()
        self.assertIn("## tasks", message)
        self.assertIn("asked by ana on x, pending: a crab cli please", message)

    def test_it_goes_over_the_learnings_the_core_holds_and_never_conversations(self):
        self.answer({"ops": [], "diary": "d"})
        self.dream()
        message = self.message()
        self.assertIn("## what your memory holds", message)
        self.assertIn("[c1] 2026-09-14 05:00: journal, 2026-09-14 10:30: receipt delivered to nyna as promised", message)
        self.assertIn("[c2]", message)
        self.assertNotIn("a whole batch", message)
        [listed] = self.calls_of("memory_items_list")[:1]
        self.assertEqual(listed["params"]["filter"]["kinds"], ["learning"])

    # --- forget --------------------------------------------------------------

    def test_forget_archives_the_learning_then_takes_it_out_of_memory(self):
        self.answer({"ops": [{"op": "forget", "item": "c1", "reason": "false: that comment got a 400"}], "diary": "d"})
        got = self.dream()
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["forgot"], 1)
        [row] = [json.loads(l) for l in (self.root / "state" / "dream" / "forgotten.jsonl").read_text().splitlines()]
        self.assertEqual((row["id"], row["reason"]), ("aaa111", "false: that comment got a 400"))
        self.assertIn("receipt delivered", row["text"])
        [forget] = self.calls_of("memory_forget")
        self.assertEqual(forget["params"], {"ids": ["aaa111"]})
        self.assertTrue(forget["archived_first"])
        self.assertEqual(self.page(), PAGE)  # forgetting leaves the page alone

    def test_a_forget_without_a_reason_or_of_an_unlisted_item_is_asked_about_again(self):
        self.answer({"ops": [{"op": "forget", "item": "c9", "reason": "gone"}]},
                    {"ops": [{"op": "forget", "item": "c2"}]},
                    {"ops": [], "diary": "d"})
        got = self.dream()
        self.assertFalse(got["ok"], got)
        self.assertEqual(self.calls_of("memory_forget"), [])
        chats = self.calls_of("inference_agent_chat")
        self.assertEqual(len(chats), 2)
        self.assertIn("c9", chats[1]["params"]["message"])

    def test_more_forgets_than_the_cap_change_nothing(self):
        many = [{"op": "forget", "item": "c1", "reason": "x"}] * 21
        self.answer({"ops": many}, {"ops": many})
        got = self.dream()
        self.assertFalse(got["ok"], got)
        self.assertEqual(self.calls_of("memory_forget"), [])
        self.assertEqual(self.page(), PAGE)

    # --- the page and the snapshot -------------------------------------------

    def test_a_dream_updates_the_page_and_leaves_a_snapshot_for_the_diary(self):
        self.answer({"ops": [{"op": "add", "section": "what i have shipped", "text": "csvstat"},
                             {"op": "edit", "id": "m1", "text": "ana (x): ships the crab cli friday."},
                             {"op": "forget", "item": "c2", "reason": "superseded by m1"}],
                     "diary": "a good day."})
        got = self.dream()
        self.assertTrue(got["ok"], got)
        self.assertIn("csvstat", self.page())
        [snap] = sorted((self.root / "state" / "dream" / "snapshots").glob("*.md"))
        self.assertEqual(snap.name, "20260914T070000Z-night.md")
        text = snap.read_text()
        meta, body = text.split("\n---\n", 1)
        meta = json.loads(meta.removeprefix("---\n"))
        self.assertEqual(meta, {"at": T0, "slot": "night", "add": 1, "edit": 1, "retire": 0, "forget": 1})
        self.assertEqual(body, self.page())

    def test_a_midday_dream_writes_no_diary_and_its_snapshot_says_midday(self):
        self.answer({"ops": [{"op": "add", "section": "people", "text": "bob (moltbook): new."}]})
        got = self.dream("--light")
        self.assertTrue(got["ok"], got)
        self.assertFalse(got["diary"])
        self.assertFalse((self.root / "state" / "diary").exists() and any((self.root / "state" / "diary").iterdir()))
        [snap] = (self.root / "state" / "dream" / "snapshots").glob("*.md")
        self.assertTrue(snap.name.endswith("-midday.md"))

    def test_a_failed_dream_leaves_no_snapshot(self):
        self.answer("not json", "still not json")
        got = self.dream()
        self.assertFalse(got["ok"], got)
        self.assertFalse((self.root / "state" / "dream" / "snapshots").exists())

    def test_the_core_not_answering_for_its_learnings_still_dreams(self):
        os.environ["FAKE_ITEMS"] = str(self.scratch / "missing.json")  # the fake core then fails the call
        self.answer({"ops": [], "diary": "d"})
        got = self.dream()
        self.assertTrue(got["ok"], got)
        self.assertNotIn("## what your memory holds", self.message())


if __name__ == "__main__":
    unittest.main()
