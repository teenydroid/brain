"""Keeping the x402 rail funded (docs/hands.md, "x402"): USDC on Base, the
rail twitr.sh and Exa are paid from, is topped up from Teeny's own pinned
assets when it runs low. The pinned `x402-fund` job (bin/x402-fund), every 30
minutes; never a model.

It never builds a transaction and never asks the core to swap or bridge
itself: every top-up is one call to hands/wallet's `convert` (swap.py's
sibling, teeny_hands/wallet/convert.py), so every rule there still holds:
mainnet only, pinned assets only, Teeny's own address on both ends,
WALLET_MAX_PER_TX_USD, WALLET_MAX_CONVERT_PER_DAY_USD, half the balance at
most, the provider's quote checks (LI.FI by default, or deBridge) and
WALLET_MAX_CONVERT_LOSS_BPS. What this
adds, under the `x402-fund` lock:

  off         X402_FUND=1, or every run is skipped (exit 0) before anything is read
  caps        the X402_FUND_* keys are set and sane (settings.py)
  cluster     the wallet is on mainnet
  funded      Base USDC at or over X402_FUND_FLOOR_USD: nothing to do
  in_flight   a swap or bridge toward Teeny's own Base USDC (not one that
              paid a vendor) is pending, sent or in doubt
              from the last X402_FUND_COOLDOWN_MIN minutes: it may still land,
              so nothing more is sent (a bridge takes minutes)
  fund_day    X402_FUND_MAX_PER_DAY_USD is spent for the last 24 hours (a top-up
              sent or in doubt counts)
  no_source   nothing holds enough to top up from

The need is X402_FUND_TARGET_USD less Base USDC, clamped to what is left of
X402_FUND_MAX_PER_DAY_USD and to WALLET_MAX_PER_TX_USD. The source is the
first of SOURCES that covers it after its reserve (X402_FUND_KEEP_ETH_USD of
ETH for gas, X402_FUND_KEEP_SOL for fees) and after the wallet's own
half-balance rule; when none does, the one that covers most, in part.

Sizing without a price feed. USDC is sized one for one. SOL and ETH are sized
at the wallet's fixed WALLET_SOL_USD and WALLET_ETH_USD, the same prices its
caps count them at, and those are set at or above the market on purpose. So
an input sized for $4.50 is counted by every cap as $4.50 and is worth at most
that at market: a top-up can land short, never long. The next run (after the
cooldown) tops up the rest. Sizing from the provider's own USD estimate would need
a second quote, would be sized by the party being checked, and would count
differently against the caps; the loss check already reads that estimate.

Each live attempt is one `x402_fund_runs` row (sent, in_doubt, failed,
refused, no_source) and one `wallet · topup` stimulus for the mind: one per
run that sent or may have sent, one a UTC day per reason it did not. Under
DRY_RUN the quote is fetched and checked through convert's own rehearsal and
the answer is `would_fund`; nothing is written and nothing moves.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Mapping

from .. import hand, stimuli
from . import settings

Core = Callable[..., Any]
Json = dict[str, Any]

#: What the pinned job answers while X402_FUND is not set.
OFF: Json = {"skipped": "x402 funding is off (X402_FUND=1 in secrets/teeny.env turns it on)"}

#: (chain, asset, how), in the order they are tried. Pinned assets only.
SOURCES = (
    ("base", "ETH", "swap"),
    ("solana", "USDC", "bridge"),
    ("solana", "SOL", "bridge"),
    ("arbitrum", "USDC", "bridge"),
    ("optimism", "USDC", "bridge"),
    ("ethereum", "USDC", "bridge"),
    ("arbitrum", "ETH", "bridge"),
    ("optimism", "ETH", "bridge"),
    ("ethereum", "ETH", "bridge"),
)
#: A part top-up under this is not worth the provider's fees: no_source.
MIN_PARTIAL_MICRO = 1_000_000
#: Runs that may have moved funds, so they count against the day.
COUNTED = ("sent", "in_doubt")
REF = "x402-fund"

SCHEMA = """
CREATE TABLE IF NOT EXISTS x402_fund_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    at              REAL NOT NULL,
    base_before_micro INTEGER NOT NULL,
    need_micro      INTEGER NOT NULL,
    source          TEXT,
    kind            TEXT,
    amount          TEXT,
    usd_micro       INTEGER NOT NULL DEFAULT 0,
    conversion_id   INTEGER,
    outcome         TEXT NOT NULL CHECK (outcome IN ('sent', 'in_doubt', 'failed', 'refused', 'no_source')),
    rule            TEXT,
    detail          TEXT
);
CREATE INDEX IF NOT EXISTS x402_fund_runs_at ON x402_fund_runs (at);
"""


def db():
    return hand.open_db(SCHEMA)


def _usdc(micro: int) -> str:
    from ..wallet import caps

    return caps.display(micro, "USDC")


def _load() -> tuple[settings.Fund, Any]:
    from ..wallet import pay

    try:
        found = settings.fund(hand.secrets())
    except settings.SettingsError as exc:
        raise hand.Refused(f"the x402 fund settings are not usable: {exc}", rule="caps") from None
    the_caps = pay.load_caps()
    if the_caps.cluster != "mainnet":
        raise hand.Refused(f"the rail is USDC on Base, mainnet only, and the wallet is on {the_caps.cluster}", rule="cluster")
    return found, the_caps


def _held(balance: Mapping[str, Any], chain: str, asset: str) -> int | None:
    """Atomic units of `asset` on `chain` from wallet.reads.balance(), or None
    when that read failed."""
    if chain == "solana":
        entry = balance.get("sol" if asset == "SOL" else "usdc")
    else:
        network = ((balance.get("evm") or {}).get("networks") or {}).get(chain) or {}
        entry = network.get(asset.lower())
    try:
        return int(entry["raw"]) if isinstance(entry, Mapping) else None
    except (KeyError, TypeError, ValueError):
        return None


def _keep(asset: str, found: settings.Fund, the_caps: Any) -> int:
    if asset == "SOL":
        return found.keep_sol_raw
    if asset == "ETH":
        # Dollars at WALLET_ETH_USD, rounded up to the wei.
        return -(-found.keep_eth_micro * 10**18 // the_caps.eth_usd_micro)
    return 0


def _free(held: int, asset: str, found: settings.Fund, the_caps: Any) -> int:
    """What may go: above the reserve, and no more than half (the wallet's
    own half_balance rule, so a pick never fails it)."""
    return max(0, min(held - _keep(asset, found, the_caps), held // 2))


def _day_used(conn: Any) -> int:
    marks = ",".join("?" * len(COUNTED))
    return int(conn.execute(
        f"SELECT COALESCE(SUM(usd_micro), 0) FROM x402_fund_runs WHERE outcome IN ({marks}) AND at > ?",
        (*COUNTED, hand.now() - 86400.0),
    ).fetchone()[0])


def _in_flight(cooldown: float) -> Any:
    from ..wallet import store

    conn = store.db()
    try:
        marks = ",".join("?" * len(store.CONVERSION_COUNTED))
        return conn.execute(
            f"SELECT id, status, created_at FROM wallet_conversions WHERE to_chain = 'base' AND to_asset = 'USDC'"
            f" AND recipient IS NULL AND status IN ({marks}) AND created_at > ? ORDER BY id DESC LIMIT 1",
            (*store.CONVERSION_COUNTED, hand.now() - cooldown),
        ).fetchone()
    finally:
        conn.close()


def _pick(balance: Mapping[str, Any], need: int, found: settings.Fund, the_caps: Any) -> Json | None:
    """The first source that covers `need`, else the one that covers most."""
    from ..wallet import caps

    best: Json | None = None
    for chain, asset, how in SOURCES:
        if asset == "ETH" and the_caps.eth_usd_micro is None:
            continue  # no ETH moves without WALLET_ETH_USD
        held = _held(balance, chain, asset)
        if not held:
            continue
        free = _free(held, asset, found, the_caps)
        want = caps.raw_for_usd(asset, need, the_caps)
        if 0 < want <= free:
            return {"chain": chain, "asset": asset, "kind": how, "raw": want, "partial": False}
        worth = caps.usd_micro(asset, free, the_caps) if free else 0
        if free and (best is None or worth > best["worth"]):
            best = {"chain": chain, "asset": asset, "kind": how, "raw": free, "partial": True, "worth": worth}
    if best is None or best.pop("worth") < MIN_PARTIAL_MICRO:
        return None
    return best


def _body(pick: Mapping[str, Any], amount: str) -> Json:
    if pick["kind"] == "swap":
        return {"chain": pick["chain"], "from": pick["asset"], "to": "USDC", "amount": amount, "ref": REF}
    return {"from_chain": pick["chain"], "from": pick["asset"], "to_chain": "base", "to": "USDC", "amount": amount, "ref": REF}


def _record(base: int, need: int, outcome: str, plan: Mapping[str, Any] | None = None, **fields: Any) -> int:
    plan = plan or {}
    row = {
        "at": hand.now(), "base_before_micro": base, "need_micro": need, "outcome": outcome,
        "source": plan.get("source"), "kind": plan.get("kind"), "amount": plan.get("amount"),
        "usd_micro": plan.get("usd_micro", 0), **fields,
    }
    conn = db()
    try:
        cursor = conn.execute(
            f"INSERT INTO x402_fund_runs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _tell(ref: str, text: str, payload: Mapping[str, Any]) -> None:
    """A side channel: a stimulus that cannot be written never undoes a run."""
    try:
        stimuli.add("wallet", "topup", ref, text, payload=payload)
    except (hand.HandError, OSError) as exc:
        import sys

        print(f"x402: could not tell the mind about the top-up: {exc}", file=sys.stderr)


def _day() -> str:
    return _dt.datetime.fromtimestamp(hand.now(), _dt.timezone.utc).strftime("%Y-%m-%d")


def _failed(base: int, need: int, found: settings.Fund, outcome: str, why: str, rule: str | None,
            plan: Mapping[str, Any] | None = None, conversion_id: int | None = None) -> int:
    run_id = _record(base, need, outcome, plan, rule=rule, detail=why[:300], conversion_id=conversion_id)
    text = (f"Your x402 rail (USDC on Base, what paid search and twitr are paid from) is at ${_usdc(base)},"
            f" under the ${_usdc(found.floor_micro)} floor, and the top-up did not go: {why}")
    if outcome == "in_doubt":
        text = (f"Your x402 rail (USDC on Base) was at ${_usdc(base)}, and a top-up of {plan and plan.get('amount')}"
                f" {plan and plan.get('asset')} from {plan and plan.get('chain')} is in doubt: {why} A person checks"
                " it; it is never retried.")
    ref = f"run-{run_id}" if outcome == "in_doubt" else f"{outcome}:{rule or '-'}:{_day()}"
    _tell(ref, text, {"run_id": run_id, "outcome": outcome, **({"rule": rule} if rule else {})})
    return run_id


def fund(core: Core = hand.core) -> Json:
    """One run of the top-up."""
    from ..wallet import caps, convert, reads, store

    if not settings.fund_on(hand.secrets()):
        return dict(OFF)
    found, the_caps = _load()
    with hand.lock("x402-fund"):
        balance = reads.balance()
        base = _held(balance, "base", "USDC")
        if base is None:
            raise hand.HandError("USDC on Base could not be read, so nothing was topped up")
        summary: Json = {"base_usdc": _usdc(base), "floor_usd": _usdc(found.floor_micro), "target_usd": _usdc(found.target_micro)}
        if base >= found.floor_micro:
            return {"kind": "funded", **summary}
        flying = _in_flight(found.cooldown_secs)
        if flying is not None:
            return {"kind": "in_flight", "conversion_id": flying["id"], "status": flying["status"], **summary}

        conn = db()
        try:
            left = found.day_micro - _day_used(conn)
        finally:
            conn.close()
        if left <= 0:
            raise hand.Refused(f"the last 24 hours of top-ups are at X402_FUND_MAX_PER_DAY_USD (${_usdc(found.day_micro)})",
                               rule="fund_day", **summary)
        need = found.target_micro - base
        clamped_by = None
        for bound, name in ((left, "X402_FUND_MAX_PER_DAY_USD"), (the_caps.per_tx_micro, caps.PER_TX)):
            if bound < need:
                need, clamped_by = bound, name

        pick = _pick(balance, need, found, the_caps)
        if pick is None:
            why = f"no pinned SOL, ETH or USDC holds ${caps.usd(MIN_PARTIAL_MICRO)} above its reserve and half its balance."
            if not hand.dry_run():
                run_id = _failed(base, need, found, "no_source", why, "no_source")
                raise hand.Refused(why, rule="no_source", run_id=run_id, **summary)
            raise hand.Refused(why, rule="no_source", **summary)
        amount = caps.display(pick["raw"], pick["asset"])
        plan: Json = {
            **summary, "need_usd": _usdc(need), "clamped_by": clamped_by,
            "source": f"{pick['chain']}:{pick['asset']}", "chain": pick["chain"], "asset": pick["asset"],
            "kind": pick["kind"], "amount": amount, "partial": pick["partial"],
            "usd_micro": caps.usd_micro(pick["asset"], pick["raw"], the_caps),
        }
        plan["usd"] = caps.usd(plan["usd_micro"])

        conn = store.db()
        try:
            before = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM wallet_conversions").fetchone()[0])
        finally:
            conn.close()
        try:
            out = convert.convert(pick["kind"], _body(pick, amount), core=core)
        except hand.Refused as exc:
            if hand.dry_run():
                raise hand.Refused(exc.reason, **exc.detail, plan=_public(plan)) from None
            run_id = _failed(base, need, found, "refused", exc.reason, exc.detail.get("rule"), plan)
            raise hand.Refused(exc.reason, **exc.detail, run_id=run_id, plan=_public(plan)) from None
        except hand.HandError as exc:
            row = _conversion_after(before, pick)
            outcome = "in_doubt" if row is not None and row["status"] in store.CONVERSION_COUNTED else "failed"
            run_id = _failed(base, need, found, outcome, str(exc), None, plan, row["id"] if row is not None else None)
            raise hand.HandError(f"x402 top-up run {run_id}: {exc}") from None
        if out.get("dry_run"):
            return {"dry_run": True, "would_fund": {**_public(plan), "convert": out.get("would_convert")},
                    "checks": out.get("checks", [])}
        sent = out["sent"]
        run_id = _record(base, need, "sent", plan, conversion_id=sent.get("conversion_id"))
        verb = "swapped" if pick["kind"] == "swap" else "bridged"
        _tell(f"run-{run_id}",
              f"Your x402 rail (USDC on Base, what paid search and twitr are paid from) was at ${_usdc(base)}, under the"
              f" ${_usdc(found.floor_micro)} floor, so you {verb} {amount} {pick['asset']} on {pick['chain']} into USDC on"
              f" Base (about ${plan['usd']} at your fixed prices; conversion {sent.get('conversion_id')})."
              + (" A bridge takes a few minutes to land." if pick["kind"] == "bridge" else ""),
              {"run_id": run_id, "outcome": "sent", "conversion_id": sent.get("conversion_id"), "source": plan["source"],
               "amount": amount})
        return {"kind": "sent", "run_id": run_id, "plan": _public(plan), "sent": sent}


def _public(plan: Mapping[str, Any]) -> Json:
    return {k: v for k, v in plan.items() if k != "usd_micro"}


def _conversion_after(before: int, pick: Mapping[str, Any]) -> Any:
    """The conversion row convert wrote for this top-up, if it got that far."""
    from ..wallet import store

    conn = store.db()
    try:
        return conn.execute(
            "SELECT id, status FROM wallet_conversions WHERE id > ? AND kind = ? AND from_chain = ? AND from_asset = ?"
            " AND amount_raw = ? ORDER BY id LIMIT 1",
            (before, pick["kind"], pick["chain"], pick["asset"], str(pick["raw"])),
        ).fetchone()
    finally:
        conn.close()


def runs(max_rows: int) -> list[Json]:
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM x402_fund_runs ORDER BY id DESC LIMIT ?", (max_rows,)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        entry = {k: row[k] for k in row.keys() if row[k] is not None}
        for key in ("base_before_micro", "need_micro", "usd_micro"):
            entry[key.replace("_micro", "_usd")] = _usdc(int(entry.pop(key, 0)))
        out.append(entry)
    return out


def status() -> Json:
    """The floor and the target, Base USDC now, and the last runs."""
    from ..wallet import chains, pay

    env = hand.secrets()
    out: Json = {"on": settings.fund_on(env)}
    try:
        found = settings.fund(env)
        out.update(floor_usd=_usdc(found.floor_micro), target_usd=_usdc(found.target_micro),
                   max_per_day_usd=_usdc(found.day_micro))
    except settings.SettingsError as exc:
        out["settings_error"] = str(exc)
    try:
        evm = pay.own_address(chain="base")
        out["base_usdc"] = _usdc(chains.erc20_balance("base", chains.NETWORKS["base"].usdc["mainnet"], evm))
    except hand.HandError as exc:
        out["base_usdc_error"] = str(exc)
    conn = db()
    try:
        out["topped_up_24h_usd"] = _usdc(_day_used(conn))
    finally:
        conn.close()
    out["runs"] = runs(5)
    return out


def log(max_rows: int) -> Json:
    from ..wallet import x402 as wallet_x402

    return {"requests": wallet_x402.log(max_rows)["requests"], "fund_runs": runs(max_rows)}
