"""The on-chain step of a round, signed by the giveaway wallet inside the core.

`CoreChain` is chain.py's `Chain`: it creates a round's merkle distribution on
the Solana Foundation Rewards program, funded with exactly the round's total,
and closes it after the clawback time. The giveaway wallet is Solana account
index 1 (`m/44'/501'/1'/0'`) in the core; its key never leaves the core, and
it signs only what `[wallet_policy.accounts.solana_1]` allows (written by
bin/lib/wallet_config.py from caps.policy_account: the Rewards program alone,
the round's mint within GIVEAWAY_POOL_MAX_TINY a transaction and
GIVEAWAY_SEASON_MAX_TINY a day).

One step, the same prepare -> check -> execute as hands/wallet's pay:

1. Build. `teeny-spine giveaway-tx create|close` (spine/src/giveaway_tx.rs)
   builds the unsigned legacy transaction over a fresh blockhash from
   GIVEAWAY_RPC_URL. For a create it makes a throwaway seed keypair, which
   signs at once and is forgotten: the distribution's address is
   PDA["merkle_distribution", mint, authority, seed].
2. Record. The attempt is written to `giveaway_chain` (seed, distributor,
   vault, blockhash and the height it expires at) before anything is sent.
3. Prepare. `openhuman.wallet_prepare_program {accountIndex: 1, transaction,
   expectedDebits}`: the core checks its policy and simulates. The quote it
   returns is checked here against the plan, never against the builder: one
   instruction, to the Rewards program, with exactly the plan's accounts, and
   data carrying exactly its root, total (as amount and total_amount) and
   clawback time; the simulation clean and debiting exactly the total of the
   mint (nothing, for a close). Any difference is refused (rule `quote` or
   `simulation`) and nothing is executed.
4. Execute. The attempt is marked `sent` first, then
   `openhuman.wallet_execute_prepared_program`. A `WALLET_POLICY_REFUSED`
   answer is `refused` (rule `core_policy`; nothing was sent). Any other
   failure is `in_doubt`. A signature is waited on (CONFIRM_SECONDS) until the
   cluster confirms it: an error there is `failed` (nothing was made, so a
   later run may try again), no answer leaves it `sent`.

Idempotency. A round has at most one distribution. Before building, the
round's earlier attempts are read: one `done` is the answer; one `sent` or
`in_doubt` is settled from the chain (finalized): the distribution there and
matching the plan is `done`, and absent, it is `dead` only once the chain's
finalized block height has passed the attempt's blockhash's last valid height,
when that transaction can never land; otherwise it stays `in_doubt` and the
call raises HandError with "in_doubt" in it, for a person or a later run.
Nothing is ever sent twice on a guess.

Safety checks before any of it (rule `cluster` or `authority`): the plan's
cluster is the core's (WALLET_CLUSTER: the core signs and sends on its own
cluster), the giveaway RPC answers with that cluster's genesis hash, and the
plan's authority (GIVEAWAY_AUTHORITY) is the address the core has for Solana
index 1.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import hand
from ..wallet import caps as wallet_caps
from ..wallet import solana
from . import caps, draw, store

Json = dict[str, Any]
Core = Callable[..., Any]
Rpc = Callable[[str, list[Any]], Any]
Build = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]

POLICY_REFUSED = "WALLET_POLICY_REFUSED"
SPINE_COMMAND = "giveaway-tx"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
IX_CREATE = 5
IX_CLOSE = 8
#: Account discriminators (distributor.ts): a live distribution, a closed one.
ACCOUNT_DISTRIBUTION = 2
ACCOUNT_DISTRIBUTION_CLOSED = 6
GENESIS = {
    "devnet": "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG",
    "mainnet": "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
}
#: How long a sent transaction is waited on before it is left `sent`.
CONFIRM_SECONDS = 60.0
CONFIRM_POLL = 2.0
FINALIZED = {"commitment": "finalized"}
SIGNATURE_PAGE = 1000


# --- reading the program's bytes ------------------------------------------------------------


def parse_distribution(data: bytes) -> Json | None:
    """A MerkleDistribution account, as distributor.ts's parseDistribution:
    [disc 2][version][bump][revocable][6 pad][authority][mint][seed][root]
    [u64 total][u64 claimed][i64 clawback_ts]; {"closed": True} for the
    closed marker; None for anything else."""
    if len(data) >= 2 and data[0] == ACCOUNT_DISTRIBUTION_CLOSED:
        return {"closed": True}
    if len(data) < 162 or data[0] != ACCOUNT_DISTRIBUTION:
        return None
    total, claimed, clawback = struct.unpack_from("<QQq", data, 138)
    return {
        "closed": False,
        "authority": solana.b58encode(data[10:42]),
        "mint": solana.b58encode(data[42:74]),
        "seed": solana.b58encode(data[74:106]),
        "root": data[106:138].hex(),
        "total_amount": total,
        "total_claimed": claimed,
        "clawback_ts": clawback,
    }


def parse_create_data(data_hex: str) -> Json | None:
    """CreateMerkleDistribution's data: 5, bump, revocable, u64 amount,
    root[32], u64 total_amount, i64 clawback_ts (59 bytes)."""
    try:
        data = bytes.fromhex(data_hex)
    except (TypeError, ValueError):
        return None
    if len(data) != 59 or data[0] != IX_CREATE:
        return None
    amount, = struct.unpack_from("<Q", data, 3)
    total, clawback = struct.unpack_from("<Qq", data, 43)
    return {"bump": data[1], "revocable": data[2], "amount": amount, "root": data[11:43].hex(),
            "total_amount": total, "clawback_ts": clawback}


# --- the default seams -------------------------------------------------------------------------


def spine_bin() -> str | None:
    """The spine binary: OPENHUMAN_CORE_BIN (the image sets it), else this
    checkout's release build, else `teeny-spine` on PATH."""
    named = os.environ.get("OPENHUMAN_CORE_BIN")
    for candidate in ([named] if named else []) + [str(hand.root() / "target" / "release" / "teeny-spine")]:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("teeny-spine")


