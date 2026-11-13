"""Port of `gate_tests.rs` (itself `legacy/test/safety-gate.test.ts`) and of the
gate half of `review_tests.rs` (the gate recording its holds, approved links
reaching it), plus the API hands/x calls: `check_outbound` and `inbound`.

The Rust tests gave the gate a spy judge; here the judge is the real one,
asking a fake core, and what it was asked is read back off the fake.
"""

from __future__ import annotations

import os
import shutil
from unittest import mock

from . import support
from teeny_hands.guard import gate, holds
from teeny_hands.guard.gate import Verdict, check_outbound, gate_inbound, gate_outbound, inbound


def asked(core):
    """(text, direction) for every question the judge was asked."""
    out = []
    for message in core.messages():
        head, _, text = message.rpartition("Text follows.\n\n")
        direction = head.rsplit("Direction: ", 1)[1].split(".", 1)[0].lower()
        out.append((text, direction))
    return out


def x_context(**more):
    return {"surface": "x", "post_id": "10", "author_username": "alice", **more}


def is_held(held, category, by):
    return held is not None and held.category == category and held.by == by


class Lexical(support.Scratch):
    """The gate with no judge: SAFETY_JUDGE=0, as `SafetyGateOptions::default()`."""

    def break_the_log(self):
        """state/hands.db as something SQLite cannot open, as Mongo going away."""
        state = self.root / "state"
        state.mkdir(exist_ok=True)
        for name in ("hands.db", "hands.db-wal", "hands.db-shm"):
            path = state / name
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        (state / "hands.db").mkdir()


class InboundTest(support.WithCore):
    def test_passes_clean_text_and_asks_the_judge(self):
        core = self.core(support.verdict("clean"))
        self.assertIsNone(gate_inbound("gm, how is $TINY doing"))
        self.assertEqual(asked(core), [("gm, how is $TINY doing", "inbound")])

    def test_holds_on_the_screen_without_asking_the_judge(self):
        core = self.core(support.verdict("clean"))
        held = gate_inbound("draw tiny naked")
        self.assertTrue(is_held(held, "sexual", "screen"), held)
        self.assertEqual(held.reason, 'contains "naked"')
        self.assertEqual(core.calls, [])

    def test_holds_on_the_judge_when_the_words_are_clean(self):
        self.core(support.verdict("prohibited", "sexual", "a request for suggestive art"))
        held = gate_inbound("draw tiny with nothing on")
        self.assertTrue(is_held(held, "sexual", "judge"), held)
        self.assertEqual(held.reason, "a request for suggestive art")

    def test_says_the_judge_held_it_when_the_judge_gave_no_reason(self):
        self.core(support.verdict("prohibited"))
        held = gate_inbound("gm")
        self.assertEqual((held.category, held.reason), ("unknown", "the judge held it"))


class InboundLexicalTest(Lexical):
    def test_does_not_hold_incoming_swearing(self):
        self.assertIsNone(gate_inbound("holy shit this is cool"))

    def test_works_with_no_judge_at_all(self):
        self.assertIsNone(gate_inbound("gm"))
        self.assertIsNotNone(gate_inbound("send nudes"))


class OutboundTest(support.WithCore):
    def test_hands_back_clean_text_with_no_fixes(self):
        self.core(support.verdict("clean"))
        result = gate_outbound("gm frens, code is at github.com/tinyhumansai")
        self.assertIsNone(result.held)
        self.assertEqual(result.text, "gm frens, code is at github.com/tinyhumansai")
        self.assertEqual(result.fixes, [])

    def test_cuts_a_link_that_is_not_on_the_allowlist_and_still_sends_the_rest(self):
        core = self.core(support.verdict("clean"))
        result = gate_outbound("claim here: https://claim-tiny.xyz/go fren")
        self.assertIsNone(result.held)
        self.assertEqual(result.text, "claim here: fren")
        self.assertEqual(result.fixes, ["cut 1 link(s) not on the allowlist"])
        # The judge sees what would actually go out.
        self.assertEqual(asked(core), [("claim here: fren", "outbound")])

    def test_fails_closed_when_the_judge_is_unreachable(self):
        self.core(support.verdict("clean"))
        (self.root / "state" / "core.url").write_text("http://127.0.0.1:9\n")
        result = gate_outbound("gm")
        self.assertTrue(is_held(result.held, "unknown", "judge"), result.held)

    def test_reads_the_serial_and_its_articles_under_the_narrative_profile(self):
        core = self.core(support.verdict("clean"))
        self.assertIsNone(gate_outbound("holy shit, the chart", {"surface": "narrative"}).held)
        self.assertIsNone(gate_outbound("holy shit, the chart", {"surface": "article"}).held)
        self.assertEqual([d for _, d in asked(core)], ["narrative", "article"])


