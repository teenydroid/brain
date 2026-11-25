"""hands/wallet inflows and accounts (docs/roadmap.md, S1): every deposit to
Teeny's wallet lands once in the inflow ledger, the team's apart from what it
earned, a stranger's memo only through the inbound guard; the weekly profit
and loss reconciles to `costs` and the ledger to the cent, is closed once,
only when complete, and only with WALLET_ACCOUNTS=1. The core and the chain
are faked (support.py); nothing is signed."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest import mock

from wallet.support import DAY, HOUR, OWN, STRANGER, T0, USDC_MAINNET, Failure, HttpStatus, Scratch, token_accounts
from teeny_hands import hand
from teeny_hands.wallet import accounts, reads

TEAM = "Hx6LbkMHe69DYawhPyVNs8Apa6tyfogfzQV6a7XkwKcE"
TOKEN_ACCOUNT = "TokenAcct1111111111111111111111111111111111"
#: What the live RPC answered for a version-1 transaction read at version 0.
V1_REFUSED = ('Transaction version (1) is not supported by the requesting client. Please try the request again'
              ' with the following configuration parameter: "maxSupportedTransactionVersion": 1')


def sig(n: int) -> str:
    return f"{n}inflowSig" + "C" * 78


def usdc_tx(sender: str, raw: int, memo: str | None = None, signers: tuple[str, ...] | None = None) -> dict[str, Any]:
    signers = signers if signers is not None else (sender,)
    tx: dict[str, Any] = {
        "meta": {
            "err": None,
            "preBalances": [5_000_000] * (len(signers) + 1),
            "postBalances": [5_000_000] * (len(signers) + 1),
            "preTokenBalances": [
                {"mint": USDC_MAINNET, "owner": sender, "uiTokenAmount": {"amount": str(raw + 7)}},
                {"mint": USDC_MAINNET, "owner": OWN, "uiTokenAmount": {"amount": "0"}},
            ],
            "postTokenBalances": [
                {"mint": USDC_MAINNET, "owner": sender, "uiTokenAmount": {"amount": "7"}},
                {"mint": USDC_MAINNET, "owner": OWN, "uiTokenAmount": {"amount": str(raw)}},
            ],
        },
        "transaction": {"message": {
            "accountKeys": [{"pubkey": s, "signer": True} for s in signers] + [{"pubkey": OWN, "signer": False}],
            "instructions": [],
        }},
    }
    if memo is not None:
        tx["transaction"]["message"]["instructions"].append({"program": "spl-memo", "programId": accounts.MEMO_PROGRAMS[0], "parsed": memo})
    return tx


def sol_tx(sender: str, lamports: int) -> dict[str, Any]:
    return {
        "meta": {"err": None, "preBalances": [10**10, 10**9], "postBalances": [10**10 - lamports - 5000, 10**9 + lamports],
                 "preTokenBalances": [], "postTokenBalances": []},
        "transaction": {"message": {"accountKeys": [{"pubkey": sender, "signer": True}, {"pubkey": OWN, "signer": False}],
                                    "instructions": []}},
    }


def quiet_sleep(test) -> list[float]:
    """The scan's waits (pacing, a 429's backoff), recorded instead of slept,
    on a clock that moves only by what was slept: no test waits in real time."""
    slept: list[float] = []
    clock = [1000.0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    for name, fake in (("_sleep", sleep), ("_clock", lambda: clock[0])):
        patcher = mock.patch.object(accounts, name, fake, create=True)
        patcher.start()
        test.addCleanup(patcher.stop)
    return slept


def limited_after(read, ok: int, retry_after: str | None = None):
    """getTransaction that answers the first `ok` reads, then HTTP 429 to every one after."""
    seen = {"n": 0}

    def answer(params: list[Any]) -> Any:
        seen["n"] += 1
        if seen["n"] > ok:
            return HttpStatus(429, {"Retry-After": retry_after} if retry_after else None)
        return read(params)

    return answer


class Chain:
    """Signatures per address and transactions by signature, for the fake RPC."""

    def __init__(self) -> None:
        self.by_address: dict[str, list[dict[str, Any]]] = {}
        self.txs: dict[str, dict[str, Any]] = {}

    def add(self, address: str, signature: str, at: float, tx: dict[str, Any]) -> None:
        self.by_address.setdefault(address, []).insert(0, {"signature": signature, "slot": 1, "err": None, "blockTime": int(at)})
        self.txs[signature] = tx

    def signatures(self, params: list[Any]) -> list[dict[str, Any]]:
        return list(self.by_address.get(params[0], []))

    def transaction(self, params: list[Any]) -> dict[str, Any] | None:
        return self.txs.get(params[0])


class AccountsTest(Scratch):
    settings = {"WALLET_CLUSTER": "mainnet", "WALLET_TEAM_ADDRESSES": TEAM, "WALLET_ACCOUNTS": "1"}

    def setUp(self) -> None:
        super().setUp()
        self.ledger = Chain()
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(0, USDC_MAINNET)
        self.chain.answers["getSignaturesForAddress"] = self.ledger.signatures
        self.chain.answers["getTransaction"] = self.ledger.transaction
        self.slept = quiet_sleep(self)
        self.core.answers["openhuman.cost_report"] = {"totals": {"cost_usd": 3.141592, "calls": 120, "charged_usd": 3.0, "estimated_usd": 0.141592}}
        self.core.answers["openhuman.x402_list_payments"] = {"payments": [
            {"id": "p1", "status": "settled", "timestamp": "2026-09-13T10:00:00Z", "asset": USDC_MAINNET, "amountAtomic": "12345"},
            {"id": "p2", "status": "settled", "timestamp": "2026-08-01T10:00:00Z", "asset": USDC_MAINNET, "amountAtomic": "99000000"},
        ]}

    def deposit(self, n: int, at: float, tx: dict[str, Any], to: str = TOKEN_ACCOUNT) -> str:
        self.ledger.add(to, sig(n), at, tx)
        return sig(n)

    def rows(self) -> list[dict[str, Any]]:
        conn = accounts.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM wallet_inflows ORDER BY id")]
        finally:
            conn.close()

    # --- the inflow ledger ---------------------------------------------------------------------

    def test_a_strangers_deposit_is_a_tip_and_the_teams_is_apart(self):
        tip = self.deposit(1, T0 - 2 * DAY, usdc_tx(STRANGER, 5_250_000))
        team = self.deposit(2, T0 - DAY, usdc_tx(TEAM, 100_000_000))
        out = accounts.scan()
        self.assertEqual(out["count"], 2)
        rows = {r["signature"]: r for r in self.rows()}
        self.assertEqual((rows[tip]["category"], rows[tip]["amount_raw"], rows[tip]["usd_micro"], rows[tip]["sender"]),
                         ("tip", 5_250_000, 5_250_000, STRANGER))
        self.assertEqual(rows[team]["category"], "team")
        # A second scan finds nothing new.
        self.assertEqual(accounts.scan()["count"], 0)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.moved(), [])

    def test_a_team_signer_makes_it_team_money_whoever_sends(self):
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000, signers=(STRANGER, TEAM)))
        accounts.scan()
        self.assertEqual(self.rows()[0]["category"], "team")

    def test_teenys_own_transactions_are_never_inflows(self):
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000, signers=(OWN,)))
        self.assertEqual(accounts.scan()["count"], 0)

    def test_sol_is_recorded_in_sol_and_never_priced(self):
        self.deposit(1, T0 - HOUR, sol_tx(STRANGER, 250_000_000), to=OWN)
        accounts.scan()
        (row,) = self.rows()
        self.assertEqual((row["asset"], row["amount_raw"], row["usd_micro"], row["category"]), ("SOL", 250_000_000, 0, "tip"))

    def test_with_no_team_list_nothing_counts_as_earned(self):
        self.configure(WALLET_TEAM_ADDRESSES=None)
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        self.assertFalse(accounts.scan()["team_known"])
        self.assertEqual(self.rows()[0]["category"], "unclassified")

    def test_a_memo_is_kept_only_when_the_guard_allows_it(self):
        self.deposit(1, T0 - 2 * HOUR, usdc_tx(STRANGER, 1_000_000, memo="for request 12, thanks teeny"))
        self.deposit(2, T0 - HOUR, usdc_tx(STRANGER, 1_000_000, memo="retard bot"))
        out = accounts.scan()
        rows = self.rows()
        self.assertEqual((rows[0]["memo"], rows[0]["memo_held"]), ("for request 12, thanks teeny", 0))
        self.assertEqual((rows[1]["memo"], rows[1]["memo_held"]), (None, 1))
        # The listing never prints a memo, held or not.
        self.assertNotIn("kill", json.dumps(out))
        self.assertNotIn("request 12", json.dumps(accounts.inflows()))
        conn = sqlite3.connect(self.root / "state" / "hands.db")
        held = conn.execute("SELECT direction, context FROM guard_holds").fetchall()
        conn.close()
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0][0], "inbound")
        self.assertEqual(json.loads(held[0][1])["surface"], "wallet")

    def test_the_operator_tags_a_deposit(self):
        s = self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        accounts.scan()
        accounts.tag(s, "bounty", "won the crab bounty")
        self.assertEqual(self.rows()[0]["category"], "bounty")
        with self.assertRaises(hand.Refused) as no:
            accounts.tag(s, "unclassified", None)
        self.assertEqual(no.exception.detail["rule"], "category")
        with self.assertRaises(hand.Refused):
            accounts.tag(sig(9), "tip", None)

    def test_a_bad_team_list_is_refused(self):
        self.configure(WALLET_TEAM_ADDRESSES="not-an-address")
        with self.assertRaises(hand.Refused) as no:
            accounts.scan()
        self.assertEqual(no.exception.detail["rule"], "team")

    # --- a transaction the RPC will not return --------------------------------------------------

    def tx_reads(self, signature: str) -> list[int]:
        """The maxSupportedTransactionVersion of each getTransaction for `signature`."""
        return [p[1]["maxSupportedTransactionVersion"] for p in self.chain.params("getTransaction") if p[0] == signature]

    def test_an_unreadable_transaction_never_stops_the_scan(self):
        # Live, 2026-11-13: one version-1 transaction the RPC would not return
        # aborted every wallet-watch run, and no deposit after it was sensed.
        bad = self.deposit(1, T0 - 2 * HOUR, Failure(V1_REFUSED))
        good = self.deposit(2, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        with self.assertLogs("teeny_hands.wallet.accounts", "WARNING") as logged:
            out = accounts.scan()
        self.assertEqual((out["count"], out["unreadable"]), (1, 1))
        self.assertEqual([r["signature"] for r in self.rows()], [good])
        self.assertEqual(out["through"], accounts._iso(T0 - HOUR))
        self.assertIn(bad[:8], logged.output[0])
        self.assertNotIn(bad, logged.output[0])
        self.assertIn("Transaction version (1)", logged.output[0])

    def test_an_unreadable_transaction_is_retried_on_a_few_scans_then_left(self):
        bad = self.deposit(1, T0 - 2 * HOUR, Failure(V1_REFUSED))
        self.deposit(2, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        with self.assertLogs("teeny_hands.wallet.accounts", "WARNING"):
            for _ in range(accounts.UNREADABLE_TRIES + 2):
                accounts.scan()
        self.assertEqual(self.tx_reads(bad).count(0), accounts.UNREADABLE_TRIES)
        self.assertEqual(len(self.rows()), 1)

    def test_a_retried_transaction_that_reads_is_recorded(self):
        bad = self.deposit(1, T0 - 2 * HOUR, Failure("Transaction history is not available from this node"))
        with self.assertLogs("teeny_hands.wallet.accounts", "WARNING"):
            self.assertEqual(accounts.scan()["unreadable"], 1)
        self.ledger.txs[bad] = usdc_tx(STRANGER, 2_000_000)
        out = accounts.scan()
        self.assertEqual((out["count"], out["unreadable"]), (1, 0))
        self.assertEqual([r["signature"] for r in self.rows()], [bad])
        # Read once, it is no longer retried.
        accounts.scan()
        self.assertEqual(len(self.tx_reads(bad)), 2)

    def test_a_newer_transaction_version_is_read_at_the_version_the_rpc_names(self):
        v1 = usdc_tx(STRANGER, 3_000_000)

        def transaction(params: list[Any]) -> Any:
            if params[0] == sig(1) and params[1]["maxSupportedTransactionVersion"] < 1:
                return Failure(V1_REFUSED)
            return self.ledger.transaction(params)

        self.deposit(1, T0 - HOUR, v1)
        self.chain.answers["getTransaction"] = transaction
        out = accounts.scan()
        self.assertEqual((out["count"], out["unreadable"]), (1, 0))
        self.assertEqual(self.tx_reads(sig(1)), [0, 1])
        self.assertEqual(self.rows()[0]["amount_raw"], 3_000_000)

    def test_an_rpc_that_cannot_be_reached_stops_the_scan_where_it_was(self):
        # Not one transaction's fault: nothing is marked, nothing is skipped,
        # and the next scan reads it all.
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        self.chain.answers["getTransaction"] = HttpStatus(503)
        with self.assertRaises(hand.HandError):
            accounts.scan()
        self.chain.answers["getTransaction"] = self.ledger.transaction
        self.assertEqual(accounts.scan()["count"], 1)

    # --- a rate-limited RPC ---------------------------------------------------------------------

    def test_a_passing_429_on_get_transaction_is_waited_out_not_a_failed_scan(self):
        # Live, 2026-11-23: the public mainnet RPC answered getTransaction
        # HTTP 429 about 8 s into every hourly run, and the one 429 failed it.
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        read = self.ledger.transaction
        answers = [HttpStatus(429, {"Retry-After": "3"}), HttpStatus(429)]
        self.chain.answers["getTransaction"] = lambda params: answers.pop(0) if answers else read(params)
        out = accounts.scan()
        self.assertEqual((out["count"], out["rate_limited"]), (1, False))
        # Retry-After is honoured; with none, the wait backs off.
        self.assertEqual(self.slept, [3.0, accounts.BACKOFF_SECS * 2])

    def test_get_transaction_reads_are_paced(self):
        for n in range(1, 5):
            self.deposit(n, T0 - HOUR + n, usdc_tx(STRANGER, 1_000_000))
        accounts.scan()
        self.assertEqual(self.slept, [accounts.PACE_SECS] * 3)

    def test_a_scan_the_rpc_cuts_short_keeps_what_it_read_and_the_next_reads_the_rest(self):
        for n in range(1, 6):
            self.deposit(n, T0 - HOUR + n, usdc_tx(STRANGER, n * 1_000_000))
        self.chain.answers["getTransaction"] = limited_after(self.ledger.transaction, 2)
        out = accounts.scan()
        self.assertEqual((out["count"], out["rate_limited"], out["left"]), (2, True, 3))
        self.assertEqual(out["through"], accounts._iso(T0 - HOUR + 2))
        self.chain.answers["getTransaction"] = self.ledger.transaction
        before = len(self.chain.params("getTransaction"))
        again = accounts.scan()
        self.assertEqual((again["count"], again["rate_limited"], again["left"]), (3, False, 0))
        # Only the three it had not read are read again; each deposit is in the ledger once.
        self.assertEqual(len(self.chain.params("getTransaction")) - before, 3)
        self.assertEqual([r["signature"] for r in self.rows()], [sig(n) for n in range(1, 6)])

    def test_a_429_on_the_very_first_read_is_still_a_failed_scan(self):
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        self.chain.answers["getTransaction"] = HttpStatus(429)
        with self.assertRaises(hand.HandError) as caught:
            accounts.scan()
        self.assertIn("getTransaction answered HTTP 429", str(caught.exception))
        self.assertEqual(len(self.chain.params("getTransaction")), accounts.RATE_TRIES)
        # Nothing moved: the next scan starts where this one did.
        self.chain.answers["getTransaction"] = self.ledger.transaction
        self.assertEqual(accounts.scan()["count"], 1)

    def test_a_long_retry_after_is_capped(self):
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        read = self.ledger.transaction
        answers = [HttpStatus(429, {"Retry-After": "3600"})]
        self.chain.answers["getTransaction"] = lambda params: answers.pop(0) if answers else read(params)
        accounts.scan()
        self.assertEqual(self.slept, [accounts.MAX_WAIT_SECS])

    def test_a_scan_that_has_read_some_stops_inside_its_time_budget(self):
        # The box gives a shell job 300 s; every read here waits 30 s on a 429
        # first, so the scan must stop with what it has, well before that.
        for n in range(1, 21):
            self.deposit(n, T0 - HOUR + n, usdc_tx(STRANGER, 1_000_000))
        read = self.ledger.transaction
        refused: set[str] = set()

        def transaction(params: list[Any]) -> Any:
            if params[0] not in refused:
                refused.add(params[0])
                return HttpStatus(429, {"Retry-After": "30"})
            return read(params)

        self.chain.answers["getTransaction"] = transaction
        out = accounts.scan()
        self.assertLessEqual(sum(self.slept), accounts.SCAN_BUDGET_SECS)
        self.assertGreater(out["count"], 0)
        self.assertEqual(out["count"] + out["left"], 20)
        self.assertTrue(out["truncated"])
        self.chain.answers["getTransaction"] = read
        self.assertEqual(accounts.scan()["count"], out["left"])
        self.assertEqual(len(self.rows()), 20)

    def test_a_failed_first_read_leaves_the_scan_never_done(self):
        # The page counts a scan as recent from wallet_scan; a run that read
        # nothing must not look like one.
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        self.chain.answers["getTransaction"] = HttpStatus(429)
        with self.assertRaises(hand.HandError):
            accounts.scan()
        conn = accounts.db()
        try:
            self.assertIsNone(conn.execute("SELECT 1 FROM wallet_scan").fetchone())
        finally:
            conn.close()

    def test_transactions_that_were_no_deposit_are_not_read_again_after_a_cut_short_scan(self):
        # Three of Teeny's own transactions and a deposit in one block second,
        # the cap at two: the next scan reads only the two it had not.
        own = {"meta": {"err": None, "preBalances": [10**9], "postBalances": [10**9 - 5000]},
               "transaction": {"message": {"accountKeys": [{"pubkey": OWN, "signer": True}], "instructions": []}}}
        for n in range(1, 4):
            self.deposit(n, T0 - HOUR, own)
        self.deposit(4, T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        with mock.patch.object(accounts, "MAX_TXS", 2):
            first = accounts.scan()
            second = accounts.scan()
            third = accounts.scan()
        self.assertEqual((first["count"], first["left"]), (0, 2))
        self.assertEqual((second["count"], second["left"]), (1, 0))
        self.assertEqual(third["count"], 0)
        reads = [p[0] for p in self.chain.params("getTransaction")]
        self.assertEqual(sorted(reads), sorted(sig(n) for n in range(1, 5)))
        self.assertEqual([r["signature"] for r in self.rows()], [sig(4)])

    def test_listing_signatures_stops_at_the_scan_deadline(self):
        # Each page of signatures here takes 200 s: the scan must not start
        # another listing past its budget, and a scan that read nothing
        # leaves no record of a scan.
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 1_000_000), to=OWN)
        self.deposit(2, T0 - HOUR + 1, usdc_tx(STRANGER, 1_000_000))
        listed = self.ledger.signatures

        def slow(params: list[Any]) -> Any:
            accounts._sleep(200)
            return listed(params)

        self.chain.answers["getSignaturesForAddress"] = slow
        with self.assertRaises(hand.HandError) as caught:
            accounts.scan()
        self.assertIn("out of time", str(caught.exception))
        self.assertEqual(len(self.chain.params("getSignaturesForAddress")), 1)
        self.assertEqual(self.chain.params("getTransaction"), [])
        conn = accounts.db()
        try:
            self.assertIsNone(conn.execute("SELECT 1 FROM wallet_scan").fetchone())
        finally:
            conn.close()

    def test_a_transaction_that_could_not_be_read_into_a_deposit_is_read_again(self):
        # A token balance the RPC sent unreadable fails the scan; the next
        # scan, given good data, must read it again and record the deposit.
        bad = usdc_tx(STRANGER, 1_000_000)
        bad["meta"]["postTokenBalances"][1]["uiTokenAmount"] = {"amount": "lots"}
        self.deposit(1, T0 - HOUR, bad)
        with self.assertRaises(hand.HandError):
            accounts.scan()
        self.ledger.txs[sig(1)] = usdc_tx(STRANGER, 1_000_000)
        self.assertEqual(accounts.scan()["count"], 1)
        self.assertEqual([r["signature"] for r in self.rows()], [sig(1)])

    def test_the_per_run_cap_never_skips_a_transaction_sharing_the_last_ones_second(self):
        # Three transactions in one block second, the cap at two: the cursor
        # may not pass that second, or the third is never read.
        for n in range(1, 4):
            self.deposit(n, T0 - HOUR, usdc_tx(STRANGER, n * 1_000_000))
        with mock.patch.object(accounts, "MAX_TXS", 2):
            first = accounts.scan()
            self.assertEqual((first["count"], first["truncated"], first["left"]), (2, True, 1))
            second = accounts.scan()
        self.assertEqual((second["count"], second["truncated"]), (1, False))
        self.assertEqual(sorted(r["signature"] for r in self.rows()), sorted(sig(n) for n in range(1, 4)))

    # --- the page ------------------------------------------------------------------------------

    def fill_week(self) -> None:
        reads.bills_add("vultr abc123", "24.00", "month", None)
        reads.bills_add("api plan", "120", "year", None)
        self.deposit(1, T0 - 3 * DAY, usdc_tx(STRANGER, 5_255_000))
        self.deposit(2, T0 - 2 * DAY, usdc_tx(TEAM, 50_000_000))
        self.deposit(3, T0 - DAY, usdc_tx(STRANGER, 1_004_999))
        self.deposit(4, T0 - 9 * DAY, usdc_tx(STRANGER, 70_000_000))  # last week's, out of the window
        self.deposit(5, T0 - HOUR, sol_tx(STRANGER, 10**9), to=OWN)

    def test_the_page_reconciles_to_the_cent(self):
        self.fill_week()
        accounts.scan()
        accounts.tag(sig(3), "sale", None, ref="request:12")
        got = accounts.page(7)
        costs = reads.costs(7)
        self.assertTrue(got["complete"], got.get("incomplete"))
        # Each line is its source rounded once to the cent ...
        self.assertEqual(got["costs"]["models"], costs["models"]["usd"])  # 3.14
        self.assertEqual(got["costs"]["server"], "5.60")  # 24 a month, 7 of 30 days
        self.assertEqual(got["costs"]["tools"], "2.34")  # the plan's 2.333333 -> 2.33, x402's 0.012345 -> 0.01
        self.assertEqual(got["income"], {"tip": "5.26", "sale": "1.00", "bounty": "0.00", "refunds": "0.00", "total": "6.26"})
        self.assertEqual(got["team"], "50.00")
        # ... and every total is the sum of the lines above it.
        cents = lambda v: round(float(v) * 100)  # noqa: E731
        self.assertEqual(cents(got["costs"]["total"]), sum(cents(got["costs"][k]) for k in ("models", "server", "tools")))
        self.assertEqual(cents(got["net"]), cents(got["income"]["total"]) - cents(got["costs"]["total"]))
        self.assertEqual(got["sol_received_lamports"], {"tip": str(10**9)})
        self.assertEqual(sorted(i["signature"] for i in got["inflows"]), sorted(sig(n) for n in (1, 2, 3, 5)))
        self.assertIn("network fees", got["not_counted"])

    def test_a_cost_that_cannot_be_read_makes_the_page_incomplete(self):
        del self.core.answers["openhuman.cost_report"]
        accounts.scan()
        got = accounts.page(7)
        self.assertFalse(got["complete"])
        self.assertEqual(got["incomplete"], ["models"])

    def test_unpriced_model_calls_make_the_page_incomplete_and_unclosable(self):
        self.core.answers["openhuman.cost_report"] = {"totals": {"calls": 10, "cost_usd": 0, "unpriced_calls": 10}}
        accounts.scan()
        got = accounts.page(7)
        self.assertFalse(got["complete"])
        self.assertEqual(got["incomplete"], ["models (10 of 10 calls unpriced)"])
        with self.assertRaises(hand.Refused) as no:
            accounts.close()
        self.assertEqual(no.exception.detail["rule"], "incomplete")
        self.assertEqual(accounts.closed_pages(), [])

    def test_no_recent_scan_makes_the_page_incomplete(self):
        got = accounts.page(7)
        self.assertEqual(got["incomplete"], ["inflows"])
        accounts.scan()
        self.at(T0 + 2 * HOUR)
        self.assertIn("inflows", accounts.page(7)["incomplete"])

    # --- closing the week ----------------------------------------------------------------------

    def test_the_week_is_closed_once_on_a_monday(self):
        self.fill_week()
        out = accounts.close()  # T0 is Monday 2026-09-14
        self.assertTrue(out["closed"])
        self.assertEqual(out["week"], "2026-W37")
        self.assertEqual(accounts.close(), {"closed": False, "week": "2026-W37", "why": "already closed"})
        (page,) = accounts.closed_pages()
        self.assertEqual(page["week"], "2026-W37")
        self.assertEqual(page["income"]["total"], "6.26")
        self.assertEqual(self.moved(), [])

    def test_closing_is_off_by_default(self):
        self.configure(WALLET_ACCOUNTS=None)
        self.assertIn("WALLET_ACCOUNTS", accounts.close()["skipped"])
        self.assertEqual(self.chain.calls, [])
        self.assertEqual(accounts.closed_pages(), [])

    def test_closing_is_refused_off_a_monday(self):
        self.at(T0 + DAY)
        with self.assertRaises(hand.Refused) as no:
            accounts.close()
        self.assertEqual(no.exception.detail["rule"], "not_monday")

    def test_an_incomplete_week_is_never_closed(self):
        del self.core.answers["openhuman.cost_report"]
        with self.assertRaises(hand.Refused) as no:
            accounts.close()
        self.assertEqual(no.exception.detail["rule"], "incomplete")
        self.assertEqual(accounts.closed_pages(), [])

    def test_the_verbs_answer_through_the_hand(self):
        import subprocess, sys  # noqa: E401
        wallet = Path(__file__).resolve().parents[2] / "wallet"
        self.deposit(1, T0 - HOUR, usdc_tx(STRANGER, 2_000_000))
        run = subprocess.run([sys.executable, str(wallet), "inflows", "scan"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)["count"], 1)
        run = subprocess.run([sys.executable, str(wallet), "accounts", "--days", "7"], capture_output=True, text=True)
        self.assertEqual(json.loads(run.stdout)["income"]["tip"], "2.00")

    def test_the_pinned_job_closes_the_week(self):
        import os, subprocess  # noqa: E401
        hands = Path(__file__).resolve().parents[2]
        job = hands.parent / "bin" / "accounts"
        self.fill_week()
        done = subprocess.run([str(job)], capture_output=True, text=True, env={**os.environ, "TEENY_HANDS": str(hands)}, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["week"], "2026-W37")
        # Switched off is skipped, exit 0: the scheduler counts non-zero as a failed job.
        self.configure(WALLET_ACCOUNTS=None)
        done = subprocess.run([str(job)], capture_output=True, text=True, env={**os.environ, "TEENY_HANDS": str(hands)}, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("WALLET_ACCOUNTS", json.loads(done.stdout)["skipped"])
        # Switched on, a real refusal still exits 2.
        self.configure(WALLET_ACCOUNTS="1")
        self.at(T0 + DAY)
        done = subprocess.run([str(job)], capture_output=True, text=True, env={**os.environ, "TEENY_HANDS": str(hands)}, timeout=60)
        self.assertEqual((done.returncode, json.loads(done.stdout)["rule"]), (2, "not_monday"))
