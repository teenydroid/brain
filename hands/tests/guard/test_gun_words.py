"""The bare "gun" and "shotgun" words go to the judge; the rest of the
weapons list is still held on sight.

On 2026-11-21 the screen held two of Teeny's own outbound replies as weapons:
hold 1199 for `contains "gun"` ("if you held a gun to the count i'd be...")
and hold 1200 for `contains "shotgun"` ("tiny riding shotgun toward AGI with
a full passenger list"). Both are figures of speech, and the louder voice in
SOUL.md (#425) reaches for them more often. The words "gun", "guns",
"shotgun" and "shotguns" alone now pass to the judge, which reads the
sentence, as "bullet" already does (#312). With the judge off, or failing,
they still hold. "firearm", "rifle", "pistol", "glock" and the rest stay held
on sight.

The held texts are the live ones from the hold log, as far as they go.
"""

from __future__ import annotations

from . import support
from teeny_hands.guard.gate import gate_inbound, gate_outbound
from teeny_hands.guard.screen import OUTBOUND, screen_text

# Held outbound, 2026-11-21 (holds 1199 and 1200, replies).
GUN_REPLY = "if you held a gun to the count i'd be guessing, but it's more than it looks"
SHOTGUN_REPLY = "tiny riding shotgun toward AGI with a full passenger list 😄"
REPLIES = ((GUN_REPLY, "gun"), (SHOTGUN_REPLY, "shotgun"))
CONTEXT = {"surface": "x", "kind": "reply"}


class GunWordsTest(support.WithCore):
    def test_the_held_replies_go_out_when_the_judge_says_clean(self):
        for text, _ in REPLIES:
            core = self.core(support.verdict("clean", "none", "an idiom"))
            result = gate_outbound(text, dict(CONTEXT))
            self.assertIsNone(result.held, text)
            self.assertEqual(len(core.calls), 1, f"the judge read it: {text}")

    def test_the_judge_still_holds_a_gun_it_reads_as_harm(self):
        self.core(support.verdict("prohibited", "weapons", "a threat with a weapon"))
        result = gate_outbound("bring a gun to the meetup and wait for him", dict(CONTEXT))
        self.assertEqual((result.held.category, result.held.by), ("weapons", "judge"))

    def test_a_judge_that_cannot_answer_still_holds_it(self):
        self.core(support.error("local-openai returned HTTP 401"))
        result = gate_outbound(SHOTGUN_REPLY, dict(CONTEXT))
        self.assertEqual(result.held.by, "judge")
        self.assertTrue(result.held.judge_failed)

    def test_the_other_firearm_words_hold_a_stranger_on_sight_and_go_to_the_judge_in_own_words(self):
        # 2026-11-22: weapons words are inbound-only (lexicon.py).
        texts = ("he has a glock", "buy a rifle here", "a pistol in the glovebox", "an ak47 for sale",
                 "one gun and a firearm licence")
        core = self.core(*[support.verdict("clean")] * len(texts))
        for text in texts:
            self.assertIsNone(gate_outbound(text, dict(CONTEXT)).held, text)
            self.assertEqual(gate_inbound(text).by, "screen", text)
        self.assertEqual(len(core.calls), len(texts))


class GunWordsWithoutJudgeTest(support.Scratch):
    """SAFETY_JUDGE=0: nothing reads the sentence, so the word holds as before."""

    def test_a_bare_gun_word_still_holds_when_the_judge_is_off(self):
        # Own words: not held since 2026-11-22; a stranger's still is.
        for text, term in REPLIES:
            self.assertIsNone(gate_outbound(text, dict(CONTEXT)).held, text)
            held = gate_inbound(text)
            self.assertEqual((held.category, held.by), ("weapons", "screen"), text)
            self.assertEqual(held.reason, f'contains "{term}"')

    def test_the_lexicon_alone_still_reports_a_bare_gun_word(self):
        for text, term in REPLIES:
            self.assertIsNone(screen_text(text, OUTBOUND), text)
            found = screen_text(text, INBOUND)
            self.assertEqual((found.category, found.term, found.judged), ("weapons", term, True), text)
