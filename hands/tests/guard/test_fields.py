"""The long-form surfaces: per-surface limits and emoji caps, the multi-field
check (`gate.check_fields`, `hands/guard check --fields`), and the links the
built-in allowlist gained for GitHub, Moltbook, mail and wallet receipts."""

from __future__ import annotations

import json

from . import support
from .test_cli import Cli
from teeny_hands.guard import gate, holds
from teeny_hands.guard.gate import check_fields, check_outbound, default_max_emoji, field_limit
from teeny_hands.guard.links import DEFAULT_LINK_ALLOWLIST, filter_links, parse_allowlist


class SurfacesTest(support.Scratch):
    def test_the_long_form_surfaces_are_hold_surfaces(self):
        for surface in ("github", "moltbook", "mail"):
            self.assertIn(surface, holds.SURFACES)

    def test_limits_per_surface_and_field(self):
        self.assertEqual(field_limit("x", "text"), 280)
        # Telegram's own message limit; a surface with none named has X's.
        self.assertEqual(field_limit("telegram", "text"), 4096)
        self.assertEqual(field_limit("task", "text"), 280)
        self.assertEqual((field_limit("moltbook", "title"), field_limit("moltbook", "body")), (300, 10000))
        self.assertEqual((field_limit("github", "title"), field_limit("github", "body")), (256, 20000))
        self.assertEqual((field_limit("mail", "subject"), field_limit("mail", "body")), (200, 10000))
        # A long-form surface's text is its body.
        self.assertEqual(field_limit("github", "text"), 20000)

    def test_an_unknown_field_is_an_error(self):
        with self.assertRaises(Exception):
            field_limit("x", "title")
        with self.assertRaises(Exception):
            field_limit("mail", "title")

    def test_emoji_default_is_per_surface(self):
        self.assertEqual(default_max_emoji("x"), 1)
        self.assertEqual(default_max_emoji("github"), gate.LONG_FORM_MAX_EMOJI)
        self.secrets("SAFETY_JUDGE=0\nMAX_EMOJI_PER_POST=0\n")
        self.assertEqual(default_max_emoji("x"), 0)
        self.assertEqual(default_max_emoji("moltbook"), gate.LONG_FORM_MAX_EMOJI)


class SingleTextTest(support.Scratch):
    def test_x_is_unchanged(self):
        self.assertEqual(check_outbound("a" * 281).kind, "refuse")
        self.assertIn("over the 280 limit", check_outbound("a" * 281).reason)
        self.assertEqual(check_outbound("gm 🚀 🔥").kind, "refuse")

    def test_a_long_form_surface_takes_its_body_limit_and_a_few_emoji(self):
        verdict = check_outbound("word " * 100, surface="github")
        self.assertEqual(verdict.kind, "allow")
        self.assertEqual(check_outbound("shipped 🚀 🔥", surface="github").kind, "allow")
        self.assertEqual(check_outbound("a" * 20001, surface="github").kind, "refuse")

    def test_a_long_form_surface_keeps_a_leading_handle(self):
        self.assertEqual(check_outbound("@alice thanks, merged", surface="github").text, "@alice thanks, merged")
        self.assertEqual(check_outbound("@alice thanks, merged").text, "thanks, merged")


