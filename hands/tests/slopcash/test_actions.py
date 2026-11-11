"""hands/slopcash's operator verbs against the fakes: post-job's preview (no
sign-in, no write), its confirmed run (sign in, read the base, PUT the merged
manifest), the local refusals, slop.cash's refusals, DRY_RUN, and the setup
check and listing."""

from __future__ import annotations

import contextlib
import io
import json

from .support import PROJECT, SESSION_TOKEN, Scratch, manifest
from teeny_hands import hand
from teeny_hands.slopcash import actions

PUT = f"/api/v1/projects/{PROJECT}"
CHECK = f"/api/v1/project-setup/{PROJECT}/check"
SETUP_STATUS = {
    "projectId": PROJECT, "revision": 4, "status": "paused", "state": "action_required",
    "nextAction": {"kind": "install_slopbot", "message": "Slopbot is not installed on teenydroid/brain.",
                   "href": "https://github.com/apps/slopdotcash/installations/new"},
    "checks": {"authority": {"state": "denied"}, "installation": {"state": "missing"}, "scan": {"state": "queued"}},
}  # fmt: skip


class Quiet:
    """Runs a verb with stderr captured (the sign-in prompt), and checks
    nothing secret reached the answer or stderr."""

    def quiet(self, call, *args, **kwargs):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = call(*args, **kwargs)
        self.assert_no_secret(json.dumps(out), err.getvalue())
        return out

    def raises(self, kind, call, *args, **kwargs):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(kind) as caught:
            call(*args, **kwargs)
        self.assert_no_secret(str(caught.exception), json.dumps(getattr(caught.exception, "detail", {})), err.getvalue())
        return caught.exception


class PostJobTest(Scratch, Quiet):
    def edit(self, change: dict) -> str:
        path = self.root / "edit.json"
        path.write_text(json.dumps(change))
        return str(path)

    def joined(self) -> bool:
        return any(s.path == "/api/v1/points/join" for s in self.slop.seen)

    def published(self, revision: int = 4) -> None:
        project = {**manifest(), "revision": revision}
        self.slop.route("PUT", PUT, {"project": project, "setup": SETUP_STATUS,
                        "changes": {"presentation": ["headline"], "executable": ["contribution"], "financial": []}})  # fmt: skip

    # --- the preview ------------------------------------------------------------------------

    def test_the_preview_diffs_each_field_and_signs_in_to_nothing(self):
        before = self.a_project()
        out = self.quiet(actions.post_job, self.edit({"headline": "build the gate", "contribution": {"requiredTests": "t"}}), None, False)
        self.assertEqual(out["preview"], True)
        self.assertEqual((out["project"], out["revision"]), (PROJECT, 3))
        self.assertEqual(out["changes"], [
            {"field": "headline", "before": before["headline"], "after": "build the gate"},
            {"field": "contribution.requiredTests", "before": before["contribution"]["requiredTests"], "after": "t"},
        ])  # fmt: skip
        self.assertTrue(out["pauses_project"])
        self.assertTrue(out["queues_rescan"])
        self.assertEqual(self.idp.seen, [])
        self.assertEqual(self.slop.writes(), [])

    def test_a_presentation_edit_does_not_pause(self):
        self.a_project()
        out = self.quiet(actions.post_job, self.edit({"eyebrow": "a gate", "links": {"repository": "https://github.com/teenydroid/brain"}}), None, False)
        self.assertEqual([c["field"] for c in out["changes"]], ["eyebrow", "links"])
        self.assertFalse(out["pauses_project"])

    def test_dry_run_with_confirm_is_a_preview(self):
        self.configure(DRY_RUN="1")
        self.a_project()
        out = self.quiet(actions.post_job, self.edit({"headline": "x"}), None, True)
        self.assertEqual((out["preview"], out["dry_run"]), (True, True))
        self.assertEqual(self.idp.seen, [])
        self.assertEqual(self.slop.writes(), [])

    def test_no_change_is_said_and_nothing_is_sent(self):
        current = self.a_project()
        out = self.quiet(actions.post_job, self.edit({"headline": current["headline"]}), None, False)
        self.assertEqual(out["changes"], [])
        self.signin()
        out = self.quiet(actions.post_job, self.edit({"headline": current["headline"]}), None, True)
        self.assertEqual(out["unchanged"], True)
        self.assertEqual(self.slop.requests("PUT", PUT), [])

    # --- the confirmed run ------------------------------------------------------------------

    def test_confirm_signs_in_reads_the_base_and_puts_the_merged_manifest(self):
        self.signin(pending=1)
        answer = self.a_project()
        # The base is read only once signed in: before that, no project answers.
        self.slop.routes[0].when = lambda s: self.joined()
        self.published()
        out = self.quiet(actions.post_job, self.edit({"headline": "build the gate", "contribution": {"requiredTests": "t"}}),
                         "tighter tests", True)  # fmt: skip
        self.assertEqual(len(self.idp.requests("POST", "/v1/oauth/poll")), 2)
        sent = self.slop.requests("PUT", PUT)[0]
        self.assertEqual(sent.headers["cookie"], f"__Host-slop_points={SESSION_TOKEN}")
        self.assertEqual(sent.headers["origin"], self.slop.base)
        self.assertEqual(sent.headers["content-type"], "application/json")
        want = manifest()
        want["headline"] = "build the gate"
        want["contribution"]["requiredTests"] = "t"
        self.assertEqual(sent.json(), {"manifest": want, "expectedRevision": 3, "reason": "tighter tests"})
        self.assertEqual(answer["contribution"]["approvedScope"], sent.json()["manifest"]["contribution"]["approvedScope"])
        self.assertEqual((out["posted"], out["revision"], out["signed_in_as"]), (PROJECT, 4, "teenydroid"))
        self.assertEqual(out["changes"]["executable"], ["contribution"])
        self.assertEqual(out["next_action"]["kind"], "install_slopbot")
        self.assertEqual(out["setup_state"], "action_required")
        self.assertEqual(self.leaked(), [])

    def test_no_reason_sends_none(self):
        self.signin()
        self.a_project()
        self.published()
        self.quiet(actions.post_job, self.edit({"name": "Teeny's brain, rewritten"}), None, True)
        self.assertNotIn("reason", self.slop.requests("PUT", PUT)[0].json())

    def test_a_stale_revision_is_a_clean_error(self):
        self.signin()
        self.a_project()
        self.slop.route("PUT", PUT, {"error": "stale_revision", "message": "The project changed since you loaded it"}, status=409)
        error = self.raises(hand.HandError, actions.post_job, self.edit({"headline": "x"}), None, True)
        self.assertIn("stale_revision", str(error))
        self.assertIn("post-job again", str(error))

    def test_slopcash_refusals_name_their_code_and_what_to_do(self):
        self.signin()
        self.a_project()
        self.slop.route("PUT", PUT, {"error": "slopbot_required", "message": "Install Slopbot"}, status=409)
        error = self.raises(hand.HandError, actions.post_job, self.edit({"headline": "x"}), None, True)
        self.assertIn("slopbot_required", str(error))
        self.assertIn("installations/new", str(error))

    # --- local refusals -----------------------------------------------------------------------

    def test_owner_restricted_fields_are_refused_before_anything_is_sent(self):
        for field in ("steward", "authority", "terms", "status", "listingTier", "repositories", "skill", "reviewSkill"):
            error = self.raises(hand.Refused, actions.post_job, self.edit({"headline": "x", field: {}}), None, True)
            self.assertEqual((error.reason, error.detail["rule"]), ("owner_field_restricted", "owner_field_restricted"))
            self.assertEqual(error.detail["fields"], [field])
        self.assertEqual((self.slop.seen, self.idp.seen), ([], []))

    def test_fields_that_are_not_the_listing_are_refused(self):
        error = self.raises(hand.Refused, actions.post_job, self.edit({"reward": {}}), None, False)
        self.assertEqual((error.detail["rule"], error.detail["fields"]), ("not_editable", ["reward"]))
        error = self.raises(hand.Refused, actions.post_job, self.edit({"contribution": {"payout": "all"}}), None, False)
        self.assertEqual(error.detail["fields"], ["contribution.payout"])
        self.assertEqual(self.slop.seen, [])

    def test_bad_files_and_values_are_errors(self):
        for change in ({"headline": 5}, {"headline": ""}, {"contribution": "x"}, {"contribution": {"requiredTests": 1}},
                       {"links": {"repository": 3}}, {}, ["headline"]):  # fmt: skip
            self.raises(hand.HandError, actions.post_job, self.edit(change), None, False)
        self.raises(hand.HandError, actions.post_job, str(self.root / "missing.json"), None, False)
        (self.root / "bad.json").write_text("{not json")
        self.raises(hand.HandError, actions.post_job, str(self.root / "bad.json"), None, False)
        self.raises(hand.HandError, actions.post_job, self.edit({"headline": "x"}), "r" * 501, False)
        self.assertEqual(self.slop.seen, [])


