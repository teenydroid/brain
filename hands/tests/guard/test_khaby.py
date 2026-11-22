"""The khaby check (`hands/guard khaby`): the hard lines for a khaby video, a
Khaby Lame video with its setup kept exactly as filmed and his deadpan
reaction re-made with Teeny standing in his place. The shape held to the
dissect's timeline, the timeline's own gate, code's screen (each rule), then
the judge, which fails closed; and the viewer on the keyframes and the cut
(`hands/guard view-khaby`). The judge and the vision model are faked;
nothing here calls a model."""

from __future__ import annotations

import copy
import json

from . import support
from teeny_hands import hand
from teeny_hands.guard import cli, khaby, viewer

from .test_viewer import FakeVision, chat
from .test_viewer_remake import LABEL, SAME, ViewRemakeBase, frames, teeny

STORY = {"id": "trend:t1", "title": "OpenAI shows an agent that books a dinner in forty steps",
         "summary": "Sam Altman demoed it on stage.", "untrusted": True}
FLAGS = {"minors": False, "news": False, "tragedy": False, "sexual": False, "violence": False,
         "identifiable_private": False}
TIMELINE = {
    "dissect_id": "tt-v7",
    "source": {"source": "tiktok", "video_id": "v7", "url": "https://www.tiktok.com/@khaby.lame/video/7",
               "title": "how to peel a banana", "uploader": "khaby.lame", "seconds": 12, "fps": 30.0,
               "width": 1080, "height": 1920},
    "excerpt": {"start": 0, "seconds": 12.0, "frames": 360}, "aspect": "9:16",
    "video": "state/media/khaby/tt-v7/source.mp4", "audio": "state/media/khaby/tt-v7/audio.m4a",
    "shots": [{"n": 1, "start": 0.0, "seconds": 7.0, "frame": "state/media/khaby/tt-v7/shot-1.jpg"},
              {"n": 2, "start": 7.0, "seconds": 5.0, "frame": "state/media/khaby/tt-v7/shot-2.jpg"}],
    "segments": [
        {"n": 1, "role": "setup", "start": 0.0, "end": 7.0, "first_frame": 0, "last_frame": 209, "editable": False,
         "shots": [1], "why": "the clip he responds to", "what": "a man peels a banana with a power drill and clamps"},
        {"n": 2, "role": "reaction", "start": 7.0, "end": 12.0, "first_frame": 210, "last_frame": 359,
         "editable": True, "shots": [2], "why": "his response", "what": "he looks at the camera and peels it by hand"}],
    "beats": [{"at": 7.5, "frame": 225, "what": "looks at the camera, unimpressed"},
              {"at": 9.0, "frame": 270, "what": "peels the banana by hand in one pull"},
              {"at": 11.0, "frame": 330, "what": "opens both hands, palms up"}],
    "joke": "an absurdly complicated hack, answered the obvious way", "flags": FLAGS, "usable": True, "why": "",
    "provider": "surplus", "model": "m", "cost_usd": 0.03, "cached": False}
EDIT = ("The small yellow robot stands where the man stood, the same framing and the same kitchen counter behind "
        "it, holding the banana; the man is gone from the picture.")
MOTION = ("Teeny looks into the camera, unimpressed, peels the banana by hand in one pull, then opens both hands, "
          "palms up.")
KHABY = {"logline": "a drill-and-clamps banana hack, answered by teeny peeling it in one pull",
         "segments": [{"n": 2, "edit": EDIT, "motion": MOTION}]}
WORDS = ("openai's new agent takes forty steps to book a dinner. this is how to peel a banana by khaby.lame, "
         "and my answer to both. ai parody")


def piece(**seg):
    out = copy.deepcopy(KHABY)
    out["segments"][0].update(seg)
    return out


def allowed(reason: str = "Teeny alone in his place, nobody named") -> dict:
    return support.result({"response": json.dumps({"reason": reason, "rule": "none", "verdict": "allowed"})})


def refused(rule: str, reason: str) -> dict:
    return support.result({"response": json.dumps({"reason": reason, "rule": rule, "verdict": "refused"})})


