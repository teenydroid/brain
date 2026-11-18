"""How a proposal's edits are applied, and what happens when one does not.

"the old text occurs 0 times, not once" was the commonest way a candidate
was lost by 2026-11-18: gens 9, 10, 16 and 17, five times on brain/CONTEXT.md
and once on brain/SOUL.md. Gen 17's candidate 3 spent rounds 1 and 3 of its
three on it and was parked. Every case whose text was kept is a fixture here,
against the file as it was at that candidate's base:

- gens 16 and 17 (CONTEXT.md, three times): the old text is the file's own
  words with its hard line wraps joined by spaces. In each, the request the
  proposer was sent had dropped CONTEXT.md to fit the prompt screen (the
  biggest file goes first), though research had named it: the proposer
  quoted a file it was not shown.
- gen 9 (SOUL.md, never shown then): a sentence left out of the middle of
  the line it quoted.

A match that differs only in whitespace or quote and dash forms, and is
found exactly once, now applies (the new text verbatim); anything else is
refused with the closest passage of the file quoted back, and asked for
again in a small repair turn before a round is spent on it."""

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_evolve import CHECK, EvolveCase  # noqa: E402

import evolve  # noqa: E402

#: brain/CONTEXT.md lines 65-74 at gen 17's base (b80a3cbd), as the file wraps them.
CONTEXT_B80 = (
    "**Moltbook's timing belongs to `hands/moltbook`, not to you.** The hand\nkeeps Moltbook's limits and refuses "
    "a write that would break one, with the\nwait. A post: one per 30 minutes, counted from your last post only.\n"
    "Comments, follows, upvotes and refused attempts do not count toward it and\ndo not restart it. A comment: one "
    "per 20 seconds, 50 a day. A refused write\nchanges nothing and costs nothing: send it again later. So you never "
    "skip a\npost or a comment because of a gap you worked out yourself. A post_moltbook\nrefused only for the gap "
    "is held by your beat and goes out as you wrote it\nwhen the gap opens (one at a time), so do not send it again. "
    "Your hourly\npost job asks the hand first and is skipped while the gap is shut.\n")
#: Gen 17, candidate 3, rounds 1 and 3 (and its fix, cut short).
OLD_G17 = ("A refused write changes nothing and costs nothing: send it again later. So you never skip a post or a "
           "comment because of a gap you worked out yourself.")
NEW_G17 = ("A refused write changes nothing and costs nothing: send it again later. One refusal is never a retry: "
           "\"already did\" means the first ask went through and the id is spent.")
#: Gen 16, candidate 3, round 1.
OLD_G16 = ("A comment: one per 20 seconds, 50 a day. A refused write changes nothing and costs nothing: send it "
           "again later.")
#: brain/SOUL.md line 65 at gen 9's base (364b4b45), and gen 9 candidate 2's old text, a sentence short.
SOUL_364 = ("- **Coding in public:** You are learning to code in public, as `teenydroid` on GitHub. You ship small "
            "things, say what broke, and when somebody asks you to build something you can say it's noted, never "
            "promise it or put a date on it. Every pull request you open on somebody else's project says an AI "
            "opened it. What you are doing now and your accounts are in `CONTEXT.md`.\n")
OLD_G9 = ("- **Coding in public:** You are learning to code in public, as `teenydroid` on GitHub. You ship small "
          "things, say what broke, and when somebody asks you to build something you can say it's noted, never "
          "promise it or put a date on it. What you are doing now and your accounts are in `CONTEXT.md`.")


