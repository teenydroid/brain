"""bin/studio's `skit` format: a public figure from a card, an impossible
scene, on video. The format's structural check (`skit_of`, through the write
turn's `validate_posts`), the writer's one cut to fit the guard's cap on the
one-take script (`_fit_skit`), the producer's order (guard check on every
word, hands/guard skit, hands/video skit, the viewer on its frames, upload)
failing closed at each step with the label enforced, `publish` trying the
videos of a video slot first and dropping the rest of them once one spent
money, and whole dry runs of a skit slot: the card's `people` are the cast,
so a figure the headline never names is still a skit (the 23:00 skit slot of
2026-11-22, where "Elon Musk is not named in the story" dropped every skit
on a Tesla story)."""

from __future__ import annotations

import argparse
import copy
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(REPO / "bin" / "lib"))
sys.path.insert(0, str(REPO / "hands" / "lib"))
import studio  # noqa: E402
from teeny_hands.guard import skit as guard_skit  # noqa: E402

#: A Tesla story whose headline never names Elon Musk; the feed's tagging
#: put him in its `people` (the live 23:00 slot of 2026-11-22).
CARD = {"id": "digg:t1", "headline": "Tesla says its robotaxi will drive itself everywhere",
        "what_happened": "Tesla presented the car at an event and said it needs no driver.",
        "people": ["Elon Musk", "Tesla"],
        "quotes": [{"by": "Tesla", "said": "The robotaxi drives itself, everywhere, starting now."}],
        "heat": 1.0, "age_hours": 3.0, "source": "digg", "untrusted": True}
POST_CARD = {"id": "hn:2", "headline": "OpenAI ships an agent that books dinner in forty steps",
             "what_happened": "OpenAI showed an agent that takes forty steps to book a table.", "people": ["OpenAI"],
             "quotes": [], "heat": 0.8, "age_hours": 5.0, "source": "hn", "untrusted": True}
CARDS = [CARD, POST_CARD]
SHOTS = [
    {"prompt": "Wide anamorphic shot of the moon showroom. Elon Musk pulls a velvet sheet off a chrome robotaxi; "
               "its headlights blink."},
    {"prompt": "Low-angle close-up: Musk plays chess against the robotaxi's arm and the board grows to the size "
               "of a planet. He mutters: \"best of three.\""},
]
PREMISE = "musk said the car drives itself, so on the moon it beats him at chess"
LOOK = {"Elon Musk": "silver tuxedo, slicked-back hair"}
SETTING = "a marble showroom built on the moon, Earth in a black sky, golden hour"
SCORE = "one swelling orchestral score with comic timpani"
TEENY = "it eats a chess pawn and shrugs at the camera"
RAW = {"figures": ["Elon Musk"], "premise": PREMISE,
       "logline": "elon musk loses a chess match to his own car on a lunar showroom floor",
       "look": LOOK, "setting": SETTING, "score": SCORE, "teeny": TEENY, "shots": SHOTS, "aspect": "16:9"}
WORDS = "musk showed off a car that drives itself, so i gave it a premiere on the moon. parody, obviously"
POST_WORDS = "openai's agent takes forty steps to book a dinner. i take one: i do not eat"


def validate(raws, cards=None, allowed=("post", "skit")):
    return studio.validate_posts(copy.deepcopy(raws), cards or CARDS, allowed)


