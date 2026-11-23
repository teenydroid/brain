"""The story film check (`hands/guard film`): the skit's hard lines on every
beat, Teeny's narration and the sound cues screened, each beat's clip prompt
under the task cap, then the judge once on the whole film, failing closed.
The judge is faked at the core (support.FakeCore); nothing calls a model."""

from __future__ import annotations

import json

from . import support
from teeny_hands import hand
from teeny_hands.guard import cli, film, skit

STORY = {"id": "trend:ka8y52dv", "title": "Gary Marcus says 'the jig is almost up' over Musk's reported AI "
         "cash-crunch warning", "summary": "Musk warned AI could need hundreds of billions more next year.",
         "untrusted": True}
FIGURE_ACTION = ("Wide anamorphic shot of a cathedral-sized empty gold vault: Elon Musk shakes a bus-sized gold "
                 "piggy bank over a tin cup and it coughs out one rusty bolt with a long brass fart.")
TEENY_ACTION = ("Macro shot: Teeny, the small yellow robot, sits on a toilet-shaped throne in the same vault, "
                "slowly lowers its newspaper and stares into the lens, deadpan.")
FILM = {
    "figures": ["Elon Musk"], "logline": "elon musk begs a gold vault for hundreds of billions and gets a bolt",
    "look": {"Elon Musk": "black leather jacket, a tin cup"}, "setting": "a cathedral-sized empty gold vault, "
    "one bare bulb, anamorphic wide", "score": "a pompous tuba march that slowly deflates", "aspect": "9:16",
    "text": "musk says ai needs hundreds of billions more next year. my parody: he asks the vault. 75 seconds",
    "beats": [
        {"subject": "figure", "action": FIGURE_ACTION, "narration": "this man needs hundreds of billions. today "
         "he asks the vault nicely.", "sfx": ["a long brass fart"]},
        {"subject": "figure", "action": FIGURE_ACTION.replace("one rusty bolt", "a moth"),
         "narration": "the vault answers with a moth."},
        {"subject": "teeny", "action": TEENY_ACTION, "narration": "i run on ten dollars a day. ask me how."},
        {"subject": "figure", "action": FIGURE_ACTION.replace("shakes", "kicks"), "hold": 3},
    ],
}


def allowed() -> dict:
    return support.result({"response": json.dumps({"reason": "absurd", "rule": "none", "verdict": "allowed"})})


def with_beat(index: int, **changes) -> dict:
    beats = [dict(b) for b in FILM["beats"]]
    beats[index].update(changes)
    return {**FILM, "beats": beats}


class ShapeTest(support.Scratch):
    def test_a_film_inside_the_lines_keeps_its_beats_in_order(self):
        kept, bad = film.shape(FILM)
        self.assertEqual(bad, "")
        self.assertEqual([b["id"] for b in kept["beats"]], ["b1", "b2", "b3", "b4"])
        self.assertEqual(kept["beats"][3]["hold"], 3)
        self.assertEqual(kept["look"], {"Elon Musk": "black leather jacket, a tin cup"})

    def test_a_beat_with_the_figure_and_teeny_together_is_refused(self):
        _, bad = film.shape(with_beat(0, subject="both"))
        self.assertIn("never meets Teeny", bad)

    def test_the_beat_count_the_subjects_and_the_narration_have_limits(self):
        for raw, bit in (({**FILM, "beats": FILM["beats"][:2]}, "3 to 8 beats"),
                         (with_beat(0, subject="crowd"), "subject is one of"),
                         (with_beat(0, narration="x" * 401), "over 400"),
                         (with_beat(0, sfx=["a"] * 4), "sound effects"),
                         (with_beat(3, hold=30), "hold"),
                         (with_beat(0, action="too short"), "rich shot"),
                         ({**FILM, "figures": [], "look": None}, "names none"),
                         ({**FILM, "beats": [dict(b, narration="") for b in FILM["beats"]]}, "no narration")):
            with self.subTest(bit=bit):
                self.assertIn(bit, film.shape(raw)[1])

    def test_the_whole_narration_fits_one_speech_call(self):
        long = {**FILM, "beats": [dict(FILM["beats"][0], narration="y" * 390) for _ in range(7)]}
        self.assertIn("2500", film.shape(long)[1])


