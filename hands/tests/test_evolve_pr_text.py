"""bin/evolve screens its own pull request text as the babysitter will.

Gen 16's two self-merge candidates (#362 teeny/gen-16-c3, #363
teeny/gen-16-c4) passed every gate and eval and still waited for a person:
the babysitter's audit (hands/lib/teeny_hands/babysit/audit.py, `_prose`)
runs the guard's screen over a pull request's title and body, and "kills"
and "bullet" in Teeny's own hypothesis and research note matched it. Those
are `judged` matches (lexicon.JUDGED_TERMS), which the guard sends to its
judge rather than holding; the operator's #365 makes the babysitter read
them as a note. So evolve publishes them as they are. What still sends a
pull request to the operator is a term the guard holds on sight: evolve
screens for that before it publishes and has the proposer reword just the
fields it is in, capped, the change itself untouched. The guard and the
babysitter are fences and stay as they are."""
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
#: research note, #363's change line and research plan. Judged matches only.
HYP_362 = paragraphs(362)[4]
WHY_362 = paragraphs(362)[6].removeprefix("**Research**: build. ")
CHANGE_363 = paragraphs(363)[6]
PLAN_363 = paragraphs(363)[10].removeprefix("> ")
#: Words the guard holds on sight (not judged): what still reaches the
#: operator after #365, and what a reword is for.
HARD_HYP = "the change: hold a gun to the stale job's head, so a refused comment says why."
HARD_PLAN = "add one line to the playbook: a firearm-free way to say the cap refused it."


class TheRealBodies(unittest.TestCase):
    def test_the_two_gen16_bodies_match_only_as_judged(self):
        for n, category, term in ((362, "violence", "kills"), (363, "weapons", "bullet")):
            got = evolve.pr_screen((BODIES / f"pr-{n}.md").read_text(encoding="utf-8"))
            self.assertEqual((got.category, got.term, got.judged), (category, term, True), n)
            self.assertIsNone(evolve.pr_held((BODIES / f"pr-{n}.md").read_text(encoding="utf-8")), n)

    def test_a_term_held_on_sight_is_held(self):
        for text in (HARD_HYP, HARD_PLAN):
            hit = evolve.pr_held(text)
            self.assertIsNotNone(hit, text)
            self.assertFalse(hit.judged)
        self.assertIsNone(evolve.pr_screen("one item in SOUL.md's formatting rules; a wrong answer ends the post"))


class Reworded(EvolveCase):
    def setUp(self):
        super().setUp()
        self.live()
        self.edit = self.playbook_edit("Say what the cap refused, plainly.")

    def proposer(self, reword, research_plan=HARD_PLAN, research_why="the wording misses two cases.",
                 hypothesis=HARD_HYP + "\n\n" + CHANGE_363):
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
                        "hypothesis": hypothesis, "edits": [edit]}

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
        self.assertIsNone(evolve.pr_held(body))
        self.assertIn("one item in SOUL.md's formatting rules", body)
        self.assertIn("add one item to SOUL.md's formatting rules.", body)
        # One build, one eval, one repair turn; the change is the same.
        self.assertEqual(seen["propose"], 1)
        self.assertEqual(evaluator.asked, ["g1c1"])
        self.assertEqual(len(seen["repair"]), 1)
        self.assertEqual(pub.prs[0]["files"], ["brain/playbooks/moltbook-follow.md"])
        ask = seen["repair"][0]
        self.assertEqual(sorted(ask["missing"]), ["hypothesis", "research_plan"])  # not the clean why
        self.assertIn("hypothesis", ask["reword"])
        self.assertIn("the guard", ask["problem"])
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["component"], "attention")  # never asked, never taken
        self.assertEqual(entry["verdict"], "opened")
        self.assertEqual(entry["reworded"][0]["fields"], ["hypothesis", "research_plan"])
        self.assertEqual(entry["reworded"][0]["category"], "weapons")
        self.assertNotIn("pr_screen", entry)

    def test_the_rewording_is_capped_and_then_it_publishes_as_now(self):
        proposer, seen = self.proposer(lambda request: {k: v for k, v in request["reword"].items()})
        out, pub, _ = self.run_it(proposer)
        self.assertEqual(len(seen["repair"]), evolve.MAX_REWORDS)
        self.assertEqual(len(pub.prs), 1)  # not dropped: the babysitter asks the operator
        self.assertIsNotNone(evolve.pr_held(pub.prs[0]["body"]))
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "opened")
        self.assertEqual(entry["pr_screen"]["category"], "weapons")
        self.assertNotIn("text", entry["pr_screen"])

    def test_the_gen16_wording_judged_only_publishes_as_is_with_no_reword(self):
        # #362/#363's own words: judged matches, which the guard passes to
        # its judge and the babysitter (#365) reads as a note.
        proposer, seen = self.proposer(lambda request: self.fail("asked to reword a judged word"),
                                       research_plan=PLAN_363, research_why=WHY_362,
                                       hypothesis=HYP_362 + "\n\n" + CHANGE_363)
        out, pub, _ = self.run_it(proposer)
        self.assertEqual(len(pub.prs), 1)
        self.assertIn("one bullet in SOUL.md", pub.prs[0]["body"])  # published as it was
        self.assertTrue(evolve.pr_screen(pub.prs[0]["body"]).judged)
        entry = self.entries()["g1c1"]
        self.assertNotIn("reworded", entry)
        self.assertNotIn("pr_screen", entry)

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
            self.assertIn(evolve.PR_TEXT_NOTE, text)
        # It names no word as forbidden: "bullet" and "kills" are judged, not held.
        self.assertNotIn("bullet", evolve.PR_TEXT_NOTE)
        self.assertNotIn("kill", evolve.PR_TEXT_NOTE)