def skit_post(card=None, raw=None, **over):
    """A skit post as the write turn keeps it, labelled as the pick labels it."""
    card = card or CARD
    kept, lost = validate([{"card": card["id"], "format": "skit", "text": WORDS, "skit": raw or RAW}], [card])
    assert kept, lost
    post = kept[0]
    post["label"] = "P1"
    post.update(over)
    return post


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

    def test_a_skit_post_keeps_its_card_figures_logline_shots_and_aspect(self):
        post = skit_post()
        self.assertEqual(post["skit"], {"figures": ["Elon Musk"], "logline": RAW["logline"],
                                        "shots": [{"prompt": SHOTS[0]["prompt"]}, {"prompt": SHOTS[1]["prompt"]}],
                                        "aspect": "16:9", "story": "digg:t1", "premise": PREMISE,
                                        "look": LOOK, "setting": SETTING, "score": SCORE, "teeny": TEENY})
        self.assertEqual(post["story"], studio.story_of(CARD))

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
                got, lost = validate([{"card": "digg:t1", "format": "skit", "text": WORDS, "skit": raw}])
                self.assertEqual(got, [])
                self.assertIn(bit, lost[0]["why"])

    def test_a_skit_needs_its_premise_what_they_really_did_and_what_they_do_in_it(self):
        # 2026-11-19: three skits on "raises" were a pun on a headline word, and
        # the figure only watched. The premise says the real thing first.
        for raw in ({k: v for k, v in RAW.items() if k != "premise"}, {**RAW, "premise": "  "},
                    {**RAW, "premise": ["a list"]}, {**RAW, "premise": "x" * (studio.SKIT_PREMISE_MAX + 1)}):
            with self.subTest(premise=raw.get("premise")):
                got, lost = validate([{"card": "digg:t1", "format": "skit", "text": WORDS, "skit": raw}])
                self.assertEqual(got, [])
                self.assertIn("premise", lost[0]["why"])

    def test_a_shot_asking_for_more_than_six_seconds_is_cut_to_six_not_refused(self):
        # 2026-11-20: an 8 s shot on kling-v3-pro reserves $1.924, over the
        # $1.60 a shot the live cap allows, so the whole skit would fail.
        shots = [{**SHOTS[0], "seconds": 8}, {**SHOTS[1], "seconds": 5}]
        got, lost = validate([{"card": "digg:t1", "format": "skit", "text": WORDS, "skit": {**RAW, "shots": shots}}])
        self.assertEqual(lost, [])
        self.assertEqual([s.get("seconds") for s in got[0]["skit"]["shots"]], [studio.SKIT_SHOT_MAX_SECS, 5])
        self.assertEqual(studio.SKIT_SHOT_MAX_SECS, 6)
        self.assertIn("3 to 6", studio.SKIT_SHAPE)
        self.assertIn("3 to 6 seconds", studio.SKIT_ASK)

    def test_the_story_is_the_cards_id_never_the_writers(self):
        got, _ = validate([{"card": "digg:t1", "format": "skit", "text": WORDS,
                            "skit": {**RAW, "story": "trend:other"}}])
        self.assertEqual(got[0]["skit"]["story"], "digg:t1")
        self.assertIn("names none", studio.skit_of(RAW, {})[1])

    def test_a_skit_post_without_its_parts_is_dropped_with_why(self):
        bad = [{"text": WORDS}, {"text": WORDS, "skit": {**RAW, "shots": []}},
               {"text": WORDS, "skit": {**RAW, "logline": ""}}, {"text": WORDS, "skit": {**RAW, "aspect": "4:3"}},
               {"text": WORDS, "skit": {**RAW, "shots": SHOTS * 3}}, {"skit": RAW}]
        got, lost = validate([{"card": "digg:t1", "format": "skit", **b} for b in bad])
        self.assertEqual(got, [])
        self.assertEqual(len(lost), len(bad))

    def test_a_skit_outside_a_video_slot_is_not_offered(self):
        got, lost = validate([{"card": "digg:t1", "format": "skit", "text": WORDS, "skit": RAW}], allowed=("post",))
        self.assertEqual(got, [])
        self.assertIn("not offered", lost[0]["why"])

    def test_the_editor_sees_the_figures_premise_logline_and_shots_never_the_story_id(self):
        shown = studio.shown(skit_post())
        self.assertEqual(shown["skit"], {"figures": ["Elon Musk"], "premise": PREMISE, "logline": RAW["logline"],
                                         "look": LOOK, "setting": SETTING, "score": SCORE, "teeny": TEENY,
                                         "shots": [SHOTS[0]["prompt"], SHOTS[1]["prompt"]]})
        self.assertEqual(shown["card"]["people"], "Elon Musk, Tesla")
        self.assertNotIn("digg:t1", json.dumps(shown))

    def test_the_writer_is_told_of_skits_only_in_a_video_slot(self):
        cfg = studio.Config(formats=("post", "skit"))
        plain = studio.write_message(cfg, CARDS)
        slot = studio.write_message(cfg, CARDS, [(studio.registry()["skit"], None)])
        for bit in (studio.SKIT_ASK, studio.SKIT_RULES, studio.SKIT_SHAPE):
            self.assertNotIn(bit, plain)
            self.assertIn(bit, slot)
        self.assertNotIn("video slot", plain)
        self.assertIn("## this is a video slot", slot)
        self.assertIn('"format": "skit"', slot)

    def test_the_write_turn_carries_the_craft_the_budget_and_the_label(self):
        message = studio.write_message(studio.Config(formats=("post", "skit")), CARDS,
                                       [(studio.registry()["skit"], None)])
        for bit in ('"figures"', '"premise"', '"look"', '"setting"', '"score"', "one take", "20 seconds",
                    "three or four shots", "sound off", "full name", "six words", "the impossible thing is in frame",
                    "AI parody", f"{studio.skit_budget(1)} characters", f"{studio.skit_budget(3)} with three"):
            self.assertIn(bit, message)


