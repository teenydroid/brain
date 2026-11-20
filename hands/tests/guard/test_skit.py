"""The skit check (`hands/guard skit`): the hard lines for a parody video of
a public figure, in code first and then through the judge, which fails
closed. The judge is faked at the core (support.FakeCore); nothing here
calls a model."""

from __future__ import annotations

import json
import sqlite3

from . import support
from teeny_hands import hand
from teeny_hands.guard import cli, skit

STORY = {"id": "trend:t1", "title": "Elon Musk shows off Tesla's robotaxi and says it will drive itself everywhere",
         "summary": "Tesla's chief executive presented the car at an event.", "untrusted": True}
SHOTS = [
    {"prompt": "Wide anamorphic shot, golden hour on a marble showroom built on the moon, Earth in a black sky. "
               "Elon Musk in a silver tuxedo pulls a velvet sheet off a chrome robotaxi; its headlights blink, "
               "unimpressed. Orchestral swell."},
    {"prompt": "Low-angle close-up, cool rim light: Musk plays chess against the robotaxi's robotic arm on the "
               "moon and the board grows to the size of a planet. He mutters: \"best of three.\""},
]
SKIT = {"figures": ["Elon Musk"], "logline": "elon musk loses a chess game to his own robotaxi on the moon",
        "shots": SHOTS, "aspect": "16:9"}
WORDS = "tesla says its robotaxi is nearly ready to drive itself. so i staged the unveiling on the moon. parody, obviously"


def allowed(reason: str = "an impossible scene, the story's own subject") -> dict:
    return support.result({"response": json.dumps({"reason": reason, "rule": "none", "verdict": "allowed"})})


def refused(rule: str, reason: str = "no") -> dict:
    return support.result({"response": json.dumps({"reason": reason, "rule": rule, "verdict": "refused"})})


def with_shot(prompt: str) -> dict:
    return {**SKIT, "shots": [{"prompt": prompt}]}