class CheckFieldsTest(support.Scratch):
    def test_allows_and_rewrites_each_field(self):
        verdict = check_fields({"title": "fix: a typo — in the docs", "body": "thanks “all”"}, surface="github")
        self.assertEqual(verdict.kind, "allow")
        self.assertEqual(verdict.fields, {"title": "fix: a typo, in the docs", "body": 'thanks "all"'})
        self.assertEqual(holds.list_holds(), [])

    def test_refuses_a_field_over_its_limit_and_names_it(self):
        verdict = check_fields({"title": "t" * 257, "body": "ok"}, surface="github")
        self.assertEqual((verdict.kind, verdict.field), ("refuse", "title"))
        self.assertIn("the title is 257 characters, over the 256 limit", verdict.reason)
        verdict = check_fields({"subject": "hi", "body": "b" * 10001}, surface="mail")
        self.assertEqual((verdict.kind, verdict.field), ("refuse", "body"))

    def test_refuses_too_many_emoji_in_a_field(self):
        verdict = check_fields({"title": "🚀🔥💛👀", "body": "ok"}, surface="moltbook")
        self.assertEqual((verdict.kind, verdict.field), ("refuse", "title"))
        self.assertEqual(check_fields({"title": "🚀🔥💛👀", "body": "ok"}, surface="moltbook", max_emoji=4).kind, "allow")

    def test_refuses_an_empty_field(self):
        verdict = check_fields({"title": "  ", "body": "ok"}, surface="github")
        self.assertEqual((verdict.kind, verdict.field), ("refuse", "title"))

    def test_one_hold_when_any_field_is_held(self):
        verdict = check_fields({"title": "a fine title", "body": "this is fucking great"}, surface="github", kind="pr")
        self.assertEqual((verdict.kind, verdict.field, verdict.category), ("hold", "body", "profanity"))
        rows = holds.list_holds()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], verdict.hold_id)
        self.assertEqual(rows[0]["kind"], "draft")
        self.assertEqual(
            rows[0]["context"],
            {"surface": "github", "post_kind": "pr", "fields": ["title", "body"], "field": "body"},
        )
        self.assertEqual(json.loads(rows[0]["text"]), {"title": "a fine title", "body": "this is fucking great"})

    def test_a_link_off_the_list_is_refused_with_one_link_hold(self):
        verdict = check_fields({"title": "see https://evil.example", "body": "and https://evil.example/x"}, surface="github")
        self.assertEqual((verdict.kind, verdict.field, verdict.category), ("refuse", "title", "links"))
        rows = holds.list_holds()
        self.assertEqual([r["kind"] for r in rows], ["link"])
        self.assertEqual(len(rows[0]["links"]), 2)

    def test_teenys_own_links_go_out(self):
        verdict = check_fields(
            {"title": "new repo", "body": "https://github.com/teenydroid/hello and https://www.moltbook.com/u/teeny"},
            surface="github",
        )
        self.assertEqual(verdict.kind, "allow")

    def test_unknown_fields_and_surfaces_are_errors(self):
        with self.assertRaises(Exception):
            check_fields({"title": "t"}, surface="x")
        with self.assertRaises(Exception):
            check_fields({"title": "t"}, surface="myspace")
        with self.assertRaises(Exception):
            check_fields({}, surface="github")


class FieldsJudgeTest(support.WithCore):
    def test_asks_the_judge_once_per_field(self):
        core = self.core(support.verdict("clean"))
        verdict = check_fields({"title": "hello", "body": "world"}, surface="moltbook")
        self.assertEqual(verdict.kind, "allow")
        self.assertEqual(len(core.calls), 2)

    def test_the_judge_holding_one_field_holds_the_draft(self):
        self.core(support.verdict("clean"), support.verdict("prohibited", "hate", "no"))
        verdict = check_fields({"subject": "hello", "body": "world"}, surface="mail")
        self.assertEqual((verdict.kind, verdict.field, verdict.category), ("hold", "body", "hate"))
        self.assertEqual(len(holds.list_holds()), 1)

    def test_a_hold_the_judge_made_by_failing_says_so(self):
        self.core(support.verdict("clean"),
                  support.error("model error: local-openai returned: provider stream ended before a completion signal"))
        verdict = check_fields({"subject": "hello", "body": "world"}, surface="mail")
        self.assertEqual((verdict.kind, verdict.field, verdict.category, verdict.judge_failed),
                         ("hold", "body", "unknown", True))

    def test_a_hold_from_a_real_verdict_is_not_a_judge_failure(self):
        self.core(support.verdict("prohibited", "hate", "no"))
        self.assertFalse(check_fields({"body": "world"}, surface="mail").judge_failed)
        self.assertFalse(check_fields({"body": "this is fucking great"}, surface="mail").judge_failed)

    def test_operator_text_can_go_without_the_judge_but_not_without_the_lexicon(self):
        # The judge's model is down: every judged draft is held, and an
        # operator script's fixed sentences still go out.
        dead = {"jsonrpc": "2.0", "id": 1, "error": {"message": "local-openai returned HTTP 401 (invalid_api_key)"}}
        core = self.core(dead)
        self.assertEqual(check_fields({"title": "t", "body": "b"}, surface="github").kind, "hold")
        asked = len(core.calls)
        verdict = check_fields({"title": "t", "body": "b"}, surface="github", judge=False)
        self.assertEqual((verdict.kind, verdict.fields), ("allow", {"title": "t", "body": "b"}))
        self.assertEqual(len(core.calls), asked)
        verdict = check_fields({"title": "t", "body": "this is fucking great"}, surface="github", judge=False)
        self.assertEqual((verdict.kind, verdict.category), ("hold", "profanity"))