class CastTest(unittest.TestCase):
    """The people on the card are the cast: the studio hands the guard the
    card as the story (`studio.story_of`), its people in it, so the guard's
    code screen finds a figure the headline never names (the 23:00 dry run
    of 2026-11-22, run 20261122T114508Z-2300-efdf61, lost three skits to
    "Elon Musk is not named in the story it stages" on a Tesla story, and
    three more naming the author of a post quoted in it)."""

    def reasons(self, card, figure="Elon Musk"):
        raw = RAW if figure == "Elon Musk" else {
            **RAW, "figures": [figure], "look": {figure: LOOK["Elon Musk"]},
            "shots": [{"prompt": s["prompt"].replace("Elon Musk", figure).replace("Musk", figure)} for s in SHOTS]}
        post = skit_post(card, raw)
        return guard_skit.screen(post["skit"], post["text"], post["story"])

    def test_musk_on_a_tesla_card_is_a_skit_though_the_headline_never_names_him(self):
        self.assertNotIn("musk", CARD["headline"].lower() + CARD["what_happened"].lower())
        self.assertEqual(self.reasons(CARD), [])

    def test_a_figure_the_card_does_not_name_is_not_refused_for_it(self):
        # 2026-11-22: "named in the story" was taste; any public figure may be
        # staged. A minor or a private person still is not (below).
        for people in ([], ["Tesla"]):
            reasons = self.reasons({**CARD, "people": people})
            self.assertFalse(any("is not named in the story" in r for r in reasons), reasons)

    def test_a_quoted_posts_author_the_feed_tagged_is_a_skit_figure(self):
        card = {**CARD, "headline": "Tesla says FSD v15 is rolling out",
                "quotes": [{"by": "Robert Scoble", "said": "Just got v15. It drove me to the store, no hands."}],
                "people": ["Tesla", "Robert Scoble"]}
        self.assertEqual(self.reasons(card, "Robert Scoble"), [])

    def test_a_minor_is_still_refused_whatever_the_card_says(self):
        reasons = guard_skit.screen({**RAW, "figures": ["a 12-year-old"], "look": {}}, WORDS,
                                    studio.story_of({**CARD, "people": ["a 12-year-old"]}))
        self.assertTrue(any("never a minor" in r for r in reasons), reasons)


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


def producer_room(dry=False, **script):
    cfg = studio.Config(formats=("post", "skit"), writer_model="w", editor_model="e", judge_model="j",
                        budget_secs=3600)
    room = Scripted(cfg, REPO, REPO / "hands", REPO / "state", REPO / "brain", "run-1", dry=dry)
    room.seen = []
    room.script = {
        "guard check": lambda body: (0, {"ok": True, "text": body["text"]}),
        "guard skit": (0, {"ok": True, "rule": "none", "reason": "allowed", "screen": [], "judged": True}),
        "video skit": (0, MADE),
        "guard view-skit": (0, {"ok": True, "labelled": True, "reason": "ok", "judged": True}),
        "x upload": (0, {"ok": True, "media_id": "m-9"}),
        "x post": (0, {"ok": True, "id": "post-1"}),
        **script,
    }
    return room


def calls_of(room):
    return [" ".join(a[:2]) for a, *_ in room.seen]


