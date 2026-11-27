"""The builder is shown the files it is sent to edit.

Gens 28 to 34 (2026-11-25 to 11-27) lost seven candidates the same way:
g28c4, g30c2, g31c1, g32c2, g32c3, g33c1 and g34c1 were skipped or parked
because "the file's text is not shown this turn", so the builder could not
write an old text that occurs once. Three causes, each a fixture here:

- fit_request dropped every file, the ones research named too, before it
  cut a byte of anything else. The evidence alone was ~39 KB and the list of
  editable paths ~14 KB, so a 60 KB message fit only with no file in it:
  g34c1's research named brain/playbooks/moltbook-general.md and
  brain/skills/moltbook/SKILL.md, and the build was shown neither.
- A file too big to show whole (docs/hands.md, ~360 KB; docs/architecture.md,
  ~100 KB) was left out without a word, though the brief sends every new hand
  or playbook to them (g31c1, g33c1).
- A skip or an edit that named a file the builder was never shown settled
  the candidate (g28c4: "not shown this turn; retry when their text is in
  the message"; g30c2: "brain/skills/moltbook/SKILL.md exists; edit it with
  old and new")."""

import json
import sys
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_evolve import CHECK, EvolveCase  # noqa: E402

import evolve  # noqa: E402


def gen34_request():
    """A build request shaped like g34c1's (gen 34, 2026-11-27): the two
    files research named, the schedule and a dozen playbooks, ~39 KB of
    evidence and ~400 editable paths."""
    keep = ["brain/playbooks/moltbook-general.md", "brain/skills/moltbook/SKILL.md"]
    files = {keep[0]: "general line\n" * 680, keep[1]: "skill line\n" * 415, "brain/cron.toml": "t" * 26000}
    files.update({f"brain/playbooks/p{n:02d}.md": "p" * 1500 for n in range(12)})
    editable = sorted(set(files) | {f"brain/{d}/{n:03d}-a-longish-file-name.md"
                                    for d in ("playbooks", "skills/conversation", "agents") for n in range(130)})
    evidence = {"world": {"j": 0.4}, "at": "2026-11-27T00:00:00Z", "outcomes": {"x": ["o" * 100] * 120},
                "archive": {"recent": ["a" * 100] * 80}, "builds": ["b" * 100] * 60, "guard_holds": ["g" * 100] * 30}
    return {"gen": 34, "cand": 1, "learning": {"id": "m1", "section": "what i have decided", "hash": "h",
                                               "text": "pace the moltbook posts"},
            "evidence": evidence, "editable": editable, "files": files, "frozen": ["bin/**"], "keep": keep,
            "rounds": 3, "max_lines": 200, "siblings": [],
            "research": {"verdict": "build", "plan": "One line in the playbook.", "read": keep, "document": []}}


class TheNamedFilesOutlastTheRest(unittest.TestCase):
    def test_g34c1_the_files_research_named_are_shown_and_the_evidence_is_cut_instead(self):
        request = gen34_request()
        # As on the box: it fits with no file in it, so dropping every file
        # was enough, and that is what happened.
        self.assertLessEqual(len(evolve.render_request({**request, "files": {}}).encode("utf-8")),
                             evolve.MAX_PROPOSER_PROMPT_BYTES)
        fitted = evolve.fit_request(request)
        message = evolve.render_request(fitted)
        self.assertLessEqual(len(message.encode("utf-8")), evolve.MAX_PROPOSER_PROMPT_BYTES)
        for rel in request["keep"]:
            self.assertEqual(fitted["files"][rel], request["files"][rel])
            self.assertIn(f"## file: {rel}\n\n````text\n{request['files'][rel]}\n````", message)
            self.assertNotIn(rel, fitted["omitted"])
        # What made room is said: the other files, then what is not a file.
        self.assertIn("brain/cron.toml", fitted["omitted"])
        self.assertTrue(fitted["cut"])
        self.assertIn("## left out to fit the message", message)

    def test_a_named_file_goes_only_when_nothing_else_is_left_to_cut(self):
        request = {**gen34_request(), "files": {"brain/SOUL.md": "s" * 70_000, "brain/playbooks/a.md": "a"}}
        request["keep"] = ["brain/SOUL.md"]
        fitted = evolve.fit_request(request)
        self.assertLessEqual(len(evolve.render_request(fitted).encode("utf-8")), evolve.MAX_PROPOSER_PROMPT_BYTES)
        self.assertNotIn("brain/SOUL.md", fitted["files"])
        self.assertIn("brain/SOUL.md", fitted["omitted"])  # said, so it is never quoted from memory
        self.assertNotIn("brain/playbooks/a.md", fitted["files"])  # the unnamed one went first

    def test_an_excerpt_is_kept_like_a_named_file(self):
        request = gen34_request()
        request["excerpts"] = {"docs/hands.md": "Too long to show whole\n\n````text\n⟦lines 1-2⟧\nx\n````"}
        request["keep"] = [*request["keep"], "docs/hands.md"]
        fitted = evolve.fit_request(request)
        self.assertIn("docs/hands.md", fitted["excerpts"])
        self.assertIn("## file (excerpt): docs/hands.md", evolve.render_request(fitted))


