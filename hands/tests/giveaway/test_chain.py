"""The on-chain step (teeny_hands/giveaway/onchain.py) against a fake core and
a fake giveaway RPC: the prepare -> check -> execute flow, the refusals when
the core's quote or simulation is not the plan, and the attempts that make a
retry safe (same round, never a second distribution; in_doubt never resent).

The transactions are the shared vectors in fixtures/rewards_tx.json, which
@solana/web3.js made the way web/src/lib/distributor.ts encodes the program
and which spine/src/giveaway_tx.rs reproduces byte for byte (its own tests).
The fake core summarises the transaction it is handed the way the real one
does, so the account order the hand expects is checked against them too.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import unittest
from pathlib import Path
from typing import Any

from giveaway.support import DEADLINE, HOUR, GiveawayCase, T0
from giveaway.test_rounds import RoundCase
from teeny_hands import hand
from teeny_hands.giveaway import chain, onchain, rounds, store
from teeny_hands.wallet import solana

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "rewards_tx.json").read_text())
SIG = "5" + "K" * 87
CLOSE_SIG = "4" + "Q" * 87
REPO = Path(__file__).resolve().parents[3]


def summarise(wire: bytes) -> dict[str, Any]:
    """What the core's prepare_program says of a legacy transaction."""
    at = 0

    def shortvec() -> int:
        nonlocal at
        value = shift = 0
        while True:
            byte = wire[at]
            at += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7

    sigs = shortvec()
    at += 64 * sigs
    at += 3
    keys = []
    for _ in range(shortvec()):
        keys.append(solana.b58encode(wire[at:at + 32]))
        at += 32
    blockhash = solana.b58encode(wire[at:at + 32])
    at += 32
    instructions = []
    for _ in range(shortvec()):
        program = keys[wire[at]]
        at += 1
        accounts = [keys[wire[at + i]] for i in range(shortvec())]
        at += len(accounts)
        size = shortvec()
        instructions.append({"programId": program, "accounts": accounts, "dataHex": wire[at:at + size].hex()})
        at += size
    programs = list(dict.fromkeys(ix["programId"] for ix in instructions))
    return {"feePayer": keys[0], "signers": keys[:sigs], "recentBlockhash": blockhash, "programs": programs,
            "instructions": instructions, "movements": [], "totals": []}


class FakeCore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.address = VECTORS["authority"]
        self.tamper = None
        self.prepare_error: str | None = None
        self.execute_error: str | None = None

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    def __call__(self, method: str, params: dict, timeout: float = 0) -> Any:
        self.calls.append((method, dict(params)))
        if method == "wallet_account":
            return {"chain": "solana", "address": self.address, "index": 1, "label": "giveaway"}
        if method == "wallet_prepare_program":
            if self.prepare_error:
                raise hand.CoreError(f"openhuman.wallet_prepare_program: {self.prepare_error}")
            debits = params["expectedDebits"]
            quote = {"quoteId": f"prog-{len(self.calls)}", "chain": "solana", "accountIndex": params["accountIndex"],
                     "address": self.address, "declaredDebits": debits,
                     "simulation": {"debits": debits, "tokenAccounts": []},
                     "summary": summarise(base64.b64decode(params["transaction"]))}
            if self.tamper:
                self.tamper(quote)
            return quote
        if method == "wallet_execute_prepared_program":
            if self.execute_error:
                raise hand.CoreError(f"openhuman.wallet_execute_prepared_program: {self.execute_error}")
            return {"quoteId": params["quoteId"], "accountIndex": 1, "address": self.address,
                    "transactionHash": SIG}
        raise AssertionError(f"unexpected core call {method}")


