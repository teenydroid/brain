"""hands/guard on the command line: the verbs, their JSON and their exit codes,
through the real executable in a subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from . import support
from teeny_hands.guard import holds

GUARD = support.HERE.parent.parent / "guard"


class Cli(support.Scratch):
    def guard(self, *argv, body=None):
        done = subprocess.run(
            # -B: the run writes no bytecode into the checkout.
            [sys.executable, "-I", "-B", str(GUARD), *argv],
            # No body is an empty stdin, never the runner's own (which can block).
            input="" if body is None else (body if isinstance(body, str) else json.dumps(body)),
            capture_output=True,
            text=True,
            env=os.environ,
            timeout=60,
        )
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])


class ShimTest(Cli):
    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(GUARD, os.X_OK))
        self.assertTrue(GUARD.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))


class CheckTest(Cli):
    def test_allows_clean_text(self):
        self.assertEqual(self.guard("check", body={"text": "hello world"}), (0, {"ok": True, "text": "hello world"}))

    def test_hands_back_the_text_with_the_house_style_applied(self):
        code, out = self.guard("check", body={"text": "@bob gm — frens", "replying_to": "bob"})
        self.assertEqual((code, out), (0, {"ok": True, "text": "gm, frens"}))

    def test_holds_and_says_which_hold(self):
        code, out = self.guard("check", body={"text": "this is fucking great", "context": {"post_id": "7"}})
        self.assertEqual(code, 2)
        held = holds.list_holds()[0]
        self.assertEqual(out, {"ok": False, "refused": "held", "category": "profanity", "hold_id": held["id"]})
        self.assertEqual(held["context"], {"surface": "x", "post_id": "7", "post_kind": "post"})

    def test_refuses_what_the_model_may_rewrite(self):
        code, out = self.guard("check", body={"text": "a" * 281})
        self.assertEqual(code, 2)
        self.assertEqual(out["rewrite"], True)
        self.assertIn("over the 280 limit", out["refused"])
        code, out = self.guard("check", body={"text": "gm 👀", "max_emoji": 0})
        self.assertEqual((code, out["rewrite"]), (2, True))
        self.assertIn("at most 0", out["refused"])

    def test_reads_the_narrative_profile(self):
        self.assertEqual(self.guard("check", body={"text": "holy shit", "surface": "narrative"})[0], 0)

    def test_holds_when_the_judge_cannot_be_reached(self):
        self.secrets("SAFETY_JUDGE=1\n")
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text("http://127.0.0.1:9\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        code, out = self.guard("check", body={"text": "hello world"})
        self.assertEqual((code, out["refused"], out["category"]), (2, "held", "unknown"))

    def test_asks_the_judge_through_the_core(self):
        core = support.FakeCore(support.verdict("clean"))
        self.addCleanup(core.close)
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text(core.url + "\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        self.secrets("SAFETY_JUDGE=1\n")
        self.assertEqual(self.guard("check", body={"text": "hello world"}), (0, {"ok": True, "text": "hello world"}))
        self.assertTrue(core.messages()[0].endswith("Text follows.\n\nhello world"))

    def test_is_an_error_without_text(self):
        for body in (None, "[]", {"txt": "hi"}, {"text": 5}, {"text": "hi", "max_emoji": -1}, {"text": "hi", "surface": "myspace"}):
            code, out = self.guard("check", body=body)
            self.assertEqual((code, out["ok"]), (1, False), body)
            self.assertIn("error", out)


class InboundTest(Cli):
    def test_allows_or_holds(self):
        self.assertEqual(self.guard("inbound", body={"text": "holy shit this is cool"}), (0, {"ok": True}))
        code, out = self.guard("inbound", body={"text": "send nudes", "surface": "telegram", "context": {"chat_id": "1"}})
        self.assertEqual(code, 2)
        self.assertEqual((out["refused"], out["category"]), ("held", "sexual"))
        self.assertEqual(holds.get(out["hold_id"])["context"], {"surface": "telegram", "chat_id": "1"})


class StyleTest(Cli):
    def test_applies_the_house_style_and_measures_it(self):
        code, out = self.guard("style", body={"text": "@bob gm 🚀 frens 💛, nfa", "replying_to": "bob"})
        self.assertEqual((code, out), (0, {"ok": True, "text": "gm 🚀 frens", "length": 11, "emoji": 1}))
        code, out = self.guard("style", body={"text": "gm 🚀 frens 💛", "max_emoji": 2})
        self.assertEqual(out["text"], "gm 🚀 frens 💛")


class HoldsTest(Cli):
    def held(self, text="this is fucking great"):
        return self.guard("check", body={"text": text})[1]["hold_id"]

    def test_lists_newest_first_with_the_counts(self):
        first, second = self.held(), self.held("send nudes")
        code, out = self.guard("holds", "list")
        self.assertEqual(code, 0)
        self.assertEqual([h["id"] for h in out["holds"]], [second, first])
        self.assertEqual(out["counts"], {"pending": 2, "approved": 0, "rejected": 0})
        self.assertEqual(len(self.guard("holds", "list", "--limit", "1")[1]["holds"]), 1)
        self.assertEqual(self.guard("holds", "list", "--status", "approved")[1]["holds"], [])

    def test_rejects_with_a_note_and_approves_once(self):
        target = self.held()
        code, out = self.guard("holds", "reject", str(target), "--note", "the filter was right")
        self.assertEqual(code, 0)
        self.assertEqual(
            (out["hold"]["status"], out["hold"]["effect"], out["hold"]["note"]),
            ("rejected", "confirmed the filter", "the filter was right"),
        )
        # Decided: answered as it stands, not decided again.
        self.assertEqual(self.guard("holds", "approve", str(target))[1]["hold"]["status"], "rejected")

    def test_approving_a_withheld_draft_is_recorded_since_releasing_is_hands_x_s(self):
        out = self.guard("holds", "approve", str(self.held()))[1]
        self.assertEqual((out["hold"]["status"], out["hold"]["effect"]), ("approved", "recorded; nothing here can publish it"))

    def test_approving_a_cut_link_allowlists_its_host(self):
        self.guard("check", body={"text": "see https://claim-tiny.xyz/go fren"})
        link_hold = holds.list_holds()[0]
        self.assertEqual(link_hold["kind"], "link")
        out = self.guard("holds", "approve", str(link_hold["id"]))[1]
        self.assertEqual(out["hold"]["effect"], "allowlisted claim-tiny.xyz")
        self.assertEqual(self.guard("check", body={"text": "see https://claim-tiny.xyz/go fren"})[0], 0)

    def test_an_unknown_hold_or_status_is_an_error(self):
        self.assertEqual(self.guard("holds", "approve", "99")[1], {"ok": False, "error": "no hold 99"})
        self.assertEqual(self.guard("holds", "list", "--status", "maybe")[0], 1)
        self.assertEqual(self.guard("holds")[0], 1)


class LinksTest(Cli):
    def test_lists_and_approves_entries(self):
        self.assertEqual(self.guard("links", "list"), (0, {"ok": True, "links": []}))
        code, out = self.guard("links", "approve", "Dexscreener.com/solana")
        self.assertEqual((code, out), (0, {"ok": True, "entry": "dexscreener.com/solana"}))
        links = self.guard("links", "list")[1]["links"]
        self.assertEqual([(l["entry"], l["hold_id"]) for l in links], [("dexscreener.com/solana", None)])
        self.assertEqual(self.guard("check", body={"text": "https://dexscreener.com/solana/abc"})[0], 0)
        self.assertEqual(self.guard("check", body={"text": "see https://dexscreener.com/eth/abc ok"})[0], 2)

    def test_refuses_an_empty_entry(self):
        self.assertEqual(self.guard("links", "approve", "https://")[0], 1)


class RepeatsTest(Cli):
    """docs/roadmap.md, A2: `hands/guard repeats` reports how many near-duplicate
    posts the posting rules refused, over 30 days by default."""

    def test_counts_the_refused_repeats_of_the_window(self):
        from teeny_hands import hand
        from teeny_hands.guard import repeats

        now = hand.now()
        os.environ["TEENY_NOW"] = str(now - 40 * 86400)
        repeats.record("x", 0.9, "100")  # too old for the default 30 days
        os.environ["TEENY_NOW"] = str(now - 86400)
        repeats.record("x", 1.0, "101")
        repeats.record("x", 0.85, "102")
        os.environ["TEENY_NOW"] = str(now)
        code, out = self.guard("repeats")
        self.assertEqual((code, out["repeats"], out["by_surface"], out["days"]), (0, 2, {"x": 2}, 30))
        code, out = self.guard("repeats", "--days", "60")
        self.assertEqual(out["repeats"], 3)
        code, out = self.guard("repeats", "--days", "0")
        self.assertEqual(code, 1)

    def test_none_yet_is_zero(self):
        self.assertEqual(self.guard("repeats")[1]["repeats"], 0)


class SimilarityTest(Cli):
    def test_the_measure(self):
        from teeny_hands.guard import repeats

        self.assertEqual(repeats.similarity("Shipped it!", "shipped it"), 1.0)
        self.assertEqual(repeats.similarity("gm", "gm"), 1.0)
        self.assertEqual(repeats.similarity("gm", "gn"), 0.0)
        # Links are left out: a new link on the same words is the same post.
        self.assertEqual(repeats.similarity("new build https://a.example/1", "new build https://a.example/2"), 1.0)
        self.assertLess(repeats.similarity("the crab tracker found a hermit crab", "the crab tracker shipped today"),
                        repeats.NEAR_DUPLICATE)