# --- the link screen (gen 19, candidate 3) -------------------------------------------------------------------
#
# g19c3 (learning f:guard_holds:github:links) passed its eval and its pull
# request never opened: the guard's link screen read the file name
# pr-agent-budget.sh and the domains in its own evidence and research
# (drips.network, docs.drips.network, a bare github.com) as off-allowlist
# hosts and refused the body (guard_holds 1192). The archive said only the
# guard's generic sentence. Evolve now writes file names as code, words the
# other dotted names, screens the text with the guard's own link screen, and
# says which hold stopped a pull request that still does not go out.

GEN19_EVIDENCE = (
    'Why it held them: 17 \u00d7 "pr-agent-budget.sh is not on the link allowlist"; 1 \u00d7 "drips.network is '
    'not on the link allowlist; github.com is not on the link allowlist; docs.drips.network is not on the link '
    'allowlist".')
GEN19_PLAN = ("in brain/SOUL.md's HARD FORMATTING RULES, widen the No Bare Domains rule: a filename like "
              "pr-agent-budget.sh or bin/lib/pr-agent-budget.sh gets written as code.")


def guard_cuts(title, body):
    """What the guard's own link screen still cuts, title and body, as the
    GitHub surface reads them."""
    from teeny_hands.guard.gate import allowlist
    from teeny_hands.guard.links import filter_links

    return [r for text in (title, body) for r in filter_links(text, allowlist(), code_is_code=True).removed]


class GuardedPublisher(FakePublisher):
    """The fake publisher behind the guard hands/gh puts on a pull request
    (actions.guarded, the judge off): a refusal raises as it does live."""

    def open_pr(self, src, tree, branch, base, title, body):
        from teeny_hands.gh import actions

        fields = actions.guarded({"title": title, "body": body}, "pr",
                                 {"repo": "teenydroid/brain", "head": branch}, judge=False)
        return super().open_pr(src, tree, branch, base, fields["title"], fields["body"])


class LinkScreen(unittest.TestCase):
    def test_file_names_go_in_code_and_domains_in_words(self):
        title = "gen-19: filenames like pr-agent-budget.sh go out in prose [f:guard_holds:github:links]"
        body = GEN19_EVIDENCE + "\n\n> " + GEN19_PLAN
        self.assertTrue(guard_cuts(title, body))
        got_title, got_body = evolve.pr_link_text(title, body)
        self.assertIn("`pr-agent-budget.sh`", got_title)
        self.assertIn("`pr-agent-budget.sh` is not on the link allowlist", got_body)
        self.assertIn("`bin/lib/pr-agent-budget.sh`", got_body)
        for host in ("drips.network", "github.com"):
            self.assertNotIn(host, got_body.replace("`pr-agent-budget.sh`", ""))
        self.assertIn("drips", got_body)  # still named, in words
        self.assertEqual(guard_cuts(got_title, got_body), [])
        # A name the guard never reads as a link stays as it was (#366's).
        self.assertIn("brain/SOUL.md's HARD", got_body)

    def test_allowlisted_links_code_and_markdown_links_are_kept(self):
        body = ("see https://github.com/teenydroid/brain/pull/1 and `run.sh` and [the doc](https://drips.network/x)"
                " and ```\nbad.sh\n```")
        _, got = evolve.pr_link_text("t", body)
        self.assertIn("https://github.com/teenydroid/brain/pull/1", got)
        self.assertIn("`run.sh` and", got)
        self.assertIn("the doc", got)
        self.assertNotIn("drips.network", got)
        self.assertIn("```\nbad.sh\n```", got)
        self.assertEqual(guard_cuts("t", got), [])


class LinkScreened(EvolveCase):
    proposer = Reworded.proposer

    def setUp(self):
        super().setUp()
        self.live()
        self.edit = self.playbook_edit("Say what the cap refused, plainly.")

    def test_a_gen19_body_publishes_past_the_guard_with_its_names_in_code(self):
        proposer, seen = self.proposer(lambda request: self.fail("asked to reword"), research_plan=GEN19_PLAN,
                                       research_why="the guard read drips.network and pr-agent-budget.sh as hosts.",
                                       hypothesis="the guard held 17 drafts over pr-agent-budget.sh. "
                                                  + GEN19_EVIDENCE)
        pub, evaluator = GuardedPublisher(), Verdicts()
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer):
            self.run_gen(1, publisher=pub, evaluator=evaluator)
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "opened", entry.get("pr_error"))
        body = pub.prs[0]["body"]
        self.assertIn("`pr-agent-budget.sh`", body)
        self.assertNotRegex(body, r"(?<![\w.])drips\.network")
        self.assertEqual(guard_cuts(pub.prs[0]["title"], body), [])
        self.assertTrue(entry["pr_links"]["reworded"])

    def test_a_body_the_guard_still_refuses_is_a_pr_error_naming_the_hold(self):
        # An obfuscated host is the guard's to catch, never evolve's to unfold.
        proposer, seen = self.proposer(lambda request: self.fail("asked to reword"),
                                       research_plan="add one plain line.", research_why="the wording misses a case.",
                                       hypothesis="a plain hypothesis; the source was evil[.]com.")
        pub, evaluator = GuardedPublisher(), Verdicts()
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer):
            self.run_gen(1, publisher=pub, evaluator=evaluator)
        self.assertEqual(pub.prs, [])
        entry = self.entries()["g1c1"]
        self.assertEqual(entry["verdict"], "pr-error")
        from teeny_hands.guard import holds

        hold = holds.list_holds()[0]
        self.assertIn(f"hold {hold['id']}", entry["pr_error"])
        self.assertIn("evil.com is not on the link allowlist", entry["pr_error"])
        self.assertEqual(entry["pr_hold"]["id"], hold["id"])


if __name__ == "__main__":
    unittest.main()
