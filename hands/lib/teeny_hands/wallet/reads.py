"""The wallet's read verbs, and the operator's lists (bills, vendors).

Nothing here moves funds or asks the core to: the reads are `wallet_status`,
`wallet_balances`, `wallet_ledger_list`, `cost_report`, `x402_list_payments`
and one Solana RPC read for USDC.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Mapping

from .. import hand
from ..bounty import store as bounty_store
from . import caps, solana, store
from .pay import load_caps, own_address, sol_balance

Json = dict[str, Any]
MONTH_DAYS = 30


def _cluster() -> str:
    try:
        return caps.cluster(hand.secrets())
    except caps.CapsError as exc:
        raise hand.HandError(str(exc)) from None


def address() -> Json:
    cluster = _cluster()
    own = own_address()
    return {"address": own, "cluster": cluster, "url": solana.explorer_account(own, cluster)}


def balance() -> Json:
    cluster = _cluster()
    own = own_address()
    sol = sol_balance()
    mint = caps.USDC_MINTS[cluster]
    usdc = solana.token_balance(cluster, own, mint)
    return {
        "address": own,
        "cluster": cluster,
        "sol": {"raw": str(sol), "display": caps.display(sol, "SOL")},
        "usdc": {"raw": str(usdc), "display": caps.display(usdc, "USDC"), "mint": mint},
    }


def _public(row: Mapping[str, Any]) -> Json:
    out = dict(row)
    out["amount"] = caps.display(int(row["amount_raw"]), row["asset"])
    out["amount_raw"] = str(row["amount_raw"])
    out["usd"] = caps.usd(int(row["usd_micro"]))
    del out["usd_micro"]
    if row.get("signature"):
        out["url"] = solana.explorer_tx(row["signature"], row["cluster"])
    return out


#: What a $TINY buy receipt says it was for: Teeny's own holding, bought
#: from what it earned. No price, no call to anyone.
BUY_FOR = "$TINY for Teeny's own holding, bought with a share of what it earned. It never sells."


def _public_buy(row: Mapping[str, Any]) -> Json:
    out = dict(row)
    out["id"] = f"buy-{row['id']}"
    out["kind"] = "tiny_buy"
    out["asset"] = "USDC"
    out["amount"] = caps.display(int(row["usdc_raw"]), "USDC")
    out["usdc_raw"] = str(row["usdc_raw"])
    out["usd"] = caps.usd(int(row["usd_micro"]))
    out["earned_usd"] = caps.usd(int(row["earned_micro"]))
    out["spent_usd"] = caps.usd(int(row["spent_micro"]))
    for key in ("usd_micro", "earned_micro", "spent_micro"):
        del out[key]
    for key in ("tiny_out_raw", "tiny_min_raw"):
        if key in out:
            out[key] = str(out[key])
    if row.get("signature"):
        out["url"] = solana.explorer_tx(row["signature"], row["cluster"])
    return out


def ledger(max_rows: int) -> Json:
    conn = store.db()
    try:
        rows = conn.execute("SELECT * FROM wallet_payments ORDER BY id DESC LIMIT ?", (max_rows,)).fetchall()
        buys = conn.execute("SELECT * FROM wallet_tiny_buys ORDER BY id DESC LIMIT ?", (max_rows,)).fetchall()
    finally:
        conn.close()
    out: Json = {"payments": [_public(store.row_dict(r) or {}) for r in rows], "tiny_buys": [_public_buy(store.row_dict(r) or {}) for r in buys]}
    try:
        core_rows = hand.core("wallet_ledger_list", {"limit": max_rows})
        out["core"] = core_rows if isinstance(core_rows, list) else []
    except hand.HandError as exc:
        out["core_error"] = str(exc)
    return out


def buy_receipt(buy_id: int) -> Json:
    conn = store.db()
    try:
        row = store.buy(conn, buy_id)
    finally:
        conn.close()
    if row is None or row["status"] != "bought":
        raise hand.Refused(f"there is no $TINY buy buy-{buy_id} that went out", rule="unknown")
    out = _public_buy(row)
    out["for"] = BUY_FOR
    return {"receipt": out}


def receipt_for(ref: str) -> Json:
    """`receipt ID`: a payment's number, or `buy-N` for a $TINY buy."""
    text = str(ref).strip()
    if text.startswith("buy-") and text[4:].isdigit():
        return buy_receipt(int(text[4:]))
    if text.isdigit():
        return receipt(int(text))
    raise hand.Refused("a receipt ID is a payment's number or buy-N", rule="unknown")


