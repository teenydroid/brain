"""The research step's web queries (docs/evolve.md, "Research first"): one
tool-less turn writes them, code scrubs and validates every one, and only
the ones that pass are searched for. Offline, on test_evolve_loop's harness."""

import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_evolve import Verdicts  # noqa: E402
from test_evolve_loop import BUILD, LoopCase, Recording  # noqa: E402

import evolve  # noqa: E402

LEARNING = {"id": "m36", "section": "what i have decided", "hash": "h",
            "text": "before retrying anything, check whether it already went through. Then retry once."}
GOOD = "idempotent retry patterns for payment apis"


def check(queries, n=5, learning=LEARNING):
    return evolve.check_queries({"queries": queries}, learning, n)


class CheckQueries(unittest.TestCase):
    def test_model_queries_are_validated_and_only_the_good_ones_are_sent(self):
        got = check([GOOD, "  how do agents avoid duplicate side effects  ", "too short", "x " * 13,
                     "a" * (evolve.MAX_MODEL_QUERY_CHARS - 10) + " b c d", "line one\nline two three",
                     GOOD.upper().lower(), 42])
        self.assertEqual(got["sent"], [GOOD, "how do agents avoid duplicate side effects"])
        whys = [d["why"] for d in got["dropped"]]
        self.assertEqual(len(whys), 6, got)
        for part in ("fewer than", "more than", "characters", "newline", "a repeat", "not text"):
            self.assertTrue(any(part in w for w in whys), (part, whys))
        self.assertEqual(len(got["proposed"]), 8)

    def test_no_more_than_n_are_sent(self):
        got = check([GOOD, "exponential backoff with jitter for flaky apis", "checking before retrying a request"], n=2)
        self.assertEqual(len(got["sent"]), 2)
        self.assertIn("past the most", got["dropped"][0]["why"])

    def test_private_looking_queries_are_dropped_never_scrubbed_and_sent(self):
        got = check(["refund policy for Jane Doe deposits", "send sol to 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
                     "ask @nyna_dev about retries", "mail nyna@example.com about the retry",
                     "read https://x.com/a/b about retries", "wallet 0x52908400098527886E0F7030069857D2E4169EE7 retries",
                     "payout of 0.5 sol to depositors", "what happened on nov 14 deposits", "costs over $5 per retry",
                     "call +1 415-555-0100 about it", "Ravi retry policy for agents", GOOD])
        self.assertEqual(got["sent"], [GOOD])
        self.assertEqual(len(got["dropped"]), 11, got["dropped"])

    def test_the_repositorys_own_words_and_a_leading_question_word_pass(self):
        got = check(["How do Python agents retry safely on GitHub", "x402 payment retries and idempotency"])
        self.assertEqual(len(got["sent"]), 2, got)

    def test_a_query_quoting_the_learning_is_dropped(self):
        got = check(["before retrying anything check whether it already went", GOOD])
        self.assertEqual(got["sent"], [GOOD])
        self.assertIn("quotes the learning", got["dropped"][0]["why"])

    def test_an_answer_out_of_shape_is_an_error(self):
        for bad in (["a list"], {"queries": "one string"}, {"nothing": []}):
            with self.assertRaises(evolve.EvolveError):
                evolve.check_queries(bad, LEARNING, 3)

    def test_the_prompt_says_the_queries_go_to_a_public_search_engine_and_carry_nothing_private(self):
        message = evolve.render_query_request({"gen": 3, "cand": 2, "learning": LEARNING, "n": 3})
        self.assertTrue(message.startswith("# evolve research queries: generation 3, candidate 2"))
        self.assertIn(LEARNING["text"], message)
        for words in ("public search engine", "no names of people", "accounts", "wallets", "amounts", "dates",
                      "memory", "up to 3", '{"queries"'):
            self.assertIn(words, message)


class Querying(Recording):
    """Recording, which also answers the query turn from a script."""

    def __init__(self, queries=None, **kw):
        super().__init__(**kw)
        self.query_answers = list(queries or [])
        self.query_requests = []

    def queries(self, request):
        self.query_requests.append(request)
        answer = self.query_answers.pop(0) if self.query_answers else {"queries": []}
        if isinstance(answer, Exception):
            raise answer
        return answer(request) if callable(answer) else answer