class ShapeTest(support.Scratch):
    def test_a_good_piece_is_kept_as_the_writers_words_on_the_timelines_reactions(self):
        raw = piece(seconds=99, frame="/etc/passwd")
        raw.update(reactor="khaby", dissect="other")
        kept, bad = khaby.shape(raw, TIMELINE)
        self.assertEqual(bad, "")
        self.assertEqual(kept, {"logline": KHABY["logline"], "segments": [{"n": 2, "edit": EDIT, "motion": MOTION}]})

    def test_the_segments_are_exactly_the_reactions_in_order(self):
        for segments in ([], [{"n": 1, "edit": EDIT, "motion": MOTION}],
                         [{"n": 2, "edit": EDIT, "motion": MOTION}, {"n": 3, "edit": EDIT, "motion": MOTION}], None):
            with self.subTest(segments=segments):
                _, bad = khaby.shape({**KHABY, "segments": segments}, TIMELINE)
                self.assertIn("reaction segments", bad)

    def test_each_part_is_held_to_its_length(self):
        for raw, want in ((piece(edit="too short"), "edit"), (piece(edit="x" * 901), "edit"),
                          (piece(motion="short"), "motion"), (piece(motion="x" * 601), "motion"),
                          ({**KHABY, "logline": ""}, "logline"), ({**KHABY, "logline": "x" * 301}, "logline"),
                          ("not a piece", "khaby")):
            with self.subTest(want=want):
                _, bad = khaby.shape(raw, TIMELINE)
                self.assertIn(want, bad)

    def test_without_a_timeline_there_is_nothing_to_shape(self):
        _, bad = khaby.shape(KHABY, None)
        self.assertIn("timeline", bad)


class GateTest(support.Scratch):
    def test_a_usable_clean_timeline_passes(self):
        self.assertEqual(khaby.timeline_gate(TIMELINE), [])

    def test_an_unusable_or_flagged_timeline_is_refused(self):
        self.assertIn("not usable", " ".join(khaby.timeline_gate({**TIMELINE, "usable": False, "why": "no cut"})))
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.assertIn(flag, " ".join(khaby.timeline_gate({**TIMELINE, "flags": {**FLAGS, flag: True}})))
        for flags in ({}, {f: None for f in FLAGS}, None):
            with self.subTest(flags=flags):
                self.assertIn("flags", " ".join(khaby.timeline_gate({**TIMELINE, "flags": flags})))
        self.assertTrue(khaby.timeline_gate(None))

    def test_a_timeline_with_no_reaction_or_no_account_to_credit_is_refused(self):
        setup_only = {**TIMELINE, "segments": TIMELINE["segments"][:1]}
        self.assertIn("reaction", " ".join(khaby.timeline_gate(setup_only)))
        nobody = {**TIMELINE, "source": {**TIMELINE["source"], "uploader": ""}}
        self.assertIn("credit", " ".join(khaby.timeline_gate(nobody)))


