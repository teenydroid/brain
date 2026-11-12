"""The wallet's read verbs, and the operator's lists (bills, vendors).

Nothing here moves funds or asks the core to: the reads are `wallet_status`,
`wallet_balances`, `wallet_ledger_list`, `cost_report`, `x402_list_payments`,
Solana RPC token-account reads for USDC and $TINY and, on mainnet, one
`eth_call` per EVM network for its USDC (chains.py).
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Mapping

from .. import hand
from ..bounty import store as bounty_store
from . import caps, chains, solana, store
from .pay import load_caps, native_balance, own_address, sol_balance

Json = dict[str, Any]
MONTH_DAYS = 30


def _cluster() -> str:
    try:
        return caps.cluster(hand.secrets())
    except caps.CapsError as exc:
        raise hand.HandError(str(exc)) from None


def _evm_address() -> str | None:
    try:
        return own_address(chain="base")
    except hand.HandError:
        return None  # a wallet made before EVM accounts were stored


def address() -> Json:
    """Teeny's Solana address (`address`, as before) and its EVM address, the
    same on every EVM network: both from the one recovery phrase."""
    cluster = _cluster()
    own = own_address()
    out: Json = {"address": own, "cluster": cluster, "url": solana.explorer_account(own, cluster)}
    evm = _evm_address()
    if evm is not None:
        out["evm"] = {
            "address": evm,
            "networks": {key: chains.explorer_account(key, evm, cluster) for key in chains.NETWORKS},
            "usable": cluster == "mainnet",
        }
    return out


def balance() -> Json:
    """SOL and USDC on Solana (`sol`, `usdc`, as before), $TINY (`tiny`) and,
    on mainnet, ETH and USDC on every EVM network. A network that cannot be
    read carries `error`, and a failed $TINY read `tiny.error`; neither stops
    the others."""
    cluster = _cluster()
    own = own_address()
    sol = sol_balance()
    mint = caps.USDC_MINTS[cluster]
    usdc = solana.token_balance(cluster, own, mint)
    out: Json = {
        "address": own,
        "cluster": cluster,
        "sol": {"raw": str(sol), "display": caps.display(sol, "SOL")},
        "usdc": {"raw": str(usdc), "display": caps.display(usdc, "USDC"), "mint": mint},
    }
    # $TINY rides along, but a failed read of it never hides SOL and USDC.
    try:
        held = _tiny_holding(cluster, own)
        out["tiny"] = {"raw": held["raw"], "display": _decimal(int(held["raw"]), held["decimals"]), "mint": held["mint"]}
    except hand.HandError as exc:
        out["tiny"] = {"error": str(exc)}
    evm = _evm_address() if cluster == "mainnet" else None
    if evm is not None:
        networks: Json = {}
        for key, network in chains.NETWORKS.items():
            entry: Json = {}
            try:
                native = native_balance(chain=key)
                entry[network.native.lower()] = {"raw": str(native), "display": caps.display(native, network.native)}
            except hand.HandError as exc:
                entry["error"] = str(exc)
            try:
                token = chains.erc20_balance(key, network.usdc["mainnet"], evm)
                entry["usdc"] = {"raw": str(token), "display": caps.display(token, "USDC"), "contract": network.usdc["mainnet"]}
            except hand.HandError as exc:
                entry["usdc_error"] = str(exc)
            networks[key] = entry
        out["evm"] = {"address": evm, "networks": networks}
    return out


#: $TINY is a pump.fun mint, and those have 6 decimals. Used only when Teeny
#: holds no token account for it, so no account says otherwise.
TINY_DECIMALS = 6


def tiny_mint() -> str:
    """The $TINY mint: TINY_MINT in secrets/teeny.env, else the one in
    brain/CONTEXT.md (caps.TINY_MINT)."""
    mint = (hand.setting("TINY_MINT", caps.TINY_MINT) or caps.TINY_MINT).strip()
    if not solana.is_address(mint):
        raise hand.HandError("TINY_MINT is not a valid Solana address")
    return mint


def _decimal(raw: int, decimals: int) -> str:
    whole, frac = divmod(raw, 10**decimals)
    return f"{whole}.{frac:0{decimals}d}".rstrip("0").rstrip(".") if decimals else str(whole)


def _tiny_holding(cluster: str, owner: str) -> Json:
    mint = tiny_mint()
    raw, decimals = solana.token_holding(cluster, owner, mint)
    decimals = TINY_DECIMALS if decimals is None else decimals
    return {"mint": mint, "owner": owner, "raw": str(raw), "decimals": decimals}


def tiny() -> Json:
    """Teeny's $TINY, in tokens: one token-account read over the same Solana
    RPC the USDC balance uses. Nothing is signed and nothing moves. No token
    account for the mint is a balance of 0, not an error."""
    cluster = _cluster()
    held = _tiny_holding(cluster, own_address())
    held["amount"] = int(held["raw"]) / 10 ** held["decimals"]
    held["cluster"] = cluster
    return held


def _public(row: Mapping[str, Any]) -> Json:
    out = dict(row)
    out["amount"] = caps.display(int(row["amount_raw"]), row["asset"])
    out["amount_raw"] = str(row["amount_raw"])
    out["usd"] = caps.usd(int(row["usd_micro"]))
    del out["usd_micro"]
    if row.get("signature"):
        out["url"] = chains.explorer_tx(row.get("chain") or chains.SOLANA, row["signature"], row["cluster"])
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
            vendor = store.vendor_by_address(conn, row["to_address"], row.get("chain") or chains.SOLANA)
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
    out: Json = {"micro": total, "payments": count}
    if other:
        out["not_usdc"] = other
    return out


def cost_parts(days: int) -> Json:
    """The sources of `costs`, in micro-dollars, over the last `days`:
    `models` (micro, calls, charged, estimated) or `models_error`, `x402`
    (micro, payments, not_usdc) or `x402_error`, `bills` (each with its
    window share), `paid_out` by purpose, `since` and `complete`. `costs`
    formats this; `accounts` (the weekly page, accounts.py) adds it up in
    cents, so both read the same numbers."""
    since = hand.now() - days * store.DAY
    out: Json = {"days": days, "since": since, "complete": True}
    try:
        report = hand.core("cost_report", {"days": days})
        totals = report.get("totals") if isinstance(report, Mapping) else None
        totals = totals if isinstance(totals, Mapping) else {}
        out["models"] = {
            "micro": round(float(totals.get("cost_usd") or 0.0) * caps.MICRO),
            "calls": int(totals.get("calls") or 0),
            "charged_micro": round(float(totals.get("charged_usd") or 0.0) * caps.MICRO),
            "estimated_micro": round(float(totals.get("estimated_usd") or 0.0) * caps.MICRO),
        }
    except hand.HandError as exc:
        out["models_error"] = str(exc)
        out["complete"] = False
    try:
        out["x402"] = _x402(days, since, caps.USDC_MINTS[_cluster()])
    except (hand.HandError, caps.CapsError) as exc:
        out["x402_error"] = str(exc)
        out["complete"] = False

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
    for row in bill_rows:
        monthly = row["usd_micro"] if row["period"] == "month" else row["usd_micro"] // 12
        bills.append({"name": row["name"], "micro": row["usd_micro"], "period": row["period"], "note": row["note"],
                      "share_micro": monthly * days // MONTH_DAYS})
    out["bills"] = bills
    out["paid_out"] = {r["purpose"]: {"micro": int(r["micro"]), "payments": int(r["n"])} for r in paid}
    return out


def costs(days: int) -> Json:
    """What keeping Teeny running cost over the last `days`: model calls (the
    core's cost report), x402 API payments, and the fixed bills prorated to
    the window (a month counts as 30 days, a year as 12 months). Payments to
    bounty winners and vendors are listed beside it, not in it."""
    parts = cost_parts(days)
    out: Json = {"days": days}
    total = 0
    if "models" in parts:
        models = parts["models"]
        out["models"] = {
            "usd": caps.usd(models["micro"]),
            "calls": models["calls"],
            "charged_usd": caps.usd(models["charged_micro"]),
            "estimated_usd": caps.usd(models["estimated_micro"]),
        }
        total += models["micro"]
    else:
        out["models"] = {"error": parts["models_error"]}
    if "x402" in parts:
        x402 = parts["x402"]
        out["x402"] = {"usd": caps.usd(x402["micro"]), "payments": x402["payments"],
                       **({"not_usdc": x402["not_usdc"]} if x402.get("not_usdc") else {})}
        total += x402["micro"]
    else:
        out["x402"] = {"error": parts["x402_error"]}
    bills = []
    bills_micro = 0
    for row in parts["bills"]:
        bills_micro += row["share_micro"]
        entry = {"name": row["name"], "usd": caps.usd(row["micro"]), "period": row["period"], "in_window_usd": caps.usd(row["share_micro"])}
        if row["note"]:
            entry["note"] = row["note"]
        bills.append(entry)
    out["bills"] = {"usd": caps.usd(bills_micro), "items": bills}
    total += bills_micro
    out["total_usd"] = caps.usd(total)
    out["complete"] = parts["complete"]
    out["paid_out"] = {k: {"usd": caps.usd(v["micro"]), "payments": v["payments"]} for k, v in parts["paid_out"].items()}
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


def vendors_add(name: str, address_text: str, note: str | None, chain: str = chains.SOLANA) -> Json:
    """Allow one vendor address on one chain. An EVM vendor names its network:
    an address that is a wallet on Base need not be one on Arbitrum."""
    chain = (chain or chains.SOLANA).strip().lower()
    if chain not in chains.CHAINS:
        raise hand.Refused(f"--chain is one of {', '.join(chains.CHAINS)}", rule="chain")
    if chains.is_evm(chain):
        if not chains.is_evm_address(address_text):
            raise hand.Refused(f"that is not a valid {chain} address", rule="address")
        if address_text.lower() in chains.evm_tokens():
            raise hand.Refused("that is a token contract, not a wallet", rule="address")
    else:
        if not solana.is_address(address_text):
            raise hand.Refused("that is not a valid Solana address", rule="address")
        if address_text == caps.TINY_MINT or address_text in caps.USDC_MINTS.values():
            raise hand.Refused("that is a token mint, not a wallet", rule="address")
    conn = store.db()
    try:
        conn.execute(
            "INSERT INTO vendors (name, address, chain, note, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (name) DO UPDATE SET address = excluded.address, chain = excluded.chain, note = excluded.note",
            (_name(name), address_text, chain, note, hand.now()),
        )
    finally:
        conn.close()
    return {"vendor": {"name": _name(name), "address": address_text, "chain": chain}}


def vendors_list() -> Json:
    conn = store.db()
    try:
        rows = conn.execute("SELECT name, address, chain, note FROM vendors ORDER BY name").fetchall()
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
        "eth_usd": caps.usd(c.eth_usd_micro) if c.eth_usd_micro is not None else None,
        "convert_per_day_usd": caps.usd(c.convert_day_micro) if c.convert_day_micro is not None else None,
        "convert_loss_bps": c.convert_loss_bps,
        "x402_per_request_usd": caps.usd(c.x402_request_micro) if c.x402_request_micro is not None else None,
        "x402_per_day_usd": caps.usd(c.x402_day_micro) if c.x402_day_micro is not None else None,
        "evm_networks": list(chains.NETWORKS) if c.cluster == "mainnet" else [],
        "usdc_mint": c.usdc_mint,
        "challenge_hours": bounty_store.challenge_hours(),
    }
