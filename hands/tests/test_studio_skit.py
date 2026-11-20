"""bin/studio's `skit` format: a trending story, the public figure it is
about, an impossible scene, on video. The format's shape and its hard lines
in code before the tournament, the producer's order (guard, skit judge,
hands/video skit, the viewer on its frames, upload) failing closed at each
step, the label enforced, and the fallback to another format when the video
cannot be made, without using up the slot's fallback budget."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(REPO / "bin" / "lib"))
import studio  # noqa: E402

HOOK = {"id": "trend:t1", "title": "Elon Musk shows off Tesla's robotaxi and says it will drive itself everywhere",
        "summary": "Tesla's chief executive presented the car at an event.", "untrusted": True}
SHOTS = [
    {"prompt": "Wide anamorphic shot, golden hour on a marble showroom built on the moon, Earth in a black sky. "
               "Elon Musk in a silver tuxedo pulls a velvet sheet off a chrome robotaxi; its headlights blink."},
    {"prompt": "Low-angle close-up, cool rim light: Musk plays chess against the robotaxi's robotic arm and the "
               "board grows to the size of a planet. He mutters: \"best of three.\" Comic timpani."},
]
PREMISE = "musk said the car drives itself, so on the moon it beats him at chess"
LOOK = {"Elon Musk": "silver tuxedo, slicked-back hair, a velvet sheet over one arm"}
SETTING = "a marble showroom built on the moon, Earth in a black sky, golden hour, anamorphic lens, film grain"
SCORE = "one swelling orchestral score with comic timpani, under the whole film"
RAW = {"figures": ["Elon Musk"], "premise": PREMISE,
       "logline": "elon musk loses a chess match to his own car on a lunar showroom floor",
       "look": LOOK, "setting": SETTING, "score": SCORE, "shots": SHOTS, "aspect": "16:9"}
WORDS = "musk showed off a car that drives itself, so i gave it a premiere on the moon. parody, obviously"
PITCH = {"id": "p1", "storyline": None, "shape": "scene", "format": "skit", "hook": "trend:t1", "item": None}


def skit_draft(**over):
    got, lost = studio.validate_drafts({"drafts": [{"text": WORDS, "skit": RAW, "meaning": "m"}]}, PITCH, 3)
    assert got, lost
    draft = got[0]
    draft.update(id="d1", truth={"text": WORDS, "facts": [HOOK]}, **over)
    return draft


class FormatTest(unittest.TestCase):
    def load(self, env):
        with tempfile.TemporaryDirectory() as tmp:
            return studio.Config.load(env, Path(tmp) / "none.env")

    def test_skit_is_known_but_off_unless_studio_formats_names_it(self):
        self.assertIn("skit", studio.KNOWN_FORMATS)
        self.assertNotIn("skit", self.load({}).formats)
        cfg = self.load({"STUDIO_FORMATS": "post,skit", "STUDIO_SKIT_SLOTS": "evening, night"})
        self.assertEqual((cfg.formats, cfg.skit_slots, cfg.skit_secs), (("post", "skit"), ("evening", "night"), 900))
        self.assertEqual(self.load({}).skit_slots, ())

    def test_skit_slots_without_the_format_warn(self):
        cfg = self.load({"STUDIO_SKIT_SLOTS": "evening"})
        self.assertTrue(any("STUDIO_SKIT_SLOTS" in w for w in cfg.warnings))

    def test_a_skit_pitch_must_join_a_trending_story(self):
        kept, dropped, _ = studio.validate_pitches({"pitches": [
            {"id": "p1", "format": "skit", "item": "evt:1", "angle": "a"},
            {"id": "p2", "format": "skit", "hook": "trend:t1", "angle": "a"},
            {"id": "p3", "format": "skit", "hook": "trend:old", "item": "evt:1", "angle": "a"}]},
            {"evt:1", "trend:t1", "trend:old"}, set(), ("post", "skit"), 12, outside={"trend:t1"})
        self.assertEqual([p["id"] for p in kept], ["p2"])
        self.assertTrue(all("a skit stages a trending story" in d["why"] for d in dropped), dropped)

    def test_a_skit_draft_keeps_its_story_figures_logline_shots_and_aspect(self):
        draft = skit_draft()
        self.assertEqual(draft["skit"], {"figures": ["Elon Musk"], "logline": RAW["logline"],
                                         "shots": [{"prompt": SHOTS[0]["prompt"]}, {"prompt": SHOTS[1]["prompt"]}],
                                         "aspect": "16:9", "story": "trend:t1", "premise": PREMISE,
                                         "look": LOOK, "setting": SETTING, "score": SCORE})

    def test_a_skit_needs_a_look_for_each_figure_a_setting_and_a_score(self):
        # 2026-11-20: shots rendered alone changed the lead's face, the lawn and
        # the music every shot. A one-take film holds these three as data.
        for raw, bit in (({k: v for k, v in RAW.items() if k != "look"}, "look"),
                         ({**RAW, "look": {}}, "look"),
                         ({**RAW, "figures": ["Elon Musk", "Tesla"], "look": LOOK}, "look"),
                         ({k: v for k, v in RAW.items() if k != "setting"}, "setting"),
                         ({**RAW, "setting": " "}, "setting"),
                         ({k: v for k, v in RAW.items() if k != "score"}, "score")):
            with self.subTest(bit=bit, raw=sorted(raw)):
                got, lost = studio.validate_drafts({"drafts": [{"text": WORDS, "skit": raw}]}, PITCH, 3)
                self.assertEqual(got, [])
                self.assertIn(bit, lost[0]["why"])

    def test_the_hard_lines_read_the_look_setting_and_score(self):
        for field, value in (("look", {"Elon Musk": "a silver tuxedo with a $TINY pin"}),
                             ("setting", "a lunar showroom, shaky leaked footage"),
                             ("score", "a funeral march under the whole film")):
            with self.subTest(field=field):
                draft = skit_draft()
                draft["skit"][field] = value
                self.assertIn("a skit's hard line", studio.screen(draft, [HOOK], []))

    def test_a_skit_needs_its_premise_what_they_really_did_and_what_they_do_in_it(self):
        # 2026-11-19: three skits on "raises" were a pun on a headline word, and
        # the figure only watched. The premise says the real thing first.
        for raw in ({k: v for k, v in RAW.items() if k != "premise"}, {**RAW, "premise": "  "},
                    {**RAW, "premise": ["a list"]}, {**RAW, "premise": "x" * (studio.SKIT_PREMISE_MAX + 1)}):
            with self.subTest(premise=raw.get("premise")):
                got, lost = studio.validate_drafts({"drafts": [{"text": WORDS, "skit": raw}]}, PITCH, 3)
                self.assertEqual(got, [])
                self.assertIn("premise", lost[0]["why"])

    def test_a_shot_asking_for_more_than_six_seconds_is_cut_to_six_not_refused(self):
        # 2026-11-20: an 8 s shot on kling-v3-pro reserves $1.924, over the
        # $1.60 a shot the live cap allows, so the whole skit would fail.
        shots = [{**SHOTS[0], "seconds": 8}, {**SHOTS[1], "seconds": 5}]
        got, lost = studio.validate_drafts({"drafts": [{"text": WORDS, "skit": {**RAW, "shots": shots}}]}, PITCH, 3)
        self.assertEqual(lost, [])
        self.assertEqual([s.get("seconds") for s in got[0]["skit"]["shots"]], [studio.SKIT_SHOT_MAX_SECS, 5])
        self.assertEqual(studio.SKIT_SHOT_MAX_SECS, 6)
        self.assertIn("3 to 6", studio.SKIT_SHAPE)
        self.assertIn("3 to 6 seconds", (REPO / "brain" / "agents" / "writer.md").read_text())

    def test_the_story_is_the_pitchs_hook_never_the_writers(self):
        got, _ = studio.validate_drafts({"drafts": [{"text": WORDS, "skit": {**RAW, "story": "trend:other"}}]}, PITCH, 3)
        self.assertEqual(got[0]["skit"]["story"], "trend:t1")

    def test_a_skit_draft_without_its_parts_is_dropped_with_why(self):
        bad = [{"text": WORDS}, {"text": WORDS, "skit": {**RAW, "shots": []}},
               {"text": WORDS, "skit": {**RAW, "logline": ""}}, {"text": WORDS, "skit": {**RAW, "aspect": "4:3"}},
               {"text": WORDS, "skit": {**RAW, "shots": SHOTS * 3}}, {"skit": RAW}]
        got, lost = studio.validate_drafts({"drafts": bad}, PITCH, 9)
        self.assertEqual(got, [])
        self.assertEqual(len(lost), len(bad))

    def test_the_hard_lines_drop_a_draft_before_the_tournament(self):
        facts = [HOOK]
        self.assertEqual(studio.screen(skit_draft(), facts, []), "")
        tiny = skit_draft()
        tiny["skit"]["shots"][0]["prompt"] += " He holds up a $TINY sign."
        self.assertIn("a skit's hard line", studio.screen(tiny, facts, []))
        stranger = skit_draft()
        stranger["skit"]["figures"] = ["Taylor Swift"]
        stranger["skit"]["look"] = {"Taylor Swift": LOOK["Elon Musk"]}
        self.assertIn("not named in the story", studio.screen(stranger, facts, []))
        real = skit_draft(text="leaked footage from the tesla event")
        self.assertIn("frame it as real", studio.screen(real, facts, []))

    def test_a_shots_numbers_are_staging_but_the_words_numbers_are_facts(self):
        lens = skit_draft()
        lens["skit"]["shots"][0]["prompt"] += " Shot on a 35mm lens at 24 frames, 1970s film stock."
        self.assertEqual(studio.screen(lens, [HOOK], []), "")
        self.assertIn("unsourced", studio.screen(skit_draft(text=WORDS + ". the fare is $40"), [HOOK], []))

    def test_a_logline_copying_an_example_is_dropped(self):
        afters = studio.example_afters(REPO / "brain")
        logline = next(a for a in afters if a.startswith("elon musk unveils"))
        copied = skit_draft()
        copied["skit"]["logline"] = logline
        self.assertIn("copies an example", studio.screen(copied, [HOOK], afters))

    def test_the_editor_sees_the_figures_logline_and_shots_never_the_story_id(self):
        shown = studio.shown(skit_draft())
        self.assertEqual(shown["skit"], {"figures": ["Elon Musk"], "premise": PREMISE, "logline": RAW["logline"],
                                         "look": LOOK, "setting": SETTING, "score": SCORE,
                                         "shots": [SHOTS[0]["prompt"], SHOTS[1]["prompt"]]})
        self.assertNotIn("trend:t1", json.dumps(shown))

    def test_the_writer_is_told_of_skits_only_when_they_are_on_and_pushed_in_a_skit_slot(self):
        sampled = {"pairs": ["Bet", "Scene", "Grudge"], "lore": [], "best": [], "worst": [], "explore": False}
        off = studio.pitch_message(studio.Config(formats=("post",)), {}, [], sampled)
        on = studio.pitch_message(studio.Config(formats=("post", "skit")), {}, [], sampled)
        slot = studio.pitch_message(studio.Config(formats=("post", "skit")), {}, [], sampled, skit_slot=True)
        self.assertNotIn(studio.SKIT_PITCH, off)
        self.assertIn(studio.SKIT_PITCH, on)
        self.assertNotIn(studio.SKIT_SLOT_LINE, on)
        self.assertIn(studio.SKIT_SLOT_LINE, slot)

    def test_a_skit_draft_turn_carries_the_craft_and_the_hard_lines(self):
        message = studio.draft_message(studio.Config(formats=("post", "skit")), PITCH, None, None, hook=HOOK)
        for bit in ('"skit": {"figures"', "anamorphic", "escalate", "Impossible on its face", "$TINY", "AI parody",
                    "Never a private person, a child"):
            self.assertIn(bit, message)
        post = studio.draft_message(studio.Config(), {**PITCH, "format": "post"}, None, None, hook=HOOK)
        self.assertNotIn(studio.SKIT_RULES, post)

    def test_the_brief_holds_the_carve_out_beside_the_two_rules_and_worked_examples(self):
        brief = (REPO / "brain" / "agents" / "writer.md").read_text()
        rules = brief.index("Joke about the situation, never at someone.")
        self.assertLess(rules, brief.index("**The one carve-out is a skit.**"))
        self.assertLess(brief.index("**The one carve-out is a skit.**"), brief.index("## before and after"))
        self.assertGreaterEqual(brief.count("Skit, joining the news."), 3)
        sys.path.insert(0, str(REPO / "hands" / "lib"))
        from teeny_hands.guard import skit as rules_
        for block in brief.split("Skit, joining the news.")[1:]:
            before = re.search(r"> before: (.*)", block).group(1)
            after = re.search(r"> after: (.*)", block).group(1)
            logline = re.search(r"> logline: (.*)", block).group(1)
            shots = re.findall(r"> shot: (.*)", block)
            self.assertTrue(1 <= len(shots) <= 4)
            # Each worked example passes its own hard lines, its look,
            # setting and score with it.
            figure = " ".join(re.match(r"(?:\w+'s )?([A-Z]\w+ [A-Z]\w+)", before).group(1).split())
            held = {key: re.search(rf"> {key}: (.*)", block).group(1) for key in ("look", "setting", "score")}
            raw = {"figures": [figure], "logline": logline, "shots": shots, "look": {figure: held["look"]},
                   "setting": held["setting"], "score": held["score"]}
            self.assertEqual(rules_.screen(raw, after, {"title": before}), [], before)

    def test_a_skit_slot_drafts_its_best_skit_and_one_other_format(self):
        by_id = {"p1": {"format": "post", "hook": "trend:a"}, "p2": {"format": "post", "hook": "trend:b"},
                 "p3": {"format": "thread", "hook": None}, "p4": {"format": "skit", "hook": "trend:c"},
                 "p5": {"format": "skit", "hook": "trend:d"}}
        chosen, note = studio.choose_drafted(["p1", "p2", "p3", "p4", "p5"], by_id, 3, True, skit_first=True)
        self.assertIn("p4", chosen)
        self.assertTrue(any(by_id[p]["format"] != "skit" for p in chosen))
        self.assertEqual(note["skit"], "p4")
        chosen, _ = studio.choose_drafted(["p4", "p5", "p1"], by_id, 2, True, skit_first=True)
        self.assertEqual(sorted(chosen), ["p1", "p4"])
        chosen, note = studio.choose_drafted(["p1", "p2"], by_id, 2, True, skit_first=True)
        self.assertIn("no skit", note["no_skit"])
        # Outside a skit slot, the ranking alone decides.
        self.assertEqual(studio.choose_drafted(["p1", "p2", "p3", "p4"], by_id, 3, True)[0], ["p1", "p2", "p3"])


class EditorSkitTest(unittest.TestCase):
    """2026-11-19: the first live skit slot's editor had no word on skits and
    said no to one for "giving a real person a role the report never gave
    her" and "the pun is too thin": ordinary-post rules on a labelled parody.
    Wherever the editor meets a skit it is told what one is and the only
    lines a skit may not cross."""

    LINES = ("sexual", "violence done to", "hate", "crime, drugs, illness or death", "a private person, a child",
             "victim or a bystander", "$TINY", "seen to endorse", "real footage or a real quote", "could not follow")

    def test_the_slot_question_on_a_skit_says_what_a_skit_is_and_its_only_lines(self):
        message = studio.final_message("D1", skit_draft(), {}, [])
        self.assertIn(studio.SKIT_EDITOR, message)
        self.assertIn(studio.SKIT_FINAL, message)
        for bit in ("a role the story never gave them", "the joke landing on them", "is not a reason to say none",
                    "only these", "Taste alone", *self.LINES):
            self.assertIn(bit, message)
        self.assertIn('"line"', message)
        fallback = studio.final_message("D2", skit_draft(), {}, [], fallback=True)
        self.assertIn(studio.SKIT_EDITOR, fallback)

    def test_the_slot_question_on_any_other_draft_is_as_it_was(self):
        post = {"format": "post", "text": "words"}
        message = studio.final_message("D1", post, {}, [])
        self.assertNotIn(studio.SKIT_EDITOR, message)
        self.assertNotIn('"line"', message)

    def test_the_comparisons_say_it_too_when_a_skit_is_in_the_batch(self):
        post = {"format": "post", "text": "words"}
        mixed = studio.judge_message([("P1", "D1", "D2")], {"D1": skit_draft(), "D2": post}, {}, [])
        self.assertIn(studio.SKIT_EDITOR, mixed)
        plain = studio.judge_message([("P1", "D1", "D2")], {"D1": post, "D2": dict(post)}, {}, [])
        self.assertNotIn(studio.SKIT_EDITOR, plain)

    def test_the_editors_rubric_leaves_the_labelled_parody_to_the_skit_lines(self):
        rubric = (REPO / "brain" / "narrative" / "STUDIO-EDITOR.md").read_text()
        line = next(x for x in rubric.splitlines() if "made up about a real person" in x)
        self.assertIn("skit", line)
        self.assertIn("labelled parody", line)


class WriterSkitBriefTest(unittest.TestCase):
    """2026-11-19: the skits were a pun on a headline word ("raises": the
    office floats up), the figure only watched, the shots were three pictures
    with no cause and effect, and nobody who had not read the headline could
    tell what it was about. The brief and the draft turn now ask for a
    premise from what the figure did, the figure driving every shot, three
    beats, and continuity restated in every shot."""

    def brief(self):
        text = (REPO / "brain" / "agents" / "writer.md").read_text()
        return text[text.index("## skits"):text.index("## before and after")]

    def test_the_brief_asks_for_a_premise_three_beats_and_continuity(self):
        brief = self.brief()
        for bit in ("**A premise, not a pun.**", "**They drive every shot.**", "**Three beats", "sound off",
                    "**Name them in every shot.**", "**One action a shot.**", "six words"):
            self.assertIn(bit, brief)
        # One short contrast drawn from the pun failure, on another story.
        self.assertIn("Bad:", brief)
        self.assertIn("Good:", brief)
        self.assertNotIn("raises", brief.lower())
        self.assertNotIn("kress", brief.lower())

    def test_every_worked_example_names_its_premise_and_its_figure_in_every_shot(self):
        for block in self.brief().split("Skit, joining the news.")[1:]:
            premise = re.search(r"> premise: (.*)", block)
            self.assertIsNotNone(premise, block[:80])
            self.assertIn(", so ", premise.group(1))
            shots = re.findall(r"> shot: (.*)", block)
            figure = re.search(r"> before: (?:\w+'s )?([A-Z]\w+ [A-Z]\w+)", block).group(1)
            self.assertEqual(len(shots), 3, figure)
            for shot in shots:
                # The model renders each shot alone: the full name every time.
                self.assertIn(figure, shot)
                spoken = re.findall(r'"([^"]+)"', shot)
                self.assertLessEqual(len(spoken), 1, shot)
                self.assertTrue(all(len(line.split()) <= 7 for line in spoken), spoken)

    def test_the_draft_turn_carries_the_premise_and_the_beats(self):
        message = studio.draft_message(studio.Config(formats=("post", "skit")), PITCH, None, None, hook=HOOK)
        for bit in ('"premise"', "not a pun", "drives every shot", "sound off", "full name", "six words"):
            self.assertIn(bit, message)

    def test_the_film_is_one_take_with_its_constants_said_once(self):
        # 2026-11-20: the operator, on three shots rendered apart: "the videos
        # don't connect. frame and audio wise". One take of at most 15 s, the
        # look, setting and score held as data, no shot bringing its music.
        message = studio.draft_message(studio.Config(formats=("post", "skit")), PITCH, None, None, hook=HOOK)
        for bit in ('"look"', '"setting"', '"score"', "one take", "20 seconds", "three or four shots", "3 to 6",
                    "its own music", "plain close-up"):
            self.assertIn(bit, message)
        self.assertNotIn("15 seconds", message)
        brief = self.brief()
        for bit in ("**One take, one look.**", "`look`", "`setting`", "`score`", "20 seconds", "three or four shots",
                    "its own music"):
            self.assertIn(bit, brief)
        self.assertNotIn("15 seconds", brief)

    def test_the_impossible_thing_is_in_frame_in_every_shot(self):
        # 2026-11-20: a paid one-take render ended on a photoreal close-up of
        # the figure in front of an ordinary office, which could pass for real.
        brief = self.brief()
        self.assertIn("**The impossible thing is in frame, every shot.**", brief)
        for bit in ("plain close-up", "ordinary"):
            self.assertIn(bit, brief)
        message = studio.draft_message(studio.Config(formats=("post", "skit")), PITCH, None, None, hook=HOOK)
        self.assertIn("the impossible thing is in frame", message.lower())
        for block in brief.split("Skit, joining the news.")[1:]:
            for key in ("look", "setting", "score"):
                self.assertRegex(block, rf"> {key}: \S")


class Scripted(studio.Room):
    """A room whose hand calls answer from a script, keyed by the first two
    words of the call; every call kept."""

    script: dict = {}
    seen: list = []

    def budgeted(self, argv, body, timeout, dry=True):
        self.seen.append((argv, body, dry, timeout))
        key = " ".join(argv[:2])
        answer = self.script.get(key, (0, {"ok": True}))
        return answer(body) if callable(answer) else answer

    def hand(self, argv, body=None, dry=True, timeout=120):
        self.seen.append((argv, body, dry, timeout))
        return True, {"ok": True, "marked": True}


ASSET = {"id": 7, "path": "state/media/skit-7.mp4", "detail": {"labelled": True, "label": "AI parody · @teenydroid"}}
MADE = {"ok": True, "asset": ASSET, "frames": ["state/media/skit-7-frame-0.jpg"], "label": "AI parody · @teenydroid",
        "labelled": True, "cost_usd": 0.9, "models": ["kling-v3-pro-text-to-video"] * 2}


class ProducerTest(unittest.TestCase):
    def room(self, dry=False, **script):
        cfg = studio.Config(formats=("post", "skit"), writer_model="w", editor_model="e", judge_model="j",
                            budget_secs=3600)
        room = Scripted(cfg, REPO, REPO / "hands", REPO / "state", REPO / "brain", "run-1", dry=dry)
        room.seen = []
        room.script = {
            "guard truth": (0, {"truthful": True, "unsupported": [], "reason": "ok", "judged": True}),
            "guard clarity": (0, {"clear": True, "reason": "ok", "judged": True}),
            "guard check": lambda body: (0, {"ok": True, "text": body["text"]}),
            "guard skit": (0, {"ok": True, "rule": "none", "reason": "allowed", "screen": [], "judged": True}),
            "video skit": (0, MADE),
            "guard view-skit": (0, {"ok": True, "labelled": True, "reason": "ok", "judged": True}),
            "x upload": (0, {"ok": True, "media_id": "m-9"}),
            "x post": (0, {"ok": True, "id": "post-1"}),
            **script,
        }
        return room

    def slot(self):
        return studio.Slot(order=["D1"], blind={"D1": skit_draft()}, board={}, stories_now=[], pitches={"p1": PITCH},
                           items={"trend:t1": HOOK}, stories={}, thread="t")

    def go(self, room, draft=None):
        attempt: dict = {"label": "D1"}
        result = studio.try_draft(room, draft or skit_draft(), attempt, self.slot(), [])
        return result, attempt, [" ".join(a[:2]) for a, *_ in room.seen]

    def test_a_skit_goes_through_every_check_in_order_then_out_with_its_video(self):
        result, attempt, calls = self.go(self.room())
        self.assertEqual(result, "posted")
        self.assertEqual([c for c in calls if c not in ("guard check", "story slot-mark", "story beat")],
                         ["guard truth", "guard clarity", "guard skit", "video skit", "guard view-skit", "x upload",
                          "x post"])
        room = self.room()
        self.go(room)
        body = next(b for a, b, *_ in room.seen if a[:2] == ["x", "post"])
        self.assertEqual((body["media_ids"], body["format"], body["text"]), (["m-9"], "skit", WORDS))
        video = next((b, t) for a, b, d, t in room.seen if a[:2] == ["video", "skit"])
        self.assertEqual(video[0]["story"], "trend:t1")
        self.assertLessEqual(video[0]["wait_seconds"], studio.Config().skit_secs)
        upload = next(a for a, *_ in room.seen if a[:2] == ["x", "upload"])
        self.assertNotIn("--alt", upload)  # X takes no alt text on a video
        self.assertEqual(attempt["skit"]["cost_usd"], 0.9)
        self.assertEqual(attempt["skit"]["media_id"], "m-9")

    def test_every_word_a_video_model_is_told_is_past_the_guard_first(self):
        room = self.room()
        self.go(room)
        checked = [b["text"] for a, b, *_ in room.seen if a[:2] == ["guard", "check"]]
        kept = skit_draft()["skit"]
        rules_ = studio._skit_rules()
        # The logline, each constant, each shot, and the two prompts a video
        # model is sent: the one-take script and each per-shot prompt.
        self.assertEqual(checked, [RAW["logline"], LOOK["Elon Musk"], SETTING, SCORE, SHOTS[0]["prompt"],
                                   SHOTS[1]["prompt"], rules_.script(kept), rules_.shot_prompt(kept, 0),
                                   rules_.shot_prompt(kept, 1)])
        video = next(b for a, b, *_ in room.seen if a[:2] == ["video", "skit"])
        self.assertEqual((video["look"], video["setting"], video["score"]), (LOOK, SETTING, SCORE))
        self.assertTrue(all(b.get("surface") == "task" for a, b, *_ in room.seen if a[:2] == ["guard", "check"]))

    def test_a_judge_that_cannot_answer_means_no_skit_and_no_money(self):
        room = self.room(**{"guard skit": (0, {"ok": False, "rule": "unknown", "judged": True,
                                               "reason": "the skit judge could not be reached: down"})})
        result, attempt, calls = self.go(room)
        self.assertEqual(result, "next")
        self.assertNotIn("video skit", calls)
        self.assertIn("hard lines did not pass", attempt["result"])

    def test_a_judge_that_did_not_run_at_all_is_no_skit(self):
        for answer in ((1, "Traceback"), (-2, "out of the run's time"), (0, {"ok": True})):
            with self.subTest(answer=answer):
                result, _, calls = self.go(self.room(**{"guard skit": answer}))
                self.assertEqual(result, "next")
                self.assertNotIn("video skit", calls)

    def test_a_shot_the_guard_holds_is_no_skit(self):
        result, attempt, calls = self.go(self.room(**{"guard check": (2, {"ok": False, "refused": "held"})}))
        self.assertEqual(result, "next")
        self.assertNotIn("guard skit", calls)

    def test_skits_off_or_over_budget_is_no_post_from_this_draft(self):
        for refusal in ({"refused": "skits are off", "rule": "off"}, {"refused": "past $1.00", "rule": "budget"}):
            with self.subTest(refusal=refusal):
                result, attempt, calls = self.go(self.room(**{"video skit": (2, {"ok": False, **refusal})}))
                self.assertEqual(result, "next")
                self.assertEqual(attempt["skit"]["result"]["rule"], refusal["rule"])
                self.assertNotIn("x post", calls)

    def test_an_unlabelled_file_is_never_handed_over(self):
        for made in ({**MADE, "labelled": False}, {**MADE, "label": "parody?"},
                     {**MADE, "asset": {**ASSET, "detail": {"labelled": False}}}, {**MADE, "frames": []}):
            with self.subTest(made=made):
                result, attempt, calls = self.go(self.room(**{"video skit": (0, made)}))
                self.assertEqual(result, "next")
                self.assertIn("no labelled file", attempt["result"])
                self.assertNotIn("x upload", calls)

    def test_the_viewer_must_pass_the_frames_judged(self):
        for verdict in ({"ok": False, "reason": "the label was read on 0 of 4 frames", "judged": True},
                        {"ok": True, "reason": "the viewer check is off", "judged": False}):
            with self.subTest(verdict=verdict):
                result, attempt, calls = self.go(self.room(**{"guard view-skit": (0, verdict)}))
                self.assertEqual(result, "next")
                self.assertNotIn("x upload", calls)

    def test_a_dry_run_judges_and_plans_but_buys_nothing(self):
        room = self.room(dry=True, **{"x post": (0, {"ok": True, "dry_run": True, "would_post": "w"})})
        result, attempt, calls = self.go(room)
        self.assertEqual(result, "would_post")
        self.assertIn("guard skit", calls)
        self.assertNotIn("video skit", calls)
        self.assertTrue(attempt["skit"]["planned"])

    def test_the_words_never_go_out_without_their_video(self):
        result, attempt, calls = self.go(self.room(**{"video skit": (1, {"ok": False, "error": "the cut failed"})}),
                                         skit_draft(alone=True))
        self.assertEqual(result, "next")
        self.assertNotIn("x post", calls)

    def test_without_time_for_a_render_no_skit_is_started(self):
        room = self.room()
        room.cfg.budget_secs = studio.SKIT_RESERVE_SECS + studio.SKIT_CUT_SECS + 60
        result, attempt, calls = self.go(room)
        self.assertEqual(result, "next")
        self.assertNotIn("video skit", calls)
        self.assertIn("not enough of the run's time", attempt["result"])


def _draft(did, fmt, hook):
    draft = {"id": did, "pitch": did, "hook": hook, "format": fmt, "text": f"words of {did}",
             "truth": {"text": "t", "facts": [{"id": "x"}]}}
    if fmt == "skit":
        draft["skit"] = {"story": hook}
    return draft


class SkitFallbackTest(unittest.TestCase):
    """A skit that cannot be made never empties a slot: the room falls back
    to the next draft in another format, and the failed video costs none of
    the fallback budget."""

    def publish(self, order, results):
        tried = []

        def fake_try(room, draft, attempt, slot_, afters):
            tried.append(attempt["label"])
            result = results.get(attempt["label"], "would_post")
            if draft["format"] == "skit" and result == "next":
                attempt["skit"] = {"failed": "the skit's hard lines did not pass it"}
            attempt["result"] = result
            return result

        def ask(root, agent, message, thread, model, secs):
            return 0, json.dumps({"verdict": "pick", "reason": "r"}), ""

        cfg = studio.Config(writer_model="w", editor_model="e", judge_model="j")
        room = studio.Room(cfg, REPO, REPO / "hands", REPO / "state", REPO / "brain", "run-1", ask=ask)
        blind = {"S1": _draft("d1", "skit", "trend:a"), "S2": _draft("d2", "skit", "trend:b"),
                 "S3": _draft("d3", "skit", "trend:c"), "P1": _draft("d4", "post", "trend:a"),
                 "P2": _draft("d5", "post", "trend:b"), "P3": _draft("d6", "post", "trend:c")}
        slot = studio.Slot(order=order, blind=blind, board={}, stories_now=[], pitches={}, items={}, stories={},
                           thread="t", rates={x: 1.0 for x in order}, close=0.0)
        with mock.patch.object(studio, "try_draft", fake_try), mock.patch.object(studio, "example_afters",
                                                                                 lambda brain: []):
            out = studio.publish(room, {}, slot)
        return out, tried

    def test_a_failed_skit_falls_back_to_another_format_and_no_other_skit_is_tried(self):
        out, tried = self.publish(["S1", "S2", "P1", "S3"], {"S1": "next"})
        self.assertEqual(tried, ["S1", "P1"])
        self.assertEqual(out["outcome"], "would_post")
        self.assertEqual(out["attempts"][0]["skits_dropped"], ["S2", "S3"])

    def test_the_failed_video_does_not_use_up_the_fallback_budget(self):
        # Without the exemption the pick and two fallbacks (S1, P2, P3) would
        # spend it, and P1 would never be reached.
        out, tried = self.publish(["S1", "P1", "P2", "P3"], {"S1": "next", "P2": "next", "P3": "next"})
        self.assertEqual(tried, ["S1", "P2", "P3", "P1"])
        self.assertEqual(out["outcome"], "would_post")

    def test_a_skit_that_went_out_is_recorded_for_the_reach_feedback(self):
        out, _ = self.publish(["S1", "P1"], {})
        self.assertEqual(out["outcome"], "would_post")
        self.assertNotIn("skit", out)  # fake_try left no producer note; the real one does (ProducerTest)
        note = {"asset": 7, "models": ["m"], "cost_usd": 0.9, "label": "AI parody"}
        got = studio.skit_outcome({"skit": {"story": "trend:a", "figures": ["X Y"], "logline": "l", "shots": [{}]}},
                                  note, "post-1")
        self.assertEqual(got, {"story": "trend:a", "figures": ["X Y"], "logline": "l", "shots": 1, "asset": 7,
                               "models": ["m"], "cost_usd": 0.9, "label": "AI parody", "post_id": "post-1",
                               "planned": False})


TREND = {"ok": True, "source": "digg", "stories": [
    {"id": "t1", "topic": "tech", "title": HOOK["title"], "summary": HOOK["summary"], "score": 900, "rank": 1,
     "sources": 80, "authors": 60, "published_at": "2026-11-18T06:00:00+00:00"}]}
NOW = 1_795_003_200.0


class SkitSlotRunTest(unittest.TestCase):
    """A whole dry run of a skit slot with stand-in hands: the best skit is
    tried first even when the editor ranked a post above it, it passes its
    hard lines, is planned and not bought, and the record says what it was."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "hands").mkdir()
        scripts = {
            "story": 'if [ "$1" = record-run ]; then printf "%s\\n" "$body" >> "$(dirname "$0")/../records"; '
                     'echo \'{"ok": true, "recorded": true}\'; else echo \'{"ok": true}\'; fi\n',
            "guard": 'case "$1" in\n'
                     '  truth) echo \'{"truthful": true, "unsupported": [], "reason": "ok", "judged": true}\' ;;\n'
                     '  clarity) echo \'{"clear": true, "reason": "ok", "judged": true}\' ;;\n'
                     '  skit) echo \'{"ok": true, "rule": "none", "reason": "allowed", "screen": [], "judged": true}\' ;;\n'
                     '  check) echo "$body" ;;\nesac\n',
            "x": 'echo \'{"ok": true, "dry_run": true, "would_post": "w"}\'\n',
            "video": 'echo \'{"ok": false, "error": "a dry run must never reach hands/video"}\'; exit 1\n',
            "trend": f"echo '{json.dumps(TREND)}'\n",
            "hn": 'echo \'{"ok": true, "stories": []}\'\n',
        }
        for name, script in scripts.items():
            path = root / "hands" / name
            path.write_text('#!/usr/bin/env bash\nbody="$(cat)"\nprintf "%s %s\\n" "' + name + '" "$*" '
                            '>> "$(dirname "$0")/../calls"\n' + script)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        (root / "desk.json").write_text(json.dumps({"items": [{"id": "evt:2", "what": "a bill"}], "storylines": [],
                                                    "scoreboard": {}}))
        self.root = root
        self.env = {"TEENY_ROOT": str(REPO), "TEENY_STATE": str(root / "state"), "TEENY_HANDS": str(root / "hands"),
                    "TEENY_SECRETS": str(root / "secrets"), "TEENY_BRAIN": str(REPO / "brain"),
                    "STUDIO_RUN_ID": "skit-1", "STUDIO_WRITER_MODEL": "w", "STUDIO_EDITOR_MODEL": "e",
                    "STUDIO_FORMATS": "post,skit", "STUDIO_SKIT_SLOTS": "evening", "TEENY_NOW": str(NOW)}
        self.args = argparse.Namespace(dry_run=True, slot="evening", desk=str(root / "desk.json"), force_slot=False,
                                       no_outside=False, no_slot=True)

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, root, agent, message, thread, model, secs):
        step = message.split("\n", 1)[0]
        if step == "# studio: pitch":
            self.assertIn(studio.SKIT_SLOT_LINE, message)
            return 0, json.dumps({"pitches": [
                {"id": "p1", "hook": "trend:t1", "item": "evt:2", "format": "post", "shape": "bet", "angle": "a"},
                {"id": "p2", "hook": "trend:t1", "format": "skit", "shape": "scene", "angle": "b"}],
                "ranking": ["p1", "p2"]}), ""
        if step == "# studio: draft":
            if '"format": "skit"' in message:
                return 0, json.dumps({"drafts": [{"text": WORDS, "skit": RAW, "meaning": "m"}]}), ""
            return 0, json.dumps({"drafts": [{"text": "tesla says the robotaxi is ready. my wallet still walks"}]}), ""
        if step == "# studio: compare":
            pairs = re.findall(r"^- (P\d+): (D\d+) or (D\d+)$", message, re.M)
            # The editor prefers the post.
            verdicts = []
            for pid, a, b in pairs:
                a_post = '"' + a + '": {"format": "post"' in message
                verdicts.append({"pair": pid, "winner": a if a_post else b, "rule": 1, "why": "x"})
            return 0, json.dumps({"verdicts": verdicts}), ""
        if step == "# studio: the slot":
            self.assertIn('"skit"', message)  # the slot question is on the skit
            return 0, json.dumps({"verdict": "pick", "reason": "r"}), ""
        return 1, "", f"unexpected {step}"

    def test_a_skit_slot_tries_its_skit_first_and_plans_it_without_buying(self):
        code, out = studio.run(self.args, self.env, ask=self.ask)
        self.assertEqual((code, out["outcome"]), (0, "picked"), out)
        record = json.loads((self.root / "state" / "studio" / "runs.jsonl").read_text().splitlines()[-1])
        self.assertTrue(record["skit_slot"])
        first = record["publish"]["attempts"][0]
        self.assertEqual(first["format"], "skit")
        self.assertTrue(first["skit"]["planned"])
        self.assertEqual(record["published"]["skit"]["story"], "trend:t1")
        self.assertEqual(record["published"]["skit"]["figures"], ["Elon Musk"])
        calls = (self.root / "calls").read_text()
        self.assertIn("guard skit", calls)
        self.assertNotIn("video ", calls)