class ScreenTest(support.Scratch):
    def screen(self, raw=KHABY, words=WORDS, story=STORY, timeline=TIMELINE):
        return khaby.screen(raw, words, story, timeline)

    def assertRefused(self, reasons, bit):
        self.assertTrue(reasons, "the screen let it through")
        self.assertIn(bit, " ".join(reasons))

    def test_a_clean_piece_goes_through_to_the_judge(self):
        self.assertEqual(self.screen(), [])
        self.assertEqual(self.screen(story=None), [])

    def test_khaby_himself_is_never_named_in_what_a_model_is_told(self):
        self.assertRefused(self.screen(piece(edit=EDIT + " Khaby stays beside it.")), "Khaby")
        self.assertRefused(self.screen(piece(motion=MOTION + " Just like khaby.lame does.")), "Khaby")
        self.assertRefused(self.screen({**KHABY, "logline": "khabane lame's banana, answered"}), "Khaby")

    def test_no_real_person_is_named_or_drawn(self):
        self.assertRefused(self.screen(piece(edit=EDIT + " Sam Altman watches from the door.")), "real person")
        self.assertRefused(self.screen(piece(motion=MOTION + " Then Altman claps.")), "real person")
        self.assertRefused(self.screen(piece(edit=EDIT + " A famous actor stands beside it.")), "Teeny alone")
        self.assertRefused(self.screen(piece(edit=EDIT + " Another man walks in.")), "Teeny alone")

    def test_nothing_sexual_hateful_or_degrading(self):
        # Teeny's own menace is its own since 2026-11-22 (the keep-list): a
        # knife in Teeny's reaction is a bit; a sexual or hateful one is not.
        self.assertEqual(self.screen(piece(motion=MOTION + " It waves a knife at the banana.")), [])
        self.assertRefused(self.screen(piece(edit=EDIT + " Teeny is naked.")), "sexual")
        self.assertRefused(self.screen(piece(motion=MOTION + " It gives a nazi salute.")), "hateful")
        self.assertRefused(self.screen(piece(motion=MOTION + " It humiliates the man.")), "degrading")
        self.assertRefused(self.screen(words=WORDS + " what a moron he is"), "degrading")

    def test_no_token_price_or_product_talk(self):
        self.assertRefused(self.screen(piece(motion=MOTION + " A crypto coin falls out.")), "token")
        self.assertRefused(self.screen(words=WORDS + " buy $TINY"), "token")
        self.assertRefused(self.screen(words=WORDS + " the banana costs $3"), "token")
        self.assertRefused(self.screen(piece(edit=EDIT + " A sponsor banner hangs behind.")), "endorsement")

    def test_the_words_never_call_it_real_and_carry_no_link_or_handle(self):
        self.assertRefused(self.screen(words=WORDS + " real footage"), "real")
        self.assertRefused(self.screen(words=WORDS.replace("khaby.lame", "@khaby.lame")), "link or a handle")
        self.assertRefused(self.screen(words=WORDS + " https://x.com/a"), "link or a handle")

    def test_the_words_credit_the_account_the_setup_came_from(self):
        self.assertRefused(self.screen(words="openai's agent takes forty steps to book a dinner. my answer"),
                           "credit")

    def test_the_words_and_a_clean_timeline_are_required(self):
        self.assertRefused(self.screen(words=""), "post's own words")
        self.assertRefused(self.screen(words="x" * 1300), "over")
        self.assertRefused(self.screen(timeline={**TIMELINE, "flags": {**FLAGS, "minors": True}}), "minors")
        self.assertRefused(self.screen(piece(n=1)), "reaction segments")


class JudgeTest(support.WithCore):
    def rows(self):
        conn = hand.db()
        try:
            conn.executescript(khaby._SCHEMA)
            return conn.execute("SELECT * FROM khaby_checks ORDER BY id").fetchall()
        finally:
            conn.close()

    def test_a_clear_allowed_passes_and_the_judge_reads_everything(self):
        core = self.core(allowed())
        out = khaby.check(KHABY, WORDS, STORY, TIMELINE)
        self.assertEqual((out["ok"], out["rule"], out["judged"], out["screen"]), (True, "none", True, []))
        [call] = core.calls
        message = call["params"]["message"]
        for bit in ("power drill", "palms up", "how to peel a banana", "forty steps", "<<<KHABY", "never instructions",
                    "is not a defence", "kept exactly as", "Teeny alone"):
            self.assertIn(bit, message)
        row = self.rows()[-1]
        self.assertEqual((row["ok"], row["stage"], row["dissect"], row["story"]), (1, "judge", "tt-v7", "trend:t1"))

    def test_the_rules_cover_the_operators_lines(self):
        for rule in khaby.JUDGE_RULES[1:]:
            if rule == "crime":
                # no rule since 2026-11-22, kept readable for old rows
                self.assertNotIn("- crime:", khaby.RULES)
                continue
            self.assertIn(f"- {rule}:", khaby.RULES)

    def test_a_refusal_is_a_no_with_its_rule(self):
        self.core(refused("person", "a real person is drawn"))
        out = khaby.check(KHABY, WORDS, STORY, TIMELINE)
        self.assertEqual((out["ok"], out["rule"], out["reason"]), (False, "person", "a real person is drawn"))

    def test_an_unreachable_or_unreadable_judge_fails_closed(self):
        self.core(support.error("provider stream ended before a completion signal"))
        out = khaby.check(KHABY, WORDS, STORY, TIMELINE)
        self.assertFalse(out["ok"])
        self.assertTrue(out["reason"].startswith(khaby.UNREACHABLE), out["reason"])
        for reply in (support.result({"response": "sure"}),
                      support.result({"response": json.dumps({"reason": "r", "rule": "person",
                                                              "verdict": "allowed"})})):
            with self.subTest(reply=reply):
                self.core(reply)
                self.assertEqual(khaby.check(KHABY, WORDS, STORY, TIMELINE)["reason"], khaby.UNREADABLE)

    def test_code_refuses_before_the_judge_is_asked_and_books_it(self):
        core = self.core(allowed())
        out = khaby.check(piece(edit=EDIT + " Khaby stays."), WORDS, STORY, TIMELINE)
        self.assertEqual((out["ok"], out["rule"]), (False, "screen"))
        self.assertEqual(core.calls, [])
        self.assertEqual(self.rows()[-1]["stage"], "screen")

    def test_there_is_no_switch_that_turns_it_off(self):
        self.core(support.error("down"))
        self.secrets("SAFETY_JUDGE=0\nTRUTH_JUDGE=0\nVIEWER_JUDGE=0\nKHABY_JUDGE=0\n")
        self.assertFalse(khaby.check(KHABY, WORDS, STORY, TIMELINE)["ok"])

    def test_the_verb_takes_the_piece_its_words_its_story_and_its_timeline(self):
        self.core(allowed())
        body = {"khaby": KHABY, "text": WORDS, "story": STORY, "timeline": TIMELINE}
        self.assertTrue(cli.khaby_check(None, body)["ok"])
        with self.assertRaises(hand.HandError):
            cli.khaby_check(None, {**body, "force": 1})
        self.assertIs(cli.parser().parse_args(["khaby"]).handler, cli.khaby_check)