def distribution_bytes(plan: dict, seed: str, closed: bool = False) -> bytes:
    if closed:
        return bytes([onchain.ACCOUNT_DISTRIBUTION_CLOSED, 1, 255])
    data = bytearray(162)
    data[0:4] = bytes([2, 1, 255, 0])
    data[10:42] = solana.b58decode(plan["authority"])
    data[42:74] = solana.b58decode(plan["mint"])
    data[74:106] = solana.b58decode(seed)
    data[106:138] = bytes.fromhex(plan["merkle_root"])
    struct.pack_into("<QQq", data, 138, int(plan["total_amount"]), 0, int(plan["clawback_ts"]))
    return bytes(data)


class FakeRpc:
    def __init__(self, cluster: str = "devnet") -> None:
        self.genesis = onchain.GENESIS[cluster]
        self.accounts: dict[str, bytes] = {}
        self.height = 100
        self.status: dict | None = {"err": None, "confirmationStatus": "confirmed"}
        self.signatures: list[dict] = []
        self.calls: list[str] = []

    def __call__(self, method: str, params: list) -> Any:
        self.calls.append(method)
        if method == "getGenesisHash":
            return self.genesis
        if method == "getLatestBlockhash":
            return {"context": {"slot": 1}, "value": {"blockhash": VECTORS["recent_blockhash"], "lastValidBlockHeight": 250}}
        if method == "getSignatureStatuses":
            return {"value": [self.status]}
        if method == "getAccountInfo":
            data = self.accounts.get(params[0])
            return {"value": None if data is None else {"data": [base64.b64encode(data).decode(), "base64"]}}
        if method == "getBlockHeight":
            return self.height
        if method == "getSignaturesForAddress":
            return self.signatures
        raise AssertionError(f"unexpected RPC {method}")


def vector_build(op: str, request: dict) -> dict:
    """The spine's answer for the vectors' plan (the real one makes a fresh seed)."""
    for key in ("program_id", "mint", "token_program", "authority"):
        assert request[key] == VECTORS[key], key
    common = {"distribution": VECTORS["distribution"], "vault": VECTORS["vault"],
              "authority_token_account": VECTORS["authority_token_account"], "event_authority": VECTORS["event_authority"]}
    if op == "create":
        return {**common, "transaction_b64": VECTORS["create_transaction_b64"], "distribution_bump": VECTORS["distribution_bump"],
                "seed_pubkey": VECTORS["seed_pubkey"], "data_hex": VECTORS["create_data_hex"]}
    return {**common, "transaction_b64": VECTORS["close_transaction_b64"], "data_hex": "08"}


class ChainCase(GiveawayCase):
    def setUp(self) -> None:
        super().setUp()
        self.fake_core = FakeCore()
        self.rpc = FakeRpc()
        self.slept: list[float] = []
        self.clock_at = 0.0
        self.impl = onchain.CoreChain(core=self.fake_core, rpc=self.rpc, build=vector_build,
                                      sleep=self.sleep, clock=lambda: self.clock_at)
        conn = store.db()
        try:
            conn.execute("INSERT INTO giveaway_rounds (id, created_at, format, pool_raw, winners, best_reply_raw, prompt,"
                         " deadline, cluster, post_id, status) VALUES (1, ?, 'drop', 3500042, 1, 0, 'p', ?, 'devnet', '5000', 'drawn')",
                         (T0, DEADLINE))
        finally:
            conn.close()

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock_at += seconds

    def plan(self, **overrides: Any) -> dict:
        plan = {"round_id": 1, "cluster": "devnet", "mint": VECTORS["mint"], "decimals": 6,
                "token_program": VECTORS["token_program"], "program_id": VECTORS["program_id"],
                "authority": VECTORS["authority"], "merkle_root": VECTORS["merkle_root"],
                "total_amount": int(VECTORS["total_amount"]), "clawback_ts": VECTORS["clawback_ts"], "leaves": 3}
        plan.update(overrides)
        return plan

    def close_plan(self) -> dict:
        p = self.plan()
        return {k: p[k] for k in ("round_id", "cluster", "mint", "token_program", "program_id", "authority")} | {
            "distributor": VECTORS["distribution"], "vault": VECTORS["vault"], "distribution_seed": VECTORS["seed_pubkey"]}

    def attempts(self, op: str = "create") -> list[dict]:
        conn = store.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM giveaway_chain WHERE op = ? ORDER BY id", (op,))]
        finally:
            conn.close()

    def refused(self, rule: str, call, *args) -> hand.Refused:
        with self.assertRaises(hand.Refused) as caught:
            call(*args)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        return caught.exception

    def doubted(self, call, *args) -> hand.HandError:
        with self.assertRaises(hand.HandError) as caught:
            call(*args)
        self.assertIn("in_doubt", str(caught.exception))
        return caught.exception