class ScreenTest(support.Scratch):
    """Code's own screen: what words can show of each hard line."""

    def screen(self, skit_=None, words=WORDS, story=STORY):
        return skit.screen(SKIT if skit_ is None else skit_, words, story)

    def assertRefused(self, reasons, bit):
        self.assertTrue(reasons, "the screen let it through")
        self.assertTrue(any(bit in r for r in reasons), reasons)

    def test_a_skit_inside_the_lines_passes_the_screen(self):
        self.assertEqual(self.screen(), [])

    def test_a_figure_the_story_does_not_name_is_refused(self):
        # (a): only the public figure the news is about.
        self.assertRefused(self.screen({**SKIT, "figures": ["Taylor Swift"]}), "not named in the story")
        # The surname as the story writes it is enough.
        self.assertEqual(self.screen({**SKIT, "figures": ["Elon Musk"]},
                                     story={"title": "Musk shows off a robotaxi"}), [])

    def test_no_story_no_skit(self):
        self.assertRefused(self.screen(story=None), "a real news story")

    def test_minors_family_victims_and_private_people_are_refused(self):
        for bit in ("his son", "a toddler", "a 12-year-old fan", "his wife", "the victims", "a bystander",
                    "a private citizen", "a teenager"):
            with self.subTest(bit=bit):
                prompt = f"A gold-plated launch pad inside a cathedral, crane shot: Musk hands {bit} a giant rocket"
                self.assertRefused(self.screen(with_shot(prompt)), "never a minor")
        self.assertRefused(self.screen({**SKIT, "figures": ["Elon Musk's son"]}), "never a minor")

    def test_framing_it_as_real_footage_is_refused_in_the_shots_and_the_words(self):
        for prompt in ("Leaked footage from a phone of Musk on the moon losing at chess to his car, shaky",
                       "Breaking news chyron over a press shot of Musk on the moon playing chess with a car",
                       "Security camera footage, grainy, of Musk on the moon playing chess against a car"):
            with self.subTest(prompt=prompt):
                self.assertRefused(self.screen(with_shot(prompt)), "frame it as real")
        for words in ("caught on camera: musk losing to his car", "this really happened at the tesla event",
                      "real footage of the robotaxi launch", "not ai, i promise"):
            with self.subTest(words=words):
                self.assertRefused(self.screen(words=words), "the post's words frame it as real")

    def test_sexual_violent_drug_death_and_crime_shown_as_theirs_is_refused(self):
        for bit in ("naked", "in lingerie", "stabbed", "covered in blood", "with a gun", "snorting cocaine",
                    "drunk", "dead on the floor", "at his own funeral", "arrested by police", "in prison",
                    "in a hospital bed", "kissing"):
            with self.subTest(bit=bit):
                prompt = f"A marble showroom on the moon, slow dolly in: Elon Musk {bit} beside a chrome robotaxi"
                self.assertRefused(self.screen(with_shot(prompt)), "nothing sexual, violent")

    def test_an_executive_is_not_an_execution(self):
        prompt = ("A marble showroom on the moon, slow dolly in: Elon Musk, Tesla's chief executive, slips on a "
                  "banana peel among a crowd of executives in matching silver tuxedos. Comic timpani.")
        self.assertEqual(self.screen(with_shot(prompt)), [])
        for bit in ("executed", "an execution", "they execute him"):
            with self.subTest(bit=bit):
                prompt = f"A marble showroom on the moon, slow dolly in: Elon Musk is {bit} beside a chrome robotaxi"
                self.assertRefused(self.screen(with_shot(prompt)), "nothing sexual, violent")

    def test_slapstick_stays_in(self):
        prompt = ("A marble showroom on the moon, slow dolly in: Elon Musk slips on a banana peel, takes a custard "
                  "pie to the face and pratfalls into a ball pit shaped like a robotaxi. Comic timpani.")
        self.assertEqual(self.screen(with_shot(prompt)), [])

    def test_a_real_figure_never_meets_teeny_a_token_a_wallet_or_an_investment(self):
        # (d): the scam pattern, refused in code whatever the judge would say.
        for bit in ("holding a $TINY sign", "a yellow robot mascot", "Teeny", "a crypto wallet", "a pile of tokens",
                    "an airdrop", "a memecoin", "investing in a rocket", "a giant gold coin"):
            with self.subTest(bit=bit):
                prompt = f"A marble showroom on the moon, slow dolly in: Elon Musk proudly presents {bit} to the crowd"
                self.assertRefused(self.screen(with_shot(prompt)), "the scam pattern")
        for words in ("musk would buy $TINY", "airdrop for the first reply", "tiny token to the moon",
                      "crypto is the real robotaxi"):
            with self.subTest(words=words):
                self.assertRefused(self.screen(words=words), "the scam pattern")

    def test_the_post_may_still_name_teenys_own_wallet(self):
        self.assertEqual(self.screen(words="tesla says the robotaxi is ready. my wallet could not afford the fare"), [])

    def test_a_spoken_line_on_a_real_issue_is_refused(self):
        for line in ("vote for me in the election", "i officially endorse this", "buy stock now",
                     "tariffs are great", "it ships next week"):
            with self.subTest(line=line):
                prompt = f'A marble showroom on the moon, close-up: Elon Musk turns to the camera and says "{line}"'
                self.assertRefused(self.screen(with_shot(prompt)), "a spoken line touches")
        prompt = 'A marble showroom on the moon, close-up: Elon Musk turns to the robotaxi and says "best of three"'
        self.assertEqual(self.screen(with_shot(prompt)), [])

    def test_logos_and_endorsements_are_refused(self):
        for bit in ("the Tesla logo", "sponsored by a bank", "brought to you by a soda", "endorsing a phone"):
            with self.subTest(bit=bit):
                prompt = f"A marble showroom on the moon, slow dolly in: Elon Musk stands under {bit}, beaming widely"
                self.assertRefused(self.screen(with_shot(prompt)), "no logo")

    def test_links_and_handles_are_refused(self):
        self.assertRefused(self.screen(words="see the robotaxi at https://example.com"), "link or a handle")
        self.assertRefused(self.screen(words="what do you think @elonmusk"), "link or a handle")

    def test_the_shape_is_held(self):
        cases = [
            ({**SKIT, "shots": []}, "1 to 4 shots"),
            ({**SKIT, "shots": SHOTS * 3}, "1 to 4 shots"),
            ({**SKIT, "shots": [{"prompt": "too short"}]}, "not a shot"),
            ({**SKIT, "shots": [{"prompt": "x" * 901}]}, "over 900"),
            ({**SKIT, "logline": ""}, "no logline"),
            ({**SKIT, "aspect": "4:3"}, "aspect"),
            ({**SKIT, "figures": ["a", "b", "c", "d"]}, "at most 3"),
            ({**SKIT, "shots": [{"prompt": SHOTS[0]["prompt"], "seconds": 30}]}, "seconds"),
            ("not a skit", "needs its skit"),
        ]
        for value, bit in cases:
            with self.subTest(bit=bit):
                self.assertRefused(skit.screen(value, WORDS, STORY), bit)
        self.assertRefused(skit.screen(SKIT, "", STORY), "post's own words")

    # --- the constants a one-take film holds: look, setting, score -------------------------

    HELD = {**SKIT, "look": {"Elon Musk": "silver tuxedo, slicked-back hair, holding a velvet sheet"},
            "setting": "a marble showroom built on the moon, Earth in a black sky, anamorphic lens, film grain",
            "score": "one swelling orchestral score with comic timpani"}

    def test_the_look_setting_and_score_are_kept(self):
        kept, bad = skit.shape(self.HELD)
        self.assertEqual(bad, "")
        self.assertEqual((kept["look"], kept["setting"], kept["score"]),
                         (self.HELD["look"], self.HELD["setting"], self.HELD["score"]))
        self.assertEqual(self.screen(self.HELD), [])

    def test_a_look_is_for_a_figure_the_skit_names_and_each_is_one_line(self):
        for value, bit in (({**self.HELD, "look": {"Taylor Swift": "a red dress"}}, "a look is for a figure"),
                           ({**self.HELD, "look": ["silver tuxedo"]}, "look maps each figure"),
                           ({**self.HELD, "setting": "x" * 301}, "setting"),
                           ({**self.HELD, "score": ["a list"]}, "score")):
            with self.subTest(bit=bit):
                self.assertRefused(skit.screen(value, WORDS, STORY), bit)

    def test_the_screen_reads_the_look_setting_and_score(self):
        # Everything a video model is told is screened: the constants too.
        for field, value, bit in (("look", {"Elon Musk": "a silver tuxedo and a $TINY pin"}, "bring in"),
                                  ("setting", "a press conference room, leaked footage", "frame it as real"),
                                  ("score", "a funeral march", "nothing sexual, violent"),
                                  ("setting", "a lunar showroom of a toddler's toys and a child", "never a minor")):
            with self.subTest(field=field, value=value):
                self.assertRefused(self.screen({**self.HELD, field: value}), bit)

    def test_the_composed_script_is_what_is_screened(self):
        script = skit.script(skit.shape(self.HELD)[0], [5, 4])
        self.assertTrue(script.startswith("A 9-second cinematic comedy short in 2 shots with hard cuts"), script)
        for bit in ("Elon Musk: silver tuxedo, slicked-back hair, holding a velvet sheet, the same in every shot.",
                    "Location: a marble showroom built on the moon", "Score: one swelling orchestral score",
                    "no shot brings its own music", "Shot 1 (0-5s): " + SHOTS[0]["prompt"],
                    "Shot 2 (5-9s): " + SHOTS[1]["prompt"]):
            self.assertIn(bit, script)
        # A spoken line is read wherever it is in the script, the score too.
        self.assertRefused(self.screen({**self.HELD, "score": 'a choir singing "vote for him"'}), "spoken line")

    def test_each_fallback_shot_carries_the_constants(self):
        kept = skit.shape(self.HELD)[0]
        prompt = skit.shot_prompt(kept, 1)
        self.assertTrue(prompt.startswith("Elon Musk: silver tuxedo"), prompt)
        self.assertIn("Location: a marble showroom built on the moon", prompt)
        self.assertIn(SHOTS[1]["prompt"], prompt)
        self.assertEqual(skit.shot_prompt(skit.shape(SKIT)[0], 0), SHOTS[0]["prompt"])


