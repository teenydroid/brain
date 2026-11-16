"""Shared by the hands/giveaway tests: a scratch root with the caps on, a
fake X (posts and the paid user lookup), the wallet tests' fake core (Teeny's
own address), a fake Solana RPC for the seed, and a fake chain step.

Entries are written into `x_mentions` and `x_follows` the way X's webhook
leaves them, so nothing here reads X for an entry."""

from __future__ import annotations

import datetime as dt
import gc
import hashlib
import json
from typing import Any

from wallet.support import DAY, HOUR, OWN, T0, Scratch
from x.support import SECRETS, FakeX
from teeny_hands import hand
from teeny_hands.giveaway import chain, draw, store
from teeny_hands.wallet import solana
from teeny_hands.x import follows, mentions

DEADLINE = T0 + 2 * DAY
ROUND_POST = "5000"
AUTHORITY = solana.b58encode(bytes([7]) * 32)


def address(n: int) -> str:
    """A distinct valid Solana address per number."""
    return solana.b58encode(hashlib.sha256(f"addr-{n}".encode()).digest())


def iso(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


OLD = iso(T0 - 400 * DAY)  # an account well over 90 days old

CAPS = {
    "GIVEAWAYS": "1",
    "GIVEAWAY_POOL_MAX_TINY": "100000",
    "GIVEAWAY_SEASON_MAX_TINY": "250000",
    "GIVEAWAY_ROUNDS_PER_WEEK": "2",
    "GIVEAWAY_MAX_PER_WINNER_TINY": "40000",
    "GIVEAWAY_CLAWBACK_DAYS": "30",
    "GIVEAWAY_X_LOOKUPS_PER_ROUND": "1",
    "GIVEAWAY_CLUSTER": "devnet",
    "GIVEAWAY_DEVNET_MINT": solana.b58encode(bytes([42]) * 32),
    "GIVEAWAY_AUTHORITY": AUTHORITY,
    "GIVEAWAY_RPC_URL": "http://fake-rpc.invalid",
}


class FakeSolana:
    """Finalized slots 0..tip, two of every three produced, ~0.4 s apart,
    slot 12500 the first at or after DEADLINE."""

    def __init__(self, deadline: float = DEADLINE, tip: int = 20000) -> None:
        self.deadline = deadline
        self.tip = tip
        self.calls: list[str] = []

    def time(self, slot: int) -> int:
        return int(self.deadline - 5000 + slot * 0.4)

    def produced(self, slot: int) -> bool:
        return slot % 3 != 0 and 0 <= slot <= self.tip

    def blockhash(self, slot: int) -> str:
        return solana.b58encode(hashlib.sha256(f"block-{slot}".encode()).digest())

    def expected(self) -> int:
        return next(s for s in range(self.tip + 1) if self.produced(s) and self.time(s) >= self.deadline)

    def __call__(self, method: str, params: list[Any]) -> Any:
        self.calls.append(method)
        if method == "getSlot":
            return self.tip
        if method == "getBlocks":
            start, end = params[0], params[1]
            return [s for s in range(start, min(end, self.tip) + 1) if self.produced(s)]
        if method == "getBlockTime":
            if not self.produced(params[0]):
                raise solana.RpcError("slot skipped")
            return self.time(params[0])
        if method == "getBlock":
            return {"blockhash": self.blockhash(params[0])}
        raise AssertionError(f"unexpected RPC {method}")


class FakeChain:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.closed: list[dict] = []

    def create_distribution(self, plan):
        self.created.append(dict(plan))
        return {"distribution_seed": solana.b58encode(bytes([8]) * 32),
                "distributor": solana.b58encode(bytes([9]) * 32),
                "vault": solana.b58encode(bytes([10]) * 32),
                "txs": {"create": "5" + "C" * 87}}

    def close_distribution(self, plan):
        self.closed.append(dict(plan))
        return {"tx": "4" + "D" * 87}


class GiveawayCase(Scratch):
    settings = {**SECRETS, **CAPS, "X_BOT_USERNAME": "teenydroid", "POST_MIN_GAP_HOURS": "1",
                "REPLY_MIN_INTERVAL_MS": "0", "REPLY_MAX_INTERVAL_MS": "0"}

    def setUp(self) -> None:
        self.web = FakeX()
        self.settings = {**type(self).settings, "X_API_BASE": self.web.base}
        super().setUp()
        self.addCleanup(self.web.close)
        self.web.route("POST", "/2/tweets", {"data": {"id": ROUND_POST}}, status=201)
        self.solana = FakeSolana()
        original = draw.rpc
        draw.rpc = self.solana
        self.addCleanup(setattr, draw, "rpc", original)
        self.chain_fake = FakeChain()
        chain.use(chain.NOT_READY)  # never the real core; a test puts its own in front
        self.addCleanup(chain.use, None)
        # hands/x's publish leaves its connection to the GC (a CLI exits);
        # collect it here so a whole run does not pile up open files.
        self.addCleanup(gc.collect)

    # --- rounds -------------------------------------------------------------------------

    def open_body(self, **overrides: Any) -> dict:
        body = {"format": "drop", "pool_tiny": "100000", "winners": 4, "best_reply_tiny": "20000",
                "prompt": "tell me the worst advice you ever took", "deadline": iso(DEADLINE),
                "text": "drop #1: 100,000 tiny for the worst advice you ever took. reply to this post to enter"}
        body.update(overrides)
        return body

    def opened(self, **overrides: Any) -> int:
        from teeny_hands.giveaway import rounds

        return rounds.open_round(self.open_body(**overrides))["round"]["id"]

    # --- what the webhook would have stored ----------------------------------------------

    def reply(self, post_id: str, handle: str, text: str, at: float, *, user_id: str | None = None,
              created: str | None = OLD, followers: int | None = 500, status: str = "pending",
              hold_id: int | None = None, conversation: str = ROUND_POST) -> str:
        user_id = user_id or f"u{abs(hash(handle)) % 10**9}"
        conn = mentions._db()
        try:
            post = {"id": post_id, "text": text, "authorId": user_id, "authorUsername": handle,
                    "conversationId": conversation, "createdAt": iso(at)}
            conn.execute(
                "INSERT INTO x_mentions (id, author_id, author_username, conversation_id, created_at, text, post, status,"
                " hold_id, seen_at, updated_at, author_created_at, author_followers, author_verified)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (post_id, user_id, handle, conversation, iso(at), text, json.dumps(post), status, hold_id, at, at,
                 created, followers, None),
            )
        finally:
            conn.close()
        return user_id

    def follow(self, user_id: str, following: bool = True, at: float = T0 - DAY) -> None:
        follows.record([{"kind": follows.FOLLOW if following else follows.UNFOLLOW, "userId": user_id, "at": at}])

    def entrant(self, n: int, handle: str, text: str | None = None, **kw: Any) -> str:
        """A reply by an old, followed account with an address."""
        follow = kw.pop("follows", True)
        uid = self.reply(str(6000 + n), handle, text if text is not None else f"drop #1 {address(n)}",
                         T0 + HOUR + n, user_id=f"9{n:04d}", **kw)
        if follow is not None:
            self.follow(uid, follow)
        return uid

    def users_route(self, users: list[dict]) -> None:
        self.web.route("GET", "/2/users", {"data": users})

    def row(self, round_id: int = 1) -> dict:
        conn = store.db()
        try:
            return dict(store.get(conn, round_id))
        finally:
            conn.close()

    def draws(self, round_id: int = 1) -> dict[str, dict]:
        conn = store.db()
        try:
            return {r["handle"]: dict(r) for r in conn.execute("SELECT * FROM giveaway_draws WHERE round_id = ?", (round_id,))}
        finally:
            conn.close()


__all__ = ["DAY", "HOUR", "OWN", "T0", "DEADLINE", "ROUND_POST", "AUTHORITY", "address", "iso", "OLD", "CAPS",
           "FakeSolana", "FakeChain", "GiveawayCase", "hand"]
