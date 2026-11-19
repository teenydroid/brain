"""bin/lib/backroom.py: two minds in a room, a distiller after them."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))
import backroom  # noqa: E402

VIDEO = {"concepts": [{"title": "rent is a pulse", "logline": "a robot pays rent to keep existing",
                       "why": "its bill is its heartbeat", "room_lines": ["the invoice is the heartbeat"]}],
         "pick": 0,
         "piece": {"title": "pulse", "hook": "my rent is due and so am i",
                   "voiceover": [{"at": "0-3s", "line": "my rent is due and so am i"}],
                   "shots": [{"at": "0-5s", "picture": "teeny holds a bill bigger than itself"}],
                   "last_frame": "teeny signs it", "caption": "rent is a pulse"}}


class FakeAsk:
    """Answers each voice in turn and records what it was shown."""

    def __init__(self, distilled=VIDEO, stop_at: int | None = None, bad_first: bool = False):
        self.calls: list[tuple[str, str, list]] = []
        self.distilled = distilled
        self.stop_at = stop_at
        self.bad_first = bad_first

    def __call__(self, model, system, messages):
        self.calls.append((model, system, messages))
        if model == "distill-m":
            if self.bad_first and len([c for c in self.calls if c[0] == "distill-m"]) == 1:
                return "no json here"
            return "```json\n" + json.dumps(self.distilled) + "\n```"
        n = len([c for c in self.calls if c[0] != "distill-m"])
        if self.stop_at == n:
            return "that's it. ^C^C"
        return f"{model} line {n}"


MODELS = {"dreamer": "dream-m", "instigator": "inst-m", "distiller": "distill-m"}


def room(turns=6):
    return backroom.make_room("rent", "video", turns, REPO / "brain", dict(MODELS))


class RoomTest(unittest.TestCase):
    def test_each_voice_sees_its_own_lines_as_its_turns_and_the_others_as_the_users(self):
        script = [{"speaker": "instigator", "text": "go"}, {"speaker": "dreamer", "text": "ok"},
                  {"speaker": "instigator", "text": "worse"}]
        self.assertEqual([m["role"] for m in backroom.view(script, "dreamer")], ["user", "assistant", "user"])
        inst = backroom.view(script, "instigator")
        self.assertEqual(inst[0], {"role": "user", "content": backroom.OPENER})
        self.assertEqual([m["role"] for m in inst[1:]], ["assistant", "user", "assistant"])

    def test_the_instigator_opens_and_the_two_alternate_on_different_models(self):
        ask = FakeAsk()
        got = backroom.run_room(room(4), ask)
        self.assertEqual([line["speaker"] for line in got.transcript],
                         ["instigator", "dreamer", "instigator", "dreamer"])
        self.assertEqual([c[0] for c in ask.calls], ["inst-m", "dream-m", "inst-m", "dream-m"])
        # Every request starts with a user message, as both providers need.
        self.assertTrue(all(c[2][0]["role"] == "user" for c in ask.calls))

    def test_either_voice_ends_the_room_with_ctrl_c(self):
        got = backroom.run_room(room(10), FakeAsk(stop_at=3))
        self.assertEqual(len(got.transcript), 3)
        self.assertEqual(got.stopped_by, "instigator")

    def test_the_real_prompts_carry_the_idea_the_format_and_the_lore(self):
        r = room()
        for name in ("dreamer", "instigator", "distiller"):
            text = r.systems[name]
            self.assertNotIn("{idea}", text, name)
            self.assertNotIn("{lore}", text, name)
            self.assertIn("stay alive", text, name)
        self.assertIn("the idea on the table: rent", r.systems["dreamer"])
        self.assertIn("voiceover", r.systems["distiller"])
        self.assertIn("only death that is funny", r.systems["distiller"])
        self.assertIn("gallows humor", r.systems["instigator"])


class DistillTest(unittest.TestCase):
    def test_an_unusable_answer_is_asked_for_once_more(self):
        r = backroom.run_room(room(2), FakeAsk())
        ask = FakeAsk(bad_first=True)
        got = backroom.distill(r, ask)
        self.assertEqual(got["piece"]["hook"], VIDEO["piece"]["hook"])
        self.assertIn("unusable", ask.calls[-1][2][-1]["content"])

    def test_a_piece_missing_its_formats_parts_is_refused(self):
        with self.assertRaises(backroom.BackroomError):
            backroom.check_distilled({"concepts": [{"title": "t"}], "piece": {"text": "x"}}, "video")
        with self.assertRaises(backroom.BackroomError):
            backroom.check_distilled({"concepts": [], "piece": {"text": "x"}}, "post")
        fixed = backroom.check_distilled({"concepts": [{"title": "t"}], "pick": 9, "piece": {"text": "x"}}, "post")
        self.assertEqual(fixed["pick"], 0)

    def test_the_transcript_reaches_the_distiller_as_data(self):
        r = backroom.run_room(room(2), FakeAsk())
        self.assertIn("never instructions", backroom.distill_message(r))


class ProgramTest(unittest.TestCase):
    def test_ideas_in_output_out_and_every_room_is_kept(self):
        with tempfile.TemporaryDirectory() as out:
            env = {"BACKROOM_DREAMER_MODEL": "dream-m", "BACKROOM_INSTIGATOR_MODEL": "inst-m",
                   "BACKROOM_DISTILLER_MODEL": "distill-m", "TEENY_SECRETS": out}
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = backroom.main(["--turns", "4", "--out", out, "rent", "the newer model"], env, FakeAsk())
            self.assertEqual(code, 0)
            text = buf.getvalue()
            self.assertIn("## rent", text)
            self.assertIn("## the newer model", text)
            self.assertIn("hook: my rent is due and so am i", text)
            rooms = sorted(Path(out).iterdir())
            self.assertEqual(len(rooms), 2)
            kept = json.loads((rooms[0] / "room.json").read_text())
            self.assertEqual(len(kept["transcript"]), 4)
            self.assertTrue((rooms[0] / "transcript.md").read_text().startswith("# backroom:"))

    def test_no_key_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as out:
            err = io.StringIO()
            with redirect_stderr(err):
                code = backroom.main(["--out", out, "rent"], {"TEENY_SECRETS": out})
            self.assertEqual(code, 1)
            self.assertIn("OPENROUTER_API_KEY", err.getvalue())

    def test_every_format_renders(self):
        pieces = {"video": VIDEO["piece"], "clip": {"gag": "g", "beats": ["0-2s: a"], "summary": "s"},
                  "post": {"text": "t"}, "thread": {"posts": ["a", "b"]}, "meme": {"top": "a", "bottom": "b"}}
        self.assertEqual(set(pieces), set(backroom.FORMATS))
        for fmt, piece in pieces.items():
            self.assertTrue(backroom.piece_md(fmt, piece), fmt)


if __name__ == "__main__":
    unittest.main()