def spine_build(op: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
    binary = spine_bin()
    if binary is None:
        raise hand.Refused("no spine binary builds the transaction (OPENHUMAN_CORE_BIN); nothing was signed",
                           rule="chain_not_ready")
    try:
        done = subprocess.run([binary, SPINE_COMMAND, op], input=json.dumps(dict(request)), capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise hand.HandError(f"the spine could not build the {op} transaction ({type(exc).__name__})") from None
    if done.returncode != 0:
        raise hand.HandError(f"the spine refused to build the {op} transaction: {done.stderr.strip()[:300]}")
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise hand.HandError(f"the spine answered the {op} build with something unreadable") from None


def _core(method: str, params: Mapping[str, Any], timeout: float = 120.0) -> Any:
    return hand.core(method, params, timeout=timeout)


def _rpc(method: str, params: list[Any]) -> Any:
    return draw.rpc(method, params)


# --- the chain ---------------------------------------------------------------------------------


class CoreChain:
    """chain.Chain, signed by the core's Solana index 1. Every seam can be
    stood in for: `core` (the JSON-RPC call), `rpc` (the giveaway RPC),
    `build` (the spine), `sleep` and `clock`."""

    def __init__(self, core: Core | None = None, rpc: Rpc | None = None, build: Build | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.core = core or _core
        self.rpc = rpc or _rpc
        self.build = build or spine_build
        self.sleep = sleep
        self.clock = clock

    # --- public ------------------------------------------------------------------------------

    def create_distribution(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        self._check_cluster(plan)
        round_id = int(plan["round_id"])
        conn = store.db()
        try:
            for attempt in _attempts(conn, round_id, "create"):
                if attempt["state"] == "done":
                    return _created(attempt)
                if attempt["state"] in ("sent", "in_doubt"):
                    settled = self._settle_create(conn, attempt, plan)
                    if settled is not None:
                        return settled
            self._check_authority(plan)
            blockhash, last_valid = self._blockhash()
            built = self.build("create", {
                "program_id": plan["program_id"], "mint": plan["mint"], "token_program": plan["token_program"],
                "authority": plan["authority"], "merkle_root": plan["merkle_root"],
                "total_amount": int(plan["total_amount"]), "clawback_ts": int(plan["clawback_ts"]),
                "recent_blockhash": blockhash,
            })
            if any(not solana.is_address(built.get(k)) for k in ("distribution", "vault", "seed_pubkey")):
                raise hand.HandError("the spine built a create without the accounts it owes")
            attempt_id = _record(conn, round_id, "create", blockhash, last_valid, seed=built["seed_pubkey"],
                                 distributor=built["distribution"], vault=built["vault"])
            total = str(int(plan["total_amount"]))
            debits = [{"mint": plan["mint"], "amountRaw": total}]
            quote = self._prepare(conn, attempt_id, built, debits)
            self._check_create(conn, attempt_id, quote, plan, built)
            self._check_simulation(conn, attempt_id, quote, debits)
            tx = self._execute(conn, attempt_id, quote, f"round {round_id}'s distribution")
            return _created(_attempt(conn, attempt_id)) if tx else {}
        finally:
            conn.close()

    def close_distribution(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        self._check_cluster(plan)
        round_id = int(plan["round_id"])
        conn = store.db()
        try:
            for attempt in _attempts(conn, round_id, "close"):
                if attempt["state"] == "done":
                    return {"tx": attempt["tx"]}
                if attempt["state"] in ("sent", "in_doubt"):
                    settled = self._settle_close(conn, attempt, plan)
                    if settled is not None:
                        return settled
            self._check_authority(plan)
            state = self._distribution(plan["distributor"])
            if state is None or state.get("closed"):
                raise hand.HandError(f"round {round_id}'s distribution {plan['distributor']} is not open on chain; a person has to look")
            mismatch = _mismatch(state, plan, ("authority", "mint", ("seed", "distribution_seed")))
            if mismatch:
                raise hand.HandError(f"round {round_id}'s distribution on chain does not match the round ({', '.join(mismatch)})")
            blockhash, last_valid = self._blockhash()
            built = self.build("close", {
                "program_id": plan["program_id"], "mint": plan["mint"], "token_program": plan["token_program"],
                "authority": plan["authority"], "distribution_seed": plan["distribution_seed"],
                "distributor": plan["distributor"], "recent_blockhash": blockhash,
            })
            if built.get("distribution") != plan["distributor"] or built.get("vault") != plan["vault"]:
                raise hand.Refused("the close the spine built is not for the round's distributor and vault; nothing was signed",
                                   rule="quote")
            attempt_id = _record(conn, round_id, "close", blockhash, last_valid, seed=plan["distribution_seed"],
                                 distributor=plan["distributor"], vault=plan["vault"])
            quote = self._prepare(conn, attempt_id, built, [])
            self._check_close(conn, attempt_id, quote, plan, built)
            self._check_simulation(conn, attempt_id, quote, [])
            tx = self._execute(conn, attempt_id, quote, f"round {round_id}'s clawback")
            return {"tx": tx}
        finally:
            conn.close()

    # --- before anything is built -----------------------------------------------------------

    def _check_cluster(self, plan: Mapping[str, Any]) -> None:
        cluster = plan.get("cluster")
        try:
            core_cluster = wallet_caps.cluster(hand.secrets())
        except wallet_caps.CapsError as exc:
            raise hand.Refused(f"the core's cluster is not known: {exc}", rule="cluster") from None
        if cluster != core_cluster:
            raise hand.Refused(f"the round is on {cluster} but the core signs on {core_cluster} (WALLET_CLUSTER); nothing was signed",
                               rule="cluster")
        genesis = self.rpc("getGenesisHash", [])
        if genesis != GENESIS.get(str(cluster)):
            raise hand.Refused(f"GIVEAWAY_RPC_URL is not a {cluster} RPC (its genesis hash is {genesis}); nothing was signed",
                               rule="cluster")

    def _check_authority(self, plan: Mapping[str, Any]) -> None:
        try:
            account = self.core("wallet_account", {"chain": "solana", "accountIndex": caps.ACCOUNT_INDEX}, timeout=60)
        except hand.CoreError as exc:
            raise hand.HandError(f"the core could not say the giveaway wallet's address ({exc})") from None
        address = account.get("address") if isinstance(account, Mapping) else None
        if address != plan.get("authority"):
            raise hand.Refused(f"GIVEAWAY_AUTHORITY is not the core's Solana index 1 ({address}); nothing was signed",
                               rule="authority")

    def _blockhash(self) -> tuple[str, int]:
        answer = self.rpc("getLatestBlockhash", [FINALIZED])
        value = answer.get("value") if isinstance(answer, Mapping) else None
        blockhash = value.get("blockhash") if isinstance(value, Mapping) else None
        last_valid = value.get("lastValidBlockHeight") if isinstance(value, Mapping) else None
        if not solana.is_address(blockhash) or not isinstance(last_valid, int):
            raise hand.HandError("the giveaway RPC did not give a recent blockhash")
        return blockhash, last_valid

    # --- prepare, check, execute ------------------------------------------------------------

    def _prepare(self, conn: Any, attempt_id: int, built: Mapping[str, Any], debits: list[Json]) -> Mapping[str, Any]:
        params = {"accountIndex": caps.ACCOUNT_INDEX, "transaction": built.get("transaction_b64"), "expectedDebits": debits}
        try:
            quote = self.core("wallet_prepare_program", params, timeout=60)
        except hand.CoreError as exc:
            # A quote moves nothing, so a failed prepare is a plain failure.
            if POLICY_REFUSED in str(exc):
                _update(conn, attempt_id, state="refused", error=str(exc)[:300])
                raise hand.Refused("the core's policy for the giveaway wallet refused the transaction; nothing was signed",
                                   rule="core_policy", error=str(exc)[:300]) from None
            _update(conn, attempt_id, state="failed", error=str(exc)[:300])
            raise hand.HandError(f"the core could not prepare the transaction ({exc}); nothing was signed") from None
        if not isinstance(quote, Mapping) or not quote.get("quoteId"):
            _update(conn, attempt_id, state="failed", error="the prepare answered without a quote")
            raise hand.HandError("the core prepared no quote; nothing was signed")
        return quote

    def _refuse(self, conn: Any, attempt_id: int, rule: str, fields: list[str]) -> None:
        _update(conn, attempt_id, state="failed", error=f"{rule} did not match: {', '.join(fields)}")
        raise hand.Refused(f"the core's prepared transaction does not match the round ({', '.join(fields)}); nothing was executed",
                           rule=rule, fields=fields)

    def _check_common(self, quote: Mapping[str, Any], plan: Mapping[str, Any], accounts: list[str], data_ok: Callable[[str], bool]) -> list[str]:
        summary = quote.get("summary") if isinstance(quote.get("summary"), Mapping) else {}
        instructions = summary.get("instructions") if isinstance(summary.get("instructions"), list) else []
        ix = instructions[0] if len(instructions) == 1 and isinstance(instructions[0], Mapping) else {}
        return [name for name, ok in (
            ("accountIndex", quote.get("accountIndex") in (None, caps.ACCOUNT_INDEX)),
            ("address", quote.get("address") in (None, plan["authority"])),
            ("feePayer", summary.get("feePayer") == plan["authority"]),
            ("programs", summary.get("programs") == [plan["program_id"]]),
            ("instructions", len(instructions) == 1),
            ("programId", ix.get("programId") == plan["program_id"]),
            ("accounts", ix.get("accounts") == accounts),
            ("data", data_ok(str(ix.get("dataHex", "")))),
            ("movements", not summary.get("movements")),
        ) if not ok]

    def _check_create(self, conn: Any, attempt_id: int, quote: Mapping[str, Any], plan: Mapping[str, Any],
                      built: Mapping[str, Any]) -> None:
        total = int(plan["total_amount"])
        accounts = [plan["authority"], plan["authority"], built["seed_pubkey"], built["distribution"], plan["mint"],
                    built["vault"], built.get("authority_token_account"), SYSTEM_PROGRAM, plan["token_program"],
                    ATA_PROGRAM, built.get("event_authority"), plan["program_id"]]

        def data_ok(data_hex: str) -> bool:
            fields = parse_create_data(data_hex)
            return fields is not None and fields["revocable"] == 0 and fields["amount"] == total \
                and fields["total_amount"] == total and fields["root"] == str(plan["merkle_root"]).lower() \
                and fields["clawback_ts"] == int(plan["clawback_ts"]) \
                and fields["bump"] == built.get("distribution_bump")

        mismatch = self._check_common(quote, plan, accounts, data_ok)
        if mismatch:
            self._refuse(conn, attempt_id, "quote", mismatch)

    def _check_close(self, conn: Any, attempt_id: int, quote: Mapping[str, Any], plan: Mapping[str, Any],
                     built: Mapping[str, Any]) -> None:
        accounts = [plan["authority"], plan["distributor"], plan["mint"], plan["vault"], built.get("authority_token_account"),
                    plan["token_program"], built.get("event_authority"), plan["program_id"]]
        mismatch = self._check_common(quote, plan, accounts, lambda data_hex: data_hex == f"{IX_CLOSE:02x}")
        if mismatch:
            self._refuse(conn, attempt_id, "quote", mismatch)

    def _check_simulation(self, conn: Any, attempt_id: int, quote: Mapping[str, Any], debits: list[Json]) -> None:
        simulation = quote.get("simulation") if isinstance(quote.get("simulation"), Mapping) else None
        mismatch = [name for name, ok in (
            ("simulation", simulation is not None),
            ("err", simulation is not None and simulation.get("err") is None),
            ("debits", simulation is not None and _debits(simulation.get("debits")) == _debits(debits)),
            ("declaredDebits", _debits(quote.get("declaredDebits", debits)) == _debits(debits)),
        ) if not ok]
        if mismatch:
            self._refuse(conn, attempt_id, "simulation", mismatch)

    def _execute(self, conn: Any, attempt_id: int, quote: Mapping[str, Any], what: str) -> str:
        _update(conn, attempt_id, state="sent", quote_id=str(quote["quoteId"]))
        try:
            result = self.core("wallet_execute_prepared_program",
                               {"accountIndex": caps.ACCOUNT_INDEX, "quoteId": str(quote["quoteId"])}, timeout=120)
        except hand.CoreError as exc:
            if POLICY_REFUSED in str(exc):
                _update(conn, attempt_id, state="refused", error=str(exc)[:300])
                raise hand.Refused("the core's policy for the giveaway wallet refused the transaction; nothing was signed",
                                   rule="core_policy", error=str(exc)[:300]) from None
            _update(conn, attempt_id, state="in_doubt", error=str(exc)[:300])
            raise hand.HandError(f"{what} is in_doubt: the execute was sent and did not come back ({exc}). It is never"
                                 " sent again on a guess; a later run settles it from the chain.") from None
        tx = result.get("transactionHash") if isinstance(result, Mapping) else None
        if not isinstance(tx, str) or not tx:
            _update(conn, attempt_id, state="in_doubt", error="the execute answered without a signature")
            raise hand.HandError(f"{what} is in_doubt: the core answered without a signature. A later run settles it from the chain.")
        _update(conn, attempt_id, tx=tx)
        status = self._confirm(tx)
        if status == "failed":
            _update(conn, attempt_id, state="failed", error="the transaction failed on chain")
            raise hand.HandError(f"{what} failed on chain ({tx}); nothing was made, a later run tries again")
        if status != "confirmed":
            raise hand.HandError(f"{what} is in_doubt: {tx} was sent and not yet confirmed; a later run settles it from the chain")
        _update(conn, attempt_id, state="done")
        return tx

    def _confirm(self, tx: str) -> str:
        """confirmed, failed, or unknown after CONFIRM_SECONDS."""
        deadline = self.clock() + CONFIRM_SECONDS
        while True:
            try:
                answer = self.rpc("getSignatureStatuses", [[tx], {"searchTransactionHistory": True}])
                value = (answer or {}).get("value") if isinstance(answer, Mapping) else None
                status = value[0] if isinstance(value, list) and value else None
            except hand.HandError:
                status = None
            if isinstance(status, Mapping):
                if status.get("err") is not None:
                    return "failed"
                if status.get("confirmationStatus") in ("confirmed", "finalized"):
                    return "confirmed"
            if self.clock() >= deadline:
                return "unknown"
            self.sleep(CONFIRM_POLL)

    # --- settling an attempt from the chain -------------------------------------------------

    def _distribution(self, address: str) -> Json | None:
        """The distribution account at finalized commitment: parsed, or None
        when there is no account."""
        answer = self.rpc("getAccountInfo", [address, {"encoding": "base64", **FINALIZED}])
        value = answer.get("value") if isinstance(answer, Mapping) else None
        if value is None:
            return None
        data = value.get("data") if isinstance(value, Mapping) else None
        if not isinstance(data, list) or not data or not isinstance(data[0], str):
            raise hand.HandError(f"the giveaway RPC answered unreadably for {address}")
        parsed = parse_distribution(base64.b64decode(data[0]))
        if parsed is None:
            raise hand.HandError(f"{address} is not a merkle distribution; a person has to look")
        return parsed

    def _expired(self, attempt: Mapping[str, Any]) -> bool:
        height = self.rpc("getBlockHeight", [FINALIZED])
        return isinstance(height, int) and attempt["last_valid"] is not None and height > int(attempt["last_valid"])

    def _first_signature(self, address: str, newest: bool) -> str | None:
        try:
            rows = self.rpc("getSignaturesForAddress", [address, {"limit": SIGNATURE_PAGE, **FINALIZED}]) or []
        except hand.HandError:
            return None
        ok = [r["signature"] for r in rows if isinstance(r, Mapping) and r.get("err") is None and r.get("signature")]
        if not ok:
            return None
        return ok[0] if newest else ok[-1]

    def _in_doubt(self, conn: Any, attempt: Mapping[str, Any], what: str) -> None:
        _update(conn, attempt["id"], state="in_doubt")
        raise hand.HandError(f"{what} is in_doubt: attempt {attempt['id']} was sent and the chain does not show it yet,"
                             " and its blockhash has not expired. It is never sent again on a guess.")

    def _settle_create(self, conn: Any, attempt: Mapping[str, Any], plan: Mapping[str, Any]) -> Mapping[str, Any] | None:
        what = f"round {plan['round_id']}'s distribution"
        state = self._distribution(attempt["distributor"])
        if state is not None:
            mismatch = _mismatch(state, plan, ("authority", "mint", "total_amount", "clawback_ts",
                                               ("root", "merkle_root")))
            if state.get("closed") or state.get("seed") != attempt["seed"] or mismatch:
                raise hand.HandError(f"{what} at {attempt['distributor']} does not match the round; a person has to look")
            tx = attempt["tx"] or self._first_signature(attempt["distributor"], newest=False)
            _update(conn, attempt["id"], state="done", tx=tx)
            return _created(_attempt(conn, attempt["id"]))
        if self._expired(attempt):
            _update(conn, attempt["id"], state="dead", error="never landed: its blockhash expired")
            return None
        self._in_doubt(conn, attempt, what)
        return None

    def _settle_close(self, conn: Any, attempt: Mapping[str, Any], plan: Mapping[str, Any]) -> Mapping[str, Any] | None:
        what = f"round {plan['round_id']}'s clawback"
        state = self._distribution(attempt["distributor"])
        if state is None or state.get("closed"):
            tx = attempt["tx"] or self._first_signature(attempt["distributor"], newest=True)
            if not tx:
                self._in_doubt(conn, attempt, what)
            _update(conn, attempt["id"], state="done", tx=tx)
            return {"tx": tx}
        if self._expired(attempt):
            _update(conn, attempt["id"], state="dead", error="never landed: its blockhash expired")
            return None
        self._in_doubt(conn, attempt, what)
        return None


# --- the attempts table ------------------------------------------------------------------------


def _attempts(conn: Any, round_id: int, op: str) -> list[Any]:
    return conn.execute("SELECT * FROM giveaway_chain WHERE round_id = ? AND op = ? ORDER BY id DESC", (round_id, op)).fetchall()


def _attempt(conn: Any, attempt_id: int) -> Any:
    return conn.execute("SELECT * FROM giveaway_chain WHERE id = ?", (attempt_id,)).fetchone()


def _record(conn: Any, round_id: int, op: str, blockhash: str, last_valid: int, **fields: Any) -> int:
    now = hand.now()
    cursor = conn.execute(
        "INSERT INTO giveaway_chain (round_id, op, state, seed, distributor, vault, blockhash, last_valid, created_at, updated_at)"
        " VALUES (?, ?, 'built', ?, ?, ?, ?, ?, ?, ?)",
        (round_id, op, fields.get("seed"), fields.get("distributor"), fields.get("vault"), blockhash, last_valid, now, now),
    )
    return int(cursor.lastrowid)


def _update(conn: Any, attempt_id: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE giveaway_chain SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), hand.now(), attempt_id))


def _created(attempt: Mapping[str, Any]) -> Json:
    return {"distribution_seed": attempt["seed"], "distributor": attempt["distributor"], "vault": attempt["vault"],
            "txs": {"create": attempt["tx"]} if attempt["tx"] else {}}


def _debits(value: Any) -> list[tuple[str, str]] | None:
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        out.append((str(item.get("mint")), str(item.get("amountRaw"))))
    return sorted(out)


def _mismatch(state: Mapping[str, Any], plan: Mapping[str, Any], keys: tuple[Any, ...]) -> list[str]:
    out = []
    for key in keys:
        ours, theirs = key if isinstance(key, tuple) else (key, key)
        want = plan.get(theirs)
        have = state.get(ours)
        if isinstance(have, str) and isinstance(want, str):
            same = have.lower() == want.lower() if ours == "root" else have == want
        else:
            same = have is not None and want is not None and int(have) == int(want)
        if not same:
            out.append(ours)
    return out