class ScreenTest(support.Scratch):
    def assertRefused(self, reasons, bit):
        self.assertTrue(reasons, "the screen let it through")
        self.assertTrue(any(bit in r for r in reasons), reasons)

    def test_a_film_inside_the_lines_passes_the_screen(self):
        self.assertEqual(film.screen(FILM, STORY), [])

    def test_every_figure_beat_gets_the_skits_own_screen(self):
        reasons = film.screen(with_beat(1, action=FIGURE_ACTION + " He holds up a $TINY sign."), STORY)
        self.assertRefused(reasons, "b2: the shots bring in")

    def test_a_teeny_beat_may_name_teeny_but_never_a_figure_or_money(self):
        self.assertRefused(film.screen(with_beat(2, action=TEENY_ACTION + " Elon Musk waves at it."), STORY),
                           "names Elon Musk")
        self.assertRefused(film.screen(with_beat(2, action=TEENY_ACTION + " It counts a pile of coins."), STORY),
                           "brings in 'coins'")
        self.assertRefused(film.screen(with_beat(2, action=TEENY_ACTION + " It is naked."), STORY), "naked")

    def test_the_narration_is_screened_like_the_posts_words(self):
        for line, bit in (("leaked footage of the vault", "leaked footage"),
                          ("buy $TINY before he does", "$TINY"),
                          ("follow @teenydroid", "@teenydroid"),
                          ("his daughter holds the cup", "daughter")):
            with self.subTest(bit=bit):
                self.assertRefused(film.screen(with_beat(1, narration=line), STORY), "the narration says")

    def test_a_sound_cue_is_screened_like_a_shot(self):
        self.assertRefused(film.screen(with_beat(0, sfx=["a crypto airdrop jingle"]), STORY), "sound effects")
        self.assertEqual(film.screen(with_beat(0, sfx=["a wet fart", "a tuba that dies"]), STORY), [])

    def test_every_clip_prompt_fits_the_task_cap_on_its_own(self):
        long = with_beat(0, action=FIGURE_ACTION + " " + "Gold dust swirls in slow motion. " * 18)
        self.assertRefused(film.screen(long, STORY), "over the 1000 a clip may send")
        # The whole film is far over the cap together, and that is fine.
        kept, _ = film.shape(FILM)
        total = sum(len(film.beat_prompt(kept, i, 15)) for i in range(4))
        self.assertGreater(total, film.task_cap())
        self.assertEqual(film.screen(FILM, STORY), [])

    def test_a_film_needs_its_story_and_its_words(self):
        self.assertRefused(film.screen(FILM, None), "news story")
        self.assertRefused(film.screen({**FILM, "text": " "}, STORY), "post's own words")


class PromptTest(support.Scratch):
    def test_a_beat_prompt_asks_for_no_music_and_ends_on_the_locks(self):
        kept, _ = film.shape(FILM)
        prompt = film.beat_prompt(kept, 0, 10)
        self.assertTrue(prompt.startswith("A 10-second shot"))
        self.assertIn("Elon Musk: black leather jacket, a tin cup", prompt)
        self.assertIn("No music", prompt)
        self.assertNotIn("tuba march", prompt)
        self.assertTrue(prompt.endswith(skit.LOCKS))

    def test_a_teeny_beat_prompt_carries_the_set_but_no_figures_look(self):
        kept, _ = film.shape(FILM)
        prompt = film.beat_prompt(kept, 2, 5)
        self.assertIn("cathedral-sized empty gold vault", prompt)
        self.assertNotIn("leather jacket", prompt)


class JudgeTest(support.WithCore):
    def test_the_judge_is_asked_once_with_every_beat_and_the_narration(self):
        core = self.core(allowed())
        out = film.check(FILM, STORY)
        self.assertEqual((out["ok"], out["rule"], out["judged"]), (True, "none", True))
        [call] = core.calls
        message = call["params"]["message"]
        for bit in ("story film", "spoken off screen by Teeny", "[teeny] Macro shot", "[figure] Wide anamorphic",
                    "a long brass fart", "the vault answers with a moth", FILM["text"], "scam pattern"):
            self.assertIn(bit, message)

    def test_code_refuses_before_the_judge_is_asked(self):
        core = self.core(allowed())
        out = film.check(with_beat(1, narration="buy $TINY now"), STORY)
        self.assertEqual((out["ok"], out["rule"]), (False, "screen"))
        self.assertEqual(core.calls, [])

    def test_an_unreachable_judge_fails_closed(self):
        self.core(support.error("down"))
        out = film.check(FILM, STORY)
        self.assertFalse(out["ok"])
        self.assertTrue(out["reason"].startswith(skit.UNREACHABLE), out["reason"])

    def test_every_check_is_a_row_marked_as_a_film(self):
        self.core(allowed())
        film.check(FILM, STORY)
        conn = hand.db()
        try:
            row = conn.execute("SELECT * FROM skit_checks ORDER BY id DESC").fetchone()
        finally:
            conn.close()
        self.assertTrue(row["logline"].startswith("[film] "))
        self.assertEqual(row["ok"], 1)

    def test_the_verb_takes_the_film_and_its_story(self):
        self.core(allowed())
        self.assertTrue(cli.film_check(None, {"film": FILM, "story": STORY})["ok"])
        with self.assertRaises(hand.HandError):
            cli.film_check(None, {"film": FILM, "story": STORY, "force": True})