HANDS_DOC = "".join(
    ["# Hands\n\nThe hands are many.\n\n"]
    + [f"## `hand{n}`: what hand {n} does\n\nHand {n} does its thing, at length.\n" + "Filler line.\n" * 60 + "\n"
       for n in range(40)]
    + ["## `wallet`: the only holder of the keys\n\nThe wallet buys and never sells.\n\n"
       "| `costs` | R | What it cost to keep Teeny running. |\n\n"]
    + [f"## `tail{n}`\n\nMore.\n" + "Filler line.\n" * 60 + "\n" for n in range(10)])


class OversizedFilesAreExcerpted(unittest.TestCase):
    def test_an_excerpt_quotes_the_files_own_lines_so_an_edit_copied_from_it_applies(self):
        terms = evolve.focus_terms(["brain/playbooks/cost-of-living.md", "docs/hands.md"], None,
                                   ['run = "hands/wallet costs --days 7"'])
        self.assertIn("wallet", terms)
        got = evolve.file_excerpt("docs/hands.md", HANDS_DOC, terms)
        self.assertLess(len(got), evolve.MAX_EXCERPT_CHARS_SHOWN + evolve.MAX_OUTLINE_CHARS + 1000)
        self.assertIn("Too long to show whole", got)
        self.assertIn("never from memory", got)
        self.assertIn("## `wallet`: the only holder of the keys\n\nThe wallet buys and never sells.", got)
        self.assertNotIn("Hand 3 does its thing", got)  # not about the change
        # The outline says where the rest is, by line.
        line = HANDS_DOC.splitlines().index("## `tail0`") + 1
        self.assertIn(f"{line}: ## `tail0`", got)
        # Every passage is the file's own text, so an old text quoted from
        # one occurs in the file once and applies.
        old = "| `costs` | R | What it cost to keep Teeny running. |"
        self.assertIn(old, got)
        self.assertEqual(HANDS_DOC.count(old), 1)

    def test_the_triggers_section_the_brief_names_is_always_in_the_architecture_excerpt(self):
        doc = "# Arch\n\n" + "".join(f"## {n}. Part\n\n" + "words\n" * 400 + "\n" for n in range(1, 6)) \
              + "## 6. Triggers\n\nEvery scheduled job is in one of two files:\n- one\n- two\n\n" + "z\n" * 3000
        got = evolve.file_excerpt("docs/architecture.md", doc, [])
        self.assertIn("## 6. Triggers\n\nEvery scheduled job is in one of two files:\n- one\n- two", got)

    def test_with_nothing_named_the_first_and_last_lines_are_shown(self):
        doc = "first line\n" + "middle\n" * 5000 + "last line\n"
        got = evolve.file_excerpt("docs/notes.md", doc, ["absent"])
        self.assertIn("first line", got)
        self.assertIn("last line", got)


