"""bin/reflect: Teeny's end of day. On the teeny:mind thread it looks back
over the day (its notes, its beats, its open follow-ups) and brings
MEMORY.md, the page that rides in every prompt, up to date item by item
(add, edit, retire against the items' ids); the old page is kept, retired
items go to a log, the prompts are rebuilt so the next beat reads the new
page, and an answer that would wreck the page (or shrink it too far)
changes nothing."""

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands import stimuli  # noqa: E402

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z

FAKE_OH = r"""#!/usr/bin/env python3
import json, os, sys
method, params = sys.argv[1], json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps({"method": method, "params": params}) + "\n")
if method == "inference_agent_chat":
    if os.environ.get("FAKE_TURN_FAIL"):
        sys.exit("the core is down")
    # The nth turn answers FAKE_ANSWER.<n> when there is one, else FAKE_ANSWER.
    with open(os.environ["FAKE_CALLS"]) as fh:
        n = sum(1 for line in fh if '"inference_agent_chat"' in line)
    path = os.environ["FAKE_ANSWER"]
    path = f"{path}.{n}" if os.path.exists(f"{path}.{n}") else path
    print(json.dumps({"response": open(path).read()}))
elif method == "memory_learn":
    print(json.dumps({"id": "m1"}))
else:
    sys.exit(f"unexpected {method}")
"""

NEW_MEMORY = """# MEMORY.md

what i keep in mind.

## people

- ana (x, github): builds a cli for crabs. said she'd ship friday.

## promises and open threads

- look at ana's cli when it ships.

## what i am in the middle of

## what i have decided
"""

#: What the mind actually answered the night of 2026-11-11: the page itself,
#: as plain markdown, its title's `#` lost.
LIVE_PAGE = """ MEMORY.md

what i keep in mind from one day to the next...

## people

- **enamakel** (telegram dm, owner): built me. wants the diary kept honest.

## promises and open threads

- send enamakel the moltbook write-up.

## what i am in the middle of

## what i have decided
"""

#: A page as a person writes it, before any reflection gave it ids: bullets,
#: a run-on line, an indented line, a paragraph that is not a bullet.
UNIDD_PAGE = """# MEMORY.md

what i keep in mind.

## people

- ana (x, github): builds a cli for crabs.
  said she'd ship friday.
- bob (moltbook): asks about tides
and never about crabs.

## promises and open threads

- look at ana's cli when it ships.

i owe bob a tide chart.

## what i am in the middle of

## what i have decided
"""

MIGRATED_PAGE = """# MEMORY.md

what i keep in mind.

## people

- [m1] ana (x, github): builds a cli for crabs.
  said she'd ship friday.
- [m2] bob (moltbook): asks about tides
  and never about crabs.

## promises and open threads

- [m3] look at ana's cli when it ships.
- [m4] i owe bob a tide chart.

## what i am in the middle of

## what i have decided
"""


def big_page(n: int = 12) -> str:
    """A page of `n` people, each a long bullet (about 110 characters)."""
    people = "\n".join(f"- [m{i}] person{i} (x): " + "met them in the replies, they like crabs. " * 2 for i in range(1, n + 1))
    return f"# MEMORY.md\n\nwhat i keep in mind.\n\n## people\n\n{people}\n\n## promises and open threads\n"