class CreateTest(ChainCase):
    def test_happy_path_prepares_checks_and_executes_once(self):
        out = self.impl.create_distribution(self.plan())
        self.assertEqual(out, {"distribution_seed": VECTORS["seed_pubkey"], "distributor": VECTORS["distribution"],
                               "vault": VECTORS["vault"], "txs": {"create": SIG}})
        self.assertEqual(self.fake_core.methods(), ["wallet_account", "wallet_prepare_program", "wallet_execute_prepared_program"])
        prepare = self.fake_core.calls[1][1]
        self.assertEqual(prepare["accountIndex"], 1)
        self.assertEqual(prepare["expectedDebits"], [{"mint": VECTORS["mint"], "amountRaw": "3500042"}])
        self.assertEqual(self.fake_core.calls[2][1], {"accountIndex": 1, "quoteId": "prog-2"})
        [attempt] = self.attempts()
        self.assertEqual((attempt["state"], attempt["tx"], attempt["seed"]), ("done", SIG, VECTORS["seed_pubkey"]))

    def test_the_same_round_never_makes_a_second_distribution(self):
        first = self.impl.create_distribution(self.plan())
        self.fake_core.calls.clear()
        self.assertEqual(self.impl.create_distribution(self.plan()), first)
        self.assertEqual(self.fake_core.calls, [])
        self.assertEqual(len(self.attempts()), 1)

    def test_a_quote_whose_root_total_or_accounts_differ_is_refused(self):
        def root(quote):
            ix = quote["summary"]["instructions"][0]
            ix["dataHex"] = ix["dataHex"][:22] + "00" * 32 + ix["dataHex"][86:]

        def total(quote):
            ix = quote["summary"]["instructions"][0]
            data = bytearray.fromhex(ix["dataHex"])
            struct.pack_into("<Q", data, 3, 9_999_999)
            ix["dataHex"] = data.hex()

        def vault(quote):
            quote["summary"]["instructions"][0]["accounts"][5] = VECTORS["authority_token_account"]

        def program(quote):
            quote["summary"]["programs"].append(VECTORS["token_program"])

        for tamper, field in ((root, "data"), (total, "data"), (vault, "accounts"), (program, "programs")):
            self.fake_core.tamper = tamper
            self.fake_core.calls.clear()
            caught = self.refused("quote", self.impl.create_distribution, self.plan())
            self.assertIn(field, caught.detail["fields"])
            self.assertNotIn("wallet_execute_prepared_program", self.fake_core.methods())
        self.assertEqual({a["state"] for a in self.attempts()}, {"failed"})

    def test_a_plan_the_builder_did_not_build_is_refused(self):
        # The builder (and so the transaction) says 3500042; a plan of one more is not what was built.
        caught = self.refused("quote", self.impl.create_distribution, self.plan(total_amount=3_500_043))
        self.assertEqual(caught.detail["fields"], ["data"])

    def test_a_simulation_that_errs_or_debits_otherwise_is_refused(self):
        def err(quote):
            quote["simulation"]["err"] = {"InstructionError": [0, "Custom"]}

        def more(quote):
            quote["simulation"]["debits"] = [{"mint": VECTORS["mint"], "amountRaw": "3500043"}]

        def none(quote):
            quote["simulation"]["debits"] = []

        for tamper in (err, more, none):
            self.fake_core.tamper = tamper
            self.refused("simulation", self.impl.create_distribution, self.plan())
        self.assertNotIn("wallet_execute_prepared_program", self.fake_core.methods())

    def test_the_cores_policy_refusal_is_a_refusal(self):
        self.fake_core.prepare_error = "WALLET_POLICY_REFUSED: account solana_1 has no policy section"
        self.refused("core_policy", self.impl.create_distribution, self.plan())
        self.assertEqual(self.attempts()[0]["state"], "refused")
        self.fake_core.prepare_error = None
        self.fake_core.execute_error = "WALLET_POLICY_REFUSED: daily cap"
        self.refused("core_policy", self.impl.create_distribution, self.plan())
        self.assertEqual([a["state"] for a in self.attempts()], ["refused", "refused"])

    def test_cluster_and_authority_are_checked_before_anything_is_built(self):
        self.configure(WALLET_CLUSTER="mainnet")
        self.refused("cluster", self.impl.create_distribution, self.plan())
        self.configure()
        self.rpc.genesis = onchain.GENESIS["mainnet"]
        self.refused("cluster", self.impl.create_distribution, self.plan())
        self.rpc.genesis = onchain.GENESIS["devnet"]
        self.fake_core.address = solana.b58encode(bytes([3]) * 32)
        self.refused("authority", self.impl.create_distribution, self.plan())
        self.assertEqual(self.attempts(), [])
        self.assertNotIn("wallet_prepare_program", self.fake_core.methods())


