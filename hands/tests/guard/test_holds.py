"""Port of the hold log's contract (`teeny-store/src/holds_tests.rs`) against
state/hands.db, and of `review_tests.rs`'s `review_hold` cases: what an
operator's decision on a hold does."""

from __future__ import annotations

import threading
import time
from unittest import mock

from . import support
from teeny_hands import hand
from teeny_hands.guard import holds


def x_context():
    return {"surface": "x", "post_id": "10", "author_username": "alice"}


def hold(text="draw yourself naked", **overrides):
    entry = dict(
        direction="inbound",
        kind="message",
        context=x_context(),
        category="sexual",
        reason='contains "naked"',
        by="screen",
        text=text,
        links=None,
    )
    entry.update(overrides)
    return holds.record(**entry)


def draft_hold():
    return hold("gm frens", direction="outbound", kind="draft")


class HoldLogContractTest(support.Scratch):
    def test_keeps_the_contract(self):
        first = hold("one")
        self.assertIsInstance(first["id"], int)
        self.assertEqual(first["status"], "pending")
        self.assertIsNone(first["decided_at"])
        self.assertEqual(first["context"], x_context())
        second = hold(
            "two",
            kind="link",
            direction="outbound",
            by="links",
            links=[{"link": "https://x.example", "reason": "not allowlisted"}],
        )

        self.assertEqual(holds.get(first["id"]), first)
        self.assertIsNone(holds.get(999))

        # Newest first, by status, and limited.
        everything = holds.list_holds()
        self.assertEqual([h["text"] for h in everything], ["two", "one"])
        self.assertEqual(everything[0]["links"], second["links"])
        self.assertEqual(len(holds.list_holds(limit=1)), 1)
        self.assertEqual(len(holds.list_holds(limit=0)), 1, "a limit is at least one")

        decided = holds.decide(first["id"], "approved", note="fine", effect="requeued")
        self.assertEqual(decided["status"], "approved")
        self.assertIsNotNone(decided["decided_at"])
        self.assertEqual(decided["note"], "fine")
        self.assertEqual(decided["effect"], "requeued")
        self.assertIsNone(holds.decide(999, "rejected"))

        self.assertEqual([h["text"] for h in holds.list_holds(status="pending")], ["two"])
        self.assertEqual(holds.counts(), {"pending": 1, "approved": 1, "rejected": 0})

        # An approved host is allowed everywhere the gate runs, once.
        self.assertEqual(holds.allowed_links(), [])
        holds.allow_link("x.example", second["id"])
        holds.allow_link("x.example", first["id"])
        holds.allow_link("*.y.example", second["id"])
        self.assertEqual(sorted(holds.allowed_links()), ["*.y.example", "x.example"])

        # A decision claims the hold first, once.
        third = hold("three")
        claimed = holds.claim_release(third["id"])
        self.assertEqual((claimed["status"], claimed["text"]), ("releasing", "three"))
        self.assertIsNone(holds.claim_release(third["id"]), "already claimed")
        self.assertIsNone(holds.claim_release(first["id"]), "decided")
        self.assertIsNone(holds.claim_release(999))
        self.assertEqual(holds.counts()["releasing"], 1)
        self.assertEqual(holds.list_holds(status="releasing")[0]["id"], third["id"])

        # A release that failed puts it back, saying why.
        back = holds.unclaim_release(third["id"], "X is down")
        self.assertEqual(back["status"], "pending")
        self.assertIn("X is down", back["effect"])
        self.assertIsNone(holds.unclaim_release(third["id"], "again"), "only a claimed hold goes back")
        self.assertIsNotNone(holds.claim_release(third["id"]))

    def test_two_claims_at_the_same_moment_get_one_hold_between_them(self):
        target = hold("contested")["id"]
        got = []
        start = threading.Barrier(8)

        def claim():
            start.wait()
            got.append(holds.claim_release(target))

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len([h for h in got if h is not None]), 1, got)

    def test_the_same_inbound_item_held_again_while_pending_is_one_hold(self):
        moltbook = {"surface": "moltbook", "post_id": "p1", "comment_id": "c1", "author": "crab"}
        first = hold("a captcha killed it", context=moltbook, category="violence", reason='contains "killed"')
        again = hold("a captcha killed it", context=dict(moltbook), category="violence", reason='contains "killed"')
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(len(holds.list_holds()), 1)
        # Another item, other words, or a hold already decided: a new hold.
        other = hold("a captcha killed it", context={**moltbook, "comment_id": "c2"})
        reworded = hold("a captcha killed it twice", context=moltbook)
        holds.decide(first["id"], "rejected")
        after = hold("a captcha killed it", context=moltbook)
        self.assertEqual(len({first["id"], other["id"], reworded["id"], after["id"]}), 4)
        # An outbound draft is the account's own: each attempt is its own hold.
        draft = hold("gm frens", direction="outbound", kind="draft")
        self.assertNotEqual(hold("gm frens", direction="outbound", kind="draft")["id"], draft["id"])

    def test_stamps_holds_with_the_hand_clock(self):
        import os

        os.environ["TEENY_NOW"] = "1789776000"
        self.assertEqual(hold()["at"], "2026-09-19T00:00:00.000Z")

    def test_lives_in_state_hands_db_under_the_guard_tables(self):
        hold()
        holds.allow_link("x.example", None)
        conn = hand.db()
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"guard_holds", "guard_approved_links"} <= tables, tables)
        self.assertEqual(conn.execute("SELECT entry FROM guard_approved_links").fetchone()[0], "x.example")

    def test_refuses_a_status_or_decision_it_does_not_know(self):
        with self.assertRaises(hand.HandError):
            holds.list_holds(status="maybe")
        with self.assertRaises(hand.HandError):
            holds.decide(hold()["id"], "pending")


