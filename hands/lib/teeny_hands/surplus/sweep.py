"""Sweeping spare SOL to the surplus wallet (docs/hands.md, "surplus"). The
surplus wallet takes only USDC on Base, so each run of the pinned
`surplus-sweep` job (bin/surplus-sweep) does two independent steps, under the
`surplus-sweep` lock and never from a model:

  payout  USDC on Base above SURPLUS_KEEP_BASE_USD (the x402 rail's reserve)
          is paid to SURPLUS_ADDRESS through wallet.pay, purpose `vendor`,
          ref `surplus-<time>`. The amount is clamped to what pay would
          allow (half the balance, WALLET_MAX_PER_TX_USD, what is left of
          WALLET_MAX_PER_DAY_USD and of this address's
          WALLET_MAX_PER_RECIPIENT_WEEK_USD), so a sweep waits instead of
          being refused. It needs SURPLUS_MIN_GAS_ETH of ETH on Base first
          (an ERC-20 transfer pays gas), and SURPLUS_ADDRESS in the vendors
          allowlist on base, which pay checks again.
  bridge  SOL above SURPLUS_KEEP_SOL, at most half of it, is bridged
          straight into USDC on Base AT THE SURPLUS WALLET, through
          wallet.convert's one way to pay a vendor (`pay_to`; LI.FI only,
          the recipient checked inside the signed transaction), ref
          `surplus-bridge-<time>`. Clamped beforehand to
          WALLET_MAX_PER_TX_USD and what is left of
          WALLET_MAX_CONVERT_PER_DAY_USD, WALLET_MAX_PER_DAY_USD and this
          address's WALLET_MAX_PER_RECIPIENT_WEEK_USD; never under
          SURPLUS_MIN_BRIDGE_SOL. It needs no gas on Base and lands in the
          surplus wallet itself, so nothing waits on it.

A direct bridge is a payment as much as a conversion: convert writes a
`wallet_payments` row for it (purpose `vendor`), so every payment cap and
ledger counts it.

Each step that tried something is one `surplus_runs` row (paid, sent,
in_doubt, failed, refused), and a payout or bridge that went, or may have
gone, is one `wallet · surplus` stimulus for the mind. Under DRY_RUN both
steps rehearse through pay's and convert's own dry runs (`would_pay`,
`would_convert`); nothing is written and nothing moves.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .. import hand, stimuli
from . import settings

Core = Callable[..., Any]
Json = dict[str, Any]

OFF: Json = {"skipped": "the surplus sweep is off (SURPLUS_SWEEP=1 in secrets/teeny.env turns it on)"}
#: Bridges are rounded down to this many lamports (0.001 SOL).
SOL_STEP = 1_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS surplus_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            REAL NOT NULL,
    step          TEXT NOT NULL CHECK (step IN ('payout', 'bridge')),
    outcome       TEXT NOT NULL CHECK (outcome IN ('paid', 'sent', 'in_doubt', 'failed', 'refused')),
    asset         TEXT,
    amount        TEXT,
    usd_micro     INTEGER NOT NULL DEFAULT 0,
    to_address    TEXT,
    ref           TEXT,
    payment_id    INTEGER,
    conversion_id INTEGER,
    signature     TEXT,
    rule          TEXT,
    detail        TEXT
);
CREATE INDEX IF NOT EXISTS surplus_runs_at ON surplus_runs (at);
"""


def db():
    return hand.open_db(SCHEMA)