class InDoubtTest(ChainCase):
    def lose_the_execute(self) -> None:
        self.fake_core.execute_error = "core unreachable"
        self.doubted(self.impl.create_distribution, self.plan())
        self.assertEqual(self.attempts()[0]["state"], "in_doubt")
        self.fake_core.execute_error = None
        self.fake_core.calls.clear()

    def test_in_doubt_is_never_sent_again_while_it_could_still_land(self):
        self.lose_the_execute()
        self.doubted(self.impl.create_distribution, self.plan())
        self.assertNotIn("wallet_prepare_program", self.fake_core.methods())
        self.assertEqual(len(self.attempts()), 1)

    def test_in_doubt_that_landed_is_the_distribution(self):
        self.lose_the_execute()
        self.rpc.accounts[VECTORS["distribution"]] = distribution_bytes(self.plan(), VECTORS["seed_pubkey"])
        self.rpc.signatures = [{"signature": "3" + "Z" * 87, "err": None}, {"signature": SIG, "err": None}]
        out = self.impl.create_distribution(self.plan())
        self.assertEqual((out["distributor"], out["txs"]), (VECTORS["distribution"], {"create": SIG}))
        self.assertEqual(self.fake_core.calls, [])
        self.assertEqual(self.attempts()[0]["state"], "done")

    def test_a_landed_account_that_is_not_the_plan_stops_for_a_person(self):
        self.lose_the_execute()
        self.rpc.accounts[VECTORS["distribution"]] = distribution_bytes(self.plan(total_amount=1), VECTORS["seed_pubkey"])
        with self.assertRaises(hand.HandError) as caught:
            self.impl.create_distribution(self.plan())
        self.assertIn("a person has to look", str(caught.exception))

    def test_in_doubt_whose_blockhash_expired_unlanded_is_dead_and_tried_afresh(self):
        self.lose_the_execute()
        self.rpc.height = 251  # past lastValidBlockHeight 250: it can never land
        out = self.impl.create_distribution(self.plan())
        self.assertEqual(out["txs"], {"create": SIG})
        self.assertEqual([a["state"] for a in self.attempts()], ["dead", "done"])

    def test_a_sent_transaction_that_fails_on_chain_is_failed_and_one_never_confirmed_stays_sent(self):
        self.rpc.status = {"err": {"InstructionError": [0, "Custom"]}, "confirmationStatus": "confirmed"}
        with self.assertRaises(hand.HandError) as caught:
            self.impl.create_distribution(self.plan())
        self.assertIn("failed on chain", str(caught.exception))
        self.assertEqual(self.attempts()[-1]["state"], "failed")
        self.rpc.status = None
        self.doubted(self.impl.create_distribution, self.plan())
        last = self.attempts()[-1]
        self.assertEqual((last["state"], last["tx"]), ("sent", SIG))
        self.assertGreaterEqual(sum(self.slept), onchain.CONFIRM_SECONDS)
        self.fake_core.calls.clear()
        self.doubted(self.impl.create_distribution, self.plan())  # still within its blockhash: never resent
        self.assertEqual(self.fake_core.calls, [])