class AllowlistTest(support.Scratch):
    def test_act_links_are_on_the_built_in_list(self):
        entries = parse_allowlist(DEFAULT_LINK_ALLOWLIST)
        for link in (
            "https://github.com/teenydroid/repo",
            "https://moltbook.com/post/1",
            "https://www.moltbook.com/u/teeny",
            "https://agentmail.to",
            "https://solscan.io/tx/abc",
        ):
            self.assertEqual(filter_links(link, entries).removed, [], link)
        # Somebody else's GitHub is still off it.
        self.assertEqual(len(filter_links("https://github.com/evil/repo", entries).removed), 1)


class FieldsCliTest(Cli):
    def test_screens_each_field(self):
        code, out = self.guard(
            "check", "--fields", "title,body", body={"title": "fix — typo", "body": "small fix", "surface": "github"}
        )
        self.assertEqual((code, out), (0, {"ok": True, "fields": {"title": "fix, typo", "body": "small fix"}}))

    def test_a_hold_names_the_field(self):
        code, out = self.guard(
            "check", "--fields", "title,body", body={"title": "send nudes", "body": "hi", "surface": "moltbook"}
        )
        self.assertEqual(code, 2)
        self.assertEqual((out["refused"], out["field"], out["category"]), ("held", "title", "sexual"))
        self.assertEqual(len(holds.list_holds()), 1)

    def test_a_refusal_names_the_field(self):
        code, out = self.guard(
            "check", "--fields", "subject,body", body={"subject": "s" * 201, "body": "hi", "surface": "mail"}
        )
        self.assertEqual((code, out["rewrite"], out["field"]), (2, True, "subject"))

    def test_bad_bodies_are_errors(self):
        for argv, body in (
            (["title,body"], {"title": "t", "surface": "github"}),
            (["title,body"], {"title": "t", "body": "b", "text": "x", "surface": "github"}),
            (["title,title"], {"title": "t", "surface": "github"}),
            ([","], {"surface": "github"}),
            (["title"], {"title": "t", "surface": "x"}),
        ):
            code, out = self.guard("check", "--fields", *argv, body=body)
            self.assertEqual((code, out["ok"]), (1, False), (argv, body))

    def test_text_check_is_unchanged(self):
        self.assertEqual(self.guard("check", body={"text": "hello world"}), (0, {"ok": True, "text": "hello world"}))


class RejectionTest(support.Scratch):
    """docs/roadmap.md, P4: after a rejection (the caller says so in the
    context), a draft that names anybody is held for a person, category
    `rejection`, before the judge is asked; without it nothing changes."""

    CONTEXT = {"repo": "riverlee/tinyfmt", "number": 41, "rejection": {"people": ["riverlee"]}}

    def test_names_a_person(self):
        self.assertTrue(gate.names_a_person("thanks @kdavis", []))
        self.assertTrue(gate.names_a_person("RiverLee should know better", ["riverlee"]))
        self.assertFalse(gate.names_a_person("understood, thanks for looking", ["riverlee"]))
        self.assertFalse(gate.names_a_person("mail me at teeny@agentmail.to", []))
        self.assertFalse(gate.names_a_person("the riverlee-fork branch", ["riverlee"]))

    def test_a_draft_naming_someone_after_a_rejection_is_held(self):
        got = check_fields({"body": "riverlee is just gatekeeping"}, surface="github", kind="comment",
                           context=dict(self.CONTEXT), judge=False)
        self.assertEqual((got.kind, got.category, got.field), ("hold", "rejection", "body"))
        (held,) = holds.list_holds()
        self.assertEqual((held["category"], held["by"]), ("rejection", "screen"))

    def test_a_gracious_draft_after_a_rejection_passes(self):
        got = check_fields({"body": "understood, thanks for taking a look"}, surface="github", kind="comment",
                           context=dict(self.CONTEXT), judge=False)
        self.assertEqual(got.kind, "allow")

    def test_without_a_rejection_naming_people_is_fine(self):
        got = check_fields({"body": "thanks @riverlee, merged"}, surface="github", kind="comment",
                           context={"repo": "riverlee/tinyfmt", "number": 41}, judge=False)
        self.assertEqual(got.kind, "allow")