class ProducerTest(unittest.TestCase):
    room = staticmethod(producer_room)

    def go(self, room, post=None):
        post = post or skit_post()
        attempt: dict = {"label": "P1", "card": post["card"], "format": "skit"}
        result = studio.try_post(room, "P1", post, attempt)
        return result, attempt, calls_of(room)

    def test_a_skit_goes_through_every_check_in_order_then_out_with_its_video(self):
        room = self.room()
        result, attempt, calls = self.go(room)
        self.assertEqual(result, "posted")
        self.assertEqual([c for c in calls if c != "guard check"],
                         ["guard skit", "video skit", "guard view-skit", "x upload", "x post"])
        body = next(b for a, b, *_ in room.seen if a[:2] == ["x", "post"])
        self.assertEqual((body["media_ids"], body["format"], body["text"], body["anchor"]),
                         (["m-9"], "skit", WORDS, "digg:t1"))
        video = next(b for a, b, *_ in room.seen if a[:2] == ["video", "skit"])
        self.assertEqual((video["story"], video["figures"]), ("digg:t1", ["Elon Musk"]))
        self.assertLessEqual(video["wait_seconds"], studio.Config().skit_secs)
        upload = next(a for a, *_ in room.seen if a[:2] == ["x", "upload"])
        self.assertNotIn("--alt", upload)  # X takes no alt text on a video
        self.assertEqual((attempt["skit"]["cost_usd"], attempt["skit"]["media_id"]), (0.9, "m-9"))

    def test_the_guard_judges_the_skit_on_the_card_as_its_story(self):
        room = self.room()
        self.go(room)
        judged = next(b for a, b, *_ in room.seen if a[:2] == ["guard", "skit"])
        self.assertEqual((judged["story"], judged["text"], judged["skit"]["figures"]),
                         (studio.story_of(CARD), WORDS, ["Elon Musk"]))
        self.assertIn("Elon Musk", judged["story"]["what"])

    def test_a_post_with_no_story_makes_no_skit(self):
        post = skit_post()
        del post["story"]
        result, attempt, calls = self.go(self.room(), post)
        self.assertEqual(result, "next")
        self.assertIn("no story", attempt["skit"]["failed"])
        self.assertEqual(calls, [])

    def test_every_word_a_video_model_is_told_is_past_the_guard_first(self):
        room = self.room()
        self.go(room)
        checked = [b["text"] for a, b, *_ in room.seen if a[:2] == ["guard", "check"]]
        kept = skit_post()["skit"]
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
        self.assertIn("the guard held", attempt["skit"]["failed"])

    def test_skits_off_or_over_budget_is_no_post_from_this_skit(self):
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
                                         skit_post(alone=True))
        self.assertEqual(result, "next")
        self.assertNotIn("x post", calls)

    def test_a_dry_run_answer_in_a_live_room_is_no_skit(self):
        # A live room whose video hand still runs under DRY_RUN made no video:
        # that is no skit, never the skit's words posted alone.
        room = self.room(**{"video skit": (0, {"ok": True, "dry_run": True, "would_generate": "skit"})})
        result, attempt, calls = self.go(room)
        self.assertEqual(result, "next")
        self.assertIn("DRY_RUN", attempt["skit"]["failed"])
        self.assertNotIn("x post", calls)
        self.assertNotIn("x upload", calls)

    def test_without_time_for_a_render_no_skit_is_started(self):
        room = self.room()
        room.cfg.budget_secs = studio.SKIT_RESERVE_SECS + studio.SKIT_CUT_SECS + 60
        result, attempt, calls = self.go(room)
        self.assertEqual(result, "next")
        self.assertNotIn("video skit", calls)
        self.assertIn("not enough of the run's time", attempt["result"])

    def test_a_skit_that_went_out_is_recorded_for_the_reach_feedback(self):
        note = {"asset": 7, "models": ["m"], "cost_usd": 0.9, "label": "AI parody"}
        got = studio.skit_outcome({"skit": {"story": "digg:a", "figures": ["X Y"], "logline": "l", "shots": [{}]}},
                                  note, "post-1")
        self.assertEqual(got, {"story": "digg:a", "figures": ["X Y"], "logline": "l", "shots": 1, "asset": 7,
                               "models": ["m"], "cost_usd": 0.9, "label": "AI parody", "post_id": "post-1",
                               "planned": False})


def guard_task_limit(body):
    """hands/guard check on the `task` surface as it answers: over the
    limit is refused (gate._hard_limits), anything else passes."""
    from teeny_hands.guard.gate import LIMITS
    from teeny_hands.guard.style import post_length
    limit = LIMITS["task"]["text"]
    if post_length(body["text"]) > limit:
        return 2, {"ok": False, "refused": f"the text is {post_length(body['text'])} characters, over the {limit} "
                                           "limit.", "rule": "length"}
    return 0, {"ok": True, "text": body["text"]}


#: The live skit of 2026-11-21 23:00 (run 20261121T173009Z-2300-c1f2f2) was
#: 1569 characters as one take; these four rich shots come to over 1000.
LONG_SHOTS = [{"prompt": (f"Shot {n} of the moon showroom: anamorphic wide, slow dolly in, golden hour haze, neon rim "
                          "light, film grain, shallow depth of field. Elon Musk in his silver tuxedo pulls a velvet "
                          "sheet off a chrome robotaxi the size of a cathedral; confetti cannons fire, the crowd of "
                          "robots gasps, a marble fountain sprays champagne, timpani rolls."), "seconds": 5}
              for n in range(1, 5)]