class CloseTest(ChainCase):
    def setUp(self) -> None:
        super().setUp()
        self.rpc.accounts[VECTORS["distribution"]] = distribution_bytes(self.plan(), VECTORS["seed_pubkey"])

    def test_close_prepares_with_no_debits_and_returns_the_signature(self):
        self.assertEqual(self.impl.close_distribution(self.close_plan()), {"tx": SIG})
        prepare = dict(self.fake_core.calls)["wallet_prepare_program"]
        self.assertEqual(prepare["expectedDebits"], [])
        self.fake_core.calls.clear()
        self.assertEqual(self.impl.close_distribution(self.close_plan()), {"tx": SIG})
        self.assertEqual(self.fake_core.calls, [])

    def test_a_close_quote_with_other_data_or_a_debit_is_refused(self):
        def data(quote):
            quote["summary"]["instructions"][0]["dataHex"] = "09"

        self.fake_core.tamper = data
        self.refused("quote", self.impl.close_distribution, self.close_plan())

        def debit(quote):
            quote["simulation"]["debits"] = [{"mint": VECTORS["mint"], "amountRaw": "1"}]

        self.fake_core.tamper = debit
        self.refused("simulation", self.impl.close_distribution, self.close_plan())

    def test_a_distribution_not_open_on_chain_or_not_the_rounds_is_not_closed(self):
        del self.rpc.accounts[VECTORS["distribution"]]
        with self.assertRaises(hand.HandError):
            self.impl.close_distribution(self.close_plan())
        self.rpc.accounts[VECTORS["distribution"]] = distribution_bytes(self.plan(), VECTORS["authority"])
        with self.assertRaises(hand.HandError):
            self.impl.close_distribution(self.close_plan())
        self.assertNotIn("wallet_prepare_program", self.fake_core.methods())

    def test_a_close_in_doubt_that_landed_is_read_back_from_the_chain(self):
        self.fake_core.execute_error = "timed out"
        self.doubted(self.impl.close_distribution, self.close_plan())
        self.fake_core.execute_error = None
        self.doubted(self.impl.close_distribution, self.close_plan())
        self.rpc.accounts[VECTORS["distribution"]] = distribution_bytes(self.plan(), VECTORS["seed_pubkey"], closed=True)
        self.rpc.signatures = [{"signature": CLOSE_SIG, "err": None}, {"signature": SIG, "err": None}]
        self.assertEqual(self.impl.close_distribution(self.close_plan()), {"tx": CLOSE_SIG})