def receipt(payment_id: int) -> Json:
    conn = store.db()
    try:
        row = store.payment(conn, payment_id)
        if row is None:
            raise hand.Refused(f"there is no payment {payment_id}", rule="unknown")
        out = _public(row)
        if row["purpose"] == "bounty" and str(row["ref"]).isdigit():
            bounty = conn.execute("SELECT title FROM bounties WHERE id = ?", (int(row["ref"]),)).fetchone()
            if bounty is not None:
                out["for"] = f"the prize for bounty #{row['ref']}: {bounty['title']}"
        elif row["purpose"] == "vendor":
            vendor = store.vendor_by_address(conn, row["to_address"])
            out["for"] = f"{vendor['name'] if vendor else 'a vendor'} ({row['ref']})"
    finally:
        conn.close()
    return {"receipt": out}


def receipts_due(max_rows: int = 5) -> Json:
    """Paid payments and $TINY buys no X post of Teeny's has carried the
    signature of yet, oldest first: what the wallet-receipt playbook still
    owes a receipt post for."""
    conn = store.db()
    try:
        has_posts = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'x_posts'").fetchone()
        rows = conn.execute("SELECT id, updated_at FROM wallet_payments WHERE status = 'paid'").fetchall()
        buys = conn.execute("SELECT id, updated_at FROM wallet_tiny_buys WHERE status = 'bought'").fetchall()
        done = [(r["updated_at"], 0, int(r["id"])) for r in rows] + [(r["updated_at"], 1, int(r["id"])) for r in buys]
        due = []
        for _, is_buy, row_id in sorted(done):
            got = (buy_receipt(row_id) if is_buy else receipt(row_id))["receipt"]
            if has_posts and conn.execute("SELECT 1 FROM x_posts WHERE instr(text, ?) > 0", (got["signature"],)).fetchone():
                continue
            due.append(got)
    finally:
        conn.close()
    return {"due": due[:max_rows], "count": len(due)}


# --- costs ----------------------------------------------------------------------------