def _record(step: str, outcome: str, **fields: Any) -> int:
    row = {"at": hand.now(), "step": step, "outcome": outcome, **{k: v for k, v in fields.items() if v is not None}}
    if "detail" in row:
        row["detail"] = str(row["detail"])[:300]
    conn = db()
    try:
        cursor = conn.execute(
            f"INSERT INTO surplus_runs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _tell(ref: str, text: str, payload: Mapping[str, Any]) -> None:
    """A side channel: a stimulus that cannot be written never undoes a run."""
    try:
        stimuli.add("wallet", "surplus", ref, text, payload=payload)
    except (hand.HandError, OSError) as exc:
        import sys

        print(f"surplus: could not tell the mind: {exc}", file=sys.stderr)


def _held(balance: Mapping[str, Any], chain: str, asset: str) -> int | None:
    if chain == "solana":
        entry = balance.get("sol" if asset == "SOL" else "usdc")
    else:
        entry = (((balance.get("evm") or {}).get("networks") or {}).get(chain) or {}).get(asset.lower())
    try:
        return int(entry["raw"]) if isinstance(entry, Mapping) else None
    except (KeyError, TypeError, ValueError):
        return None


def _load() -> tuple[settings.Sweep, Any]:
    from ..wallet import pay

    try:
        found = settings.load(hand.secrets())
    except settings.SettingsError as exc:
        raise hand.Refused(f"the surplus settings are not usable: {exc}", rule="caps") from None
    the_caps = pay.load_caps()
    if the_caps.cluster != "mainnet":
        raise hand.Refused(f"the surplus wallet is on Base, mainnet only, and the wallet is on {the_caps.cluster}",
                           rule="cluster")
    return found, the_caps


# --- payout: USDC on Base above the reserve, to the surplus wallet --------------------

def _payout_room(found: settings.Sweep, the_caps: Any) -> tuple[int, str]:
    """The most pay would let out now under the per-payment, daily and
    weekly-per-recipient caps, and the name of the one that binds."""
    from ..wallet import caps, store

    conn = store.db()
    try:
        now = hand.now()
        day_left = the_caps.per_day_micro - store.spent_since(conn, now - store.DAY)
        week_left = the_caps.recipient_week_micro - store.spent_since(conn, now - store.WEEK, found.address)
        vendor = store.vendor_by_address(conn, found.address, "base")
    finally:
        conn.close()
    if vendor is None:
        raise hand.Refused(
            f"{found.address} is not an allowlisted vendor on base; the operator runs"
            f" `hands/wallet vendors add surplus {found.address} --chain base`", rule="vendor")
    return min(((the_caps.per_tx_micro, caps.PER_TX), (day_left, "WALLET_MAX_PER_DAY_USD"),
                (week_left, "WALLET_MAX_PER_RECIPIENT_WEEK_USD")))


def _payout(balance: Mapping[str, Any], found: settings.Sweep, the_caps: Any, core: Core) -> Json:
    from ..wallet import caps, pay, store

    base = _held(balance, "base", "USDC")
    if base is None:
        return {"outcome": "skipped", "why": "USDC on Base could not be read"}
    summary: Json = {"base_usdc": caps.display(base, "USDC"), "keep_usd": caps.usd(found.keep_base_micro)}
    excess = base - found.keep_base_micro
    if excess < found.min_payout_micro:
        return {"outcome": "nothing", **summary}
    gas = _held(balance, "base", "ETH")
    if gas is None or gas < found.min_gas_wei:
        why = (f"Teeny's Base address holds {caps.display(gas or 0, 'ETH')} ETH, under the"
               f" {caps.display(found.min_gas_wei, 'ETH')} SURPLUS_MIN_GAS_ETH a USDC transfer needs for gas")
        return _refused_payout(why, "gas", summary)
    try:
        room, bound = _payout_room(found, the_caps)
    except hand.Refused as exc:
        return _refused_payout(exc.reason, exc.detail.get("rule"), summary)
    amount, clamped_by = excess, None
    for limit, name in ((base // 2, "half_balance"), (room, bound)):
        if limit < amount:
            amount, clamped_by = limit, name
    if amount < found.min_payout_micro:
        return {"outcome": "waiting", "clamped_by": clamped_by, "room_usd": caps.usd(max(0, amount)), **summary}

    text = caps.display(amount, "USDC")
    ref = f"surplus-{int(hand.now())}"
    body = {"to": found.address, "amount_usdc": text, "chain": "base", "purpose": "vendor", "ref": ref}
    plan = {**summary, "amount": text, "clamped_by": clamped_by, "to": found.address, "ref": ref}
    fields = {"asset": "USDC", "amount": text, "usd_micro": amount, "to_address": found.address, "ref": ref}
    try:
        out = pay.pay(body, core=core)
    except hand.Refused as exc:
        if hand.dry_run():
            return {"outcome": "refused", "rule": exc.detail.get("rule"), "why": exc.reason, **plan}
        run_id = _record("payout", "refused", rule=exc.detail.get("rule"), detail=exc.reason, **fields)
        return {"outcome": "refused", "run_id": run_id, "rule": exc.detail.get("rule"), "why": exc.reason, **plan}
    except hand.HandError as exc:
        conn = store.db()
        try:
            row = store.active_for_ref(conn, "vendor", ref)
        finally:
            conn.close()
        outcome = "in_doubt" if row is not None and row["status"] == "in_doubt" else "failed"
        run_id = _record("payout", outcome, detail=str(exc), payment_id=row["id"] if row is not None else None, **fields)
        if outcome == "in_doubt":
            _tell(f"run-{run_id}", f"A payout of {text} USDC on Base to the surplus wallet is in doubt: {exc} A person"
                  " checks it; it is never retried.", {"run_id": run_id, "outcome": outcome})
        return {"outcome": outcome, "run_id": run_id, "why": str(exc), **plan}
    if out.get("dry_run"):
        return {"outcome": "would_pay", "would_pay": out.get("would_pay"), "checks": out.get("checks", []), **plan}
    paid = out["paid"]
    run_id = _record("payout", "paid", payment_id=paid.get("payment_id"), signature=paid.get("signature"), **fields)
    _tell(f"run-{run_id}",
          f"You paid {text} USDC on Base to your surplus wallet ({found.address}), the spare money above the"
          f" ${caps.usd(found.keep_base_micro)} you keep on Base for the x402 rail. {paid.get('url') or ''}".strip(),
          {"run_id": run_id, "outcome": "paid", "payment_id": paid.get("payment_id"), "amount": text})
    return {"outcome": "paid", "run_id": run_id, **plan, "paid": paid}


def _refused_payout(why: str, rule: str | None, summary: Json) -> Json:
    if hand.dry_run():
        return {"outcome": "refused", "rule": rule, "why": why, **summary}
    run_id = _record("payout", "refused", rule=rule, detail=why, asset="USDC")
    return {"outcome": "refused", "run_id": run_id, "rule": rule, "why": why, **summary}


# --- bridge: SOL above the reserve, straight to the surplus wallet's USDC on Base -------

def _bridge(balance: Mapping[str, Any], found: settings.Sweep, the_caps: Any, core: Core) -> Json:
    from ..wallet import caps, convert, store

    sol = _held(balance, "solana", "SOL")
    if sol is None:
        return {"outcome": "skipped", "why": "SOL could not be read"}
    summary: Json = {"sol": caps.display(sol, "SOL"), "keep_sol": caps.display(found.keep_sol_raw, "SOL")}
    free = max(0, min(sol - found.keep_sol_raw, sol // 2))
    if free < found.min_bridge_raw:
        return {"outcome": "nothing", **summary}
    if the_caps.convert_day_micro is None:
        return _refused_bridge(f"{caps.CONVERT_PER_DAY} is not set, so the wallet bridges nothing", "caps", summary)
    conn = store.db()
    try:
        day_left = the_caps.convert_day_micro - store.converted_since(conn, hand.now() - store.DAY)
    finally:
        conn.close()
    try:
        room, bound = _payout_room(found, the_caps)
    except hand.Refused as exc:
        return _refused_bridge(exc.reason, exc.detail.get("rule") or "vendor", summary)
    amount, clamped_by = free, None
    for dollars, name in ((day_left, caps.CONVERT_PER_DAY), (room, bound)):
        limit = caps.raw_for_usd("SOL", max(0, dollars), the_caps)
        if limit < amount:
            amount, clamped_by = limit, name
    amount -= amount % SOL_STEP
    if amount < found.min_bridge_raw:
        return {"outcome": "waiting", "clamped_by": clamped_by, **summary}

    text = caps.display(amount, "SOL")
    usd = caps.usd_micro("SOL", amount, the_caps)
    ref = f"surplus-bridge-{int(hand.now())}"
    body = {"from_chain": "solana", "from": "SOL", "to_chain": "base", "to": "USDC", "amount": text, "ref": ref,
            "provider": "lifi"}
    plan = {**summary, "amount": text, "usd": caps.usd(usd), "clamped_by": clamped_by, "to": found.address, "ref": ref}
    fields = {"asset": "SOL", "amount": text, "usd_micro": usd, "ref": ref, "to_address": found.address}
    conn = store.db()
    try:
        before = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM wallet_conversions").fetchone()[0])
    finally:
        conn.close()
    try:
        out = convert.convert("bridge", body, core=core, pay_to=found.address, pay_ref=ref)
    except hand.Refused as exc:
        if hand.dry_run():
            return {"outcome": "refused", "rule": exc.detail.get("rule"), "why": exc.reason, **plan}
        run_id = _record("bridge", "refused", rule=exc.detail.get("rule"), detail=exc.reason, **fields)
        return {"outcome": "refused", "run_id": run_id, "rule": exc.detail.get("rule"), "why": exc.reason, **plan}
    except hand.HandError as exc:
        row = _conversion_after(before, amount)
        outcome = "in_doubt" if row is not None and row["status"] in store.CONVERSION_COUNTED else "failed"
        run_id = _record("bridge", outcome, detail=str(exc), conversion_id=row["id"] if row is not None else None, **fields)
        if outcome == "in_doubt":
            _tell(f"run-{run_id}", f"A bridge of {text} SOL into USDC on Base at your surplus wallet is in"
                  f" doubt: {exc} A person checks it; it is never retried.", {"run_id": run_id, "outcome": outcome})
        return {"outcome": outcome, "run_id": run_id, "why": str(exc), **plan}
    if out.get("dry_run"):
        return {"outcome": "would_bridge", "would_convert": out.get("would_convert"), "checks": out.get("checks", []), **plan}
    sent = out["sent"]
    run_id = _record("bridge", "sent", conversion_id=sent.get("conversion_id"), payment_id=sent.get("payment_id"),
                     signature=sent.get("signature"), **fields)
    _tell(f"run-{run_id}",
          f"You bridged {text} SOL (about ${plan['usd']} at your fixed SOL price) straight into USDC on Base at your"
          f" surplus wallet ({found.address}); at least {sent.get('minimum')} USDC lands there in a few minutes.",
          {"run_id": run_id, "outcome": "sent", "conversion_id": sent.get("conversion_id"), "amount": text})
    return {"outcome": "sent", "run_id": run_id, **plan, "sent": sent}


def _refused_bridge(why: str, rule: str, summary: Json) -> Json:
    if hand.dry_run():
        return {"outcome": "refused", "rule": rule, "why": why, **summary}
    run_id = _record("bridge", "refused", rule=rule, detail=why, asset="SOL")
    return {"outcome": "refused", "run_id": run_id, "rule": rule, "why": why, **summary}


def _conversion_after(before: int, raw: int) -> Any:
    from ..wallet import store

    conn = store.db()
    try:
        return conn.execute(
            "SELECT id, status FROM wallet_conversions WHERE id > ? AND kind = 'bridge' AND from_chain = 'solana'"
            " AND from_asset = 'SOL' AND amount_raw = ? ORDER BY id LIMIT 1",
            (before, str(raw)),
        ).fetchone()
    finally:
        conn.close()


# --- the verbs -------------------------------------------------------------------------

def sweep(core: Core = hand.core) -> Json:
    """One run: pay out what has landed on Base, then bridge more SOL."""
    from ..wallet import reads

    if not settings.on(hand.secrets()):
        return dict(OFF)
    found, the_caps = _load()
    with hand.lock("surplus-sweep"):
        balance = reads.balance()
        return {"to": found.address,
                "payout": _payout(balance, found, the_caps, core),
                "bridge": _bridge(balance, found, the_caps, core)}


def runs(max_rows: int) -> list[Json]:
    from ..wallet import caps

    conn = db()
    try:
        rows = conn.execute("SELECT * FROM surplus_runs ORDER BY id DESC LIMIT ?", (max_rows,)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        entry = {k: row[k] for k in row.keys() if row[k] is not None}
        entry["usd"] = caps.usd(int(entry.pop("usd_micro", 0)))
        out.append(entry)
    return out


def status() -> Json:
    """The settings, what has gone to the surplus wallet, and the last runs."""
    from ..wallet import caps

    env = hand.secrets()
    out: Json = {}
    try:
        out["on"] = settings.on(env)
        found = settings.load(env)
        out.update(to=found.address, keep_sol=caps.display(found.keep_sol_raw, "SOL"),
                   keep_base_usd=caps.usd(found.keep_base_micro))
    except settings.SettingsError as exc:
        out["settings_error"] = str(exc)
    conn = db()
    try:
        week = conn.execute("SELECT COALESCE(SUM(usd_micro), 0) FROM surplus_runs WHERE outcome IN ('paid', 'sent', 'in_doubt')"
                            " AND at > ?", (hand.now() - 7 * 86400.0,)).fetchone()[0]
        total = conn.execute("SELECT COALESCE(SUM(usd_micro), 0) FROM surplus_runs WHERE outcome IN ('paid', 'sent')").fetchone()[0]
    finally:
        conn.close()
    out["paid_7d_usd"] = caps.usd(int(week))
    out["paid_total_usd"] = caps.usd(int(total))
    out["runs"] = runs(5)
    return out