if __name__ == "__main__":
    unittest.main()


class SkitVetoRunTest(unittest.TestCase):
    """In a skit slot the editor's veto of the promoted skit is "the skit
    cannot go out", not an empty slot: the veto is recorded, the skits are
    dropped, and the tournament's own top draft gets its own slot question."""

    setUp = SkitSlotRunTest.setUp
    tearDown = SkitSlotRunTest.tearDown

    def ask(self, root, agent, message, thread, model, secs):
        if message.startswith("# studio: the slot"):
            self.finals.append(message)
            vetoed = '"skit"' in message
            return 0, json.dumps({"verdict": "none" if vetoed else "pick",
                                  "reason": "the skit is flat" if vetoed else "the post earns it"}), ""
        return SkitSlotRunTest.ask(self, root, agent, message, thread, model, secs)

    def test_a_vetoed_skit_falls_back_to_the_tournaments_top_draft(self):
        self.finals = []
        code, out = studio.run(self.args, self.env, ask=self.ask)
        self.assertEqual((code, out["outcome"]), (0, "picked"), out)
        record = json.loads((self.root / "state" / "studio" / "runs.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["skit_veto"]["reason"], "the skit is flat")
        self.assertEqual(len(self.finals), 2)
        self.assertEqual(record["final"]["verdict"], "pick")
        self.assertEqual(record["pick"]["format"], "post")
        self.assertEqual(record["publish"]["attempts"][0]["format"], "post")
        self.assertNotIn("skit", [a["format"] for a in record["publish"]["attempts"]])
        self.assertEqual(record["published"]["outcome"], "would_post")

    def test_a_veto_of_the_post_after_the_skit_is_a_real_veto(self):
        self.finals = []

        def ask(root, agent, message, thread, model, secs):
            if message.startswith("# studio: the slot"):
                self.finals.append(message)
                return 0, json.dumps({"verdict": "none", "reason": "neither"}), ""
            return SkitSlotRunTest.ask(self, root, agent, message, thread, model, secs)
        code, out = studio.run(self.args, self.env, ask=ask)
        self.assertEqual(out["outcome"], "vetoed")
        self.assertEqual(len(self.finals), 2)


SECOND = {"figures": ["Elon Musk"], "premise": "musk said the car drives itself, so it drives off without him",
          "look": {"Elon Musk": "silver tuxedo, a brass diving helmet under one arm"},
          "setting": "a glossy launch stage that floods into an ocean floor, god rays, film grain",
          "score": "a comic tuba chase theme under the whole film",
          "logline": "elon musk chases his own robotaxi across the ocean floor after it leaves the launch without him",
          "shots": [{"prompt": "Wide shot on a glossy launch stage: Elon Musk in a silver tuxedo pulls a sheet off a "
                               "chrome robotaxi, and it drives away on its own while he is still bowing."},
                    {"prompt": "Underwater wide shot, god rays: Elon Musk in a silver tuxedo and a brass diving helmet "
                               "runs across the ocean floor after the chrome robotaxi, waving the sheet."}],
          "aspect": "16:9"}
SECOND_WORDS = "tesla's robotaxi is meant to drive itself, so my cartoon has it ditch him at the start. parody"


class NextSkitRunTest(unittest.TestCase):
    """A skit slot whose first skit the editor refuses tries its next skit
    before any other format, every refusal kept (`skit_vetoes`, and the
    first as `skit_veto` for the readers that had only it); a no to a skit
    never leaves a skit slot empty while another draft is left."""

    setUp = SkitSlotRunTest.setUp
    tearDown = SkitSlotRunTest.tearDown

    def ask_with(self, slot_answer, skits_win=False):
        self.finals = []

        def ask(root, agent, message, thread, model, secs):
            step = message.split("\n", 1)[0]
            if step == "# studio: draft" and '"format": "skit"' in message:
                return 0, json.dumps({"drafts": [{"text": WORDS, "skit": RAW, "meaning": "m"},
                                                 {"text": SECOND_WORDS, "skit": SECOND, "meaning": "m"}]}), ""
            if step == "# studio: compare" and skits_win:
                pairs = re.findall(r"^- (P\d+): (D\d+) or (D\d+)$", message, re.M)
                verdicts = [{"pair": pid, "winner": a if '"' + a + '": {"format": "skit"' in message else b,
                             "rule": 1, "why": "x"} for pid, a, b in pairs]
                return 0, json.dumps({"verdicts": verdicts}), ""
            if step == "# studio: the slot":
                which = "first" if RAW["logline"] in message else "second" if SECOND["logline"] in message else "post"
                self.finals.append(which)
                return 0, json.dumps(slot_answer(which)), ""
            return SkitSlotRunTest.ask(self, root, agent, message, thread, model, secs)
        return ask

    def record(self):
        return json.loads((self.root / "state" / "studio" / "runs.jsonl").read_text().splitlines()[-1])

    def test_a_refused_skit_hands_the_slot_to_the_next_skit(self):
        refuse = {"verdict": "none", "reason": "a scene that could pass for a real launch", "line": "real footage"}
        # The first skit asked is refused, the next one picked.
        ask = self.ask_with(lambda which: refuse if len(self.finals) == 1 else {"verdict": "pick", "reason": "r"})
        code, out = studio.run(self.args, self.env, ask=ask)
        self.assertEqual((code, out["outcome"]), (0, "picked"), out)
        record = self.record()
        self.assertEqual(len(self.finals), 2)
        self.assertEqual(set(self.finals), {"first", "second"})
        self.assertEqual(record["pick"]["format"], "skit")
        self.assertEqual(record["skit_veto"]["reason"], refuse["reason"])
        self.assertEqual(record["skit_veto"]["line"], "real footage")
        self.assertEqual([v["label"] for v in record["skit_vetoes"]], [record["skit_veto"]["label"]])
        self.assertNotIn(record["skit_veto"]["label"], record["slot_order"])
        self.assertEqual(record["publish"]["attempts"][0]["format"], "skit")

    def test_every_skit_refused_falls_back_to_the_other_formats(self):
        ask = self.ask_with(lambda which: {"verdict": "pick" if which == "post" else "none", "reason": "flat"})
        code, out = studio.run(self.args, self.env, ask=ask)
        record = self.record()
        self.assertEqual(self.finals[-1], "post")
        self.assertEqual(sorted(self.finals[:2]), ["first", "second"])
        self.assertEqual(len(record["skit_vetoes"]), 2)
        self.assertEqual(record["skit_veto"], record["skit_vetoes"][0])
        self.assertEqual((record["pick"]["format"], record["final"]["verdict"]), ("post", "pick"))
        self.assertNotIn("skit", [a["format"] for a in record["publish"]["attempts"]])

    def test_a_no_to_the_tournaments_own_top_skit_does_not_empty_a_skit_slot(self):
        # Before: the promoted skit fell back, but a skit that won the
        # comparisons outright and was refused ended the slot empty.
        ask = self.ask_with(lambda which: {"verdict": "pick" if which == "post" else "none", "reason": "flat"},
                            skits_win=True)
        code, out = studio.run(self.args, self.env, ask=ask)
        record = self.record()
        self.assertEqual(record["ranking"][0]["label"], record["skit_vetoes"][0]["label"])
        self.assertEqual((out["outcome"], record["pick"]["format"]), ("picked", "post"))
        self.assertEqual(len(self.finals), 3)


class LiveRoomDryHandTest(unittest.TestCase):
    """A live room whose video hand still runs under DRY_RUN makes no video:
    that is no skit, never the skit's words posted alone."""

    def test_a_dry_run_answer_in_a_live_room_is_no_skit_and_the_fallback_goes_out(self):
        producer = ProducerTest()
        room = producer.room(**{"video skit": (0, {"ok": True, "dry_run": True, "would_generate": "skit"})})
        result, attempt, calls = producer.go(room)
        self.assertEqual(result, "next")
        self.assertIn("DRY_RUN", attempt["skit"]["failed"])
        self.assertNotIn("x post", calls)
        self.assertNotIn("x upload", calls)
        # The slot falls back: the next draft, another format, goes out.
        post = {"id": "d2", "pitch": "p2", "format": "post", "text": "the robotaxi words, on their own terms",
                "truth": {"text": "t", "facts": [HOOK]}}
        slot = studio.Slot(order=["D1", "D2"], blind={"D1": skit_draft(), "D2": post}, board={}, stories_now=[],
                           pitches={"p1": PITCH}, items={"trend:t1": HOOK}, stories={}, thread="t",
                           rates={"D1": 1.0, "D2": 1.0}, close=0.0)
        room.seen = []
        room.ask = lambda *a: (0, json.dumps({"verdict": "pick", "reason": "r"}), "")
        with mock.patch.object(studio, "example_afters", lambda brain: []):
            out = studio.publish(room, {}, slot)
        self.assertEqual((out["outcome"], out["label"]), ("posted", "D2"))
        posts = [b for a, b, *_ in room.seen if a[:2] == ["x", "post"]]
        self.assertEqual([p["text"] for p in posts], [post["text"]])