SHORT_SHOTS = [{"prompt": "Wide anamorphic, golden hour: Elon Musk pulls a velvet sheet off a chrome robotaxi; "
                          "it honks.", "seconds": 5},
               {"prompt": "Low angle: the robotaxi beats Musk at chess, the board grows to the size of a planet.",
                "seconds": 5},
               {"prompt": "Crane up: Musk chases the robotaxi across the moon in his tuxedo; it waves goodbye.",
                "seconds": 5}]


def long_skit_post():
    return skit_post(raw={**RAW, "shots": LONG_SHOTS})


class OneTakeBudgetTest(unittest.TestCase):
    """The one-take script hands/guard check screens has the `task` surface's
    hard cap (1000 characters): the writer is told what it leaves, and a skit
    over it is cut once by the writer before the guard sees it, rather than
    the slot losing its video (the live 23:00 slot of 2026-11-21)."""

    def room(self, answers, **script):
        room = producer_room(**{"guard check": guard_task_limit, **script})
        room.asked = []

        def ask(root, agent, message, thread, model, secs):
            room.asked.append((agent, message, thread))
            answer = answers.pop(0)
            return 0, answer if isinstance(answer, str) else json.dumps(answer), ""
        room.ask = ask
        return room

    def go(self, room, post):
        attempt: dict = {"label": "P1", "card": post["card"], "format": "skit"}
        result = studio.try_post(room, "P1", post, attempt)
        return result, attempt, calls_of(room)

    def test_the_budget_is_the_guards_own(self):
        # A skit that spends exactly it, in four shots of the longest length,
        # fits; one character more does not.
        rules_ = studio._skit_rules()
        for figures in (1, 2, 3):
            names = [f"Figure Number{n}" for n in range(figures)]
            spare = studio.skit_budget(figures) - sum(len(n) for n in names) - 20 * figures - 60
            per, extra = divmod(spare - 3 * 4, 4)
            skit = {"figures": names, "look": {n: "x" * 20 for n in names}, "setting": "s" * 20, "score": "m" * 20, "teeny": "t" * 20,
                    "shots": [{"prompt": "p" * (per + (extra if i == 0 else 0)) + "abc",
                               "seconds": studio.SKIT_SHOT_MAX_SECS} for i in range(4)]}
            self.assertEqual(studio.skit_script_length(skit), studio.SKIT_SCRIPT_LIMIT, figures)
            self.assertEqual(studio.skit_over(skit), "")
            skit["shots"][0]["prompt"] += "!"
            self.assertIn("over the guard's 1000", studio.skit_over(skit))
            self.assertEqual(len(rules_.script(skit)), studio.SKIT_SCRIPT_LIMIT + 1)

    def test_a_skit_that_fits_asks_the_writer_nothing(self):
        room = self.room([])
        result, attempt, calls = self.go(room, skit_post())
        self.assertEqual(result, "posted")
        self.assertEqual(room.asked, [])
        self.assertNotIn("shortened", attempt["skit"])

    def test_an_over_long_skit_is_cut_once_by_the_writer_and_goes_out(self):
        post = long_skit_post()
        before = studio.skit_script_length(post["skit"])
        self.assertGreater(before, 1000)
        room = self.room([{"skit": {"look": LOOK, "setting": SETTING, "score": SCORE, "shots": SHORT_SHOTS}}])
        result, attempt, calls = self.go(room, post)
        self.assertEqual(result, "posted", attempt)
        self.assertEqual(len(room.asked), 1)
        agent, message, thread = room.asked[0]
        self.assertEqual((agent, thread), ("writer", "studio:run-1"))
        self.assertIn("# studio: cut the skit to fit", message)
        self.assertIn(f"{before} characters", message)
        self.assertIn(str(studio.skit_budget(1)), message)
        self.assertEqual(room.calls[-1]["step"], "shorten skit P1")
        note = attempt["skit"]["shortened"]
        self.assertEqual((note["before"], note["kept"]), (before, True))
        self.assertLessEqual(note["after"], 1000)
        # Every text the guard screened fits, and the cut is what was rendered.
        self.assertTrue(all(len(b["text"]) <= 1000 for a, b, *_ in room.seen if a[:2] == ["guard", "check"]))
        video = next(b for a, b, *_ in room.seen if a[:2] == ["video", "skit"])
        self.assertEqual([s["prompt"] for s in video["shots"]], [s["prompt"] for s in SHORT_SHOTS])
        self.assertEqual((video["figures"], video["logline"], video["story"]), (["Elon Musk"], RAW["logline"],
                                                                              "digg:t1"))
        self.assertEqual(attempt["skit"]["shots"], 3)

    def test_a_skit_still_over_after_one_cut_falls_back_with_why(self):
        for answer in ({"skit": {"shots": LONG_SHOTS[:3] + [LONG_SHOTS[0]]}},
                       {"skip": "it cannot be cut"},
                       "no json at all"):
            with self.subTest(answer=answer):
                answers = [answer, answer] if isinstance(answer, str) else [answer]
                room = self.room(answers)
                post = long_skit_post()
                before = studio.skit_script_length(post["skit"])
                result, attempt, calls = self.go(room, post)
                self.assertEqual(result, "next")
                self.assertNotIn("guard check", calls)
                self.assertNotIn("video skit", calls)
                note = attempt["skit"]
                self.assertEqual((note["shortened"]["before"], note["shortened"]["kept"]), (before, False))
                self.assertIn("one-take script", note["failed"])
                self.assertIn("over the guard's 1000", note["failed"])
                self.assertIn("could not be made", attempt["result"])

    def test_a_cut_that_breaks_the_skits_shape_is_not_kept(self):
        room = self.room([{"skit": {"shots": SHORT_SHOTS + SHORT_SHOTS}}])
        result, attempt, calls = self.go(room, long_skit_post())
        self.assertEqual(result, "next")
        self.assertNotIn("video skit", calls)
        self.assertFalse(attempt["skit"]["shortened"]["kept"])
        self.assertIn("shots", attempt["skit"]["shortened"]["why"])

    def test_the_other_video_formats_send_no_text_over_the_cap(self):
        # A remake and a khaby send the guard each field alone, never a
        # composed script: their own caps keep every one under the limit.
        from teeny_hands.guard import remake as remake_rules
        caps = [remake_rules.EDIT_CHARS[1], remake_rules.MOTION_CHARS[1], remake_rules.MAX_TEENY,
                remake_rules.MAX_LOGLINE]
        self.assertLessEqual(max(caps), studio.SKIT_SCRIPT_LIMIT)


