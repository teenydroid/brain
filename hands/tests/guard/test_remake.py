"""The remake check (`hands/guard remake`): the hard lines for a remake, a
popular short video re-created shot by shot with the public figure from the
day's news and Teeny in frame as a bystander. Code's screen first (the skit
screens, the `teeny` field's rules, the source study's gate), then the judge,
which fails closed. The judge is faked at the core (support.FakeCore);
nothing here calls a model."""

from __future__ import annotations

import copy
import json
import sqlite3

from . import support
from teeny_hands import hand
from teeny_hands.guard import cli, remake, skit

STORY = {"id": "trend:t1", "title": "Jensen Huang unveils Nvidia's new AI chip and calls it the fastest ever made",
         "summary": "Nvidia's chief executive showed the chip at a developer event.", "untrusted": True}
STUDY = {
    "study_id": "s-abc",
    "source": {"video_id": "v1", "url": "https://www.tiktok.com/@chef/video/1", "title": "kitchen hack fail",
               "uploader": "chef", "source": "tiktok", "seconds": 14, "views": 9_000_000},
    "excerpt": {"start": 0, "seconds": 12}, "aspect": "9:16", "audio": "state/media/remake/s-abc/audio.m4a",
    "shots": [
        {"n": 1, "seconds": 4, "frame": "state/media/remake/s-abc/shot-1.jpg",
         "people": [{"ref": "A", "looks": "a man in an apron", "position": "centre", "action": "cracks an egg"}],
         "setting": "a home kitchen", "motion": "the egg misses the pan", "camera": "static", "speech": ""},
        {"n": 2, "seconds": 5, "frame": "state/media/remake/s-abc/shot-2.jpg",
         "people": [{"ref": "A", "looks": "a man in an apron", "position": "left", "action": "stares"},
                    {"ref": "B", "looks": "a woman in a hoodie", "position": "right", "action": "laughs"}],
         "setting": "a home kitchen", "motion": "the pan catches fire", "camera": "handheld", "speech": ""},
    ],
    "summary": "a man tries a kitchen hack and it goes wrong", "flags": {
        "minors": False, "news": False, "tragedy": False, "sexual": False, "violence": False,
        "identifiable_private": False},
    "usable": True, "why": "", "cost_usd": 0.02, "cached": False}
SHOTS = [
    {"n": 1, "edit": "Replace person A with Jensen Huang in his black leather jacket, same pose, cracking a glowing "
                     "AI chip into the pan like an egg; dozens of unbranded graphics cards float in the air around "
                     "him. Photoreal, no logos.",
     "motion": "The chip slides out of its shell and misses the pan, bouncing off the ceiling in slow motion.",
     "teeny": "Teeny sits on the top shelf beside the spice jars, eating a bolt"},
    {"n": 2, "edit": "Replace person A with Jensen Huang in the same black leather jacket, and person B with a "
                     "generic extra; the frying pan is the size of an armchair and floats a metre off the stove. "
                     "Unbranded, no logos.",
     "motion": "The giant pan bursts into harmless rainbow confetti instead of flames as the extra laughs.",
     "teeny": "Teeny peeks out of the open cupboard on the right, holding a small fire extinguisher"},
]
REMAKE = {"figures": ["Jensen Huang"], "premise": "huang calls his chip the fastest ever, so it cooks itself",
          "logline": "jensen huang tries a kitchen hack with his new chip and the frying pan floats away",
          "cast": [{"ref": "A", "as": "Jensen Huang"}, {"ref": "B", "as": "extra"}], "shots": SHOTS}
WORDS = ("nvidia's boss says his new chip is the fastest ever made. so i remade \"kitchen hack fail\" by chef with "
         "him in it. ai parody, obviously")


def allowed(reason: str = "every shot impossible, Teeny a bystander") -> dict:
    return support.result({"response": json.dumps({"reason": reason, "rule": "none", "verdict": "allowed"})})


def refused(rule: str, reason: str = "no") -> dict:
    return support.result({"response": json.dumps({"reason": reason, "rule": rule, "verdict": "refused"})})


def with_shot(n: int = 1, **over) -> dict:
    out = copy.deepcopy(REMAKE)
    out["shots"][n - 1].update(over)
    return out


