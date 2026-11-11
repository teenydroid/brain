"""bin/reflect: Teeny's end of day. On the teeny:mind thread it looks back
over the day (its notes, its beats, its open follow-ups) and rewrites
MEMORY.md, the page that rides in every prompt; the old page is kept, the
prompts are rebuilt so the next beat reads the new one, and an answer that
would wreck the page changes nothing."""

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
    print(json.dumps({"response": open(os.environ["FAKE_ANSWER"]).read()}))
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

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def answer(self, value):
        self.answer_file.write_text(value if isinstance(value, str) else json.dumps(value))

    def reflect(self) -> dict:
        proc = subprocess.run([str(REPO / "bin" / "reflect")], capture_output=True, text=True, env=os.environ)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def chats(self):
        if not self.calls.exists():
            return []
        return [c["params"] for c in map(json.loads, self.calls.read_text().splitlines()) if c["method"] == "inference_agent_chat"]

    def test_the_day_is_looked_back_on_from_the_mind_thread(self):
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
        self.assertEqual((chat["agent_id"], chat["thread_id"]), ("teeny", "teeny:mind"))
        for bit in ("# end of the day", "builds a cli for crabs", "about ana", "did ana ship?", "ana seems fun",
                    "- nobody yet", '"memory"'):
            self.assertIn(bit, chat["message"])

    def test_the_new_page_replaces_the_old_and_the_old_is_kept(self):
        self.answer({"memory": NEW_MEMORY, "diary": "a good day."})
        self.reflect()
        self.assertIn("builds a cli for crabs", (self.brain / "MEMORY.md").read_text())
        kept = list((self.root / "state" / "reflect").glob("MEMORY-*.md"))
        self.assertEqual(len(kept), 1)
        self.assertIn("nobody yet", kept[0].read_text())

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


if __name__ == "__main__":
    unittest.main()