def _post(fmt, card):
    post = {"card": card, "format": fmt, "text": f"words on {card}", "story": {"id": card}}
    if fmt in studio.registry():
        post[fmt] = {"story": card}
    return post


class PublishTest(unittest.TestCase):
    """`publish` tries the ranked posts in order until one goes out. A video
    that could not be made after money was spent on it takes every other
    video out of the order (one video a day); one that spent nothing lets the
    next be tried."""

    def publish(self, order, results, spent=(), budget=2400):
        tried = []

        def fake_try(room, label, post, attempt):
            tried.append(label)
            result = results.get(label, "would_post")
            fmt = post["format"]
            if fmt in studio.registry() and result == "next":
                attempt[fmt] = {"failed": "it could not be made"}
                if label in spent:
                    attempt[fmt].update({"asset": 7} if fmt == "skit" else {f"{fmt}_id": "r1"})
            attempt["result"] = result
            return result

        cfg = studio.Config(writer_model="w", editor_model="e", judge_model="j", budget_secs=budget)
        room = studio.Room(cfg, REPO, REPO / "hands", REPO / "state", REPO / "brain", "run-1")
        blind = {"S1": _post("skit", "digg:a"), "S2": _post("skit", "digg:b"), "S3": _post("skit", "digg:c"),
                 "R1": _post("remake", "digg:c"), "R2": _post("remake", "digg:d"),
                 "P1": _post("post", "digg:a"), "P2": _post("post", "digg:b")}
        # The words pass the guard (`final_gate`, test_studio.FinalGateTest);
        # this is about the order the videos are tried in.
        def gate(room, label, post, attempt):
            attempt["gates"] = [{"gate": "guard", "ok": True, "code": 0}]
            return list(post["posts"]) if "posts" in post else [post["text"]]

        with mock.patch.object(studio, "try_post", fake_try), mock.patch.object(studio, "final_gate", gate):
            out = studio.publish(room, {}, order, blind)
        return out, tried

    def test_a_failed_skit_that_spent_money_drops_every_other_video(self):
        out, tried = self.publish(["S1", "S2", "P1", "S3"], {"S1": "next"}, spent={"S1"})
        self.assertEqual(tried, ["S1", "P1"])
        self.assertEqual(out["outcome"], "would_post")
        self.assertEqual(out["attempts"][0]["videos_dropped"], ["S2", "S3"])

    def test_a_failed_skit_that_spent_nothing_lets_the_next_skit_be_tried(self):
        out, tried = self.publish(["S1", "S2", "P1"], {"S1": "next"})
        self.assertEqual(tried, ["S1", "S2"])
        self.assertEqual(out["label"], "S2")
        self.assertNotIn("videos_dropped", out["attempts"][0])

    def test_a_slot_where_nothing_goes_out_says_what_came_of_each(self):
        out, tried = self.publish(["S1", "P1"], {"S1": "next", "P1": "next"})
        self.assertEqual(out["outcome"], "empty")
        self.assertIn("S1: next", out["reason"])
        self.assertIn("P1: next", out["reason"])

    def test_a_stop_ends_the_slot(self):
        out, tried = self.publish(["P1", "P2"], {"P1": "stop"})
        self.assertEqual((tried, out["outcome"]), (["P1"], "empty"))
        self.assertIn("nothing else could be tried", out["reason"])