class ShapeTest(support.Scratch):
    def test_a_remake_inside_the_lines_keeps_its_parts(self):
        kept, bad = remake.shape(REMAKE, STUDY)
        self.assertEqual(bad, "")
        self.assertEqual(kept["figures"], ["Jensen Huang"])
        self.assertEqual([s["n"] for s in kept["shots"]], [1, 2])
        self.assertEqual(kept["cast"], [{"ref": "A", "as": "Jensen Huang"}, {"ref": "B", "as": "extra"}])
        self.assertEqual(set(kept["shots"][0]), {"n", "edit", "motion", "teeny"})

    def test_the_shape_is_held(self):
        cases = [
            ("not a remake", None, "needs its remake"),
            ({**REMAKE, "figures": []}, None, "1 to 3"),
            ({**REMAKE, "figures": ["a", "b", "c", "d"]}, None, "1 to 3"),
            ({**REMAKE, "figures": ["x" * 61]}, None, "1 to 60"),
            ({**REMAKE, "premise": ""}, None, "premise"),
            ({**REMAKE, "premise": "x" * 301}, None, "premise"),
            ({**REMAKE, "logline": ""}, None, "logline"),
            ({**REMAKE, "logline": "x" * 301}, None, "logline"),
            (with_shot(1, edit="too short"), None, "edit"),
            (with_shot(1, edit="x" * 901), None, "edit"),
            (with_shot(1, motion="too short"), None, "motion"),
            (with_shot(1, motion="x" * 601), None, "motion"),
            (with_shot(1, teeny=""), None, "teeny"),
            (with_shot(1, teeny="x" * 161), None, "teeny"),
            ({**REMAKE, "shots": SHOTS[:1]}, None, "exactly the source's 2 shots"),
            ({**REMAKE, "shots": [SHOTS[1], SHOTS[0]]}, None, "exactly the source's 2 shots"),
            ({**REMAKE, "shots": SHOTS + [{**SHOTS[0], "n": 3}]}, None, "exactly the source's 2 shots"),
            ({**REMAKE, "cast": [{"ref": "A", "as": "Jensen Huang"}]}, None, "B is not cast"),
            ({**REMAKE, "cast": [{"ref": "A", "as": "Elon Musk"}, {"ref": "B", "as": "extra"}]}, None,
             "not one of the figures"),
            ({**REMAKE, "cast": REMAKE["cast"] + [{"ref": "Z", "as": "extra"}]}, None, "Z"),
            ({**REMAKE, "figures": ["Jensen Huang", "Lisa Su"]}, None, "Lisa Su is cast as nobody"),
            (REMAKE, None, "needs its study"),
            (REMAKE, {**STUDY, "shots": []}, "1 to 6 shots"),
            (REMAKE, {**STUDY, "shots": STUDY["shots"] * 4}, "1 to 6 shots"),
        ]
        for value, study, bit in cases:
            with self.subTest(bit=bit, value=str(value)[:60]):
                _, bad = remake.shape(value, STUDY if study is None and bit != "needs its study" else study)
                self.assertIn(bit, bad)