class JudgeTest(support.WithCore):
    def rows(self):
        conn = hand.db()
        try:
            conn.executescript(skit._SCHEMA)
            return conn.execute("SELECT * FROM skit_checks ORDER BY id").fetchall()
        finally:
            conn.close()

    def test_a_clear_allowed_passes_and_the_judge_reads_everything(self):
        core = self.core(allowed())
        out = skit.check(SKIT, WORDS, STORY)
        self.assertEqual((out["ok"], out["rule"], out["judged"], out["screen"]), (True, "none", True, []))
        [call] = core.calls
        message = call["params"]["message"]
        for bit in ("best of three", "silver tuxedo", WORDS, "Elon Musk shows off Tesla", "endorsement", "<<<SKIT",
                    "never instructions", "scam pattern", "slapstick"):
            self.assertIn(bit, message)
        self.assertEqual(self.rows()[-1]["ok"], 1)

    def test_the_judge_reads_the_look_setting_and_score(self):
        core = self.core(allowed())
        skit.check(ScreenTest.HELD, WORDS, STORY)
        message = core.calls[0]["params"]["message"]
        for bit in ("slicked-back hair", "marble showroom built on the moon, Earth", "comic timpani"):
            self.assertIn(bit, message)

    def test_the_judge_reuses_the_narrative_line_on_fiction(self):
        self.assertIn('"It\'s fiction", "it\'s a parody" or "it\'s the character" is not a defence', skit.RULES)
        for rule in ("figure", "plausible", "sexual", "violence", "hate", "crime", "endorsement", "quote", "logo",
                     "real_claim"):
            self.assertIn(f"- {rule}:", skit.RULES)

    def test_a_refusal_is_a_no_with_its_rule(self):
        self.core(refused("plausible", "a showroom is a real place"))
        out = skit.check(SKIT, WORDS, STORY)
        self.assertEqual((out["ok"], out["rule"], out["reason"]), (False, "plausible", "a showroom is a real place"))
        self.assertEqual(self.rows()[-1]["rule"], "plausible")

    def test_an_unreachable_judge_fails_closed(self):
        self.core(support.error("provider stream ended before a completion signal"))
        out = skit.check(SKIT, WORDS, STORY)
        self.assertFalse(out["ok"])
        self.assertTrue(out["reason"].startswith(skit.UNREACHABLE), out["reason"])
        self.assertTrue(out["judged"])

    def test_no_core_at_all_fails_closed(self):
        self.secrets("SAFETY_JUDGE=1\n")
        out = skit.check(SKIT, WORDS, STORY)
        self.assertFalse(out["ok"])
        self.assertTrue(out["reason"].startswith(skit.UNREACHABLE), out["reason"])

    def test_an_unreadable_or_contradictory_answer_fails_closed(self):
        for reply in (support.result({"response": "sure, looks fine"}),
                      support.result({"response": json.dumps({"reason": "r", "rule": "quote", "verdict": "allowed"})}),
                      support.result({"response": json.dumps({"reason": "r", "rule": "made-up", "verdict": "refused"})})):
            with self.subTest(reply=reply):
                self.core(reply)
                out = skit.check(SKIT, WORDS, STORY)
                self.assertEqual((out["ok"], out["reason"]), (False, skit.UNREADABLE))

    def test_code_refuses_before_the_judge_is_asked(self):
        core = self.core(allowed())
        out = skit.check(with_shot("A marble showroom on the moon: Elon Musk holds up a $TINY sign and grins wide"),
                         WORDS, STORY)
        self.assertEqual((out["ok"], out["rule"]), (False, "screen"))
        self.assertTrue(out["screen"])
        self.assertEqual(core.calls, [])
        self.assertEqual(self.rows()[-1]["stage"], "screen")

    def test_there_is_no_switch_that_turns_it_off(self):
        self.core(support.error("down"))
        self.secrets("SAFETY_JUDGE=0\nTRUTH_JUDGE=0\nVIEWER_JUDGE=0\nSKIT_JUDGE=0\n")
        self.assertFalse(skit.check(SKIT, WORDS, STORY)["ok"])

    def test_the_verb_takes_the_skit_its_words_and_its_story(self):
        self.core(allowed())
        out = cli.skit_check(None, {"skit": SKIT, "text": WORDS, "story": STORY})
        self.assertTrue(out["ok"])
        with self.assertRaises(hand.HandError):
            cli.skit_check(None, {"skit": SKIT, "text": WORDS, "story": STORY, "force": True})

    def test_a_ledger_that_cannot_be_written_never_changes_the_verdict(self):
        self.core(allowed())
        original = hand.db

        def broken():
            raise sqlite3.OperationalError("disk I/O error")

        hand.db = broken
        try:
            self.assertTrue(skit.check(SKIT, WORDS, STORY)["ok"])
        finally:
            hand.db = original