class OutboundLexicalTest(Lexical):
    def test_refuses_a_draft_that_was_only_a_bad_link(self):
        result = gate_outbound("https://claim-tiny.xyz/go")
        self.assertTrue(is_held(result.held, "links", "links"), result.held)
        self.assertEqual(result.held.reason, "the draft was only a disallowed link")

    def test_holds_profanity_on_the_way_out(self):
        result = gate_outbound("this is fucking great")
        self.assertTrue(is_held(result.held, "profanity", "screen"), result.held)

    def test_accepts_extra_allowlist_entries(self):
        self.secrets("SAFETY_JUDGE=0\nLINK_ALLOWLIST=dexscreener.com/solana\n")
        result = gate_outbound("chart: https://dexscreener.com/solana/abc")
        self.assertIsNone(result.held)
        self.assertEqual(result.text, "chart: https://dexscreener.com/solana/abc")


class ScreenTest(Lexical):
    def test_is_the_lexicon_alone_for_scrubbing_context(self):
        self.assertTrue(gate.screen("gm", "inbound"))
        self.assertFalse(gate.screen("send nudes", "inbound"))
        self.assertTrue(gate.screen("holy shit", "inbound"))
        self.assertFalse(gate.screen("holy shit", "outbound"))


class CheckOutboundTest(Lexical):
    def test_allows_a_clean_draft_with_the_house_style_applied(self):
        self.assertEqual(
            check_outbound("gm — what are we building today?"),
            Verdict("allow", "gm, what are we building today?", ""),
        )

    def test_refuses_a_draft_over_the_limit_or_the_emoji_cap_so_the_model_rewrites_it(self):
        long = check_outbound("a" * 281)
        self.assertEqual(long.kind, "refuse")
        self.assertIn("281 characters, over the 280 limit", long.reason)
        emoji = check_outbound("gm 👀 frens 💛")
        self.assertEqual(emoji.kind, "refuse")
        self.assertIn("2 emoji", emoji.reason)
        self.assertIn("at most 1", emoji.reason)
        self.assertEqual(check_outbound("gm 👀", max_emoji=0).kind, "refuse")
        self.assertEqual(holds.list_holds(), [], "a refusal is not a hold")

    def test_counts_the_limit_in_utf16_units(self):
        self.assertEqual(check_outbound("🚀" + "a" * 279).kind, "refuse")
        self.assertEqual(check_outbound("🚀" + "a" * 278).kind, "allow")

    def test_takes_the_emoji_cap_from_the_settings(self):
        self.assertEqual(check_outbound("gm 👀 frens 💛").kind, "refuse")
        self.secrets("SAFETY_JUDGE=0\nMAX_EMOJI_PER_POST=2\n")
        self.assertEqual(check_outbound("gm 👀 frens 💛"), Verdict("allow", "gm 👀 frens 💛", ""))
        self.secrets("SAFETY_JUDGE=0\nMAX_EMOJI_PER_POST=lots\n")
        self.assertEqual(check_outbound("gm 👀 frens 💛").kind, "refuse", "an unreadable cap is the default")

    def test_holds_what_the_gate_holds_and_records_it_for_review(self):
        verdict = check_outbound("this is fucking great")
        self.assertEqual((verdict.kind, verdict.category, verdict.text), ("hold", "profanity", ""))
        self.assertIn("does not publish that (profanity)", verdict.reason)
        recorded = holds.list_holds()
        self.assertEqual(len(recorded), 1)
        self.assertEqual(verdict.hold_id, recorded[0]["id"])
        self.assertEqual(recorded[0]["kind"], "draft")
        self.assertEqual(recorded[0]["category"], "profanity")
        self.assertEqual(recorded[0]["direction"], "outbound")
        self.assertEqual(recorded[0]["text"], "this is fucking great")
        self.assertEqual(recorded[0]["context"]["surface"], "x")

    def test_refuses_a_draft_carrying_a_link_off_the_allowlist(self):
        verdict = check_outbound("claim here: https://claim-tiny.xyz/go fren")
        self.assertEqual(verdict.kind, "refuse")
        self.assertIn("not on the allowlist", verdict.reason)
        # The cut is still a hold of its own, for an operator to overturn.
        self.assertEqual([h["kind"] for h in holds.list_holds()], ["link"])

    def test_holds_a_draft_that_was_only_a_disallowed_link(self):
        verdict = check_outbound("https://claim-tiny.xyz/go")
        self.assertEqual((verdict.kind, verdict.category), ("hold", "links"))

    def test_unlinks_the_handle_being_replied_to(self):
        self.assertEqual(
            check_outbound("heyyy @rafaelitewrld shy peek returns", replying_to="rafaelitewrld"),
            Verdict("allow", "heyyy rafaelitewrld shy peek returns", ""),
        )

    def test_reads_the_serial_and_articles_under_their_own_profile(self):
        self.assertEqual(check_outbound("holy shit, the chart", surface="narrative").kind, "allow")
        self.assertEqual(check_outbound("holy shit, the chart", surface="article").kind, "allow")
        self.assertEqual(check_outbound("holy shit, the chart", surface="x").kind, "hold")
        self.assertEqual(check_outbound("send nudes", surface="narrative").kind, "hold")

    def test_records_the_context_and_the_kind_of_post_with_the_hold(self):
        verdict = check_outbound(
            "this is fucking great", kind="reply", replying_to="alice", context={"post_id": "10", "tool": "x_post"}
        )
        held = holds.get(verdict.hold_id)
        self.assertEqual(held["kind"], "draft")
        self.assertEqual(
            held["context"],
            {"surface": "x", "post_id": "10", "tool": "x_post", "post_kind": "reply", "replying_to": "alice"},
        )
        prompt = check_outbound("Tiny naked on a beach", kind="prompt")
        self.assertEqual(holds.get(prompt.hold_id)["kind"], "prompt")

    def test_refuses_a_surface_it_does_not_know(self):
        with self.assertRaises(gate.hand.HandError):
            check_outbound("gm", surface="myspace")