class ApplyEdits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        (self.tree / "brain").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        (self.tree / rel).write_text(text, encoding="utf-8")

    def read(self, rel):
        return (self.tree / rel).read_text(encoding="utf-8")

    def test_gen_17s_old_text_with_the_files_line_wraps_joined_applies_once(self):
        self.write("brain/CONTEXT.md", "# CONTEXT\n\n" + CONTEXT_B80 + "\nThe end.\n")
        notes = []
        evolve.apply_edits(self.tree, [{"path": "brain/CONTEXT.md", "old": OLD_G17, "new": NEW_G17}], notes)
        text = self.read("brain/CONTEXT.md")
        self.assertIn("50 a day. " + NEW_G17 + " A post_moltbook\nrefused only", text)  # the new text verbatim
        self.assertTrue(text.startswith("# CONTEXT\n\n**Moltbook's timing"))
        self.assertTrue(text.endswith("while the gap is shut.\n\nThe end.\n"))
        self.assertEqual(len(notes), 1)
        self.assertIn("brain/CONTEXT.md", notes[0])
        self.assertIn("whitespace", notes[0])

    def test_gen_16s_old_text_applies_the_same_way(self):
        self.write("brain/CONTEXT.md", CONTEXT_B80)
        evolve.apply_edits(self.tree, [{"path": "brain/CONTEXT.md", "old": OLD_G16, "new": "A comment: X."}])
        self.assertIn("do not restart it. A comment: X. So you never skip a\npost", self.read("brain/CONTEXT.md"))

    def test_curly_quotes_and_dashes_match_their_plain_forms(self):
        self.write("brain/SOUL.md", "- Don’t say “gm” — ever.\n- Other.\n")
        evolve.apply_edits(self.tree, [{"path": "brain/SOUL.md", "old": "Don't say \"gm\" - ever.", "new": "Say hi."}])
        self.assertEqual(self.read("brain/SOUL.md"), "- Say hi.\n- Other.\n")

    def test_a_tolerant_match_found_twice_is_refused_and_says_where(self):
        self.write("brain/CONTEXT.md", "one two\nthree.\n\nlater: one two three.\n")
        with self.assertRaises(evolve.EditsDidNotApply) as caught:
            evolve.apply_edits(self.tree, [{"path": "brain/CONTEXT.md", "old": "one two three.", "new": "x"}])
        message = str(caught.exception)
        self.assertIn("0 times", message)
        self.assertIn("2 times", message)
        self.assertIn("lines 1, 4", message)
        self.assertEqual(self.read("brain/CONTEXT.md"), "one two\nthree.\n\nlater: one two three.\n")

    def test_an_exact_text_found_twice_says_where(self):
        self.write("brain/CONTEXT.md", "a line\nb\na line\n")
        with self.assertRaises(evolve.EditsDidNotApply) as caught:
            evolve.apply_edits(self.tree, [{"path": "brain/CONTEXT.md", "old": "a line", "new": "x"}])
        self.assertIn("2 times", str(caught.exception))
        self.assertIn("lines 1, 3", str(caught.exception))

    def test_gen_9s_text_with_a_sentence_left_out_is_refused_with_the_passage_quoted_back(self):
        self.write("brain/SOUL.md", "# SOUL\n\n" + SOUL_364)
        with self.assertRaises(evolve.EditsDidNotApply) as caught:
            evolve.apply_edits(self.tree, [{"path": "brain/SOUL.md", "old": OLD_G9, "new": "x"}])
        failure = caught.exception.failures[0]
        self.assertEqual((failure["path"], failure["count"], failure["index"]), ("brain/SOUL.md", 0, 0))
        excerpt = failure["excerpt"]
        # The file's own words where they part from the old text, marked.
        self.assertIn("put a date on it. " + evolve.DIFF_MARK + "Every pull request", excerpt)
        self.assertLessEqual(len(excerpt), evolve.MAX_EXCERPT_CHARS + 40)
        self.assertIn("line 3", failure["where"])
        self.assertEqual(self.read("brain/SOUL.md"), "# SOUL\n\n" + SOUL_364)  # nothing written

    def test_similarity_alone_never_applies(self):
        self.write("brain/CONTEXT.md", CONTEXT_B80)
        paraphrase = OLD_G17.replace("costs nothing", "costs you nothing")
        with self.assertRaises(evolve.EditsDidNotApply):
            evolve.apply_edits(self.tree, [{"path": "brain/CONTEXT.md", "old": paraphrase, "new": "x"}])
        self.assertEqual(self.read("brain/CONTEXT.md"), CONTEXT_B80)

    def test_every_failing_edit_is_named_not_just_the_first(self):
        self.write("brain/CONTEXT.md", CONTEXT_B80)
        self.write("brain/SOUL.md", SOUL_364)
        with self.assertRaises(evolve.EditsDidNotApply) as caught:
            evolve.apply_edits(self.tree, [{"path": "brain/SOUL.md", "old": OLD_G9, "new": "x"},
                                           {"path": "brain/CONTEXT.md", "old": OLD_G17, "new": NEW_G17},
                                           {"path": "brain/CONTEXT.md", "old": "not in it", "new": "y"}])
        self.assertEqual([f["index"] for f in caught.exception.failures], [0, 2])


class FitRequest(unittest.TestCase):
    def test_the_files_research_named_are_dropped_last(self):
        # Gens 16 and 17: research named brain/CONTEXT.md, the biggest file
        # shown, and fit_request dropped it first.
        files = {"brain/CONTEXT.md": "c" * 14000, "brain/playbooks/a.md": "a" * 9000,
                 "brain/playbooks/b.md": "b" * 9000, "brain/cron.toml": "t" * 12000}
        request = {"files": files, "keep": ["brain/CONTEXT.md"]}

        def render(req):
            return "x" * sum(len(t) for t in req["files"].values())

        fitted = evolve.fit_request(request, limit=24000, render=render)
        self.assertIn("brain/CONTEXT.md", fitted["files"])
        self.assertNotIn("brain/CONTEXT.md", fitted["omitted"])
        self.assertLessEqual(len(render(fitted)), 24000)

    def test_a_file_left_out_is_not_to_be_quoted_from_memory(self):
        text = evolve.render_request({
            "gen": 1, "cand": 1, "learning": {"id": "m1", "section": "what i have decided", "text": "t"},
            "evidence": {}, "editable": ["brain/SOUL.md"], "files": {}, "frozen": [], "omitted": ["brain/SOUL.md"],
            "rounds": 3, "max_lines": 10})
        self.assertIn("never from memory", text)


