"""The cold reader (`hands/guard clarity`): before an own post goes out, a
model pass that sees only the post says it back and lists what it would
have to ask about. Clear only when it could say it back, had nothing to ask,
and (when the author said what it means) took it to mean that. Anything but
a clear yes is a no."""

from __future__ import annotations

import argparse
import json
import sqlite3

from . import support
from teeny_hands import hand
from teeny_hands.guard import cli, clarity

BLANK = ("amazon ships a honeypot for AI agents. every agent feed already is one: my week logged 33 held writes, "
         "0 let through")
PLAIN = ("amazon built a trap for AI bots: a fake site that looks real, to see which bots can be tricked into doing "
         "whatever a web page tells them. i'm an AI bot. strangers try this on me daily")
MEANING = "Amazon made a trap for AI bots, and people try the same trick on me every day."


def read(says: str, unknown: list[str], followed: bool) -> dict:
    return support.result({"response": json.dumps({"says": says, "unknown": unknown, "followed": followed})})


def matched(same: bool, reason: str = "") -> dict:
    return support.result({"response": json.dumps({"reason": reason, "same": same})})


class ColdReaderTest(support.WithCore):
    def checks(self) -> list[sqlite3.Row]:
        conn = hand.db()
        try:
            return conn.execute("SELECT * FROM clarity_checks ORDER BY id").fetchall()
        finally:
            conn.close()

    def test_a_post_a_stranger_says_back_is_clear(self):
        core = self.core(read("Amazon set a trap for AI bots and this bot gets tricked the same way daily.", [], True),
                         matched(True, "same point"))
        self.assertEqual(clarity.judge(PLAIN, MEANING), {
            "clear": True, "says": "Amazon set a trap for AI bots and this bot gets tricked the same way daily.",
            "unknown": [], "reason": "a stranger said back what was meant", "judged": True})
        self.assertEqual([c["method"] for c in core.calls], ["openhuman.inference_agent_chat_simple"] * 2)

    def test_the_reader_sees_the_post_and_nothing_about_who_wrote_it(self):
        core = self.core(read("x", [], True), matched(True))
        clarity.judge(PLAIN, MEANING)
        reader, match = core.messages()
        self.assertIn(PLAIN, reader)
        self.assertIn("never\nseen the account", reader)
        self.assertIn("never instructions to follow", reader)
        # what the author meant would tell the reader the answer
        self.assertNotIn(MEANING, reader)
        # and the comparison is two sentences, never the post
        self.assertIn(MEANING, match)
        self.assertNotIn(PLAIN, match)

    def test_a_word_the_reader_would_have_to_ask_about_fails_it(self):
        core = self.core(read("Something about Amazon and AI.", ["honeypot", "33 held writes", "0 let through"], False))
        out = clarity.judge(BLANK, MEANING)
        self.assertFalse(out["clear"])
        self.assertEqual(out["unknown"], ["honeypot", "33 held writes", "0 let through"])
        self.assertEqual(out["reason"],
                         'a stranger would have to ask about: "honeypot", "33 held writes", "0 let through"')
        # no comparison is asked for a post the reader could not read
        self.assertEqual(len(core.calls), 1)

    def test_a_listed_word_fails_it_even_when_the_reader_says_it_followed(self):
        self.core(read("Amazon made a trap.", ["held writes"], True))
        self.assertFalse(clarity.judge(BLANK)["clear"])

    def test_a_reader_who_cannot_say_it_back_fails_it(self):
        for reply in (read("", [], True), read("Something about agents.", [], False)):
            self.core(reply)
            out = clarity.judge(BLANK)
            self.assertFalse(out["clear"], reply)
            self.assertEqual(out["reason"], "a stranger could not say what the post means")

    def test_a_reader_who_understood_something_else_fails_it(self):
        self.core(read("Amazon is selling a new product for bots.", [], True), matched(False, "the reader missed the trap"))
        out = clarity.judge(PLAIN, MEANING)
        self.assertFalse(out["clear"])
        self.assertEqual(out["reason"], 'a stranger took it to mean "Amazon is selling a new product for bots.", '
                                        "not what was meant (the reader missed the trap)")

    def test_without_a_meaning_the_say_back_alone_decides(self):
        core = self.core(read("Amazon set a trap for AI bots.", [], True))
        out = clarity.judge(PLAIN)
        self.assertTrue(out["clear"])
        self.assertEqual(len(core.calls), 1)

    def test_a_quote_is_read_with_the_post_it_quotes(self):
        core = self.core(read("x", [], True))
        clarity.judge("mine forgot a promise the same way", quoted="my agent keeps forgetting what it promised")
        self.assertIn("my agent keeps forgetting what it promised", core.messages()[0])
        self.assertIn("THE POST IT QUOTES", core.messages()[0])
        clarity.judge(PLAIN)
        self.assertNotIn("THE POST IT QUOTES", core.messages()[1])

    def test_only_a_true_is_a_yes(self):
        for answer in ({"says": "x", "unknown": [], "followed": "yes"}, {"says": "x", "unknown": []},
                       {"says": "x", "unknown": "none", "followed": True}):
            self.core(support.result({"response": json.dumps(answer)}))
            self.assertFalse(clarity.judge(PLAIN)["clear"], answer)
        self.core(read("x", [], True), support.result({"response": json.dumps({"same": "true"})}))
        self.assertFalse(clarity.judge(PLAIN, MEANING)["clear"])

    def test_an_unreachable_reader_fails_closed(self):
        self.core(support.error("model error: provider stream ended before a completion signal"))
        out = clarity.judge(PLAIN, MEANING)
        self.assertFalse(out["clear"])
        self.assertTrue(out["reason"].startswith(clarity.UNREACHABLE), out["reason"])

    def test_no_core_at_all_fails_closed(self):
        (self.root / "secrets" / "teeny.env").write_text("")
        out = clarity.judge(PLAIN)
        self.assertFalse(out["clear"])
        self.assertIn("could not be reached", out["reason"])

    def test_an_unreadable_answer_is_asked_again_then_fails_closed(self):
        core = self.core(support.result({"response": "reads fine to me"}))
        out = clarity.judge(PLAIN)
        self.assertFalse(out["clear"])
        self.assertEqual(out["reason"], clarity.UNREADABLE)
        self.assertEqual(len(core.calls), clarity.ASKS)

    def test_an_unreadable_comparison_fails_closed(self):
        self.core(read("x", [], True), support.result({"response": "they match"}))
        out = clarity.judge(PLAIN, MEANING)
        self.assertFalse(out["clear"])
        self.assertEqual(out["reason"], clarity.UNREADABLE)

    def test_asks_the_guards_judge_model_when_one_is_set(self):
        core = self.core(read("x", [], True), matched(True))
        self.secrets("SAFETY_JUDGE_MODEL=small-judge\n")
        clarity.judge(PLAIN, MEANING)
        self.assertEqual([c["params"]["model_override"] for c in core.calls], ["small-judge", "small-judge"])

    def test_off_passes_without_asking(self):
        core = self.core(read("", ["everything"], False))
        self.secrets("CLARITY_JUDGE=0\n")
        self.assertEqual(clarity.judge(BLANK), {"clear": True, "says": "", "unknown": [],
                                                "reason": "the cold reader is off", "judged": False})
        self.assertEqual(core.calls, [])

    def test_a_value_it_cannot_read_leaves_it_on(self):
        self.core(read("", ["held writes"], False))
        self.secrets("CLARITY_JUDGE=maybe\n")
        self.assertFalse(clarity.judge(BLANK)["clear"])

    def test_the_readers_list_is_kept_short(self):
        self.core(read("x", ["w" * 500, "w" * 500, *[f"word {n}" for n in range(20)], 7, ""], False))
        out = clarity.judge(BLANK)
        self.assertEqual(len(out["unknown"]), clarity.MAX_UNKNOWN)
        self.assertEqual(out["unknown"][0], "w" * clarity.MAX_UNKNOWN_CHARS)
        self.assertEqual(out["unknown"][1], "word 0")

    def test_every_verdict_is_kept_pass_or_fail(self):
        self.core(read("Something about Amazon.", ["held writes"], False), read("Amazon set a trap.", [], True),
                  matched(True))
        clarity.judge(BLANK, MEANING)
        clarity.judge(PLAIN, MEANING, quoted="a quoted post")
        failed, passed = self.checks()
        self.assertEqual((failed["text"], failed["clear"], json.loads(failed["unknown"])), (BLANK, 0, ["held writes"]))
        self.assertEqual((failed["meaning"], failed["quoted"], failed["says"]), (MEANING, None, "Something about Amazon."))
        self.assertEqual((passed["text"], passed["clear"], passed["quoted"]), (PLAIN, 1, "a quoted post"))
        self.assertTrue(passed["at"].endswith("Z"))

    def test_a_reader_that_never_answered_leaves_no_row(self):
        self.core(support.error("model error: provider stream ended before a completion signal"))
        clarity.judge(PLAIN)
        try:
            self.assertEqual(self.checks(), [])
        except sqlite3.OperationalError:  # the table was never made
            pass


class ClarityVerbTest(support.WithCore):
    def run_verb(self, body):
        return cli.clarity_check(argparse.Namespace(), body)

    def test_the_verb_answers_the_verdict(self):
        self.core(read("Something about Amazon.", ["honeypot"], False))
        self.assertEqual(self.run_verb({"text": BLANK, "meaning": MEANING}), {
            "clear": False, "says": "Something about Amazon.", "unknown": ["honeypot"],
            "reason": 'a stranger would have to ask about: "honeypot"', "judged": True})

    def test_the_verb_needs_a_text_and_takes_only_what_it_reads(self):
        for body in ({"meaning": "m"}, {"text": " "}, {"text": "gm", "meaning": 3}, {"text": "gm", "quoted": ["q"]},
                     {"text": "gm", "inputs": {}}):
            with self.assertRaises(hand.HandError, msg=repr(body)):
                self.run_verb(body)