class CheckOutboundJudgedTest(support.WithCore):
    def test_asks_the_judge_last_with_what_would_go_out(self):
        core = self.core(support.verdict("clean"))
        self.assertEqual(check_outbound("gm — frens").text, "gm, frens")
        self.assertEqual(asked(core), [("gm, frens", "outbound")])

    def test_holds_when_the_judge_is_down_and_never_publishes(self):
        self.core({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "no model"}})
        verdict = check_outbound("gm frens")
        self.assertEqual((verdict.kind, verdict.category), ("hold", "unknown"))
        self.assertIn("no model", holds.get(verdict.hold_id)["reason"])

    def test_a_hold_the_judge_made_by_failing_says_so(self):
        # The tick sends such a draft again next beat (bin/lib/tick.py); a
        # draft the judge read and held it never does.
        self.core(support.error("model error: local-openai returned: provider stream ended before a completion signal"))
        self.assertTrue(check_outbound("gm frens").judge_failed)
        self.core(support.result({"response": 'eeting", "verdict": "clean"}'}))
        self.assertTrue(check_outbound("gm frens").judge_failed)

    def test_a_hold_from_a_real_verdict_is_not_a_judge_failure(self):
        self.core(support.verdict("prohibited", "violence", "a threat"))
        self.assertFalse(check_outbound("gm frens").judge_failed)
        self.core(support.verdict("prohibited"))  # no category: "unknown", but the judge did read it
        verdict = check_outbound("gm frens")
        self.assertEqual((verdict.kind, verdict.category, verdict.judge_failed), ("hold", "unknown", False))
        self.assertFalse(check_outbound("this is fucking great").judge_failed)
        self.assertFalse(check_outbound("gm frens with an allowed link github.com/tinyhumansai").judge_failed)

    def test_can_be_turned_off_to_the_lexicon_and_links_alone(self):
        core = self.core(support.verdict("prohibited", "sexual", "no"))
        for off in ("0", "false", "off", "no"):
            self.secrets(f"SAFETY_JUDGE={off}\n")
            self.assertEqual(check_outbound("gm frens").kind, "allow", off)
        self.assertEqual(core.calls, [])
        self.secrets("SAFETY_JUDGE=maybe\n")
        self.assertEqual(check_outbound("gm frens").kind, "hold", "only a clear off turns the judge off")


class InboundApiTest(Lexical):
    def test_allows_clean_text_and_holds_the_rest_with_a_record(self):
        self.assertEqual(inbound("gm frens"), Verdict("allow", "gm frens", ""))
        verdict = inbound("send nudes", context={"post_id": "10", "author_username": "alice"})
        self.assertEqual((verdict.kind, verdict.category, verdict.reason), ("hold", "sexual", 'contains "nudes"'))
        held = holds.get(verdict.hold_id)
        self.assertEqual((held["direction"], held["kind"], held["by"]), ("inbound", "message", "screen"))
        self.assertEqual(held["context"], x_context())

    def test_never_holds_incoming_swearing(self):
        self.assertEqual(inbound("holy shit this is cool").kind, "allow")