class TheBuildIsShownItsTargets(EvolveCase):
    """The loop end to end, with a proposer that records what it was sent."""

    def setUp(self):
        super().setUp()
        self.page("# MEMORY.md\n\n## what i have decided\n\n- [m19] a refused write is not a failed ask.\n")

    def rounds(self):
        got = json.loads((evolve.gen_dir(1) / "cand-1.rounds.json").read_text())
        return got["rounds"] if isinstance(got, dict) else got

    def recorder(self, answers, research=None):
        seen = []

        class Recording:
            name = "recording"
            searches_web = False

            def propose(self, request):
                seen.append(request)
                return answers[min(len(seen), len(answers)) - 1]

        if research is not None:
            Recording.research = lambda self, request: research
        return Recording(), seen

    def good(self):
        return {"title": "t", "component": "attention", "expected_move": "m", "hypothesis": "h", "check": CHECK,
                "edits": [self.playbook_edit("A refused write is not a failed ask.")]}

    def test_g34c1_research_named_files_and_an_oversized_doc_are_all_shown_to_the_build(self):
        path = self.playbook_edit("x")["path"]
        research = {"verdict": "build", "why": "w", "plan": "One line in the playbook, one in the docs.",
                    "read": [path], "document": ["docs/hands.md"]}
        proposer, seen = self.recorder([self.good()], research)
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer):
            self.run_gen(1)
        request = seen[0]
        self.assertEqual(request["files"][path], self.base_text(path))
        self.assertIn("docs/hands.md", request["excerpts"])  # too big to show whole, so excerpted
        self.assertNotIn("docs/hands.md", request["files"])
        self.assertIn("Too long to show whole", request["excerpts"]["docs/hands.md"])
        self.assertIn(path, request["keep"])
        self.assertIn("docs/hands.md", request["keep"])
        message = evolve.render_request(evolve.fit_request(request))
        self.assertIn("## file (excerpt): docs/hands.md", message)
        self.assertIn(f"## file: {path}", message)

    def test_g28c4_a_skip_for_want_of_a_file_it_was_not_shown_is_shown_the_file_and_asked_again(self):
        path = self.playbook_edit("x")["path"]
        skip = {"skip": "its text is not in the message", "why": "not-actionable",
                "where": f"{path} (not shown this turn; retry when their text is in the message)"}
        proposer, seen = self.recorder([skip, self.good()])
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer), \
                unittest.mock.patch.object(evolve, "context_pack", return_value=([path], {})):
            out = self.run_gen(1)
        self.assertEqual(len(seen), 2)
        self.assertNotIn(path, seen[0]["files"])
        self.assertEqual(seen[1]["files"][path], self.base_text(path))
        self.assertIn(path, seen[1]["keep"])
        self.assertEqual(seen[1]["findings"][0]["file"], path)
        self.assertEqual(self.rounds()[0]["outcome"], "unseen-skip")
        self.assertNotEqual(out["candidates"][0]["verdict"], "skipped")

    def test_a_skip_on_a_file_it_was_shown_still_settles(self):
        path = self.playbook_edit("x")["path"]
        skip = {"skip": "already says it", "why": "already", "where": path}
        proposer, seen = self.recorder([skip])
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer), \
                unittest.mock.patch.object(evolve, "context_pack",
                                           return_value=([path], {path: self.base_text(path)})):
            out = self.run_gen(1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(out["candidates"][0]["verdict"], "skipped")

    def test_a_frozen_skip_is_never_sent_back(self):
        path = self.playbook_edit("x")["path"]
        proposer, seen = self.recorder([{"skip": "needs the hand", "why": "frozen", "where": path}])
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer), \
                unittest.mock.patch.object(evolve, "context_pack", return_value=([path], {})):
            out = self.run_gen(1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(out["candidates"][0]["verdict"], "skipped")

    def test_an_already_skip_on_a_file_it_was_not_shown_still_settles(self):
        path = self.playbook_edit("x")["path"]
        proposer, seen = self.recorder([{"skip": "it says so", "why": "already", "where": path}])
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer), \
                unittest.mock.patch.object(evolve, "context_pack", return_value=([path], {})):
            out = self.run_gen(1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(out["candidates"][0]["verdict"], "skipped")

    def test_on_the_last_round_the_skip_stands(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_REVISE_ROUNDS=1)
        path = self.playbook_edit("x")["path"]
        proposer, seen = self.recorder([{"skip": "not shown", "why": "not-actionable", "where": path}])
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer), \
                unittest.mock.patch.object(evolve, "context_pack", return_value=([path], {})):
            out = self.run_gen(1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(out["candidates"][0]["verdict"], "skipped")

    def test_g30c2_an_edit_creating_a_file_that_exists_shows_that_file_next_round(self):
        path = self.playbook_edit("x")["path"]
        create = {**self.good(), "edits": [{"path": path, "old": "", "new": "a whole new playbook\n"}]}
        proposer, seen = self.recorder([create, self.good()])
        with unittest.mock.patch.object(evolve, "make_proposer", return_value=proposer), \
                unittest.mock.patch.object(evolve, "context_pack", return_value=([path], {})):
            self.run_gen(1)
        self.assertEqual(len(seen), 2)
        self.assertNotIn(path, seen[0]["files"])
        self.assertEqual(seen[1]["files"][path], self.base_text(path))
        self.assertEqual(self.rounds()[0]["outcome"], "did-not-apply")
        self.assertEqual(self.rounds()[0]["findings"][0]["file"], path)  # shown again after a restart


if __name__ == "__main__":
    unittest.main()
