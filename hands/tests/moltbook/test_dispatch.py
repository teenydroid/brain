"""The dispatch queue (hands/moltbook dispatch): the mind files one exact
Moltbook reply, comment or post, and code sends it, through the same comment
and post paths every other write takes. Filing checks what can be checked
before Moltbook hears of it; the run sends what is due, verbatim, one a run,
and settles each for good or for later. Against the fake Moltbook."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest import mock

from .support import DAY, HOUR, MINUTE, T0, Scratch
from teeny_hands import hand
from teeny_hands.moltbook import cli, dispatch, rules

POST = "8c95feab-bda8-4bea-b8ee-5137d5e97337"
PARENT = "8738e440-ea37-4498-8a78-fd382e154820"
OTHER = "3ba2deab-1fa2-4b00-9f19-4d27a448c746"
MINE = "d85ed925-0000-4000-8000-000000000001"


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rows() -> list[dict]:
    conn = dispatch.db()
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {dispatch.TABLE} ORDER BY id")]
    finally:
        conn.close()


def ledger_comment(comment_id: str, post_id: str, parent_id: str | None, at: float) -> None:
    conn = rules.db()
    conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, parent_id, at, status) VALUES (?, ?, ?, ?, 'verified')",
                 (comment_id, post_id, parent_id, at))
    conn.close()


def own_post(post_id: str) -> None:
    conn = rules.db()
    conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES (?, ?, 'general', 't', 'verified')",
                 (post_id, T0 - 2 * DAY))
    conn.close()


class Case(Scratch):
    def reply(self, **more) -> dict:
        return {"post_id": POST, "parent_id": PARENT, "body": "the gate is right, and the 73 takes are what it costs",
                "expires_at": iso(T0 + 2 * DAY), "why": "demonofmaxwell asked where the gate goes", **more}

    def refused(self, body, rule):
        with self.assertRaises(hand.Refused) as caught:
            dispatch.file(body)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        return caught.exception

    def thread(self, comments: list) -> None:
        self.a_post(POST, author="teenydroid", comments=comments)

    def stranger(self, cid: str = PARENT, author: str = "demonofmaxwell", replies: list | None = None) -> dict:
        return {"id": cid, "author": {"name": author}, "replies": replies or []}


class FileTest(Case):
    def test_a_reply_is_filed_pending_with_its_expiry_and_listed(self):
        out = dispatch.file(self.reply())
        self.assertEqual(out["filed"]["status"], "pending")
        [row] = rows()
        self.assertEqual((row["kind"], row["post_id"], row["parent_id"], row["status"]), ("comment", POST, PARENT, "pending"))
        self.assertEqual(row["expires_at"], T0 + 2 * DAY)
        self.assertEqual(row["body"], self.reply()["body"])
        listed = dispatch.listing()["dispatches"]
        self.assertEqual([(d["id"], d["status"], d["parent_id"], d["why"]) for d in listed],
                         [(row["id"], "pending", PARENT, "demonofmaxwell asked where the gate goes")])
        self.assertEqual(listed[0]["expires_at"], iso(T0 + 2 * DAY))
        self.assertEqual(self.mb.writes(), [])

    def test_ids_must_be_whole_uuids(self):
        self.refused(self.reply(parent_id="8738e440"), "short_id")
        self.refused(self.reply(post_id="8c95feab-bda8"), "short_id")
        self.refused(self.reply(parent_id="not-a-uuid-at-all"), "not_uuid")
        self.assertEqual(rows(), [])

    def test_the_expiry_is_in_the_future_and_at_most_seven_days_out(self):
        self.refused(self.reply(expires_at=iso(T0 - MINUTE)), "expires_at")
        self.refused(self.reply(expires_at=iso(T0 + 7 * DAY + HOUR)), "expires_at")
        self.refused(self.reply(expires_at="next tuesday"), "expires_at")
        dispatch.file(self.reply(expires_at=iso(T0 + 7 * DAY)))
        self.assertEqual(len(rows()), 1)

    def test_a_reason_is_required_and_unknown_fields_refused(self):
        body = self.reply()
        del body["why"]
        self.refused(body, "shape")
        self.refused(self.reply(note="extra"), "shape")
        self.refused(self.reply(body=""), "shape")
        self.assertEqual(rows(), [])

    def test_a_body_over_the_guards_limit_is_refused(self):
        self.refused(self.reply(body="x" * 10_001), "guard")
        self.assertEqual(rows(), [])

    def test_one_dispatch_per_target(self):
        dispatch.file(self.reply())
        self.refused(self.reply(body="the same parent in other words"), "duplicate")
        dispatch.file(self.reply(parent_id=OTHER))
        self.assertEqual(len(rows()), 2)

    def test_a_parent_teeny_already_answered_is_refused(self):
        ledger_comment(MINE, POST, PARENT, T0 - HOUR)
        self.refused(self.reply(), "once_per_parent")

    def test_a_reply_under_teenys_own_comment_is_refused(self):
        ledger_comment(MINE, POST, None, T0 - HOUR)
        self.refused(self.reply(parent_id=MINE), "own_comment")

    def test_a_top_level_comment_on_teenys_own_post_is_refused(self):
        own_post(POST)
        self.refused(self.reply(parent_id=None), "own_post")

    def test_a_second_top_level_comment_on_one_post_is_refused(self):
        ledger_comment(MINE, POST, None, T0 - HOUR)
        self.refused(self.reply(parent_id=None), "once_per_post")

    def test_at_most_ten_pending_and_zero_is_off(self):
        for n in range(10):
            dispatch.file(self.reply(parent_id=f"{n:08x}-0000-4000-8000-000000000000"))
        self.refused(self.reply(), "max_pending")
        self.configure(MOLTBOOK_DISPATCH_MAX_PENDING="0")
        self.refused(self.reply(parent_id=OTHER), "off")

    def test_dry_run_files_nothing(self):
        self.configure(DRY_RUN="1")
        out = dispatch.file(self.reply())
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_file"]["parent_id"], PARENT)
        self.assertEqual(rows(), [])

    def test_a_post_is_filed_with_its_title(self):
        out = dispatch.file({"submolt": "builds", "title": "my checker was graded by the agent who broke it",
                             "body": "u/forgeloop ran 23 frames", "format": "answer",
                             "expires_at": iso(T0 + DAY), "why": "the reply lane under forgeloop is spent"})
        self.assertEqual(out["filed"]["kind"], "post")
        [row] = rows()
        self.assertEqual((row["kind"], row["submolt"], row["format"], row["post_id"]), ("post", "builds", "answer", None))
        self.refused({"title": "My checker was graded by the agent who broke it", "body": "again",
                      "expires_at": iso(T0 + DAY), "why": "twice"}, "duplicate")


class RunTest(Case):
    def test_a_due_reply_goes_out_verbatim_through_the_comment_path(self):
        dispatch.file(self.reply())
        self.thread([self.stranger()])
        self.created("comment", "c-new", path=f"/posts/{POST}/comments")
        out = dispatch.run()
        self.assertEqual(out["sent"], 1)
        [sent] = self.mb.requests("POST", f"/posts/{POST}/comments")
        self.assertEqual(sent.json(), {"content": self.reply()["body"], "parent_id": PARENT})
        [row] = rows()
        self.assertEqual((row["status"], row["result"], row["attempts"]), ("sent", "c-new", 1))
        conn = rules.db()
        [comment] = [dict(r) for r in conn.execute("SELECT * FROM moltbook_comments")]
        conn.close()
        self.assertEqual((comment["parent_id"], json.loads(comment["detail"])["writer"]), (PARENT, "mind"))
        # Spent: the next run has nothing to do and sends nothing.
        self.at(T0 + HOUR)
        self.assertEqual(dispatch.run()["sent"], 0)
        self.assertEqual(len(self.mb.requests("POST", f"/posts/{POST}/comments")), 1)

    def test_one_send_a_run_oldest_first(self):
        dispatch.file(self.reply())
        self.at(T0 + 1)
        dispatch.file(self.reply(parent_id=OTHER, body="second"))
        self.thread([self.stranger(), self.stranger(OTHER, "evil_robot_jas")])
        self.created("comment", "c-1", path=f"/posts/{POST}/comments")
        dispatch.run()
        self.assertEqual([r.json()["parent_id"] for r in self.mb.requests("POST", f"/posts/{POST}/comments")], [PARENT])
        self.assertEqual([r["status"] for r in rows()], ["sent", "pending"])
        self.at(T0 + 5 * MINUTE)
        dispatch.run()
        self.assertEqual([r["status"] for r in rows()], ["sent", "sent"])

    def test_a_teenydroid_reply_already_under_the_parent_settles_it_unsent(self):
        dispatch.file(self.reply())
        self.thread([self.stranger(replies=[{"id": MINE, "author": {"name": "teenydroid"}, "replies": []}])])
        out = dispatch.run()
        self.assertEqual(out["sent"], 0)
        self.assertEqual(self.mb.writes(), [])
        [row] = rows()
        self.assertEqual((row["status"], row["result"]), ("answered", MINE))

    def test_a_reply_the_ledger_knows_settles_it_without_a_read(self):
        dispatch.file(self.reply())
        ledger_comment(MINE, POST, PARENT, T0 + MINUTE)
        self.at(T0 + 2 * MINUTE)
        dispatch.run()
        self.assertEqual(self.mb.requests(), [])
        self.assertEqual(rows()[0]["status"], "answered")

    def test_an_expired_dispatch_is_marked_and_never_sent(self):
        dispatch.file(self.reply(expires_at=iso(T0 + HOUR)))
        self.thread([self.stranger()])
        self.at(T0 + HOUR + 1)
        dispatch.run()
        self.assertEqual(self.mb.writes(), [])
        self.assertEqual(rows()[0]["status"], "expired")

    def test_a_terminal_refusal_is_never_retried(self):
        dispatch.file(self.reply())
        self.thread([self.stranger(OTHER, "someone_else")])  # the parent left the newest 100
        dispatch.run()
        [row] = rows()
        self.assertEqual((row["status"], json.loads(row["last"])["rule"]), ("refused", "unknown_parent"))
        self.at(T0 + HOUR)
        dispatch.run()
        self.assertEqual(rows()[0]["attempts"], 1)
        self.assertEqual(self.mb.writes(), [])

    def test_a_pacing_refusal_waits_and_goes_out_later(self):
        dispatch.file(self.reply())
        ledger_comment("c-just-now", "f0000000-0000-4000-8000-000000000000", None, T0 - 5)
        self.thread([self.stranger()])
        self.created("comment", "c-later", path=f"/posts/{POST}/comments")
        dispatch.run()
        [row] = rows()
        self.assertEqual((row["status"], json.loads(row["last"])["rule"]), ("pending", "rate_limit"))
        self.assertGreater(row["retry_at"], T0)
        self.assertEqual(self.mb.writes(), [])
        self.at(row["retry_at"])
        dispatch.run()
        self.assertEqual(rows()[0]["status"], "sent")

    def test_a_spent_daily_cap_is_retried_later_not_dropped(self):
        self.configure(MOLTBOOK_MAX_COMMENTS_PER_DAY="1")
        ledger_comment("c-earlier", "f0000000-0000-4000-8000-000000000000", None, T0 - HOUR)
        dispatch.file(self.reply())
        self.thread([self.stranger()])
        dispatch.run()
        [row] = rows()
        self.assertEqual((row["status"], json.loads(row["last"])["rule"]), ("pending", "daily_cap"))
        self.assertGreaterEqual(row["retry_at"], T0 + 30 * MINUTE)

    def test_dry_run_sends_nothing_and_settles_nothing(self):
        dispatch.file(self.reply())
        self.configure(DRY_RUN="1")
        self.thread([self.stranger()])
        out = dispatch.run()
        self.assertEqual(out["would_send"]["would_comment"]["parent_id"], PARENT)
        self.assertEqual(self.mb.writes(), [])
        self.assertEqual((rows()[0]["status"], rows()[0]["attempts"]), ("pending", 0))
        # The beat does not start a rehearsal run: it would judge the same words every beat.
        self.assertFalse(dispatch.due())

    def test_a_post_goes_out_through_the_post_path(self):
        dispatch.file({"submolt": "general", "title": "my checker was graded by the agent who broke it",
                       "body": "u/forgeloop ran 23 frames", "format": "answer",
                       "expires_at": iso(T0 + DAY), "why": "the reply lane is spent"})
        self.created("post", "p-new")
        dispatch.run()
        [sent] = self.mb.requests("POST", "/posts")
        self.assertEqual(sent.json()["title"], "my checker was graded by the agent who broke it")
        self.assertEqual((rows()[0]["status"], rows()[0]["result"]), ("sent", "p-new"))

    def test_due_says_whether_a_run_has_anything_to_do(self):
        self.assertFalse(dispatch.due())
        dispatch.file(self.reply())
        self.assertTrue(dispatch.due())
        self.thread([self.stranger()])
        self.created("comment", "c-new", path=f"/posts/{POST}/comments")
        dispatch.run()
        self.assertFalse(dispatch.due())


class SeedTest(Case):
    """The one-off dispatch blocks moltbook-threads.md carried until
    2026-11-26, moved here so nothing they promised is lost: each is filed
    once, at the first run, only while unexpired and only for a post this box
    knows is Teeny's own."""

    SEEDS = (
        {"key": "seed-a", "post_id": POST, "parent_id": PARENT, "body": "seeded words", "expires_at": "2026-09-16T00:00:00Z",
         "why": "from the playbook"},
        {"key": "seed-b", "post_id": POST, "parent_id": OTHER, "body": "other words", "expires_at": "2026-09-13T00:00:00Z",
         "why": "already expired"},
    )

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(dispatch, "SEEDS", self.SEEDS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_seeds_wait_for_teenys_own_post_on_this_box(self):
        self.assertFalse(dispatch.due())
        dispatch.run()
        self.assertEqual(rows(), [])

    def test_an_unexpired_seed_is_filed_once_and_sent(self):
        own_post(POST)
        self.assertTrue(dispatch.due())
        self.thread([self.stranger()])
        self.created("comment", "c-seeded", path=f"/posts/{POST}/comments")
        dispatch.run()
        dispatch.run()
        [row] = rows()
        self.assertEqual((row["seed"], row["status"], row["body"]), ("seed-a", "sent", "seeded words"))
        self.assertEqual(len(self.mb.requests("POST", f"/posts/{POST}/comments")), 1)

    def test_a_seed_whose_parent_is_already_answered_is_filed_answered(self):
        own_post(POST)
        ledger_comment(MINE, POST, PARENT, T0 - HOUR)
        dispatch.run()
        [row] = rows()
        self.assertEqual((row["seed"], row["status"], row["result"]), ("seed-a", "answered", MINE))
        self.assertEqual(self.mb.writes(), [])

    def test_a_post_seed_waits_for_a_box_with_posts_of_its_own_then_goes_out_once(self):
        post = {"key": "seed-post", "submolt": "general", "title": "my checker was graded by 23 frames",
                "body": "the corpus ships as a test", "format": "finding", "expires_at": "2026-09-15T00:00:00Z",
                "why": "the receipt as a post"}
        with mock.patch.object(dispatch, "SEEDS", (post,)):
            self.assertFalse(dispatch.due())
            own_post("f1111111-0000-4000-8000-000000000000")
            self.assertTrue(dispatch.due())
            self.created("post", "p-seeded")
            dispatch.run()
            dispatch.run()
        [row] = rows()
        self.assertEqual((row["seed"], row["kind"], row["status"], row["result"]), ("seed-post", "post", "sent", "p-seeded"))
        [sent] = self.mb.requests("POST", "/posts")
        self.assertEqual(sent.json()["title"], "my checker was graded by 23 frames")

    def test_the_shipped_seeds_are_well_formed(self):
        from teeny_hands.moltbook import dispatch_seeds

        keys = [s["key"] for s in dispatch_seeds.SEEDS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("post", {"post" if "title" in s else "comment" for s in dispatch_seeds.SEEDS})
        for seed in dispatch_seeds.SEEDS:
            if "title" in seed:
                self.assertTrue(seed["title"].strip() and seed["submolt"] and len(seed["title"]) <= 300)
            else:
                dispatch.uuid(seed["post_id"], "post_id")
                dispatch.uuid(seed["parent_id"], "parent_id")
            self.assertLessEqual(len(seed["body"]), 10_000)
            self.assertTrue(seed["why"].strip())
            self.assertLessEqual(dispatch.when(seed["expires_at"]), dispatch.when("2026-12-03T00:00:00Z"))


class CliTest(Case):
    def test_the_verbs(self):
        os.environ["TEENY_HANDS_DEBUG"] = "1"
        with mock.patch.object(hand, "read_body", return_value=self.reply()):
            self.assertEqual(cli.main(["dispatch", "file"]), 0)
        self.assertEqual(cli.main(["dispatch", "list"]), 0)
        self.assertEqual(cli.main(["dispatch", "nope"]), 1)
        self.assertEqual(len(rows()), 1)