class VectorTest(unittest.TestCase):
    """The program's bytes, read the way distributor.ts reads them."""

    def test_create_data_carries_the_root_total_and_clawback(self):
        fields = onchain.parse_create_data(VECTORS["create_data_hex"])
        self.assertEqual(fields, {"bump": VECTORS["distribution_bump"], "revocable": 0, "amount": 3_500_042,
                                  "root": VECTORS["merkle_root"], "total_amount": 3_500_042, "clawback_ts": 1_798_000_000})
        self.assertIsNone(onchain.parse_create_data("08"))
        self.assertIsNone(onchain.parse_create_data("zz"))

    def test_the_vector_transactions_have_the_accounts_the_hand_expects(self):
        create = summarise(base64.b64decode(VECTORS["create_transaction_b64"]))
        self.assertEqual(create["signers"], [VECTORS["authority"], VECTORS["seed_pubkey"]])
        self.assertEqual(create["instructions"][0]["accounts"][:6], [VECTORS["authority"], VECTORS["authority"], VECTORS["seed_pubkey"],
                                                                     VECTORS["distribution"], VECTORS["mint"], VECTORS["vault"]])
        close = summarise(base64.b64decode(VECTORS["close_transaction_b64"]))
        self.assertEqual(close["signers"], [VECTORS["authority"]])
        self.assertEqual(close["instructions"][0]["dataHex"], "08")

    def test_a_distribution_account_and_its_closed_marker(self):
        plan = {"authority": VECTORS["authority"], "mint": VECTORS["mint"], "merkle_root": VECTORS["merkle_root"],
                "total_amount": 3_500_042, "clawback_ts": 1_798_000_000}
        parsed = onchain.parse_distribution(distribution_bytes(plan, VECTORS["seed_pubkey"]))
        self.assertEqual(parsed, {"closed": False, "authority": VECTORS["authority"], "mint": VECTORS["mint"],
                                  "seed": VECTORS["seed_pubkey"], "root": VECTORS["merkle_root"], "total_amount": 3_500_042,
                                  "total_claimed": 0, "clawback_ts": 1_798_000_000})
        self.assertEqual(onchain.parse_distribution(bytes([6, 1, 255])), {"closed": True})
        self.assertIsNone(onchain.parse_distribution(bytes([9, 9])))


def _spine() -> str | None:
    for profile in ("release", "debug"):
        path = REPO / "target" / profile / "teeny-spine"
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


@unittest.skipUnless(_spine(), "no teeny-spine build in target/ (cargo build -p teeny-spine)")
class SpineTest(ChainCase):
    """The real builder, a fresh seed each time, through settle."""

    def setUp(self) -> None:
        super().setUp()
        os.environ["OPENHUMAN_CORE_BIN"] = _spine()
        self.impl.build = onchain.spine_build

    def test_the_spines_create_passes_the_hands_checks(self):
        out = self.impl.create_distribution(self.plan())
        self.assertNotEqual(out["distribution_seed"], VECTORS["seed_pubkey"])  # a fresh one
        self.assertEqual(out["txs"], {"create": SIG})
        built = base64.b64decode(self.fake_core.calls[1][1]["transaction"])
        self.assertEqual(summarise(built)["signers"][1], out["distribution_seed"])



@unittest.skipUnless(_spine(), "no teeny-spine build in target/ (cargo build -p teeny-spine)")
class SpineSettleTest(RoundCase):
    """A drawn round settles through the core: the plan rounds.py makes is the
    one the spine builds and the hand's checks pass."""

    def setUp(self):
        super().setUp()
        self.opened()
        for n, handle in enumerate(("alice", "bob", "carol", "dave", "erin"), 1):
            self.entrant(n, handle)
        self.judged("erin")
        self.at(DEADLINE + 2 * HOUR)
        rounds.draw_round(1)
        os.environ["OPENHUMAN_CORE_BIN"] = _spine()
        self.fake_core = FakeCore()
        self.fake_core.address = hand.setting("GIVEAWAY_AUTHORITY")
        chain.use(onchain.CoreChain(core=self.fake_core, rpc=FakeRpc(), sleep=lambda s: None))

    def test_a_round_settles_through_the_core(self):
        self.remote()
        out = rounds.settle_round(1)
        row = self.row()
        self.assertEqual(row["status"], "settled", out)
        self.assertEqual((row["distributor"], row["tx_create"]), (out["chain"]["distributor"], SIG))
        prepare = self.fake_core.calls[1][1]
        self.assertEqual(prepare["expectedDebits"], [{"mint": hand.setting("GIVEAWAY_DEVNET_MINT"), "amountRaw": str(row["total_raw"])}])