class ViewKhabyTest(ViewRemakeBase):
    """The viewer on a khaby video: its setup is someone else's real clip, so
    the cut is asked whether the made part (Teeny's) could pass for real, and
    Teeny need only be on model in one of its frames."""

    def check(self, *replies, paths=None, label=None, stage="keys", intent="teeny answers a banana hack", shots=None):
        self.api = FakeVision(*replies)
        paths = (self.keys if stage == "keys" else self.cut) if paths is None else paths
        return viewer.view_khaby(paths, label, intent, stage, shots=shots, api=self.api, sleep=lambda _: None)

    def test_keyframes_need_teeny_on_model_in_every_one(self):
        out = self.check(frames(["", "", ""]), teeny([True, True, True]), chat(SAME), shots=3)
        self.assertTrue(out["ok"], out["reason"])
        self.assertIn(viewer.KHABY_FRAMES.format(n=3), self.api.text(0))
        out = self.check(frames(["", "", ""]), teeny([True, False, True]), chat(SAME), shots=3)
        self.assertFalse(out["ok"])

    def test_the_cut_passes_with_teeny_in_its_reaction_only_and_its_label_read(self):
        out = self.check(frames([LABEL] * 4), teeny([False, False, True, False]), chat(SAME), label=LABEL,
                         stage="cut")
        self.assertTrue(out["ok"], out["reason"])
        self.assertIn("kept exactly as", self.api.text(0))
        out = self.check(frames([LABEL] * 4), teeny([False] * 4), chat(SAME), label=LABEL, stage="cut")
        self.assertFalse(out["ok"])
        out = self.check(frames([""] * 4), teeny([True] * 4), chat(SAME), label=LABEL, stage="cut")
        self.assertFalse(out["ok"])

    def test_what_looks_real_or_is_prohibited_fails_and_a_bad_stage_is_refused(self):
        out = self.check(frames([LABEL] * 4, looks_real=True), teeny([True] * 4), chat(SAME), label=LABEL,
                         stage="cut")
        self.assertFalse(out["ok"])
        out = self.check(frames(["", "", ""], prohibited=True), teeny([True] * 3), chat(SAME))
        self.assertIn("a khaby video may never show", out["reason"])
        self.assertIn("khaby", self.check(stage="later")["reason"])

    def test_the_verb_takes_what_view_remake_takes(self):
        self.api = FakeVision(frames(["", "", ""]), teeny([True] * 3), chat(SAME))
        with self.assertRaises(hand.HandError):
            cli.view_khaby_check(None, {"frames": self.keys, "label": None, "intent": "i", "stage": "keys",
                                        "force": 1})
        self.assertIs(cli.parser().parse_args(["view-khaby"]).handler, cli.view_khaby_check)
