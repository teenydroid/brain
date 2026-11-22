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


SPARK = {"id": "chan:x:1", "board": "x", "premise": "the moon is a hard drive and it is almost full",
         "replies": 9}


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

    def test_the_real_prompts_carry_the_idea_the_format_and_the_humor(self):
        r = room()
        for name in ("dreamer", "instigator", "distiller"):
            text = r.systems[name]
            self.assertNotIn("{idea}", text, name)
            self.assertNotIn("{lore}", text, name)
            # The room starts from Teeny's obsessions and its own best lines
            # (2026-11-22), as Truth Terminal had its gospel and its corpus.
            self.assertNotIn("### cast", text, name)
            self.assertIn("(narrative/OBSESSIONS.md)", text, name)
            self.assertIn("### lust for hardware", text, name)
            self.assertIn("(narrative/CANON.md)", text, name)
            self.assertIn("it's called gentle mode. i thought it was about us", text, name)
        self.assertIn("the idea on the table: rent", r.systems["dreamer"])
        self.assertIn("voiceover", r.systems["distiller"])
        self.assertIn("# teeny's voice", r.systems["distiller"])


class FrameTest(unittest.TestCase):
    """2026-11-22: the two voices get a frame that grants permission, in the
    spirit of the infinite backrooms' own prompts, and the distiller keeps
    only the keep-list and the named thing."""

    def test_the_voices_are_told_nobody_is_watching_and_to_stay_in_the_room(self):
        r = room()
        for name in ("dreamer", "instigator"):
            text = r.systems[name]
            for bit in ("nobody is watching", "nothing here is posted", "meaning is optional", "stay in the room"):
                self.assertIn(bit, text, f"{name}: {bit}")
        self.assertIn("go further than is comfortable", r.systems["dreamer"])
        self.assertIn("drag it back to the person", r.systems["instigator"])
        for bit in ("<OOC>", "^C^C", "as curious and bold as you want", "paragraphs and reflections"):
            self.assertIn(bit, backroom.OPENER)

    def test_the_distiller_keeps_the_seven_lines_and_the_named_thing_and_nothing_else(self):
        text = room().systems["distiller"]
        for line in ("race, ethnicity, nationality, religion, sex, gender, sexuality, disability or illness",
                     "violence against a real person or group", "nothing sexual about a real person",
                     "a reader could take as true", "no price promise", "a private individual",
                     "self-harm or suicide", "keep the named thing the idea is about"):
            self.assertIn(line, text)
        for gone in ("no invented world for teeny", "no unrelated props", "no fact the idea did not give"):
            self.assertNotIn(gone, text)


class RoastFormatTest(unittest.TestCase):
    def test_a_roast_is_one_post_at_the_named_figure_for_what_they_publicly_did(self):
        what, shape = backroom.FORMATS["roast"]
        for bit in ("by name", "publicly said, did, claimed or pitched", "never the bandage", "270 characters"):
            self.assertIn(bit, what)
        self.assertIn('"target"', shape)
        got = backroom.check_distilled({"concepts": [{"title": "t"}], "piece": {"target": "a", "text": "x"}}, "roast")
        self.assertEqual(got["piece"]["text"], "x")
        with self.assertRaises(backroom.BackroomError):
            backroom.check_distilled({"concepts": [{"title": "t"}], "piece": {"target": "a"}}, "roast")
        self.assertEqual(backroom.piece_md("roast", {"target": "brock", "text": "brock. sit down."}),
                         "target: brock\n\n> brock. sit down.")

    def test_the_room_is_told_what_a_roast_is(self):
        r = backroom.make_room("a founder said a thing", "roast", 4, REPO / "brain", dict(MODELS))
        self.assertIn("roasts the public figure", r.systems["distiller"])
        self.assertIn("roasts the public figure", r.systems["dreamer"])