class CheckTest(Scratch, Quiet):
    def test_without_confirm_it_says_what_it_would_do(self):
        out = self.quiet(actions.check, True, False)
        self.assertEqual((out["would_check"], out["request_scan"]), (PROJECT, True))
        self.assertEqual((self.slop.seen, self.idp.seen), ([], []))

    def test_dry_run_with_confirm_signs_in_to_nothing(self):
        self.configure(DRY_RUN="1")
        out = self.quiet(actions.check, False, True)
        self.assertEqual((out["would_check"], out["dry_run"]), (PROJECT, True))
        self.assertEqual(self.idp.seen, [])

    def test_confirm_signs_in_and_runs_the_check(self):
        self.signin()
        self.slop.route("POST", CHECK, {"setup": SETUP_STATUS})
        out = self.quiet(actions.check, True, True)
        sent = self.slop.requests("POST", CHECK)[0]
        self.assertEqual(sent.json(), {"requestScan": True})
        self.assertEqual(sent.headers["cookie"], f"__Host-slop_points={SESSION_TOKEN}")
        self.assertEqual((out["checked"], out["state"], out["status"]), (PROJECT, "action_required", "paused"))
        self.assertEqual(out["next_action"]["kind"], "install_slopbot")
        self.assertEqual(out["checks"], {"authority": "denied", "installation": "missing", "scan": "queued"})

    def test_without_scan_the_body_is_empty(self):
        self.signin()
        self.slop.route("POST", CHECK, {"setup": SETUP_STATUS})
        self.quiet(actions.check, False, True)
        self.assertEqual(self.slop.requests("POST", CHECK)[0].json(), {})


class SetupTest(Scratch, Quiet):
    def test_signs_in_and_lists_the_accounts_projects(self):
        self.signin()
        self.slop.route("GET", "/api/v1/project-setup", {"projects": [
            {"id": PROJECT, "status": "paused", "revision": 3}], "applications": []})  # fmt: skip
        out = self.quiet(actions.setup)
        self.assertEqual(out["projects"][0]["id"], PROJECT)
        self.assertEqual((out["applications"], out["signed_in_as"]), ([], "teenydroid"))
        self.assertEqual(self.slop.requests("GET", "/api/v1/project-setup")[0].headers["cookie"], f"__Host-slop_points={SESSION_TOKEN}")