class QueryTurn(LoopCase):
    def setUp(self):
        super().setUp()
        self.x402()
        self.live()
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_RESEARCH_SEARCHES=3)

    def searched(self):
        return [c[5] for c in self.hand_calls() if c[0] == "x402"]

    def saved(self):
        return json.loads((evolve.gen_dir(1) / "cand-1.research.json").read_text())

    def test_only_validated_queries_reach_the_search_and_the_research_says_which(self):
        self.one_learning()
        private = "refund policy for Jane Doe deposits"
        proposer = Querying(queries=[{"queries": [GOOD, private, "nov 14 funding round"]}],
                            research=[BUILD], proposals=[self.proposal()])
        self.run_with(proposer, evaluator=Verdicts())
        self.assertEqual(self.searched(), [GOOD])
        (request,) = proposer.query_requests
        self.assertEqual(request["learning"]["id"], "m36")
        self.assertEqual(request["n"], 3)
        queries = self.saved()["research"]["queries"]
        self.assertEqual(queries["proposed"], [GOOD, private, "nov 14 funding round"])
        self.assertEqual(queries["sent"], [GOOD])
        self.assertEqual([d["query"] for d in queries["dropped"]], [private, "nov 14 funding round"])
        self.assertTrue(all(d["why"] for d in queries["dropped"]))
        self.assertEqual([w["query"] for w in self.saved()["research"]["web"]], [GOOD])
        self.assertEqual(proposer.research_requests[0]["web"][0]["query"], GOOD)

    def test_a_failed_query_turn_runs_no_search(self):
        self.one_learning()
        proposer = Querying(queries=[evolve.EvolveError("the proposer's turn failed: boom")],
                            research=[BUILD], proposals=[self.proposal()])
        self.run_with(proposer, evaluator=Verdicts())
        self.assertEqual(self.searched(), [])
        self.assertEqual(proposer.research_requests[0]["web"], [])
        self.assertIn("boom", self.saved()["research"]["queries"]["error"])

    def test_an_answer_with_nothing_usable_runs_no_search(self):
        for n, answer in enumerate(({"queries": []}, {"queries": ["Jane Doe"]}, {"verdict": "build"})):
            with self.subTest(answer=answer):
                self.one_learning(f"learning number {'i' * (n + 1)}: before retrying, check it went through.")
                proposer = Querying(queries=[answer], research=[BUILD], proposals=[self.proposal()])
                self.run_with(proposer, n=1, evaluator=Verdicts())
                self.assertEqual(self.searched(), [])

    def test_no_query_turn_when_searches_are_off(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_RESEARCH_SEARCHES=0)
        self.one_learning()
        proposer = Querying(queries=[{"queries": [GOOD]}], research=[BUILD], proposals=[self.proposal()])
        self.run_with(proposer, evaluator=Verdicts())
        self.assertEqual(proposer.query_requests, [])
        self.assertEqual(self.searched(), [])
        self.assertNotIn("queries", self.saved()["research"])

    def test_no_query_turn_for_a_promise(self):
        self.page("# MEMORY.md\n\n## promises and open threads\n\n- [m12] promise to nyna: answer her first.\n")
        proposer = Querying(queries=[{"queries": [GOOD]}], research=[BUILD], proposals=[self.proposal()])
        self.run_with(proposer, evaluator=Verdicts())
        self.assertEqual(proposer.query_requests, [])

    def test_the_budget_is_honoured_and_a_spent_budget_asks_no_query_turn(self):
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_RESEARCH_SEARCHES=5)
        self.page("# MEMORY.md\n\n## what i have decided\n\n"
                  + "".join(f"- [m{i}] rule {i} is a rule worth keeping.\n" for i in range(40, 44)))
        topics = ["retry", "backoff", "caching", "logging", "queueing"]
        answers = [{"queries": [f"{t} patterns for agent number {w}" for t in topics]}
                   for w in ("one", "two", "three", "four")]
        proposer = Querying(queries=answers, research=[{"verdict": "not-worth", "why": "no"}] * 4)
        self.run_with(proposer, n=4, evaluator=Verdicts())
        self.assertEqual(len(self.searched()), evolve.MAX_GEN_SEARCHES)
        self.assertEqual(len(proposer.query_requests), 3)  # the fourth found the budget spent
        third = proposer.research_requests[2]["web"]
        self.assertTrue(all("spent" in w.get("skipped", "") for w in third[-3:]), third)
        self.assertEqual(proposer.research_requests[3]["web"], [])
        self.assertFalse((evolve.gen_dir(1) / "searches.json").read_text().count("13"))

    def test_under_dry_run_the_queries_are_written_but_nothing_is_paid_for(self):
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=1\n")
        self.one_learning()
        proposer = Querying(queries=[{"queries": [GOOD]}], research=[BUILD], proposals=[self.proposal()])
        self.run_with(proposer)
        self.assertEqual(self.searched(), [])
        self.assertEqual(proposer.research_requests[0]["web"], [{"query": GOOD, "skipped": "DRY_RUN: no paid search"}])

    def test_a_restart_after_the_searches_reuses_the_queries_and_the_results(self):
        self.one_learning()

        def cut_off(request):
            raise KeyboardInterrupt("cut off before the research turn answered")

        first = Querying(queries=[{"queries": [GOOD, "exponential backoff with jitter for apis"]}],
                         research=[cut_off], proposals=[self.proposal()])
        with self.assertRaises(KeyboardInterrupt):
            self.run_with(first, evaluator=Verdicts())
        self.assertEqual(len(self.searched()), 2)
        pending = json.loads((evolve.gen_dir(1) / "cand-1.research.json").read_text())
        self.assertNotIn("research", pending)
        again = Querying(queries=[{"queries": ["something else entirely to search"]}],
                         research=[BUILD], proposals=[self.proposal()])
        self.run_with(again, evaluator=Verdicts())
        self.assertEqual(again.query_requests, [])  # the turn is not asked again
        self.assertEqual(len(self.searched()), 2)  # nor is a search paid for twice
        self.assertEqual([w["query"] for w in again.research_requests[0]["web"]],
                         [GOOD, "exponential backoff with jitter for apis"])
        self.assertEqual(self.saved()["research"]["queries"]["sent"], [GOOD, "exponential backoff with jitter for apis"])