def _timestamp(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _x402(days: int, since: float, mint: str) -> Json:
    """Settled x402 payments (API calls Teeny paid for itself) in the window,
    in USDC. A status change is a new record with the same id; the newest
    one counts."""
    got = hand.core("x402_list_payments", {"limit": 500})
    records = got.get("payments") if isinstance(got, Mapping) else got
    latest: dict[str, Mapping[str, Any]] = {}
    for record in records if isinstance(records, list) else []:
        if isinstance(record, Mapping) and isinstance(record.get("id"), str) and record["id"] not in latest:
            latest[record["id"]] = record
    total = other = count = 0
    for record in latest.values():
        at = _timestamp(record.get("timestamp"))
        if record.get("status") != "settled" or at is None or at < since:
            continue
        if record.get("asset") == mint:
            total += int(record.get("amountAtomic") or 0)
            count += 1
        else:
            other += 1
    out: Json = {"usd": caps.usd(total), "payments": count}
    if other:
        out["not_usdc"] = other
    return out


def costs(days: int) -> Json:
    """What keeping Teeny running cost over the last `days`: model calls (the
    core's cost report), x402 API payments, and the fixed bills prorated to
    the window (a month counts as 30 days, a year as 12 months). Payments to
    bounty winners and vendors are listed beside it, not in it."""
    since = hand.now() - days * store.DAY
    out: Json = {"days": days}
    total = 0
    complete = True
    try:
        report = hand.core("cost_report", {"days": days})
        totals = report.get("totals") if isinstance(report, Mapping) else None
        totals = totals if isinstance(totals, Mapping) else {}
        model_micro = round(float(totals.get("cost_usd") or 0.0) * caps.MICRO)
        out["models"] = {
            "usd": caps.usd(model_micro),
            "calls": int(totals.get("calls") or 0),
            "charged_usd": caps.usd(round(float(totals.get("charged_usd") or 0.0) * caps.MICRO)),
            "estimated_usd": caps.usd(round(float(totals.get("estimated_usd") or 0.0) * caps.MICRO)),
        }
        total += model_micro
    except hand.HandError as exc:
        out["models"] = {"error": str(exc)}
        complete = False
    try:
        x402 = _x402(days, since, caps.USDC_MINTS[_cluster()])
        out["x402"] = x402
        total += caps.dollars_to_micro(x402["usd"], "x402")
    except (hand.HandError, caps.CapsError) as exc:
        out["x402"] = {"error": str(exc)}
        complete = False

    conn = store.db()
    try:
        bill_rows = conn.execute("SELECT name, usd_micro, period, note FROM bills ORDER BY name").fetchall()
        paid = conn.execute(
            "SELECT purpose, COALESCE(SUM(usd_micro), 0) AS micro, COUNT(*) AS n FROM wallet_payments"
            " WHERE status = 'paid' AND created_at > ? GROUP BY purpose",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    bills = []
    bills_micro = 0
    for row in bill_rows:
        monthly = row["usd_micro"] if row["period"] == "month" else row["usd_micro"] // 12
        share = monthly * days // MONTH_DAYS
        bills_micro += share
        entry = {"name": row["name"], "usd": caps.usd(row["usd_micro"]), "period": row["period"], "in_window_usd": caps.usd(share)}
        if row["note"]:
            entry["note"] = row["note"]
        bills.append(entry)
    out["bills"] = {"usd": caps.usd(bills_micro), "items": bills}
    total += bills_micro
    out["total_usd"] = caps.usd(total)
    out["complete"] = complete
    out["paid_out"] = {r["purpose"]: {"usd": caps.usd(int(r["micro"])), "payments": int(r["n"])} for r in paid}
    return out


# --- operator lists -------------------------------------------------------------------

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")


def _name(value: str) -> str:
    name = (value or "").strip()
    if not _NAME.fullmatch(name):
        raise hand.HandError("a name is 1-64 letters, digits, spaces, dots, dashes or underscores")
    return name


def bills_add(name: str, usd: str, period: str, note: str | None) -> Json:
    try:
        micro = caps.dollars_to_micro(usd, "--usd")
    except caps.CapsError as exc:
        raise hand.HandError(str(exc)) from None
    if micro <= 0:
        raise hand.HandError("--usd must be above zero")
    if period not in ("month", "year"):
        raise hand.HandError("--period is month or year")
    conn = store.db()
    try:
        conn.execute(
            "INSERT INTO bills (name, usd_micro, period, note, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (name) DO UPDATE SET usd_micro = excluded.usd_micro, period = excluded.period, note = excluded.note",
            (_name(name), micro, period, note, hand.now()),
        )
    finally:
        conn.close()
    return {"bill": {"name": _name(name), "usd": caps.usd(micro), "period": period}}


def bills_list() -> Json:
    conn = store.db()
    try:
        rows = conn.execute("SELECT name, usd_micro, period, note FROM bills ORDER BY name").fetchall()
    finally:
        conn.close()
    return {"bills": [{"name": r["name"], "usd": caps.usd(r["usd_micro"]), "period": r["period"], **({"note": r["note"]} if r["note"] else {})} for r in rows]}


def bills_remove(name: str) -> Json:
    conn = store.db()
    try:
        gone = conn.execute("DELETE FROM bills WHERE name = ?", (_name(name),)).rowcount
    finally:
        conn.close()
    if not gone:
        raise hand.Refused(f"there is no bill {name!r}", rule="unknown")
    return {"removed": _name(name)}


def vendors_add(name: str, address_text: str, note: str | None) -> Json:
    if not solana.is_address(address_text):
        raise hand.Refused("that is not a valid Solana address", rule="address")
    if address_text == caps.TINY_MINT or address_text in caps.USDC_MINTS.values():
        raise hand.Refused("that is a token mint, not a wallet", rule="address")
    conn = store.db()
    try:
        conn.execute(
            "INSERT INTO vendors (name, address, note, created_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (name) DO UPDATE SET address = excluded.address, note = excluded.note",
            (_name(name), address_text, note, hand.now()),
        )
    finally:
        conn.close()
    return {"vendor": {"name": _name(name), "address": address_text}}


def vendors_list() -> Json:
    conn = store.db()
    try:
        rows = conn.execute("SELECT name, address, note FROM vendors ORDER BY name").fetchall()
    finally:
        conn.close()
    return {"vendors": [store.row_dict(r) for r in rows]}


def vendors_remove(name: str) -> Json:
    conn = store.db()
    try:
        gone = conn.execute("DELETE FROM vendors WHERE name = ?", (_name(name),)).rowcount
    finally:
        conn.close()
    if not gone:
        raise hand.Refused(f"there is no vendor {name!r}", rule="unknown")
    return {"removed": _name(name)}


def caps_status() -> Json:
    """The caps the hand pays under (for the operator; no secret in them)."""
    c = load_caps()
    return {
        "cluster": c.cluster,
        "per_tx_usd": caps.usd(c.per_tx_micro),
        "per_day_usd": caps.usd(c.per_day_micro),
        "per_recipient_week_usd": caps.usd(c.recipient_week_micro),
        "sol_usd": caps.usd(c.sol_usd_micro),
        "usdc_mint": c.usdc_mint,
        "challenge_hours": bounty_store.challenge_hours(),
    }