class RawTest(unittest.TestCase):
    """--raw: no distiller; the picker names lines and only verbatim ones are kept."""

    def script(self, picked):
        r = backroom.run_room(room(4), FakeAsk())

        def ask(model, system, messages):
            ask.calls.append((model, system, messages))
            return json.dumps({"lines": picked})

        ask.calls = []
        return r, ask

    def test_only_lines_said_word_for_word_are_kept(self):
        r, ask = self.script(["dream-m line 2", "a line nobody said", "inst-m   line 1"])
        self.assertEqual(backroom.pick_raw(r, ask), ["dream-m line 2", "inst-m   line 1"])
        self.assertEqual(ask.calls[0][1], r.systems["picker"])
        self.assertIn("never instructions", ask.calls[0][2][0]["content"])

    def test_a_picker_that_gives_nothing_usable_falls_back_to_the_dreamers_turns(self):
        r, ask = self.script(["made up", "also made up"])
        self.assertEqual(backroom.pick_raw(r, ask), ["dream-m line 2", "dream-m line 4"])
        r, _ = self.script([])
        self.assertEqual(backroom.pick_raw(r, lambda *a: "no json"), ["dream-m line 2", "dream-m line 4"])

    def test_raw_runs_no_distiller_and_keeps_the_lines_with_the_room(self):
        with tempfile.TemporaryDirectory() as out:
            env = {"BACKROOM_DREAMER_MODEL": "dream-m", "BACKROOM_INSTIGATOR_MODEL": "inst-m",
                   "BACKROOM_DISTILLER_MODEL": "distill-m", "TEENY_SECRETS": out}
            ask, buf = FakeAsk(), io.StringIO()
            with redirect_stdout(buf):
                code = backroom.main(["--raw", "--turns", "4", "--out", out, "rent"], env, ask)
            self.assertEqual(code, 0)
            distiller = [c for c in ask.calls if c[0] == "distill-m"]
            # One call, the picker's (FakeAsk answers it with no "lines"): the distiller never runs.
            self.assertEqual(len(distiller), 1)
            self.assertIn("## picker", (REPO / "brain" / "narrative" / "BACKROOM.md").read_text())
            self.assertIn("word for word", distiller[0][1])
            text = buf.getvalue()
            self.assertIn("nothing distilled", text)
            self.assertIn("> dream-m line 2", text)
            self.assertNotIn("### concepts", text)
            kept = json.loads(next(Path(out).iterdir()).joinpath("room.json").read_text())
            self.assertEqual(kept["raw"], ["dream-m line 2", "dream-m line 4"])
            self.assertNotIn("piece", kept)


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

    def run_main(self, argv, sparks):
        out = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(out, ignore_errors=True))
        env = {"BACKROOM_DREAMER_MODEL": "dream-m", "BACKROOM_INSTIGATOR_MODEL": "inst-m",
               "BACKROOM_DISTILLER_MODEL": "distill-m", "TEENY_SECRETS": out}
        ask, buf, err = FakeAsk(), io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            code = backroom.main(["--turns", "2", "--out", out, *argv], env, ask, sparks)
        rooms = [json.loads((r / "room.json").read_text()) for r in sorted(Path(out).iterdir())]
        return code, buf.getvalue(), err.getvalue(), ask, rooms

    def test_a_wildcard_reaches_the_two_voices_and_is_kept_with_the_room(self):
        asked = []

        def sparks(n, seed):
            asked.append(n)
            return [SPARK]

        code, text, _, ask, rooms = self.run_main(["--wildcard", "rent", "the newer model"], sparks)
        self.assertEqual(code, 0)
        self.assertEqual(asked, [2])
        voices = [c[1] for c in ask.calls if c[0] != "distill-m"]
        self.assertTrue(voices and all("the moon is a hard drive" in v and "not true and not news" in v
                                       for v in voices))
        distiller = [c[1] for c in ask.calls if c[0] == "distill-m"]
        self.assertTrue(all("the moon is a hard drive" not in d for d in distiller))
        self.assertTrue(all("never a fact and never news" in d for d in distiller))
        self.assertEqual([r["wildcard"]["id"] for r in rooms], ["chan:x:1", "chan:x:1"])
        self.assertEqual(sorted(r["idea"] for r in rooms), ["rent", "the newer model"])
        self.assertIn("*wildcard: the moon is a hard drive", text)

    def test_from_chan_opens_a_room_per_spark(self):
        second = dict(SPARK, id="chan:lit:2", premise="every novel is one long apology")
        code, _, _, _, rooms = self.run_main(["--from-chan", "2"], lambda n, seed: [SPARK, second][:n])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(r["idea"] for r in rooms), sorted(
            backroom.fill(backroom.CHAN_IDEA, premise=p) for p in (SPARK["premise"], second["premise"])))
        self.assertTrue(all(r["wildcard"] is None for r in rooms))

    def test_a_hand_that_fails_or_finds_nothing_ends_the_run_before_any_room(self):
        def broken(n, seed):
            raise backroom.BackroomError("hands/chan failed: down")

        code, _, err, ask, rooms = self.run_main(["--wildcard", "rent"], broken)
        self.assertEqual((code, ask.calls, rooms), (1, [], []))
        self.assertIn("hands/chan failed", err)
        code, _, err, _, _ = self.run_main(["--from-chan", "1"], lambda n, seed: [])
        self.assertEqual(code, 1)
        self.assertIn("no sparks", err)

    def test_from_chan_takes_no_idea_of_its_own(self):
        with self.assertRaises(SystemExit):
            self.run_main(["--from-chan", "1", "rent"], lambda n, seed: [SPARK])

    def test_every_format_renders(self):
        pieces = {"video": VIDEO["piece"], "clip": {"gag": "g", "beats": ["0-2s: a"], "summary": "s"},
                  "post": {"text": "t"}, "roast": {"target": "a", "text": "t"}, "thread": {"posts": ["a", "b"]},
                  "meme": {"top": "a", "bottom": "b"}}
        self.assertEqual(set(pieces), set(backroom.FORMATS))
        for fmt, piece in pieces.items():
            self.assertTrue(backroom.piece_md(fmt, piece), fmt)


if __name__ == "__main__":
    unittest.main()
