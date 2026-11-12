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

from wallet.support import DAY, HOUR, MOVING, OWN, STRANGER, T0, USDC_MAINNET, Scratch, token_accounts
from teeny_hands import hand, stimuli
from teeny_hands.wallet import accounts, reads

TEAM = "Hx6LbkMHe69DYawhPyVNs8Apa6tyfogfzQV6a7XkwKcE"
TOKEN_ACCOUNT = "TokenAcct1111111111111111111111111111111111"


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
        self.deposit(2, T0 - HOUR, usdc_tx(STRANGER, 1_000_000, memo="kill yourself, bot"))
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
        self.assertEqual(got["costs"]["tools"], "2.35")  # 10 a month: 2.333333 -> 2.33, x402 0.012345 -> 0.01, plus
        self.assertEqual(got["income"], {"tip": "5.26", "sale": "1.00", "bounty": "0.00", "total": "6.26"})
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
        with self.assertRaises(hand.Refused) as no:
            accounts.close()
        self.assertEqual(no.exception.detail["rule"], "off")
        self.assertEqual(self.chain.calls, [])

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


class AccountsStimuliUntouched(Scratch):
    """S1 alone files nothing in the mind's inbox: the deposit watcher (A5) does."""

    settings = {"WALLET_CLUSTER": "mainnet", "WALLET_TEAM_ADDRESSES": TEAM}

    def test_a_scan_files_no_stimulus(self):
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(0, USDC_MAINNET)
        self.chain.answers["getSignaturesForAddress"] = []
        accounts.scan()
        self.assertEqual(stimuli.counts(), {})
        self.assertEqual([m for m in self.core.methods() if m in MOVING], [])