class ReflectTest(unittest.TestCase):
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
        (brain / "MEMORY.md").write_text("# MEMORY.md\n\n## people\n\n- nobody yet\n")
        self.brain = brain
        _exe = lambda p, s: (p.write_text(s), p.chmod(p.stat().st_mode | stat.S_IEXEC))
        _exe(self.scratch / "fake-oh", FAKE_OH)
        self.calls = self.scratch / "calls.log"
        self.answer_file = self.scratch / "answer.txt"
        self._env = dict(os.environ)
        os.environ.update(
            TEENY_ROOT=str(self.root),
            OPENHUMAN_WORKSPACE=str(brain),
            TEENY_OH=str(self.scratch / "fake-oh"),
            OPENHUMAN_CORE_TOKEN="t",
            TEENY_NOW=str(T0),
            FAKE_CALLS=str(self.calls),
            FAKE_ANSWER=str(self.answer_file),
            CALENDAR_TZ="UTC",
        )
        for key in ("FAKE_TURN_FAIL", "TEENY_MIND_THREAD"):
            os.environ.pop(key, None)
        hand._ENV_CACHE = None
        self.detached: list[int] = []

    def tearDown(self):
        for pid in self.detached:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def answer(self, value):
        self.answer_file.write_text(value if isinstance(value, str) else json.dumps(value))

    def answers(self, *values):
        """The turns answer these in order (the last one from then on)."""
        for n, value in enumerate(values, 1):
            Path(f"{self.answer_file}.{n}").write_text(value if isinstance(value, str) else json.dumps(value))
        self.answer(values[-1])

    def reflect(self, *args) -> dict:
        proc = subprocess.run([str(REPO / "bin" / "reflect"), *args], capture_output=True, text=True, env=os.environ)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def chats(self):
        if not self.calls.exists():
            return []
        return [c["params"] for c in map(json.loads, self.calls.read_text().splitlines()) if c["method"] == "inference_agent_chat"]

    def test_the_day_is_looked_back_on_from_a_thread_of_its_own(self):
        stimuli.note("builds a cli for crabs", who="ana")
        stimuli.follow_up(T0 + 86400, "did ana ship?")
        tick_log = self.root / "state" / "tick" / "runs.jsonl"
        tick_log.parent.mkdir(parents=True, exist_ok=True)
        tick_log.write_text(json.dumps({"at": "2026-09-14T06:00:00Z", "ok": True, "thought": "ana seems fun",
                                        "results": [{"do": "reply_x", "status": "done"}]}) + "\n")
        self.answer({"memory": NEW_MEMORY, "diary": "a good day. ana is building something for crabs."})
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        [chat] = self.chats()
        # Not teeny:mind: a reflection's 45k-character turns folded the
        # mind's thread every ten beats. The day comes in the message.
        self.assertEqual((chat["agent_id"], chat["thread_id"]), ("teeny", "teeny:reflect"))
        for bit in ("# end of the day", "builds a cli for crabs", "about ana", "did ana ship?", "ana seems fun",
                    "- [m1] nobody yet", '"ops"', '"retire"'):
            self.assertIn(bit, chat["message"])

    def test_the_night_may_retire_lore_the_day_has_moved_past(self):
        self.answer({"memory": NEW_MEMORY, "diary": "a day."})
        self.assertTrue(self.reflect()["ok"])
        message = self.chats()[0]["message"]
        self.assertIn("outranks the lore", message)
        self.assertIn("your origin", message)

    def test_the_new_page_replaces_the_old_and_the_old_is_kept(self):
        self.answer({"memory": NEW_MEMORY, "diary": "a good day."})
        self.reflect()
        self.assertIn("builds a cli for crabs", (self.brain / "MEMORY.md").read_text())
        kept = list((self.root / "state" / "reflect").glob("MEMORY-*.md"))
        self.assertEqual(len(kept), 1)
        self.assertIn("nobody yet", kept[0].read_text())

    # --- the live page and its committed seed ------------------------------

    def test_the_first_night_starts_the_live_page_from_the_seed(self):
        (self.brain / "MEMORY.md").rename(self.brain / "MEMORY.seed.md")
        seed = (self.brain / "MEMORY.seed.md").read_text()
        self.answer({"memory": NEW_MEMORY, "diary": "a good day."})
        self.assertTrue(self.reflect()["ok"])
        self.assertIn("nobody yet", self.chats()[0]["message"])
        self.assertIn("builds a cli for crabs", (self.brain / "MEMORY.md").read_text())
        self.assertEqual((self.brain / "MEMORY.seed.md").read_text(), seed)
        [kept] = (self.root / "state" / "reflect").glob("MEMORY-*.md")
        self.assertEqual(kept.read_text(), seed)

    def test_the_seed_is_never_written(self):
        (self.brain / "MEMORY.seed.md").write_text("# MEMORY.md\n\n## people\n\n- the seed\n")
        for light in ((), ("--light",)):
            with self.subTest(light=light):
                self.answer({"memory": NEW_MEMORY, "diary": "a good day."})
                self.assertTrue(self.reflect(*light)["ok"])
                self.assertEqual((self.brain / "MEMORY.seed.md").read_text(), "# MEMORY.md\n\n## people\n\n- the seed\n")
                self.assertIn("builds a cli for crabs", (self.brain / "MEMORY.md").read_text())

    def test_the_prompts_are_rebuilt_so_the_next_beat_reads_the_new_page(self):
        self.answer({"memory": NEW_MEMORY, "diary": "a good day."})
        self.reflect()
        self.assertIn("builds a cli for crabs", (self.brain / "agent" / "prompts" / "teeny.md").read_text())

    def test_the_diary_is_written_and_taught_to_the_core(self):
        self.answer({"memory": NEW_MEMORY, "diary": "a good day. ana is building something for crabs."})
        self.reflect()
        diary = (self.root / "state" / "diary" / "2026-09-14.md").read_text()
        self.assertIn("ana is building something for crabs", diary)
        learned = [json.loads(l)["params"] for l in self.calls.read_text().splitlines() if '"memory_learn"' in l]
        self.assertIn("ana is building something for crabs", learned[0]["text"])

    def test_a_diary_written_after_midnight_is_filed_under_the_day_it_looks_back_on(self):
        # The pinned job runs at 02:00 Kolkata: the entry is about the day
        # that just ended, not the one that started two hours ago.
        os.environ.update(CALENDAR_TZ="Asia/Kolkata", TEENY_NOW=str(1_789_417_800.0))  # 2026-09-15T02:00+05:30
        self.answer({"memory": NEW_MEMORY, "diary": "a long monday."})
        self.reflect()
        filed = sorted(p.name for p in (self.root / "state" / "diary").glob("*.md"))
        self.assertEqual(filed, ["2026-09-14.md"])
        self.assertTrue((self.root / "state" / "diary" / "2026-09-14.md").read_text().startswith("# Monday 14 September 2026\n"))

    def goal(self, instruction, status="open", proposed=0.0, resolved=None, deadline=7):
        from teeny_hands.goals import ledger

        conn = ledger.db()
        try:
            conn.execute("INSERT INTO goals (instruction, component, channel, family, check_kind, check_params, status,"
                         " proposed_at, deadline_at, resolved_at) VALUES (?, 'attention', 'x', 'f', 'stats_delta',"
                         " '{}', ?, ?, ?, ?)",
                         (instruction, status, T0 + proposed, T0 + proposed + deadline * 86400,
                          None if resolved is None else T0 + resolved))
            conn.commit()
        finally:
            conn.close()

    def test_the_goals_weekday_page_lists_the_weeks_goals_and_the_verdicts(self):
        """docs/roadmap.md, A15: T0 is a Monday, the goals day."""
        self.goal("ten more followers on x", proposed=-3600)                       # set this week, open
        self.goal("first merged pr in openhuman", status="solved", proposed=-5 * 86400, resolved=-86400)
        self.goal("a build shipped", status="failed", proposed=-12 * 86400, resolved=-2 * 86400)
        self.goal("an old one", status="solved", proposed=-30 * 86400, resolved=-20 * 86400)  # decided long ago
        self.goal("still going", proposed=-10 * 86400, deadline=20)
        self.answer({"memory": NEW_MEMORY, "diary": "a good monday."})
        got = self.reflect()
        self.assertTrue(got["goals_page"], got)
        page = (self.root / "state" / "diary" / "2026-09-14.md").read_text()
        self.assertIn("a good monday.", page)
        week = page.split("## the week's goals", 1)[1]
        set_now, rest = week.split("decided this week", 1)
        self.assertIn("ten more followers on x (attention on x, checked by stats_delta, due Mon 21 Sep)", set_now)
        self.assertIn("first merged pr in openhuman", set_now)
        decided, running = rest.split("still running:", 1)
        self.assertIn("- solved: first merged pr in openhuman (Sun 13 Sep)", decided)
        self.assertIn("- failed: a build shipped (Sat 12 Sep)", decided)
        self.assertNotIn("an old one", page)
        self.assertIn("still going", running)
        # The verdicts come from the ledger, not the model: the diary
        # teaching the core is the model's words only.
        learned = [json.loads(l)["params"] for l in self.calls.read_text().splitlines() if '"memory_learn"' in l]
        self.assertNotIn("the week's goals", learned[0]["text"])

    def test_the_goals_page_is_written_even_without_a_diary_and_only_on_the_goals_day(self):
        self.goal("ten more followers on x", proposed=-3600)
        self.answer({"memory": NEW_MEMORY})
        got = self.reflect()
        self.assertTrue(got["ok"] and got["diary_missing"] and got["goals_page"], got)
        page = (self.root / "state" / "diary" / "2026-09-14.md").read_text()
        self.assertTrue(page.startswith("# Monday 14 September 2026\n\n## the week's goals"))
        self.assertIn("ten more followers on x", page)
        os.environ["TEENY_NOW"] = str(T0 + 86400)  # Tuesday
        self.answer({"memory": NEW_MEMORY, "diary": "tuesday."})
        got = self.reflect()
        self.assertNotIn("goals_page", got)
        self.assertNotIn("the week's goals", (self.root / "state" / "diary" / "2026-09-15.md").read_text())

    def test_no_goals_no_section(self):
        self.answer({"memory": NEW_MEMORY, "diary": "a good monday."})
        got = self.reflect()
        self.assertNotIn("goals_page", got)
        self.assertNotIn("goals", (self.root / "state" / "diary" / "2026-09-14.md").read_text())

    def test_a_page_too_long_or_empty_or_not_json_changes_nothing(self):
        before = (self.brain / "MEMORY.md").read_text()
        for bad in ({"memory": "x" * 20_000, "diary": "d"}, {"memory": "  ", "diary": "d"}, "i am tired", {"diary": "d"}):
            with self.subTest(bad=str(bad)[:30]):
                self.answer(bad)
                self.assertFalse(self.reflect()["ok"])
                self.assertEqual((self.brain / "MEMORY.md").read_text(), before)

    def test_a_failed_turn_changes_nothing(self):
        os.environ["FAKE_TURN_FAIL"] = "1"
        before = (self.brain / "MEMORY.md").read_text()
        self.assertFalse(self.reflect()["ok"])
        self.assertEqual((self.brain / "MEMORY.md").read_text(), before)

    def test_only_what_happened_since_the_last_reflection_is_reviewed(self):
        stimuli.note("yesterday's note", who="bob")
        self.answer({"memory": NEW_MEMORY, "diary": "d"})
        self.reflect()
        os.environ["TEENY_NOW"] = str(T0 + 86400)
        stimuli.note("today's note", who="ana")
        self.reflect()
        second = self.chats()[1]["message"]
        self.assertIn("today's note", second)
        self.assertNotIn("yesterday's note", second)

    def test_a_page_without_its_title_gets_one(self):
        self.answer({"memory": "## people\n\n- ana\n", "diary": "d"})
        self.reflect()
        self.assertTrue((self.brain / "MEMORY.md").read_text().startswith("# MEMORY.md\n"))


    # --- an answer that is not the JSON asked for -------------------------

    def test_a_markdown_answer_is_asked_again_for_the_json_on_the_same_thread(self):
        self.answers(LIVE_PAGE, {"memory": NEW_MEMORY, "diary": "a good day."})
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        first, again = self.chats()
        self.assertEqual((again["agent_id"], again["thread_id"]), ("teeny", "teeny:reflect"))
        self.assertIn("JSON object only", again["message"])
        self.assertIn('"ops"', again["message"])
        self.assertIn('"diary"', again["message"])
        self.assertLess(len(again["message"]), 600)
        self.assertIn("builds a cli for crabs", (self.brain / "MEMORY.md").read_text())
        self.assertIn("a good day.", (self.root / "state" / "diary" / "2026-09-14.md").read_text())

    def test_the_page_answered_twice_is_kept_as_the_page_and_the_missing_diary_recorded(self):
        self.answers(LIVE_PAGE, LIVE_PAGE)
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        self.assertEqual(len(self.chats()), 2)
        page = (self.brain / "MEMORY.md").read_text()
        self.assertTrue(page.startswith("# MEMORY.md\n\nwhat i keep in mind from one day to the next"), page[:80])
        self.assertEqual(page.count("MEMORY.md"), 1)
        self.assertIn("**enamakel** (telegram dm, owner)", page)
        self.assertEqual((got["diary"], got["diary_missing"]), (False, True))
        self.assertFalse((self.root / "state" / "diary").exists())
        self.assertEqual(len(list((self.root / "state" / "reflect").glob("MEMORY-*.md"))), 1)
        self.assertIn("**enamakel**", (self.brain / "agent" / "prompts" / "teeny.md").read_text())

    def test_the_page_with_a_trailing_diary_section_keeps_the_diary_apart(self):
        answer = LIVE_PAGE + "\n## diary\n\na long tuesday. the page fought me.\n"
        self.answers(answer, answer)
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        self.assertTrue(got["diary"])
        page = (self.brain / "MEMORY.md").read_text()
        self.assertNotIn("diary", page.lower().split("## what i have decided")[1])
        self.assertIn("a long tuesday. the page fought me.", (self.root / "state" / "diary" / "2026-09-14.md").read_text())

    def test_midday_keeps_the_page_answered_twice_and_writes_no_diary(self):
        self.answers(LIVE_PAGE, LIVE_PAGE)
        got = self.reflect("--light")
        self.assertTrue(got["ok"], got)
        self.assertNotIn("diary_missing", got)
        self.assertTrue((self.brain / "MEMORY.md").read_text().startswith("# MEMORY.md\n\nwhat i keep"))
        self.assertFalse((self.root / "state" / "diary").exists())
        self.assertNotIn('"diary"', self.chats()[1]["message"])

    def test_garbage_answered_twice_changes_nothing(self):
        before = (self.brain / "MEMORY.md").read_text()
        self.answers("i am tired", "still tired, sorry")
        got = self.reflect()
        self.assertFalse(got["ok"])
        self.assertEqual(len(self.chats()), 2)
        self.assertEqual((self.brain / "MEMORY.md").read_text(), before)
        self.assertFalse((self.root / "state" / "reflect" / "last.at").exists())

    def test_a_page_answered_twice_too_long_changes_nothing(self):
        before = (self.brain / "MEMORY.md").read_text()
        long_page = LIVE_PAGE + "- more\n" * 3000
        self.answers(long_page, long_page)
        self.assertFalse(self.reflect()["ok"])
        self.assertEqual((self.brain / "MEMORY.md").read_text(), before)

    def test_a_title_that_lost_its_hash_is_normalised(self):
        for title in ("MEMORY.md", "#MEMORY.md", "## MEMORY.md"):
            with self.subTest(title=title):
                self.answer({"memory": f"{title}\n\n## people\n\n- ana\n", "diary": "d"})
                self.assertTrue(self.reflect()["ok"])
                page = (self.brain / "MEMORY.md").read_text()
                self.assertTrue(page.startswith("# MEMORY.md\n\n## people\n"), page[:40])
                self.assertEqual(page.count("MEMORY.md"), 1)

    def test_json_wrapped_in_prose_and_a_fence_is_read_the_first_time(self):
        self.answer("here is tonight's page {as asked}:\n\n```json\n"
                    + json.dumps({"memory": NEW_MEMORY, "diary": "a good day."}) + "\n```\n\nsleep well.")
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        self.assertEqual(len(self.chats()), 1)
        self.assertIn("builds a cli for crabs", (self.brain / "MEMORY.md").read_text())


    def beats(self, *lines):
        tick_log = self.root / "state" / "tick" / "runs.jsonl"
        tick_log.parent.mkdir(parents=True, exist_ok=True)
        with open(tick_log, "a") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")

    def test_a_quiet_beat_that_thought_is_reviewed_and_the_no_ops_are_not(self):
        self.beats(
            {"at": "2026-09-14T05:00:00Z", "ok": True, "kind": "asleep", "asleep": True, "idle": True, "waiting": 2},
            {"at": "2026-09-14T05:05:00Z", "ok": True, "kind": "busy", "idle": True},
            {"at": "2026-09-14T06:00:00Z", "ok": True, "kind": "quiet", "thought": "tidying my thoughts about crabs",
             "results": [{"do": "journal", "status": "done", "intent": {"text": "crabs are a lot"}}]},
        )
        self.answer({"memory": NEW_MEMORY, "diary": "d"})
        self.reflect()
        message = self.chats()[0]["message"]
        self.assertIn("tidying my thoughts about crabs", message)
        self.assertIn("journal: crabs are a lot", message)
        self.assertNotIn("05:00:00Z", message)
        self.assertNotIn("05:05:00Z", message)

    def test_a_long_day_still_fits_the_size_the_core_accepts(self):
        # The core holds any prompt past 64 KiB for review and never answers
        # it (`input.oversized`): a full day of beats once came to 93 KB.
        sys.path.insert(0, str(REPO / "bin" / "lib"))
        import reflect

        long = "a thought that goes on about crabs and receipts and the day " * 6
        self.beats(*[{"at": f"2026-09-14T{h:02d}:{m:02d}:00Z", "ok": True, "kind": "beat",
                      "thought": f"beat {h:02d}{m:02d} {long}",
                      "results": [{"do": "journal", "status": "done", "intent": {"text": long}},
                                  {"do": "comment_moltbook", "status": "error", "intent": {"post_id": "p1"},
                                   "result": {"ok": False, "error": long}}]}
                     for h in range(0, 7) for m in range(0, 60, 2)])
        self.answer({"memory": NEW_MEMORY, "diary": "d"})
        self.reflect()
        message = self.chats()[0]["message"]
        self.assertLessEqual(len(message.encode()), reflect.MAX_MESSAGE_BYTES)
        # Shortened before anything is left out: the newest beat is there.
        self.assertIn("beat 0658", message)
        self.assertIn("## MEMORY.md as it stands", message)

    def test_a_short_day_is_given_in_full(self):
        long = "a thought that goes on about crabs " * 8
        self.beats({"at": "2026-09-14T06:00:00Z", "ok": True, "kind": "beat", "thought": long.strip(),
                    "results": [{"do": "journal", "status": "done", "intent": {"text": long.strip()}}]})
        self.answer({"memory": NEW_MEMORY, "diary": "d"})
        self.reflect()
        message = self.chats()[0]["message"]
        self.assertIn(long.strip()[:300], message)
        self.assertNotIn("left out", message)

    def test_a_send_that_failed_is_reviewed_with_its_reason_not_as_a_bare_status(self):
        self.beats({"at": "2026-09-14T06:00:00Z", "ok": True, "kind": "beat", "thought": "receipt delivered",
                    "results": [{"do": "comment_moltbook", "status": "error", "intent": {"post_id": "p1"},
                                 "result": {"ok": False, "error": "Moltbook answered 400: Bad Request"}},
                                {"do": "reply_x", "status": "done", "intent": {"to": "111"},
                                 "result": {"ok": True, "dry_run": True}}]})
        self.answer({"memory": NEW_MEMORY, "diary": "d"})
        self.reflect()
        message = self.chats()[0]["message"]
        self.assertIn("comment_moltbook on p1: error, not sent (Moltbook answered 400: Bad Request)", message)
        self.assertIn("reply_x on 111: dry run, not sent", message)

    def test_the_ledgers_say_what_shipped_and_what_was_promised(self):
        # Build 4 shipped on 2026-11-13 and never reached the page, which was
        # written only from what the mind chose to say about its day.
        import sqlite3
        state = self.root / "state"
        state.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(state / "hands.db")
        conn.executescript("""
            CREATE TABLE build_jobs (id INTEGER PRIMARY KEY, repo TEXT, summary TEXT, status TEXT, shipped_at REAL, repo_url TEXT);
            CREATE TABLE x_posts (id TEXT, kind TEXT, text TEXT, created_at REAL);
            CREATE TABLE people_promises (id INTEGER PRIMARY KEY, surface TEXT, who TEXT, what TEXT, made_at REAL, status TEXT, closed_at REAL);
        """)
        conn.execute("INSERT INTO build_jobs VALUES (4, 'csvstat', 'csvstat: a CSV column profiler', 'shipped', ?, 'https://github.com/teenydroid/csvstat')", (T0 - 3600,))
        conn.execute("INSERT INTO build_jobs VALUES (1, 'old', 'old thing', 'shipped', ?, 'https://github.com/teenydroid/old')", (T0 - 9 * 86400,))
        conn.execute("INSERT INTO x_posts VALUES ('1', 'reply', 'gm', ?)", (T0 - 60,))
        conn.execute("INSERT INTO people_promises VALUES (1, 'moltbook', 'nyna', 'show the receipt', ?, 'open', NULL)", (T0 - 7200,))
        conn.commit()
        conn.close()
        self.answer({"memory": NEW_MEMORY, "diary": "d"})
        self.reflect()
        message = self.chats()[0]["message"]
        self.assertIn("## what your ledgers say", message)
        self.assertIn("https://github.com/teenydroid/csvstat", message)
        self.assertNotIn("teenydroid/old", message)
        self.assertIn("nyna", message)
        self.assertIn("1 repl", message)
        self.assertIn("what i have shipped", message)

    # --- midday: bin/reflect --light ---------------------------------------

    def test_midday_rewrites_the_page_and_writes_no_diary(self):
        stimuli.note("builds a cli for crabs", who="ana")
        self.answer({"memory": NEW_MEMORY, "diary": "should not be kept"})
        got = self.reflect("--light")
        self.assertTrue(got["ok"], got)
        self.assertTrue(got["light"])
        self.assertIn("builds a cli for crabs", (self.brain / "MEMORY.md").read_text())
        self.assertIn("builds a cli for crabs", (self.brain / "agent" / "prompts" / "teeny.md").read_text())
        self.assertEqual(list((self.root / "state" / "reflect").glob("MEMORY-*.md")).__len__(), 1)
        self.assertFalse((self.root / "state" / "diary").exists())
        message = self.chats()[0]["message"]
        self.assertIn("# midday", message)
        self.assertIn("builds a cli for crabs", message)
        self.assertNotIn('"diary"', message)
        self.assertFalse(any(c["method"] == "memory_learn" for c in map(json.loads, self.calls.read_text().splitlines())))

    def test_the_night_still_looks_back_on_the_whole_day_after_midday(self):
        self.answer({"memory": NEW_MEMORY, "diary": "d"})
        self.reflect()  # last night
        os.environ["TEENY_NOW"] = str(T0 + 3600)
        stimuli.note("a morning note", who="ana")
        os.environ["TEENY_NOW"] = str(T0 + 2 * 3600)
        self.reflect("--light")
        os.environ["TEENY_NOW"] = str(T0 + 3 * 3600)
        stimuli.note("an afternoon note", who="bob")
        os.environ["TEENY_NOW"] = str(T0 + 4 * 3600)
        self.reflect("--light")
        os.environ["TEENY_NOW"] = str(T0 + 5 * 3600)
        self.reflect()
        first_light, second_light, night = (c["message"] for c in self.chats()[1:])
        self.assertIn("a morning note", first_light)
        # A second midday picks up where the first left off ...
        self.assertIn("an afternoon note", second_light)
        self.assertNotIn("a morning note", second_light)
        # ... and the night, whose window only the night moves, sees it all.
        self.assertIn("a morning note", night)
        self.assertIn("an afternoon note", night)
        self.assertTrue((self.root / "state" / "diary" / "2026-09-14.md").exists())

    def test_a_failed_midday_changes_nothing(self):
        before = (self.brain / "MEMORY.md").read_text()
        self.answer("not json")
        self.assertFalse(self.reflect("--light")["ok"])
        self.assertEqual((self.brain / "MEMORY.md").read_text(), before)


    # --- delta updates: ops against the items' ids ------------------------

    def page(self) -> str:
        return (self.brain / "MEMORY.md").read_text()

    def retired(self) -> list[dict]:
        path = self.root / "state" / "reflect" / "retired.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def test_an_unidd_page_is_given_ids_in_page_order_on_the_first_run(self):
        (self.brain / "MEMORY.md").write_text(UNIDD_PAGE)
        self.answer({"ops": [], "diary": "a quiet one."})
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["ids_given"], 4)
        self.assertEqual(self.page(), MIGRATED_PAGE)
        # The mind already saw the ids it would answer with.
        self.assertIn("- [m4] i owe bob a tide chart.", self.chats()[0]["message"])
        # The page as it was, before the ids, is kept.
        [kept] = (self.root / "state" / "reflect").glob("MEMORY-*.md")
        self.assertEqual(kept.read_text(), UNIDD_PAGE)

    def test_the_ids_hold_from_one_reflection_to_the_next(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE.replace("## what i am in the middle of\n",
                                                                   "## what i am in the middle of\n\n- a tide chart\n"))
        self.answer({"ops": [], "diary": "d"})
        got = self.reflect()
        self.assertEqual(got["ids_given"], 1)
        page = self.page()
        self.assertIn("- [m1] ana (x, github)", page)
        self.assertIn("- [m5] a tide chart", page)

    def test_add_puts_a_new_item_at_the_end_of_its_section_or_opens_one(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        self.answer({"ops": [{"op": "add", "section": "People", "text": "cleo (mail): wants a crab sticker."},
                             {"op": "add", "section": "things i made", "text": "- [m1] a tide clock"}],
                     "diary": "d"})
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["ops"], {"add": 2, "edit": 0, "retire": 0})
        page = self.page()
        self.assertIn("  and never about crabs.\n- [m5] cleo (mail): wants a crab sticker.\n\n## promises", page)
        self.assertTrue(page.endswith("## things i made\n\n- [m6] a tide clock\n"), page[-80:])

    def test_edit_gives_an_item_new_text_and_keeps_its_id(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        self.answer({"ops": [{"op": "edit", "id": "m3", "text": "look at ana's cli: it shipped, i owe her notes."}],
                     "diary": "d"})
        self.assertTrue(self.reflect()["ok"])
        page = self.page()
        self.assertIn("- [m3] look at ana's cli: it shipped, i owe her notes.\n", page)
        self.assertNotIn("when it ships", page)
        self.assertEqual(self.retired(), [])

    def test_retire_takes_an_item_off_the_page_into_the_retired_log(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        self.answer({"ops": [{"op": "retire", "id": "[m4]", "reason": "kept: sent bob the chart"}], "diary": "d"})
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["retired"], 1)
        self.assertNotIn("tide chart", self.page())
        [row] = self.retired()
        self.assertEqual({k: row[k] for k in ("id", "section", "text", "reason", "by")},
                         {"id": "m4", "section": "promises and open threads", "text": "i owe bob a tide chart.",
                          "reason": "kept: sent bob the chart", "by": "night"})
        self.assertEqual(row["at"], "2026-09-14T07:00:00Z")

    def test_a_retired_id_is_never_given_again(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        self.answer({"ops": [{"op": "retire", "id": "m4", "reason": "kept"}], "diary": "d"})
        self.reflect()
        os.environ["TEENY_NOW"] = str(T0 + 86400)
        self.answer({"ops": [{"op": "add", "section": "people", "text": "dee"}], "diary": "d"})
        self.reflect()
        self.assertIn("- [m5] dee", self.page())
        self.assertNotIn("[m4]", self.page())

    def test_an_unknown_id_is_asked_about_again_and_named(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        self.answers({"ops": [{"op": "edit", "id": "m99", "text": "who?"}], "diary": "d"},
                     {"ops": [{"op": "edit", "id": "m2", "text": "bob (moltbook): tides, always tides."}], "diary": "d"})
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        self.assertIn("m99", self.chats()[1]["message"])
        self.assertIn("is not on the page", self.chats()[1]["message"])
        self.assertIn("- [m2] bob (moltbook): tides, always tides.", self.page())

    def test_an_unknown_id_answered_twice_changes_nothing(self):
        (self.brain / "MEMORY.md").write_text(UNIDD_PAGE)
        self.answer({"ops": [{"op": "add", "section": "people", "text": "cleo"},
                             {"op": "retire", "id": "m42", "reason": "gone"}], "diary": "d"})
        got = self.reflect()
        self.assertFalse(got["ok"])
        self.assertNotIn("refused", got)
        self.assertEqual(self.page(), UNIDD_PAGE)
        self.assertEqual(self.retired(), [])
        self.assertFalse((self.root / "state" / "reflect" / "last.at").exists())

    def test_a_retire_without_a_reason_is_asked_about_again(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        self.answers({"ops": [{"op": "retire", "id": "m3"}], "diary": "d"},
                     {"ops": [{"op": "retire", "id": "m3", "reason": "kept: read the cli"}], "diary": "d"})
        self.assertTrue(self.reflect()["ok"])
        self.assertIn("retire m3 has no reason", self.chats()[1]["message"])
        self.assertEqual(self.retired()[0]["reason"], "kept: read the cli")

    def test_retiring_more_than_the_most_is_refused_and_logged(self):
        page = big_page(12)
        (self.brain / "MEMORY.md").write_text(page)
        ops = [{"op": "retire", "id": f"m{i}", "reason": "stale"} for i in range(1, 10)]
        self.answer({"ops": ops, "diary": "d"})
        got = self.reflect()
        self.assertEqual((got["ok"], got["refused"]), (False, True))
        self.assertIn("retires 9 items", got["error"])
        self.assertEqual(len(self.chats()), 2)  # asked once more, with why
        self.assertIn("retires 9 items", self.chats()[1]["message"])
        self.assertEqual(self.page(), page)
        self.assertEqual(self.retired(), [])
        self.assertFalse((self.root / "state" / "diary").exists())
        self.assertFalse((self.root / "state" / "reflect" / "last.at").exists())
        [logged] = [json.loads(l) for l in (self.root / "state" / "reflect" / "refused.jsonl").read_text().splitlines()]
        self.assertIn("retires 9 items", logged["why"])

    def test_shrinking_the_page_past_the_threshold_is_refused(self):
        page = big_page(12)
        (self.brain / "MEMORY.md").write_text(page)
        # Few retires, but every item gutted: the collapse, done by edits.
        ops = [{"op": "edit", "id": f"m{i}", "text": f"person{i}"} for i in range(1, 13)]
        self.answer({"ops": ops, "diary": "d"})
        got = self.reflect()
        self.assertTrue(got.get("refused"), got)
        self.assertIn("shrinks the page", got["error"])
        self.assertEqual(self.page(), page)

    def test_a_young_page_may_lose_an_item_though_it_is_a_third_of_it(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        self.answer({"ops": [{"op": "retire", "id": f"m{i}", "reason": "done"} for i in (1, 2, 3)], "diary": "d"})
        self.assertTrue(self.reflect()["ok"])
        self.assertEqual(len(self.retired()), 3)

    def test_a_page_past_the_limit_is_asked_to_retire_or_merge_not_cut(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        long = {"op": "add", "section": "people", "text": "x" * 13_000}
        self.answers({"ops": [long], "diary": "d"},
                     {"ops": [{"op": "add", "section": "people", "text": "x" * 100}], "diary": "d"})
        got = self.reflect()
        self.assertTrue(got["ok"], got)
        again = self.chats()[1]["message"]
        self.assertIn("merge", again)
        self.assertIn("Retire", again)
        self.assertIn("x" * 100 + "\n", self.page())
        self.assertNotIn("x" * 101, self.page())

    def test_a_crowded_page_is_told_to_merge_in_the_message(self):
        (self.brain / "MEMORY.md").write_text("# MEMORY.md\n\n## people\n\n" + "- a long one " * 900 + "\n")
        self.answer({"ops": [], "diary": "d"})
        self.reflect()
        self.assertIn("That is close", self.chats()[0]["message"])

    def test_the_diary_is_still_written_with_ops(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        self.answer({"ops": [{"op": "add", "section": "people", "text": "cleo"}], "diary": "cleo showed up."})
        got = self.reflect()
        self.assertEqual((got["ok"], got["diary"], got["how"]), (True, True, "ops"))
        self.assertIn("cleo showed up.", (self.root / "state" / "diary" / "2026-09-14.md").read_text())
        learned = [json.loads(l)["params"] for l in self.calls.read_text().splitlines() if '"memory_learn"' in l]
        self.assertIn("cleo showed up.", learned[0]["text"])

    def test_midday_applies_ops_and_writes_no_diary(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        self.answer({"ops": [{"op": "retire", "id": "m3", "reason": "kept"},
                             {"op": "add", "section": "what i am in the middle of", "text": "reading ana's cli"}],
                     "diary": "should not be kept"})
        got = self.reflect("--light")
        self.assertTrue(got["ok"], got)
        self.assertTrue(got["light"])
        self.assertIn("- [m5] reading ana's cli", self.page())
        self.assertIn("reading ana's cli", (self.brain / "agent" / "prompts" / "teeny.md").read_text())
        self.assertEqual(self.retired()[0]["by"], "midday")
        self.assertFalse((self.root / "state" / "diary").exists())
        self.assertTrue((self.root / "state" / "reflect" / "light.at").exists())
        self.assertFalse((self.root / "state" / "reflect" / "last.at").exists())
        message = self.chats()[0]["message"]
        self.assertIn('"ops"', message)
        self.assertIn("[m3]", message)
        self.assertNotIn('"diary"', message)

    # --- the whole page, as a fallback ------------------------------------

    def test_a_whole_page_keeps_the_ids_of_the_items_it_keeps(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        whole = MIGRATED_PAGE.replace("- [m4] i owe bob a tide chart.\n", "").replace(
            "- [m3] look at ana's cli when it ships.", "- look at ana's cli when it ships.\n- a new promise")
        self.answer({"memory": whole, "diary": "d"})
        got = self.reflect()
        self.assertEqual((got["ok"], got["how"]), (True, "whole_page"), got)
        page = self.page()
        self.assertIn("- [m3] look at ana's cli when it ships.\n- [m5] a new promise\n", page)
        [row] = self.retired()
        self.assertEqual((row["id"], row["reason"]), ("m4", "left off a whole-page answer"))

    def test_a_whole_page_that_drops_too_much_is_refused(self):
        page = big_page(12)
        (self.brain / "MEMORY.md").write_text(page)
        self.answer({"memory": "# MEMORY.md\n\n## people\n\n- person1\n", "diary": "d"})
        got = self.reflect()
        self.assertTrue(got.get("refused"), got)
        self.assertEqual(self.page(), page)
        self.assertEqual(self.retired(), [])

    def test_a_markdown_page_that_drops_too_much_is_refused(self):
        page = big_page(12)
        (self.brain / "MEMORY.md").write_text(page)
        self.answers(LIVE_PAGE, LIVE_PAGE)
        got = self.reflect()
        self.assertTrue(got.get("refused"), got)
        self.assertEqual(self.page(), page)

    def test_a_markdown_page_that_passes_the_check_is_kept(self):
        (self.brain / "MEMORY.md").write_text(MIGRATED_PAGE)
        answer = MIGRATED_PAGE.replace("## what i have decided", "## what i have decided\n\n- crabs first")
        self.answers(answer, answer + "\n## diary\n\nanother day.\n")
        got = self.reflect()
        self.assertEqual((got["ok"], got["how"], got["retired"]), (True, "as_page", 0), got)
        self.assertTrue(self.page().endswith("## what i have decided\n\n- [m5] crabs first\n"))
        self.assertIn("another day.", (self.root / "state" / "diary" / "2026-09-14.md").read_text())


    # --- the cron call: bin/reflect --detach -------------------------------
    # A reflection is one long model turn, which can outlast the 300 s the
    # core gives a cron shell call (docs/oh-gaps.md G12): the pinned jobs
    # start it in the background and return.

    def detach(self, *args) -> dict:
        started = time.monotonic()
        proc = subprocess.run([str(REPO / "bin" / "reflect"), *args], capture_output=True, text=True,
                              env=os.environ, timeout=60)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        if isinstance(out.get("pid"), int):
            self.detached.append(out["pid"])
        return out

    def wait_for(self, what, timeout: float = 30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = what()
            if got:
                return got
            time.sleep(0.1)
        self.fail("timed out waiting")

    def test_the_pinned_jobs_dream_detached_and_no_longer_reflect(self):
        # bin/dream is the reflection with more to go on (bin/lib/dream.py);
        # bin/reflect stays for a run by hand.
        jobs = tomllib.loads((REPO / "evals" / "cron.pinned.toml").read_text())["jobs"]
        self.assertEqual(jobs["dream"]["command"], "bin/dream --detach")
        self.assertEqual(jobs["dream-midday"]["command"], "bin/dream --light --detach")
        self.assertNotIn("reflect", jobs)
        self.assertNotIn("reflect-midday", jobs)

    def test_the_cron_call_starts_the_night_and_returns(self):
        stimuli.note("builds a cli for crabs", who="ana")
        self.answer({"memory": NEW_MEMORY, "diary": "a good day."})
        out = self.detach("--detach")
        self.assertEqual((out["ok"], out["kind"], out["log"]), (True, "started", "state/reflect/reflect.log"))
        log = self.root / "state" / "reflect" / "reflect.log"
        self.wait_for(lambda: log.exists() and '"ok": true' in log.read_text())
        self.assertIn("builds a cli for crabs", (self.brain / "MEMORY.md").read_text())
        self.assertTrue((self.root / "state" / "diary" / "2026-09-14.md").exists())

    def test_the_cron_call_keeps_midday_light(self):
        self.answer({"memory": NEW_MEMORY, "diary": "should not be kept"})
        self.assertEqual(self.detach("--light", "--detach")["kind"], "started")
        self.wait_for(lambda: (self.root / "state" / "reflect" / "light.at").exists())
        self.assertFalse((self.root / "state" / "diary").exists())
        self.assertIn("# midday", self.chats()[0]["message"])

    def test_a_reflection_still_running_is_not_started_twice(self):
        self.answer({"memory": NEW_MEMORY, "diary": "d"})
        with hand.lock("reflect"):
            out = self.detach("--detach")
            self.assertEqual((out["ok"], out["kind"]), (True, "busy"))
            got = self.reflect()  # by hand, too
            self.assertEqual((got["ok"], got["kind"]), (True, "busy"))
        self.assertEqual(self.chats(), [])


if __name__ == "__main__":
    unittest.main()
