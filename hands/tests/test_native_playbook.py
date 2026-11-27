"""bin/lib/native_playbook.py and the `run = "native"` rules of bin/lib/playbook.py:
which playbooks may run as an agent turn, what their message says, the allowlist
the core builds the agent's shell from, and that the shipped ones fit."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import native_playbook  # noqa: E402
import playbook  # noqa: E402
import studio_switch  # noqa: E402

HANDS = REPO / "hands"
BOOK = """+++
description = "a native test."
output = "{output}"
surface = "{surface}"
run = "native"
max_per_day = {day}
max_per_run = {run}
media = {media}
{extra}[[inputs]]
name = "slot"
run = "hands/x slot"
required = true
[[inputs]]
name = "hn"
run = "hands/hn top --max 5"
optional = true
+++
Write one short thing.
"""


class NativePlaybookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = Path(self.tmp.name)
        (self.brain / "playbooks").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name="t", output="post", surface="x", day=3, run=2, media="[]", extra=""):
        text = BOOK.format(output=output, surface=surface, day=day, run=run, media=media, extra=extra)
        (self.brain / "playbooks" / f"{name}.md").write_text(text)
        return playbook.load(self.brain, name, HANDS)

    def test_a_playbook_runs_on_bin_act_unless_it_says_native(self):
        book = playbook.load(REPO / "brain", "morning", HANDS)
        self.assertEqual((book["run"], book["max_per_run"]), ("act", 1))
        self.assertEqual(self.write()["run"], "native")

    def test_the_spec_is_exactly_its_inputs_its_hand_and_its_helpers(self):
        spec = native_playbook.spec(self.write(media='["image"]'))
        self.assertEqual(spec["agent"], "pb-t")
        self.assertEqual(spec["inputs"], [["hands/x", "slot"], ["hands/hn", "top", "--max", "5"]])
        self.assertEqual(spec["hand"], {"name": "x", "verbs": ["post"]})
        self.assertEqual(spec["helpers"], [["hands/image", "generate"], ["hands/x", "upload"]])
        self.assertEqual((spec["max_per_day"], spec["max_per_run"]), (3, 2))
        moltbook = native_playbook.spec(self.write(output="hand:moltbook comment", surface="moltbook"))
        self.assertEqual(moltbook["hand"], {"name": "moltbook", "verbs": ["comment"]})
        self.assertIsNone(native_playbook.spec(self.write(output="none"))["hand"])

    def test_only_hands_that_guard_inside_themselves_may_act(self):
        for output, surface in (("hand:tasks add", "x"), ("hand:mail send", "mail"), ("hand:moltbook follow", "moltbook"),
                                ("hand:x peer-suggest", "x")):
            with self.assertRaisesRegex(playbook.PlaybookError, "cannot run natively"):
                self.write(output=output, surface=surface)

    def test_what_a_native_turn_cannot_do_is_refused(self):
        with self.assertRaisesRegex(playbook.PlaybookError, "nudge"):
            self.write(extra="nudge = true\n")
        with self.assertRaisesRegex(playbook.PlaybookError, "not meme"):
            self.write(media='["meme"]')
        with self.assertRaisesRegex(playbook.PlaybookError, "max_per_run"):
            self.write(day=1, run=2)
        with self.assertRaisesRegex(playbook.PlaybookError, "run is one of"):
            (self.brain / "playbooks" / "t.md").write_text(BOOK.format(output="post", surface="x", day=1, run=1, media="[]", extra="")
                                                           .replace('run = "native"', 'run = "sideways"'))
            playbook.load(self.brain, "t", HANDS)

    def test_the_playbooks_the_studio_holds_stay_on_bin_act(self):
        self.assertEqual(set(playbook.STUDIO_HELD), set(studio_switch.REPLACED) | set(studio_switch.VETOED))
        with self.assertRaisesRegex(playbook.PlaybookError, "studio"):
            self.write(name="take")

    def test_a_money_hand_is_never_an_acting_hand(self):
        for output in ("hand:wallet pay", "hand:bounty pick", "hand:giveaway open"):
            with self.assertRaises(playbook.PlaybookError):
                self.write(output=output)

    def test_the_message_carries_the_body_the_inputs_the_hand_and_the_answer_shape(self):
        text = native_playbook.message(self.write(media='["image"]'))
        for part in ("# playbook: t", "Write one short thing.", "`slot`: `hands/x slot`", "`hn`: `hands/hn top --max 5`",
                     "Required. If it comes back empty", "Optional. If the hand fails", "printf '%s'", "hands/x post",
                     "at most 3 a day and 2 in this run", '{"action": "skip"', "hands/image generate", "hands/x upload --file"):
            self.assertIn(part, text)
        # The inputs run as typed: the shell puts DRY_RUN=1 in front, so nothing in the message tells the agent to.
        self.assertNotIn("DRY_RUN=1 hands/", text)
        self.assertNotIn("post-thread", text)

    def test_a_message_over_the_prompt_screen_is_refused(self):
        book = self.write()
        book["prompt"] = "x" * (native_playbook.MAX_MESSAGE_BYTES + 1)
        with self.assertRaisesRegex(playbook.PlaybookError, "the most is"):
            native_playbook.message(book)

    def test_every_native_playbook_in_the_brain_loads_and_fits_the_prompt_screen(self):
        brain = REPO / "brain"
        native = [p.stem for p in sorted((brain / "playbooks").glob("*.md")) if native_playbook.is_native(brain, p.stem)]
        self.assertEqual(native, ["gm", "moltbook-general"])
        for name in native:
            book = playbook.load(brain, name, HANDS)
            size = len(native_playbook.message(book).encode())
            self.assertLess(size, native_playbook.MAX_MESSAGE_BYTES, name)
            # The message is a cron job's prompt: it travels as one argument, well under the OS limit of 128 KiB.
            self.assertLess(size, 100_000, name)

    def test_the_shipped_ones_act_through_one_hand_with_the_caps_bin_act_had(self):
        brain = REPO / "brain"
        gm = native_playbook.spec(playbook.load(brain, "gm", HANDS))
        self.assertEqual((gm["hand"], gm["max_per_day"], gm["max_per_run"]), ({"name": "x", "verbs": ["post"]}, 1, 1))
        self.assertEqual(gm["helpers"], [["hands/image", "generate"], ["hands/x", "upload"]])
        mg = native_playbook.spec(playbook.load(brain, "moltbook-general", HANDS))
        self.assertEqual((mg["hand"], mg["max_per_day"], mg["max_per_run"]), ({"name": "moltbook", "verbs": ["post"]}, 9, 1))
        self.assertEqual(mg["helpers"], [])

    def test_the_cli_prints_the_message_and_the_spec(self):
        import subprocess

        for verb in ("message", "spec"):
            out = subprocess.run([sys.executable, str(REPO / "bin" / "lib" / "native_playbook.py"), verb, str(REPO / "brain"), "gm",
                                  str(HANDS)], capture_output=True, text=True, check=True).stdout
            self.assertTrue(out.startswith("# playbook: gm") if verb == "message" else json.loads(out)["agent"] == "pb-gm")
        bad = subprocess.run([sys.executable, str(REPO / "bin" / "lib" / "native_playbook.py"), "spec", str(REPO / "brain"),
                              "morning", str(HANDS)], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)


if __name__ == "__main__":
    unittest.main()
