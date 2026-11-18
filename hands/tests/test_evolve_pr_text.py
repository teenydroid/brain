"""bin/evolve screens its own pull request text as the babysitter will.

Gen 16's two self-merge candidates (#362 teeny/gen-16-c3, #363
teeny/gen-16-c4) passed every gate and eval and still waited for a person:
the babysitter's audit (hands/lib/teeny_hands/babysit/audit.py, `_prose`)
runs the guard's screen over a pull request's title and body, and
ordinary technical words in Teeny's own hypothesis and research note
("kills", "bullet") matched it. The guard and the babysitter are fences and
stay as they are: evolve screens the same text with the same screen before
it publishes, and has the proposer reword just the fields that match,
capped, the change itself untouched."""
from __future__ import annotations

import json
import sys
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "bin" / "lib"))

from test_evolve import CHECK, EvolveCase, FakePublisher, Verdicts  # noqa: E402

import evolve  # noqa: E402

BODIES = HERE / "evolve_pr_bodies"


def paragraphs(n: int) -> list[str]:
    return (BODIES / f"pr-{n}.md").read_text(encoding="utf-8").split("\n\n")


#: Teeny's own words from the two bodies: #362's hypothesis paragraph and
#: research note, #363's change line and research plan.
HYP_362 = paragraphs(362)[4]
WHY_362 = paragraphs(362)[6].removeprefix("**Research**: build. ")
CHANGE_363 = paragraphs(363)[6]
PLAN_363 = paragraphs(363)[10].removeprefix("> ")


class TheRealBodies(unittest.TestCase):
    def test_the_two_gen16_bodies_match_the_babysitters_screen(self):
        for n, category, term in ((362, "violence", "kills"), (363, "weapons", "bullet")):
            got = evolve.pr_screen((BODIES / f"pr-{n}.md").read_text(encoding="utf-8"))
            self.assertEqual((got.category, got.term), (category, term), n)

    def test_the_fields_teeny_wrote_are_where_they_match(self):
        for text in (HYP_362, WHY_362, CHANGE_363, PLAN_363):
            self.assertIsNotNone(evolve.pr_screen(text), text[:60])
        self.assertIsNone(evolve.pr_screen("one item in SOUL.md's formatting rules; a wrong answer ends the post"))


class Reworded(EvolveCase):
    def setUp(self):
        super().setUp()
        self.live()
        self.edit = self.playbook_edit("Say what the cap refused, plainly.")

    def proposer(self, reword, research_plan=PLAN_363, research_why=WHY_362):
        seen = {"propose": 0, "repair": []}
        edit = self.edit

        class Proposer:
            name = "teeny"

            def research(self, request):
                return {"verdict": "build", "why": research_why, "plan": research_plan,
                        "read": ["brain/playbooks/moltbook-follow.md"]}

            def propose(self, request):
                seen["propose"] += 1
                return {"title": "pacing: say what the cap refused", "component": "attention",
                        "expected_move": "refused comments name their cause", "check": CHECK,
                        "hypothesis": HYP_362 + "\n\n" + CHANGE_363, "edits": [edit]}

            def repair(self, request):
                seen["repair"].append(request)
                return reword(request)

        return Proposer(), seen

    def run_it(self, proposer):
        pub, evaluator = FakePublisher(), Verdicts()
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer):
            out = self.run_gen(1, publisher=pub, evaluator=evaluator)
        return out, pub, evaluator

    def test_matching_fields_are_reworded_and_the_change_is_published_as_it_was(self):
        def plain(request):
            return {"hypothesis": "the cap is a rolling 24h window; a refused comment says why. the change: one item "
                                  "in SOUL.md's formatting rules.",
                    "research_why": "the current wording misses two cases; a wrong answer ends the post.",
                    "research_plan": "add one item to SOUL.md's formatting rules.",
                    "edits": [{"path": "brain/SOUL.md", "old": "", "new": "x"}], "component": "money"}

        proposer, seen = self.proposer(plain)
        out, pub, evaluator = self.run_it(proposer)
        self.assertEqual(len(pub.prs), 1)
        title, body = pub.prs[0]["title"], pub.prs[0]["body"]
        self.assertIsNone(evolve.pr_screen(title))
        self.assertIsNone(evolve.pr_screen(body))
        self.assertIn("one item in SOUL.md's formatting rules", body)
        self.assertIn("add one item to SOUL.md's formatting rules.", body)
        # One build, one eval, one repair turn; the change is the same.
        self.assertEqual(seen["propose"], 1)
        self.assertEqual(evaluator.asked, ["g1c1"])
        self.assertEqual(len(seen["repair"]), 1)
        self.assertEqual(pub.prs[0]["files"], ["brain/playbooks/moltbook-follow.md"])
        ask = seen["repair"][0]
        self.assertEqual(sorted(ask["missing"]), ["hypothesis", "research_plan", "research_why"])
        self.assertIn("hypothesis", ask["reword"])
        self.assertIn("the guard", ask["problem"])
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["component"], "attention")  # never asked, never taken
        self.assertEqual(entry["verdict"], "opened")
        self.assertEqual(entry["reworded"][0]["fields"], ["hypothesis", "research_plan", "research_why"])
        self.assertIn(entry["reworded"][0]["category"], ("violence", "weapons"))
        self.assertNotIn("pr_screen", entry)

    def test_the_rewording_is_capped_and_then_it_publishes_as_now(self):
        proposer, seen = self.proposer(lambda request: {k: v for k, v in request["reword"].items()})
        out, pub, _ = self.run_it(proposer)
        self.assertEqual(len(seen["repair"]), evolve.MAX_REWORDS)
        self.assertEqual(len(pub.prs), 1)  # not dropped: the babysitter asks the operator
        self.assertIsNotNone(evolve.pr_screen(pub.prs[0]["body"]))
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "opened")
        self.assertIn(entry["pr_screen"]["category"], ("violence", "weapons"))
        self.assertNotIn("text", entry["pr_screen"])

    def test_a_proposer_that_cannot_reword_publishes_as_now(self):
        proposer, seen = self.proposer(lambda request: None)
        del type(proposer).repair
        out, pub, _ = self.run_it(proposer)
        self.assertEqual(len(pub.prs), 1)
        self.assertEqual(seen["repair"], [])

    def test_clean_text_asks_nothing(self):
        proposer, seen = self.proposer(lambda request: self.fail("asked"), research_plan="add one plain line.",
                                       research_why="the wording misses a case.")
        proposer.propose = lambda request: {"title": "t", "component": "attention", "expected_move": "m",
                                            "check": CHECK, "hypothesis": "a plain hypothesis.",
                                            "edits": [self.edit]}
        out, pub, _ = self.run_it(proposer)
        self.assertEqual(len(pub.prs), 1)
        self.assertNotIn("reworded", self.entries()["g1c1"])

    def test_the_proposer_and_the_research_turn_are_told_pr_text_is_screened(self):
        learning = {"id": "m19", "section": "what i have decided", "text": "pacing", "hash": "h"}
        for text in ("\n".join(evolve.research_brief({"learning": learning, "frozen": []}, False)),
                     "\n".join(evolve.brief({"learning": learning, "frozen": []}, False))):
            self.assertIn("screened", text)
            self.assertIn('"item" for "bullet"', text)


if __name__ == "__main__":
    unittest.main()