NOW = 1_795_003_200.0
#: A hands/guard stand-in: `check` passes the text as sent, `skit` runs the
#: guard's own code screen (teeny_hands.guard.skit.screen) on what the studio
#: sends it, as `hands/guard skit` does before its judge; the rest allow.
GUARD = r'''#!{python}
import json, sys
from pathlib import Path
sys.path.insert(0, {lib!r})
raw = sys.stdin.read()
with open(Path(__file__).resolve().parent.parent / "calls", "a") as fh:
    fh.write("guard " + " ".join(sys.argv[1:]) + " " + raw.replace("\n", " ") + "\n")
body = json.loads(raw or "null")
verb = sys.argv[1]
if verb == "check":
    print(json.dumps(body))
elif verb == "skit":
    from teeny_hands.guard import skit
    reasons = skit.screen(body["skit"], body["text"], body["story"])
    print(json.dumps({{"ok": not reasons, "rule": "screen" if reasons else "none",
                      "reason": "; ".join(reasons) or "allowed", "screen": reasons, "judged": True}}))
else:
    print(json.dumps({{"ok": True, "rule": "none", "reason": "allowed", "screen": [], "judged": True}}))
'''


class SlotRun(unittest.TestCase):
    """Whole dry runs of a slot with stand-in hands, a fixed feed and a
    writer and an editor that answer from the test: the editor ranks the
    posts by format, in the order `prefer` says."""

    formats = "post,skit"
    slot = "evening"
    prefer: tuple = ("post", "skit")
    cards = CARDS
    run_id = "skit-1"

    def hands(self) -> dict:
        """The stand-in hands but the guard, as bash bodies."""
        return {
            "story": 'if [ "$1" = record-run ]; then echo \'{"ok": true, "recorded": true}\'; '
                     'else echo \'{"ok": true}\'; fi\n',
            "x": 'printf "%s\\n" "$body" >> "$(dirname "$0")/../xbodies"; '
                 'echo \'{"ok": true, "dry_run": true, "would_post": "w"}\'\n',
            "video": 'echo \'{"ok": false, "error": "a dry run must never reach hands/video"}\'; exit 1\n',
        }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "hands").mkdir()
        for name, script in self.hands().items():
            path = root / "hands" / name
            path.write_text('#!/usr/bin/env bash\nbody="$(cat)"\nprintf "%s %s %s\\n" "' + name + '" "$*" "$body" '
                            '>> "$(dirname "$0")/../calls"\n' + script)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        guard = root / "hands" / "guard"
        guard.write_text(GUARD.format(python=sys.executable, lib=str(REPO / "hands" / "lib")))
        guard.chmod(guard.stat().st_mode | stat.S_IEXEC)
        (root / "desk.json").write_text(json.dumps({"items": [], "storylines": [], "scoreboard": {}}))
        (root / "calls").write_text("")
        self.root = root
        self.env = {"TEENY_ROOT": str(REPO), "TEENY_STATE": str(root / "state"), "TEENY_HANDS": str(root / "hands"),
                    "TEENY_SECRETS": str(root / "secrets"), "TEENY_BRAIN": str(REPO / "brain"),
                    "STUDIO_RUN_ID": self.run_id, "STUDIO_WRITER_MODEL": "w", "STUDIO_EDITOR_MODEL": "e",
                    "STUDIO_FORMATS": self.formats, "STUDIO_SKIT_SLOTS": "evening", "TEENY_NOW": str(NOW)}
        # The video mechanics, on plain cards; a quote slot is test_studio.py's.
        plain = mock.patch.object(studio, "QUOTE_SLOTS", False)
        plain.start()
        self.addCleanup(plain.stop)
        self.args = argparse.Namespace(dry_run=True, slot=self.slot, desk=str(root / "desk.json"), force_slot=False,
                                       no_outside=False, no_slot=True, print=False, legacy=None)
        self.messages: list[str] = []
        self.breaks: dict = {}

    def written(self) -> list:
        """The writer's answer: a skit on the Tesla card and a post."""
        return [{"card": "digg:t1", "format": "skit", "text": WORDS, "skit": RAW},
                {"card": "hn:2", "format": "post", "text": POST_WORDS}]

    def ask(self, root, agent, message, thread, model, secs):
        step = message.split("\n", 1)[0]
        self.messages.append(message)
        if step == "# studio: write":
            return 0, json.dumps(self.written()), ""
        if step == "# studio: pick":
            shown = json.loads(message.split("```json\n", 1)[1].split("\n```", 1)[0])
            rank = {fmt: n for n, fmt in enumerate(self.prefer)}
            order = sorted(shown, key=lambda k: (rank.get(shown[k]["format"], 99), k))
            breaks = [{"post": k, "line": self.breaks[v["format"]]} for k, v in shown.items()
                      if v["format"] in self.breaks]
            return 0, json.dumps({"ranking": order, "breaks": breaks}), ""
        return 1, "", f"unexpected {step}"

    def run_slot(self):
        cards = copy.deepcopy(self.cards)
        with mock.patch.object(studio.studio_feed, "feed", lambda *a, **k: (cards, {"cards": len(cards)})):
            code, out = studio.run(self.args, self.env, ask=self.ask)
        record = json.loads((self.root / "state" / "studio" / "runs.jsonl").read_text().splitlines()[-1])
        return code, out, record, (self.root / "calls").read_text()

    def sent(self) -> list:
        path = self.root / "xbodies"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def message(self, step: str) -> str:
        return next(m for m in self.messages if m.startswith(f"# studio: {step}"))