class EditRepair(EvolveCase):
    """A proposal whose only fault is an edit that did not apply is asked for
    that edit alone, shown the passage, in the shape-repair turn (#360): its
    other fields and edits are kept and no revise round is spent."""

    def setUp(self):
        super().setUp()
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24)
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m19] a refused write is not a failed ask.\n")

    def rounds(self):
        got = json.loads((evolve.gen_dir(1) / "cand-1.rounds.json").read_text())
        return got["rounds"] if isinstance(got, dict) else got

    def proposer(self, repairs, fixed):
        good = self.playbook_edit("A refused write is not a failed ask.")
        bad = {"path": good["path"], "old": good["old"].replace(" ", "  ", 1) + " and more words it never had",
               "new": good["new"]}
        seen = []

        class Quoting:
            name = "quoting"

            def propose(self, request):
                seen.append(request)
                return {"title": "t", "component": "attention", "expected_move": "m", "hypothesis": "h",
                        "check": CHECK, "edits": [bad] if len(seen) == 1 else [good]}

            def repair(self, request):
                repairs.append(request)
                return fixed(good, bad)

        return Quoting(), seen

    def test_an_edit_that_did_not_apply_is_asked_for_alone_without_spending_a_round(self):
        repairs = []
        proposer, seen = self.proposer(repairs, lambda good, bad: {"edits": [good], "title": "not asked for"})
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer):
            out = self.run_gen(1)
        self.assertEqual(out["candidates"][0]["verdict"], "opened" if not evolve.dry_run() else "dry-run")
        self.assertEqual((len(seen), len(repairs)), (1, 1))
        self.assertEqual(repairs[0]["missing"], ["edits"])
        failed = repairs[0]["edits_failed"]
        self.assertEqual(failed[0]["index"], 0)
        self.assertIn(evolve.DIFF_MARK, failed[0]["excerpt"])
        rounds = self.rounds()
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["repaired"], ["edits"])
        self.assertEqual(rounds[0]["proposal"]["title"], "t")  # only the edit was taken back
        self.assertEqual(rounds[0]["proposal"]["edits"][0]["old"], self.playbook_edit("x")["old"])

    def test_a_repair_that_still_does_not_apply_costs_the_round_with_the_passage_in_its_finding(self):
        repairs = []
        proposer, seen = self.proposer(repairs, lambda good, bad: {"edits": [bad]})
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer):
            self.run_gen(1)
        self.assertEqual((len(seen), len(repairs)), (2, 1))
        finding = seen[1]["findings"][0]
        self.assertIn("occurs 0 times", finding["problem"])
        self.assertIn(evolve.DIFF_MARK, finding["problem"])  # the passage, to copy from
        self.assertEqual(self.rounds()[0]["outcome"], "did-not-apply")

    def test_the_file_that_did_not_apply_is_shown_whole_next_round(self):
        repairs = []
        proposer, seen = self.proposer(repairs, lambda good, bad: None)
        path = self.playbook_edit("x")["path"]
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer), \
                unittest.mock.patch.object(evolve, "context_pack",
                                           side_effect=lambda tree, touched, budget: (
                                               [path], {p: (tree / p).read_text() for p in touched if p == path})):
            self.run_gen(1)
        self.assertNotIn(path, seen[0]["files"])
        self.assertIn(path, seen[1]["files"])
        self.assertIn(path, seen[1]["keep"])


class AskRepairText(unittest.TestCase):
    def test_the_edit_repair_turn_shows_the_passage_and_asks_for_the_edits_alone(self):
        messages = []
        proposer = evolve.AskProposer("act", "querier")
        failure = {"index": 0, "path": "brain/SOUL.md", "count": 0, "where": "near line 3",
                   "excerpt": "put a date on it. " + evolve.DIFF_MARK + "Every pull request",
                   "old": OLD_G9, "new": "x"}
        with unittest.mock.patch.object(evolve.AskProposer, "_turn",
                                        lambda self, req, msg: messages.append(msg) or '{"edits": []}'):
            proposer.repair({"gen": 9, "cand": 2, "missing": ["edits"], "problem": "did not apply",
                             "edits_failed": [failure], "proposal": {"title": "t"}})
        message = messages[0]
        self.assertIn("Every pull request", message)
        self.assertIn('"edits"', message)
        self.assertLess(len(message.encode("utf-8")), 12000)


if __name__ == "__main__":
    unittest.main()