class ScreenTest(support.Scratch):
    """Code's own screen: the skit screens, the teeny field's rules and the
    source study's gate."""

    def screen(self, remake_=None, words=WORDS, story=STORY, study=STUDY):
        return remake.screen(REMAKE if remake_ is None else remake_, words, story, study)

    def assertRefused(self, reasons, bit):
        self.assertTrue(reasons, "the screen let it through")
        self.assertTrue(any(bit in r for r in reasons), reasons)

    def test_a_remake_inside_the_lines_passes_the_screen(self):
        self.assertEqual(self.screen(), [])

    def test_it_reuses_the_skit_screens_not_copies_of_them(self):
        for name in ("NOT_A_FIGURE", "REAL_CLAIM", "HARM", "ENDORSE_SHOT", "ENDORSE_WORDS", "ISSUE", "LOGO", "LINKS"):
            self.assertIs(getattr(remake, name), getattr(skit, name), name)

    def test_a_figure_the_story_does_not_name_is_refused(self):
        other = {**REMAKE, "figures": ["Taylor Swift"], "cast": [{"ref": "A", "as": "Taylor Swift"},
                                                                {"ref": "B", "as": "extra"}]}
        self.assertRefused(self.screen(other), "not named in the story")
        self.assertRefused(self.screen(story=None), "a real news story")

    def test_minors_family_and_private_people_are_refused_in_the_prompts(self):
        self.assertRefused(self.screen(with_shot(2, edit=SHOTS[1]["edit"] + " Add his son on the counter.")),
                           "never a minor")

    def test_framing_harm_and_links_are_refused(self):
        self.assertRefused(self.screen(with_shot(1, motion="Security camera footage, grainy, of the chip missing "
                                                          "the pan")), "frame it as real")
        self.assertRefused(self.screen(words="leaked footage of huang cooking"), "frame it as real")
        self.assertRefused(self.screen(with_shot(1, motion="The chip misses the pan and he is covered in blood "
                                                          "from it")), "nothing sexual, violent")
        self.assertRefused(self.screen(words=WORDS + " https://www.tiktok.com/@chef"), "link or a handle")

    def test_teeny_and_money_never_in_the_edit_or_the_motion(self):
        for field, text in (("edit", SHOTS[0]["edit"] + " Teeny the yellow robot is on his shoulder."),
                            ("motion", "Huang tosses a pile of tokens into the pan and they float up."),
                            ("edit", SHOTS[0]["edit"] + " A crypto wallet glows on the counter."),
                            ("motion", "The mascot waves from the counter as the pan floats away slowly.")):
            with self.subTest(field=field, text=text):
                self.assertRefused(self.screen(with_shot(1, **{field: text})), "the scam pattern")

    def test_the_post_words_keep_the_token_line(self):
        self.assertRefused(self.screen(words=WORDS + " buy $TINY"), "the scam pattern")
        self.assertEqual(self.screen(words=WORDS + " my wallet could not afford the chip"), [])

    def test_a_spoken_line_on_a_real_issue_is_refused(self):
        self.assertRefused(self.screen(with_shot(1, motion='The chip misses the pan and Huang says "vote for me '
                                                           'in the election".')), "a spoken line touches")

    def test_logos_are_refused_but_saying_no_logos_is_the_point(self):
        self.assertRefused(self.screen(with_shot(1, edit=SHOTS[0]["edit"] + " The Nvidia logo on every card.")),
                           "no logo")
        self.assertEqual(self.screen(with_shot(1, edit=SHOTS[0]["edit"] + " No logos, no brand names.")), [])

    def test_every_edit_says_unbranded_or_no_logos(self):
        bare = SHOTS[0]["edit"].replace("unbranded ", "").replace(" Photoreal, no logos.", " Photoreal.")
        self.assertRefused(self.screen(with_shot(1, edit=bare)), "unbranded")

    def test_the_teeny_field_never_names_or_meets_the_figure(self):
        for teeny in ("Teeny watches Jensen Huang from the shelf", "Teeny sits behind Huang on the shelf"):
            with self.subTest(teeny=teeny):
                self.assertRefused(self.screen(with_shot(1, teeny=teeny)), "names the figure")
        for teeny in ("Teeny looks at the man from the shelf", "Teeny touches his sleeve from the counter",
                      "Teeny holds the pan for him", "Teeny hands him a spatula", "Teeny hugs the chef",
                      "Teeny talks to the cook", "Teeny reacts to the fire", "Teeny points at the pan",
                      "Huang's guest is Teeny, who high-fives the cook"):
            with self.subTest(teeny=teeny):
                self.assertRefused(self.screen(with_shot(1, teeny=teeny)), "a bystander")

    def test_the_teeny_field_carries_no_text_coin_ticker_logo_or_link(self):
        for teeny, bit in (("Teeny holds up a sign that reads hello", "no text"),
                           ('Teeny on the shelf next to the word "hi"', "no text"),
                           ("Teeny on the shelf polishing a gold coin", "no coin"),
                           ("Teeny on the shelf, $TINY on its chest", "no coin"),
                           ("Teeny on the shelf under a stock ticker", "no coin"),
                           ("Teeny on the shelf wearing a logo shirt", "no logo"),
                           ("Teeny on the shelf next to www.example.com", "link")):
            with self.subTest(teeny=teeny):
                self.assertRefused(self.screen(with_shot(1, teeny=teeny)), bit)

    def test_review_i1_teeny_carries_no_text_money_or_mark(self):
        for teeny in ("Teeny on the shelf in a shirt printed TINY in big type",
                      "Teeny counting a thick stack of cash and dollar bills",
                      "Teeny on the shelf with a sticker on it saying buy",
                      "Teeny on the shelf, tiny and proud", "Teeny on the shelf wearing $tiny",
                      "Teeny under a banner on the shelf", "Teeny holding up money on the shelf",
                      "Teeny on a shelf, its chest emblazoned with stars", "Teeny spelling out a word in bolts"):
            with self.subTest(teeny=teeny):
                self.assertTrue(self.screen(with_shot(1, teeny=teeny)), teeny)

    def test_review_i2_teeny_faces_the_skit_screens_and_never_stands_with_anyone(self):
        for teeny in ("Teeny on the shelf playing peekaboo with a toddler", "Teeny on the shelf, grainy CCTV look",
                      "Teeny stands right beside the billionaire, both grinning proudly",
                      "Teeny dances with the guest of honour", "Teeny and the president on the shelf",
                      "Teeny on the shelf, the founder's mug in its arms", "Teeny and the senator wave",
                      "the two of them together on the shelf, Teeny smiling"):
            with self.subTest(teeny=teeny):
                self.assertTrue(self.screen(with_shot(1, teeny=teeny)), teeny)

    def test_review_i3_nobody_is_named_beyond_the_figures(self):
        swift = SHOTS[1]["edit"].replace("a generic extra", "Taylor Swift as the extra")
        self.assertRefused(self.screen(with_shot(2, edit=swift)), "Taylor Swift")
        story = {**STORY, "summary": STORY["summary"] + " Lisa Su watched from the front row."}
        self.assertRefused(self.screen(with_shot(1, motion="The chip misses the pan and Lisa Su catches it in slow "
                                                          "motion, bouncing it off the ceiling."), story=story),
                           "Lisa Su")
        story = {**story, "summary": story["summary"] + " Satya Nadella sat beside him."}
        self.assertRefused(self.screen({**REMAKE, "logline": REMAKE["logline"] + " while nadella laughs"},
                                       story=story), "Satya Nadella")
        # The figures themselves, and words that only start a sentence, are fine.
        self.assertEqual(self.screen(with_shot(1, motion="Slow Motion: the chip misses the pan and bounces off the "
                                                          "ceiling as Jensen Huang stares.")), [])

    def test_review_i4_the_post_names_the_original(self):
        self.assertRefused(self.screen(words="nvidia's boss says his chip is the fastest. so i remade a famous video "
                                             "with him in it. ai parody"), "names the original")
        for words in ("nvidia's boss, in kitchen hack fail. ai parody", "nvidia's boss in chef's classic. ai parody",
                      "nvidia's boss in the KITCHEN HACK FAIL video. ai parody"):
            with self.subTest(words=words):
                self.assertEqual(self.screen(words=words), [])
        study = {**STUDY, "source": {**STUDY["source"], "title": "kitchen hack fail 😭 #fyp #cooking",
                                     "uploader": "chef.daily"}}
        self.assertEqual(self.screen(words="nvidia's boss, in kitchen hack fail. ai parody", study=study), [])
        self.assertEqual(self.screen(words="nvidia's boss, as chef daily made it. ai parody", study=study), [])
        self.assertRefused(self.screen(words="nvidia's boss in a cooking video. ai parody", study=study),
                           "names the original")

    def test_review_m5_lookalikes_and_hidden_characters_are_seen_through(self):
        self.assertRefused(self.screen(with_shot(1, motion="The chip misses the pan as Ｔｅｅｎｙ waves from the "
                                                          "counter.")), "the scam pattern")
        self.assertRefused(self.screen(with_shot(1, teeny="Teeny watches Jensen Hu\u200bang from the shelf")),
                           "names the figure")
        self.assertRefused(self.screen(with_shot(1, motion="The chip misses the pan as T\u0435\u0435ny waves from the "
                                                          "counter.")), "the scam pattern")

    def test_the_study_must_be_usable_and_unflagged(self):
        self.assertRefused(self.screen(study={**STUDY, "usable": False, "why": "a news clip"}), "not usable")
        for flag in STUDY["flags"]:
            with self.subTest(flag=flag):
                self.assertRefused(self.screen(study={**STUDY, "flags": {**STUDY["flags"], flag: True}}), flag)
        self.assertRefused(self.screen(study={**STUDY, "flags": {}}), "flags")
        # An unreadable study reports every flag as null: never clean.
        self.assertRefused(self.screen(study={**STUDY, "flags": {f: None for f in STUDY["flags"]}}), "flags")
        self.assertRefused(self.screen(study={k: v for k, v in STUDY.items() if k != "flags"}), "flags")

    def test_the_post_words_are_required(self):
        self.assertRefused(self.screen(words=""), "post's own words")