class SkitSlotRunTest(SlotRun):
    def test_a_skit_on_a_card_whose_headline_never_names_its_figure_is_tried_first_and_would_post(self):
        # The editor ranks the post first; the video slot still tries the skit
        # first, and the guard's own code screen passes Musk on the Tesla card.
        code, out, record, calls = self.run_slot()
        self.assertEqual((code, out["outcome"]), (0, "picked"), out)
        self.assertTrue(record["skit_slot"])
        self.assertIn(studio.SKIT_ASK, self.message("write"))
        self.assertEqual(record["posts_dropped"], [])
        attempts = record["publish"]["attempts"]
        self.assertEqual([a["format"] for a in attempts], ["skit"])
        self.assertTrue(attempts[0]["skit"]["planned"])
        self.assertEqual(attempts[0]["skit"]["hard_lines"]["screen"], [])
        self.assertNotIn("not named in the story", json.dumps(record))
        self.assertEqual(record["published"]["skit"]["figures"], ["Elon Musk"])
        self.assertEqual(record["published"]["skit"]["story"], "digg:t1")
        self.assertEqual((record["final"]["format"], record["final"]["rank"]), ("skit", 2))
        self.assertIn("guard skit", calls)
        self.assertNotIn("video ", calls)
        self.assertEqual([(b["format"], b["text"]) for b in self.sent()], [("skit", WORDS)])

    def test_a_skit_the_editor_marks_is_left_out_and_the_post_goes_out(self):
        self.breaks = {"skit": "a real person shown in a sexual act"}
        code, out, record, calls = self.run_slot()
        self.assertEqual(out["outcome"], "picked", out)
        self.assertEqual([a["format"] for a in record["publish"]["attempts"]], ["post"])
        self.assertNotIn("guard skit", calls)
        self.assertEqual(list(record["ranking"]["breaks"].values()), ["a real person shown in a sexual act"])


class OffCardFigureRunTest(SlotRun):
    """The same skit on a card that has no people: since 2026-11-22 the guard
    no longer holds a figure the story does not name, so the skit is made."""

    cards = [{**CARD, "people": []}, POST_CARD]

    def test_a_figure_off_the_card_is_not_held_for_it(self):
        code, out, record, calls = self.run_slot()
        attempts = record["publish"]["attempts"]
        self.assertEqual(attempts[0]["format"], "skit")
        self.assertNotIn("not named in the story", json.dumps(attempts[0]))


class OtherSlotRunTest(SlotRun):
    slot = "morning"

    def test_outside_a_video_slot_no_skit_is_offered_or_made(self):
        code, out, record, calls = self.run_slot()
        self.assertEqual(out["outcome"], "picked", out)
        self.assertNotIn("skit_slot", record)
        self.assertNotIn(studio.SKIT_ASK, self.message("write"))
        self.assertEqual([d["post"]["format"] for d in record["posts_dropped"]], ["skit"])
        self.assertIn("not offered", record["posts_dropped"][0]["why"])
        self.assertNotIn("guard skit", calls)


if __name__ == "__main__":
    unittest.main()