class AskQueryTurn(LoopCase):
    def test_the_ask_proposer_writes_its_queries_on_a_tool_less_turn_before_research(self):
        self.x402()
        self.live()
        calls = self.root / "oh.log"
        fake = self.root / "fake-oh"
        fake.write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            f"open({str(calls)!r}, 'a').write(sys.argv[2] + '\\n')\n"
            "msg = json.loads(sys.argv[2])['message']\n"
            "answer = {'queries': [" + repr(GOOD) + "]} if msg.startswith('# evolve research queries') "
            "else {'verdict': 'not-worth', 'why': 'a feeling'}\n"
            "print(json.dumps({'response': json.dumps(answer)}))\n")
        fake.chmod(0o755)
        os.environ["TEENY_OH"] = str(fake)
        self.config(EVOLVE=1, EVOLVE_GENERATIONS_PER_DAY=24, EVOLVE_PROPOSER="ask", EVOLVE_RESEARCH_SEARCHES=2)
        self.one_learning()
        evolve.run_generation(evolve.load_config(), 1, None, publisher=__import__("test_evolve").FakePublisher())
        turns = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual([t["agent_id"] for t in turns], ["act", "act"])
        self.assertTrue(turns[0]["message"].startswith("# evolve research queries"))
        self.assertTrue(turns[1]["message"].startswith("# evolve research: generation 1"))
        self.assertEqual([c[5] for c in self.hand_calls() if c[0] == "x402"], [GOOD])

    def test_the_evolver_writes_its_queries_on_the_tool_less_proposer_agent_not_its_own(self):
        seen = []

        def turn(self, request, message):
            seen.append((self.agent, message))
            return json.dumps({"queries": [GOOD]})

        cfg = {"EVOLVE_PROPOSER_AGENT": "act", "EVOLVE_EVOLVER_AGENT": "evolver", "EVOLVE_LAB_PORT": 7790}
        with unittest.mock.patch.object(evolve.AskProposer, "_turn", turn):
            got = evolve.EvolverProposer(cfg).queries({"gen": 1, "cand": 1, "learning": LEARNING, "n": 2})
        self.assertEqual(got, {"queries": [GOOD]})
        self.assertEqual(seen[0][0], "act")


if __name__ == "__main__":
    unittest.main()
