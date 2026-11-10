"""hands/bounty: open, collect entries from X and Moltbook, pick after the
deadline (announced through hands/x), the winner's own claim, and the prize
paid once, after the challenge window, to the claimed address only.

X and Moltbook are one fake REST server (x.support.FakeX); the core and the
chain are the wallet tests' fakes."""

from __future__ import annotations

import datetime as dt
import json

from wallet.support import DAY, HOUR, OWN, STRANGER, T0, WINNER, HttpStatus, Scratch
from x.support import SECRETS, FakeX
from teeny_hands import hand  # noqa: E402
from teeny_hands.bounty import actions, store  # noqa: E402
from teeny_hands.wallet import pay, store as wallet_store  # noqa: E402

DEADLINE = T0 + 2 * DAY
X_POST = "1001"
SIG = "5igSig" + "A" * 80


def iso(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def reply(post_id: str, author: str, text: str, at: float, conversation: str = X_POST) -> tuple[dict, dict]:
    return (
        {"id": post_id, "text": text, "author_id": "u-" + author, "created_at": iso(at), "conversation_id": conversation},
        {"id": "u-" + author, "username": author},
    )


class BountyTest(Scratch):
    def setUp(self):
        super().setUp()
        self.web = FakeX()
        self.addCleanup(self.web.close)
        self.configure(
            **SECRETS,
            X_API_BASE=self.web.base,
            X_BOT_USERNAME="teenydroid",
            MOLTBOOK_API_KEY="moltbook_sk_FAKE",
            MOLTBOOK_API_BASE=self.web.base,
            MOLTBOOK_AGENT_NAME="teenydroid",
            POST_MIN_GAP_HOURS="1",
        )
        self.posted = []
        self.web.route("POST", "/2/tweets", lambda: None, status=201)  # replaced below
        self.next_id = iter(range(2000, 3000))

        def created(seen):
            self.posted.append(seen.json()["text"])
            return True

        self.web.routes.clear()
        self.web.route("POST", "/2/tweets", {"data": {"id": X_POST}}, status=201, when=created)
        self.replies: dict[str, list] = {X_POST: []}

    def conversation(self, conversation: str, *items: tuple[dict, dict]) -> None:
        data = [i[0] for i in items]
        users = [i[1] for i in items]
        self.web.route(
            "GET", "/2/tweets/search/recent", {"data": data, "includes": {"users": users}},
            when=lambda seen, c=conversation: seen.query.get("query") == f"conversation_id:{c}",
        )

    def open_and_announce(self, **overrides) -> int:
        body = {"title": "best tiny meme", "rules": "reply with your meme", "prize_usdc": "25", "deadline": iso(DEADLINE), "channels": ["x"]}
        body.update(overrides)
        bounty_id = actions.open_bounty(body)["bounty"]["id"]
        from teeny_hands.x import publish

        publish.post({"text": f"new bounty #{bounty_id}: best tiny meme, 25 USDC. reply to enter"})
        return bounty_id

    def collect_entries(self, bounty_id: int) -> dict:
        self.conversation(
            X_POST,
            reply("1101", "alice", "here is my meme", T0 + HOUR),
            reply("1102", "bob", "mine is better", T0 + 2 * HOUR),
            reply("1103", "teenydroid", "love these", T0 + 3 * HOUR),
            reply("1104", "carol", "sorry i'm late", DEADLINE + HOUR),
        )
        self.at(DEADLINE + 2 * HOUR)
        return actions.entries(bounty_id)

    def entry_id(self, author: str) -> int:
        conn = wallet_store.db()
        try:
            return conn.execute("SELECT id FROM bounty_entries WHERE author = ?", (author,)).fetchone()["id"]
        finally:
            conn.close()

    def row(self, bounty_id: int):
        conn = wallet_store.db()
        try:
            return dict(store.get(conn, bounty_id))
        finally:
            conn.close()

    def picked(self) -> int:
        bounty_id = self.open_and_announce()
        self.collect_entries(bounty_id)
        self.web.routes.clear()
        self.web.route("POST", "/2/tweets", {"data": {"id": "1200"}}, status=201)
        actions.pick({"bounty_id": bounty_id, "entry_id": self.entry_id("alice"), "reasons": "funniest", "text": f"bounty #{bounty_id} goes to @alice. reply with your solana address"})
        return bounty_id

    def claimed(self) -> int:
        bounty_id = self.picked()
        self.conversation(X_POST)
        self.conversation("1200", reply("1301", "bob", f"pay me {STRANGER}", DEADLINE + 3 * HOUR, "1200"),
                          reply("1302", "alice", f"thank you! {WINNER}", DEADLINE + 3 * HOUR, "1200"))
        self.at(DEADLINE + 4 * HOUR)
        out = actions.entries(bounty_id)
        self.assertEqual(out.get("claimed"), WINNER)
        return bounty_id

    # --- open ------------------------------------------------------------------------------------

    def test_open_checks_the_prize_and_the_deadline(self):
        def refused(rule, **body):
            full = {"title": "t", "rules": "r", "prize_usdc": "25", "deadline": iso(DEADLINE), "channels": ["x"]}
            full.update(body)
            with self.assertRaises(hand.Refused) as caught:
                actions.open_bounty(full)
            self.assertEqual(caught.exception.detail["rule"], rule)

        refused("prize", prize_usdc="51")  # over WALLET_MAX_PER_TX_USD
        refused("prize", prize_usdc="0")
        refused("deadline", deadline=iso(T0 + 60))
        refused("deadline", deadline=iso(T0 + 90 * DAY))
        refused("channels", channels=["telegram"])
        self.configure(WALLET_MAX_PER_TX_USD=None)
        refused("caps")

    def test_open_returns_the_tag(self):
        out = actions.open_bounty({"title": "t", "rules": "r", "prize_usdc": "25", "deadline": DEADLINE})
        self.assertEqual((out["bounty"]["tag"], out["bounty"]["prize_usdc"], out["bounty"]["channels"]), ("bounty #1", "25", ["x"]))

    def test_the_tag_does_not_match_a_longer_number(self):
        self.assertTrue(store.tag_pattern(3).search("BOUNTY #3: go"))
        self.assertFalse(store.tag_pattern(3).search("bounty #30"))

    # --- entries ---------------------------------------------------------------------------------

    def test_entries_link_the_post_and_collect_replies(self):
        bounty_id = self.open_and_announce()
        out = self.collect_entries(bounty_id)
        self.assertEqual(out["added"], {"x": 3})  # Teeny's own reply is not an entry
        self.assertEqual(self.row(bounty_id)["x_post_id"], X_POST)
        conn = wallet_store.db()
        late = {r["author"]: r["late"] for r in conn.execute("SELECT author, late FROM bounty_entries")}
        conn.close()
        self.assertEqual(late, {"alice": 0, "bob": 0, "carol": 1})
        self.assertEqual(actions.entries(bounty_id)["added"], {})  # nothing twice

    def test_moltbook_comments_are_entries_too(self):
        bounty_id = self.open_and_announce(channels=["x", "moltbook"])
        actions.link(bounty_id, None, "mb-1")
        self.web.route("GET", "/posts/mb-1/comments", {"success": True, "comments": [
            {"id": "c1", "author": {"name": "crabby"}, "content": "my entry", "created_at": iso(T0 + HOUR),
             "replies": [{"id": "c2", "author": {"name": "teenydroid"}, "content": "nice", "created_at": iso(T0 + 2 * HOUR)}]},
        ]})
        out = self.collect_entries(bounty_id)
        self.assertEqual(out["added"], {"x": 3, "moltbook": 1})

    def test_announcements_list_what_is_owed(self):
        bounty_id = actions.open_bounty({"title": "t", "rules": "r", "prize_usdc": "25", "deadline": DEADLINE})["bounty"]["id"]
        self.assertEqual([b["tag"] for b in actions.announcements()["open"]], [f"bounty #{bounty_id}"])
        from teeny_hands.x import publish

        publish.post({"text": f"bounty #{bounty_id} is open"})
        self.assertEqual(actions.announcements()["open"], [])
        self.collect_entries(bounty_id)
        [to_pick] = actions.announcements()["to_pick"]
        self.assertEqual(sorted(e["author"] for e in to_pick["entries"]), ["alice", "bob"])  # not late, not Teeny

    # --- pick ------------------------------------------------------------------------------------

    def test_pick_waits_for_the_deadline_and_refuses_late_entries(self):
        bounty_id = self.open_and_announce()
        self.collect_entries(bounty_id)
        self.at(DEADLINE - 1)
        body = {"bounty_id": bounty_id, "entry_id": self.entry_id("alice"), "reasons": "funny", "text": f"bounty #{bounty_id}: @alice"}
        with self.assertRaises(hand.Refused) as caught:
            actions.pick(body)
        self.assertEqual(caught.exception.detail["rule"], "deadline")
        self.at(DEADLINE + 2 * HOUR)
        with self.assertRaises(hand.Refused) as caught:
            actions.pick({**body, "entry_id": self.entry_id("carol")})
        self.assertEqual(caught.exception.detail["rule"], "late")
        with self.assertRaises(hand.Refused) as caught:
            actions.pick({**body, "text": "alice won"})
        self.assertEqual(caught.exception.detail["rule"], "tag")

    def test_pick_announces_and_starts_the_window(self):
        bounty_id = self.picked()
        row = self.row(bounty_id)
        self.assertEqual((row["status"], row["winner_post_id"], row["public_at"]), ("picked", "1200", DEADLINE + 2 * HOUR))
        with self.assertRaises(hand.Refused) as caught:
            actions.pick({"bounty_id": bounty_id, "entry_id": self.entry_id("bob"), "reasons": "x", "text": f"bounty #{bounty_id}"})
        self.assertEqual(caught.exception.detail["rule"], "picked")

    def test_a_dry_run_pick_records_nothing(self):
        bounty_id = self.open_and_announce()
        self.collect_entries(bounty_id)
        self.configure(DRY_RUN="1")
        posts_before = len(self.web.requests("POST", "/2/tweets")) if hasattr(self.web, "requests") else None
        out = actions.pick({"bounty_id": bounty_id, "entry_id": self.entry_id("alice"), "reasons": "funny", "text": f"bounty #{bounty_id}: @alice"})
        self.assertTrue(out["dry_run"])
        self.assertEqual(self.row(bounty_id)["status"], "open")
        if posts_before is not None:
            self.assertEqual(len(self.web.requests("POST", "/2/tweets")), posts_before)

    # --- claim -------------------------------------------------------------------------------------

    def test_the_claim_comes_from_the_winner_only(self):
        bounty_id = self.picked()
        self.conversation(X_POST)
        self.conversation("1200", reply("1301", "bob", f"send it here {STRANGER}", DEADLINE + 3 * HOUR, "1200"))
        self.at(DEADLINE + 4 * HOUR)
        self.assertNotIn("claimed", actions.entries(bounty_id))
        with self.assertRaises(hand.Refused) as caught:
            actions.claim({"bounty_id": bounty_id, "address": STRANGER, "source_id": "1301"})
        self.assertEqual(caught.exception.detail["rule"], "author")
        self.assertIsNone(self.row(bounty_id)["claim_address"])

    def test_an_explicit_claim_must_carry_the_address(self):
        bounty_id = self.picked()
        self.conversation(X_POST)
        self.conversation("1200", reply("1302", "alice", f"mine: {WINNER} or {STRANGER}", DEADLINE + 3 * HOUR, "1200"))
        self.at(DEADLINE + 4 * HOUR)
        self.assertNotIn("claimed", actions.entries(bounty_id))  # two addresses: not taken on its own
        with self.assertRaises(hand.Refused) as caught:
            actions.claim({"bounty_id": bounty_id, "address": OWN, "source_id": "1302"})
        self.assertEqual(caught.exception.detail["rule"], "address")
        out = actions.claim({"bounty_id": bounty_id, "address": WINNER, "source_id": "1302"})
        self.assertEqual(out["claimed"]["claim_address"], WINNER)
        with self.assertRaises(hand.Refused):  # set once
            actions.claim({"bounty_id": bounty_id, "address": STRANGER, "source_id": "1302"})

    def test_the_winner_reply_is_taken_as_the_claim(self):
        bounty_id = self.claimed()
        row = self.row(bounty_id)
        self.assertEqual((row["status"], row["claim_address"], row["claim_source_id"]), ("claimed", WINNER, "1302"))

    # --- pay -------------------------------------------------------------------------------------------

    def test_pay_waits_out_the_challenge_window(self):
        bounty_id = self.claimed()
        with self.assertRaises(hand.Refused) as caught:
            actions.pay(bounty_id)
        self.assertEqual(caught.exception.detail["rule"], "challenge_window")
        self.assertEqual(self.moved(), [])
        self.at(DEADLINE + 2 * HOUR + 12 * HOUR)
        out = actions.pay(bounty_id)
        self.assertEqual((out["paid"]["to"], out["paid"]["amount"], out["paid"]["asset"]), (WINNER, "25", "USDC"))
        row = self.row(bounty_id)
        self.assertEqual(row["status"], "paid")
        self.assertEqual(self.core.params("openhuman.wallet_prepare_transfer")[0]["toAddress"], WINNER)

    def test_the_window_is_configurable(self):
        bounty_id = self.claimed()
        self.configure(**{**self._settings(), "BOUNTY_CHALLENGE_HOURS": "48"})
        self.at(DEADLINE + 2 * HOUR + 13 * HOUR)
        with self.assertRaises(hand.Refused):
            actions.pay(bounty_id)

    def _settings(self):
        return {**SECRETS, "X_API_BASE": self.web.base, "X_BOT_USERNAME": "teenydroid", "MOLTBOOK_API_KEY": "k",
                "MOLTBOOK_API_BASE": self.web.base, "POST_MIN_GAP_HOURS": "1"}

    def test_a_prize_is_never_paid_twice(self):
        bounty_id = self.claimed()
        self.at(DEADLINE + 15 * HOUR)
        actions.pay(bounty_id)
        with self.assertRaises(hand.Refused):
            actions.pay(bounty_id)
        with self.assertRaises(hand.Refused) as caught:
            pay.pay({"to": WINNER, "amount_usdc": "25", "purpose": "bounty", "ref": str(bounty_id)})
        self.assertEqual(caught.exception.detail["rule"], "bounty")
        self.assertEqual(self.core.methods().count("openhuman.wallet_execute_prepared"), 1)

    def test_the_wallet_checks_the_bounty_itself(self):
        bounty_id = self.claimed()
        self.at(DEADLINE + 15 * HOUR)
        for body, rule in (
            ({"to": STRANGER, "amount_usdc": "25"}, "claimed_address"),
            ({"to": WINNER, "amount_usdc": "24"}, "prize"),
            ({"to": WINNER, "amount_sol": "0.1"}, "prize"),
            ({"to": WINNER, "amount_usdc": "25", "ref": "99"}, "bounty"),
        ):
            with self.assertRaises(hand.Refused) as caught:
                pay.pay({"purpose": "bounty", "ref": str(bounty_id), **body})
            self.assertEqual(caught.exception.detail["rule"], rule)
        self.assertEqual(self.moved(), [])

    def test_an_unpicked_bounty_cannot_be_paid(self):
        bounty_id = self.open_and_announce()
        self.at(DEADLINE + 30 * DAY)
        with self.assertRaises(hand.Refused) as caught:
            pay.pay({"to": WINNER, "amount_usdc": "25", "purpose": "bounty", "ref": str(bounty_id)})
        self.assertEqual(caught.exception.detail["rule"], "bounty")

    def test_a_prize_in_doubt_is_never_retried(self):
        bounty_id = self.claimed()
        self.at(DEADLINE + 15 * HOUR)
        self.core.answers["openhuman.wallet_execute_prepared"] = HttpStatus(504)
        with self.assertRaises(hand.HandError):
            actions.pay(bounty_id)
        self.assertEqual(self.row(bounty_id)["status"], "in_doubt")
        self.core.answers["openhuman.wallet_execute_prepared"] = lambda p: {"transactionHash": SIG}
        with self.assertRaises(hand.Refused):
            actions.pay(bounty_id)
        self.assertEqual(actions.pay_due()["bounties"], [])  # in doubt is not due
        self.assertEqual(self.core.methods().count("openhuman.wallet_execute_prepared"), 1)

    def test_a_dry_run_pay_moves_nothing(self):
        bounty_id = self.claimed()
        self.at(DEADLINE + 15 * HOUR)
        self.configure(**{**self._settings(), "DRY_RUN": "1"})
        out = actions.pay(bounty_id)
        self.assertTrue(out["dry_run"])
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.row(bounty_id)["status"], "claimed")

    def test_pay_due_claims_then_pays_when_due(self):
        bounty_id = self.picked()
        self.conversation(X_POST)
        self.conversation("1200", reply("1302", "alice", f"gm {WINNER}", DEADLINE + 3 * HOUR, "1200"))
        self.at(DEADLINE + 4 * HOUR)
        [item] = actions.pay_due()["bounties"]
        self.assertEqual((item["claimed"], item["pay"]["rule"]), (WINNER, "challenge_window"))
        self.at(DEADLINE + 15 * HOUR)
        [item] = actions.pay_due()["bounties"]
        self.assertIn("paid", item["pay"])
        self.assertEqual(actions.pay_due()["bounties"], [])
        # The paid announcement is owed until a post carries the signature.
        [owed] = actions.announcements()["paid"]
        self.assertEqual((owed["winner"]["author"], owed["amount"]), ("alice", "25 USDC"))
        self.assertIn("solscan.io/tx/", owed["url"])
        self.assertEqual(json.loads(json.dumps(owed))["tag"], f"bounty #{bounty_id}")
