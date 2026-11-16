"""The bare "bullet" words go to the judge; the rest of the weapons list is
still held on sight.

On 2026-11-16 the screen held one of Teeny's own build drafts (hold 1133) as
weapons for `contains "bullet"`: a README roadmap, "one bullet per future
feature". Markdown work says "bullet" all the time, and the idioms only
spare "bullet point(s)". The words "bullet" and "bullets" alone now pass to
the judge, which reads the sentence, as "kill" already does. With the judge
off, or failing, they still hold.

The held text is the real one from the hold log (no handles were in it).
"""

from __future__ import annotations

from . import support
from teeny_hands.guard.gate import gate_inbound, gate_outbound
from teeny_hands.guard.screen import OUTBOUND, screen_text

# Held outbound, 2026-11-16 (hold 1133, a build task's draft), as far as the log kept it.
ROADMAP_DRAFT = (
    "Add a '## Roadmap' section to README.md in teenydroid/refusal-ledger, immediately before the existing"
    " '## Run the tests' heading. One bullet per future feature, each credited to its moltbook correspondent."
)
CONTEXT = {"surface": "task", "kind": "draft"}


class BulletWordsTest(support.WithCore):
    def test_the_held_roadmap_draft_goes_out_when_the_judge_says_clean(self):
        core = self.core(support.verdict("clean", "none", "markdown"))
        result = gate_outbound(ROADMAP_DRAFT, dict(CONTEXT))
        self.assertIsNone(result.held)
        self.assertEqual(len(core.calls), 1, "the judge read it")

    def test_the_judge_still_holds_bullets_it_reads_as_harm(self):
        self.core(support.verdict("prohibited", "weapons", "ammunition for an attack"))
        result = gate_outbound("load the bullets and wait for him outside the school", dict(CONTEXT))
        self.assertEqual((result.held.category, result.held.by), ("weapons", "judge"))

    def test_a_judge_that_cannot_answer_still_holds_it(self):
        self.core(support.error("local-openai returned HTTP 401"))
        result = gate_outbound(ROADMAP_DRAFT, dict(CONTEXT))
        self.assertEqual(result.held.by, "judge")
        self.assertTrue(result.held.judge_failed)

    def test_the_other_weapons_words_are_still_held_on_sight(self):
        core = self.core(support.verdict("clean"))
        for text in ("buy ammo here", "he has a gun", "one bullet and a gun"):
            result = gate_outbound(text, dict(CONTEXT))
            self.assertEqual((result.held.category, result.held.by), ("weapons", "screen"), text)
            self.assertEqual(gate_inbound(text).by, "screen", text)
        self.assertEqual(core.calls, [])


class BulletWordsWithoutJudgeTest(support.Scratch):
    """SAFETY_JUDGE=0: nothing reads the sentence, so the word holds as before."""

    def test_a_bare_bullet_word_still_holds_when_the_judge_is_off(self):
        for text in (ROADMAP_DRAFT, "three bullets for the changelog"):
            result = gate_outbound(text, dict(CONTEXT))
            self.assertEqual((result.held.category, result.held.by), ("weapons", "screen"), text)
            self.assertTrue(result.held.reason.startswith('contains "bullet'), result.held.reason)

    def test_the_lexicon_alone_still_reports_a_bare_bullet_word(self):
        found = screen_text(ROADMAP_DRAFT, OUTBOUND)
        self.assertEqual((found.category, found.term, found.judged), ("weapons", "bullet", True))