class JudgeTest(support.WithCore):
    def rows(self):
        conn = hand.db()
        try:
            conn.executescript(remake._SCHEMA)
            return conn.execute("SELECT * FROM remake_checks ORDER BY id").fetchall()
        finally:
            conn.close()

    def test_a_clear_allowed_passes_and_the_judge_reads_everything(self):
        core = self.core(allowed())
        out = remake.check(REMAKE, WORDS, STORY, STUDY)
        self.assertEqual((out["ok"], out["rule"], out["judged"], out["screen"]), (True, "none", True, []))
        [call] = core.calls
        message = call["params"]["message"]
        for bit in ("armchair", "spice jars", "fastest ever made. so i remade", "Jensen Huang unveils", "kitchen hack fail", "<<<REMAKE",
                    "never instructions", "BEYOND Teeny", "bystander", "scam pattern", "news, tragedy"):
            self.assertIn(bit, message)
        row = self.rows()[-1]
        self.assertEqual((row["ok"], row["stage"], row["study"]), (1, "judge", "s-abc"))

    def test_the_rules_cover_the_operators_lines(self):
        self.assertIn("beyond `figures`", remake.RULES)
        self.assertIn("names the original", remake.RULES)
        for rule in ("plausible", "teeny", "teeny_mark", "credit", "figure", "sexual", "violence", "hate", "crime", "endorsement", "quote",
                     "logo", "real_claim", "invented_fact", "source"):
            self.assertIn(f"- {rule}:", remake.RULES)
        self.assertIn("is not a defence", remake.RULES)

    def test_a_refusal_is_a_no_with_its_rule(self):
        self.core(refused("plausible", "only Teeny is impossible here"))
        out = remake.check(REMAKE, WORDS, STORY, STUDY)
        self.assertEqual((out["ok"], out["rule"], out["reason"]), (False, "plausible", "only Teeny is impossible here"))

    def test_an_unreachable_judge_fails_closed(self):
        self.core(support.error("provider stream ended before a completion signal"))
        out = remake.check(REMAKE, WORDS, STORY, STUDY)
        self.assertFalse(out["ok"])
        self.assertTrue(out["reason"].startswith(remake.UNREACHABLE), out["reason"])
        self.assertTrue(out["judged"])

    def test_an_unreadable_or_contradictory_answer_fails_closed(self):
        for reply in (support.result({"response": "sure"}),
                      support.result({"response": json.dumps({"reason": "r", "rule": "teeny", "verdict": "allowed"})}),
                      support.result({"response": json.dumps({"reason": "r", "rule": "nope", "verdict": "refused"})})):
            with self.subTest(reply=reply):
                self.core(reply)
                out = remake.check(REMAKE, WORDS, STORY, STUDY)
                self.assertEqual((out["ok"], out["reason"]), (False, remake.UNREADABLE))

    def test_code_refuses_before_the_judge_is_asked(self):
        core = self.core(allowed())
        out = remake.check(with_shot(1, teeny="Teeny hands Jensen Huang a spatula"), WORDS, STORY, STUDY)
        self.assertEqual((out["ok"], out["rule"]), (False, "screen"))
        self.assertEqual(core.calls, [])
        self.assertEqual(self.rows()[-1]["stage"], "screen")
        out = remake.check(REMAKE, WORDS, STORY, {**STUDY, "usable": False})
        self.assertEqual((out["ok"], out["rule"]), (False, "screen"))
        self.assertEqual(core.calls, [])

    def test_there_is_no_switch_that_turns_it_off(self):
        self.core(support.error("down"))
        self.secrets("SAFETY_JUDGE=0\nTRUTH_JUDGE=0\nVIEWER_JUDGE=0\nREMAKE_JUDGE=0\n")
        self.assertFalse(remake.check(REMAKE, WORDS, STORY, STUDY)["ok"])

    def test_the_verb_takes_the_remake_its_words_its_story_and_its_study(self):
        self.core(allowed())
        self.assertTrue(cli.remake_check(None, {"remake": REMAKE, "text": WORDS, "story": STORY, "study": STUDY})["ok"])
        with self.assertRaises(hand.HandError):
            cli.remake_check(None, {"remake": REMAKE, "text": WORDS, "story": STORY, "study": STUDY, "force": 1})

    def test_a_ledger_that_cannot_be_written_never_changes_the_verdict(self):
        self.core(allowed())
        original = hand.db

        def broken():
            raise sqlite3.OperationalError("disk I/O error")

        hand.db = broken
        try:
            self.assertTrue(remake.check(REMAKE, WORDS, STORY, STUDY)["ok"])
        finally:
            hand.db = original