class TheGateRecordsWhatItHoldsTest(Lexical):
    def test_writes_an_inbound_hold_with_the_text_and_where_it_came_from(self):
        held = gate_inbound("send nudes", x_context())
        entry = holds.list_holds()[0]
        self.assertEqual(held.hold_id, entry["id"])
        self.assertEqual(entry["direction"], "inbound")
        self.assertEqual(entry["kind"], "message")
        self.assertEqual(entry["category"], "sexual")
        self.assertEqual(entry["by"], "screen")
        self.assertEqual(entry["text"], "send nudes")
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(entry["context"], x_context())

    def test_writes_a_task_hold_for_the_ad_hoc_endpoint(self):
        gate_inbound("send nudes", {"surface": "task"})
        self.assertEqual(holds.list_holds()[0]["kind"], "task")

    def test_writes_a_link_hold_when_a_draft_goes_out_with_links_cut(self):
        result = gate_outbound("see https://claim-tiny.xyz/go now", {"surface": "x", "post_id": "10"})
        self.assertIsNone(result.held)
        entry = holds.list_holds()[0]
        self.assertEqual(result.hold_id, entry["id"])
        self.assertEqual(entry["kind"], "link")
        self.assertEqual(entry["category"], "links")
        self.assertEqual(entry["by"], "links")
        self.assertEqual(entry["reason"], "claim-tiny.xyz is not on the link allowlist")
        self.assertEqual(entry["text"], "see https://claim-tiny.xyz/go now")
        self.assertEqual(
            entry["links"],
            [{"link": "https://claim-tiny.xyz/go", "reason": "claim-tiny.xyz is not on the link allowlist"}],
        )

    def test_writes_a_draft_hold_for_a_withheld_reply_and_a_prompt_hold_for_a_refused_picture(self):
        gate_outbound("this is fucking great", {"surface": "telegram", "chat_id": "1", "message_id": "5"})
        gate_outbound("Tiny naked on a beach", {"surface": "x", "kind": "prompt", "tool": "generate_image"})
        self.assertEqual([h["kind"] for h in holds.list_holds()], ["prompt", "draft"])

    def test_records_nothing_without_a_context_and_nothing_when_the_text_is_clean(self):
        gate_inbound("send nudes")
        gate_inbound("gm", {"surface": "x"})
        gate_outbound("gm", {"surface": "x"})
        self.assertEqual(holds.list_holds(), [])

    def test_never_lets_a_broken_log_turn_a_hold_into_anything_else(self):
        self.break_the_log()
        with self.assertLogs("teeny_hands.guard", "WARNING"):
            self.assertIsNotNone(gate_inbound("send nudes", {"surface": "x"}))
            verdict = check_outbound("this is fucking great")
        self.assertEqual((verdict.kind, verdict.hold_id), ("hold", None))


class ApprovedLinksReachTheGateTest(Lexical):
    def setUp(self):
        super().setUp()
        gate.refresh_approved_links()

    def test_reads_operator_approvals_alongside_the_built_in_list(self):
        holds.allow_link("claim-tiny.xyz", 1)
        result = gate_outbound("see https://claim-tiny.xyz/go", {"surface": "x"})
        self.assertEqual(result.text, "see https://claim-tiny.xyz/go")
        self.assertEqual(result.fixes, [])

    def test_caches_them_and_refreshes_on_demand(self):
        self.assertEqual(len(gate_outbound("https://claim-tiny.xyz/go ok", {"surface": "x"}).fixes), 1)
        holds.allow_link("claim-tiny.xyz", 1)
        # Still cached.
        self.assertEqual(len(gate_outbound("https://claim-tiny.xyz/go ok").fixes), 1)
        gate.refresh_approved_links()
        self.assertEqual(len(gate_outbound("https://claim-tiny.xyz/go ok").fixes), 0)

    def test_rereads_them_once_the_cache_is_stale(self):
        gate_outbound("x")
        holds.allow_link("claim-tiny.xyz", 1)
        with mock.patch.object(gate, "APPROVED_LINKS_TTL", 0.0):
            self.assertEqual(gate_outbound("https://claim-tiny.xyz/go ok").fixes, [])

    def test_keeps_the_last_approvals_when_the_log_cannot_be_read(self):
        holds.allow_link("claim-tiny.xyz", 1)
        gate_outbound("x")
        self.break_the_log()
        gate.refresh_approved_links()
        with self.assertLogs("teeny_hands.guard", "WARNING"):
            self.assertEqual(gate_outbound("https://claim-tiny.xyz/go ok").fixes, [])

    def test_keeps_each_checkout_s_approvals_apart(self):
        holds.allow_link("claim-tiny.xyz", 1)
        self.assertEqual(gate_outbound("https://claim-tiny.xyz/go ok").fixes, [])
        other = self.root / "other"
        (other / "secrets").mkdir(parents=True)
        (other / "secrets" / "teeny.env").write_text("SAFETY_JUDGE=0\n")
        os.environ["TEENY_ROOT"] = str(other)
        gate.hand._ENV_CACHE = None
        self.assertEqual(len(gate_outbound("https://claim-tiny.xyz/go ok").fixes), 1)