class SpyQueue:
    """Answers `requeue` with a fixed answer and remembers the ids."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, post_id):
        self.calls.append(post_id)
        return self.answer


class CountingRelease:
    """Counts its releases and takes a moment over each, as X does, so two
    approvals can overlap."""

    def __init__(self):
        self.releases = 0
        self._lock = threading.Lock()

    def __call__(self, held):
        time.sleep(0.05)
        with self._lock:
            self.releases += 1
            return f"published on X as {self.releases}"


class ReviewHoldTest(support.Scratch):
    def test_rejecting_confirms_the_filter_and_does_nothing_else(self):
        queue = SpyQueue(True)
        reviewed = holds.review_hold(hold()["id"], "rejected", note="the filter was right", requeue=queue)
        self.assertEqual(reviewed["status"], "rejected")
        self.assertEqual(reviewed["effect"], "confirmed the filter")
        self.assertEqual(reviewed["note"], "the filter was right")
        self.assertEqual(queue.calls, [])

    def test_approving_a_held_x_mention_sends_it_back_to_the_queue(self):
        queue = SpyQueue(True)
        reviewed = holds.review_hold(hold()["id"], "approved", requeue=queue)
        self.assertEqual(queue.calls, ["10"])
        self.assertEqual(reviewed["effect"], "requeued to be answered")

    def test_approving_a_held_x_mention_with_no_queue_is_recorded(self):
        reviewed = holds.review_hold(hold()["id"], "approved")
        self.assertEqual((reviewed["status"], reviewed["effect"]), ("approved", "recorded; no queue to return it to"))

    def test_approving_a_held_telegram_message_is_recorded_only(self):
        telegram = {"surface": "telegram", "chat_id": "1", "message_id": "5"}
        queue = SpyQueue(True)
        reviewed = holds.review_hold(hold(context=telegram)["id"], "approved", requeue=queue)
        self.assertIn("cannot be answered after the fact", reviewed["effect"])
        self.assertEqual(queue.calls, [])

    def test_approving_a_link_hold_allowlists_its_hosts_and_refreshes_the_gate(self):
        cut = [
            {"link": "https://dexscreener.com/solana/abc", "reason": "x"},
            {"link": "https://dexscreener.com/solana/def", "reason": "x"},
        ]
        held = hold(
            "see https://dexscreener.com/solana/abc and https://dexscreener.com/solana/def",
            direction="outbound",
            kind="link",
            category="links",
            by="links",
            links=cut,
        )
        refreshed = []
        reviewed = holds.review_hold(held["id"], "approved", refresh_links=lambda: refreshed.append(1))
        self.assertEqual(reviewed["effect"], "allowlisted dexscreener.com")
        self.assertEqual(holds.allowed_links(), ["dexscreener.com"])
        self.assertEqual(refreshed, [1])

    def test_approving_a_draft_with_nothing_to_publish_it_is_recorded(self):
        reviewed = holds.review_hold(draft_hold()["id"], "approved")
        self.assertEqual((reviewed["status"], reviewed["effect"]), ("approved", "recorded; nothing here can publish it"))

    def test_approving_a_draft_releases_it_and_a_failed_release_leaves_it_pending(self):
        target = draft_hold()["id"]

        def down(held):
            raise holds.ReviewError("X is down")

        with self.assertRaisesRegex(holds.ReviewError, "X is down"):
            holds.review_hold(target, "approved", release=down)
        back = holds.get(target)
        self.assertEqual(back["status"], "pending")
        self.assertIn("X is down", back["effect"], "the failure is recorded on the hold")

        reviewed = holds.review_hold(target, "approved", release=lambda held: "published on X as 99")
        self.assertEqual(reviewed["status"], "approved")
        self.assertEqual(reviewed["effect"], "published on X as 99")

    def test_a_release_that_raises_anything_still_goes_back_to_pending(self):
        target = draft_hold()["id"]

        def broken(held):
            raise RuntimeError("socket closed")

        with self.assertRaisesRegex(RuntimeError, "socket closed"):
            holds.review_hold(target, "approved", release=broken)
        self.assertEqual(holds.get(target)["status"], "pending")

    def test_two_approvals_at_once_publish_a_draft_once(self):
        target = draft_hold()["id"]
        release = CountingRelease()
        errors = []

        def approve():
            try:
                holds.review_hold(target, "approved", release=release)
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=approve) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(release.releases, 1)
        settled = holds.get(target)
        self.assertEqual((settled["status"], settled["effect"]), ("approved", "published on X as 1"))

    def failing_decides(self, times):
        """The store refusing the next `times` decisions, as Mongo going away did."""
        real = holds.decide
        left = [times]

        def decide(*args, **kwargs):
            if left[0]:
                left[0] -= 1
                raise holds.sqlite3.OperationalError("database is locked")
            return real(*args, **kwargs)

        return mock.patch.object(holds, "decide", decide)

    def test_a_release_whose_decision_could_not_be_written_is_marked_released_with_a_note(self):
        target = draft_hold()["id"]
        release = CountingRelease()
        with self.failing_decides(1), self.assertLogs("teeny_hands.guard", "WARNING"):
            reviewed = holds.review_hold(target, "approved", note="ok", release=release)
        self.assertEqual(reviewed["status"], "approved")
        self.assertEqual(reviewed["effect"], "published on X as 1")
        self.assertIn("ok", reviewed["note"])
        self.assertIn("database is locked", reviewed["note"])

    def test_a_release_whose_decision_cannot_be_written_at_all_is_never_offered_again(self):
        target = draft_hold()["id"]
        release = CountingRelease()
        with self.failing_decides(2), self.assertLogs("teeny_hands.guard", "WARNING"):
            with self.assertRaisesRegex(holds.sqlite3.OperationalError, "database is locked"):
                holds.review_hold(target, "approved", release=release)
        self.assertEqual(holds.get(target)["status"], "releasing", "published, so never pending again")
        holds.review_hold(target, "approved", release=release)
        self.assertEqual(release.releases, 1)

    def test_does_not_re_run_an_effect_on_a_hold_already_decided(self):
        target = hold()["id"]
        queue = SpyQueue(True)
        holds.review_hold(target, "approved", requeue=queue)
        holds.review_hold(target, "approved", requeue=queue)
        holds.review_hold(target, "rejected", requeue=queue)
        self.assertEqual(len(queue.calls), 1)
        self.assertEqual(holds.get(target)["status"], "approved")

    def test_answers_nothing_for_an_unknown_id(self):
        self.assertIsNone(holds.review_hold(999, "approved"))

    def test_turns_a_cut_link_into_a_host_entry(self):
        self.assertEqual(holds.allowlist_entry_for("https://Dexscreener.com/solana/abc?x=1"), "dexscreener.com")
        self.assertEqual(holds.allowlist_entry_for("evil.xyz/claim"), "evil.xyz")
        self.assertIsNone(holds.allowlist_entry_for("not a url at all ://"))
