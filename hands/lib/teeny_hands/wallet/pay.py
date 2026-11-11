"""`pay`: the only way Teeny's wallet moves money.

    {"to": ADDRESS, "amount_usdc": "25" | "amount_sol": "0.1",
     "purpose": "bounty" | "vendor", "ref": "..."}

Every rule runs before the core hears of the payment, in this order, each
refused with its `rule`:

  fields            nothing but the fields above (and `asset`/`mint`, which
                    may only name SOL or the cluster's pinned USDC mint)
  tiny              $TINY is never sent, whatever the body says
  asset             SOL or USDC, nothing else
  amount            exactly one amount, above zero, within the asset's decimals
  purpose, ref      bounty or vendor, with a reference
  address           a valid Solana address that is not a mint
  caps              the caps in secrets/teeny.env are set and sane (caps.py)
  per_tx            at most WALLET_MAX_PER_TX_USD
  own_address       never to Teeny's own address
  bounty            bounty: the winner has been public BOUNTY_CHALLENGE_HOURS,
  challenge_window  claimed with this address, the amount is the prize, and
  claimed_address   it has not been paid (bounty/store.py)
  prize
  vendor            vendor: the address is in the `vendors` allowlist
  duplicate         this purpose and ref has never been paid or tried
  per_day           the last 24 hours stay within WALLET_MAX_PER_DAY_USD
  per_recipient_week  this address's last 7 days stay within
                    WALLET_MAX_PER_RECIPIENT_WEEK_USD
  half_balance      never more than half of what the wallet holds of the asset

Under DRY_RUN the answer is `would_pay` with every rule that ran: no ledger
row and no core call that could move funds (the reads still happen).

Live, under the `wallet-pay` lock: the ledger row is written `pending`
first, then the core prepares a quote (which must match the payment
exactly) and executes it. The core applies its own `[wallet_policy]` caps
too (bin/lib/wallet_config.py sets apply_to_interactive, so its RPC executes
are capped). A refusal by that policy is `refused`; any other failure after
the execute was sent is `in_doubt`, and an in-doubt payment is never retried.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from .. import hand
from ..bounty import store as bounty_store
from . import caps, solana, store

POLICY_REFUSED = "WALLET_POLICY_REFUSED:"
PURPOSES = ("bounty", "vendor")
FIELDS = {"to", "amount_usdc", "amount_sol", "purpose", "ref", "asset", "mint"}

Core = Callable[..., Any]


@dataclass(frozen=True)
class Payment:
    to: str
    asset: str
    raw: int
    purpose: str
    ref: str


def _amount(value: Any, asset: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise hand.Refused("the amount is a decimal number", rule="amount")
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise hand.Refused("the amount is a decimal number", rule="amount") from None
    if not amount.is_finite() or amount <= 0:
        raise hand.Refused("the amount must be above zero", rule="amount")
    raw = amount * (10 ** caps.DECIMALS[asset])
    if raw != raw.to_integral_value():
        raise hand.Refused(f"{asset} has {caps.DECIMALS[asset]} decimals", rule="amount")
    return int(raw)


def _names_tiny(value: Any) -> bool:
    text = str(value or "").strip()
    return text == caps.TINY_MINT or text.lstrip("$").upper() == "TINY"


def parse(body: Any, cluster: str) -> Payment:
    """The body as a Payment, or the first rule it breaks."""
    if not isinstance(body, Mapping):
        raise hand.Refused("pay takes a JSON object", rule="fields")
    if any(_names_tiny(body.get(key)) for key in ("asset", "mint", "to")):
        raise hand.Refused("Teeny never sends $TINY, whoever asks", rule="tiny")
    unknown = sorted(set(body) - FIELDS)
    if unknown:
        raise hand.Refused(f"unknown field(s): {', '.join(unknown)}", rule="fields")
    amounts = [key for key in ("amount_usdc", "amount_sol") if body.get(key) is not None]
    if len(amounts) != 1:
        raise hand.Refused("give exactly one of amount_usdc or amount_sol", rule="amount")
    asset = "USDC" if amounts[0] == "amount_usdc" else "SOL"
    named = body.get("asset")
    if named is not None and str(named).strip().upper() != asset:
        raise hand.Refused("only SOL and USDC, and the asset must match the amount", rule="asset")
    mint = body.get("mint")
    if mint is not None and not (asset == "USDC" and str(mint).strip() == caps.USDC_MINTS[cluster]):
        raise hand.Refused("only SOL and the cluster's USDC mint; no other token", rule="asset")
    raw = _amount(body[amounts[0]], asset)
    purpose = body.get("purpose")
    if purpose not in PURPOSES:
        raise hand.Refused("purpose is bounty or vendor", rule="purpose")
    ref = body.get("ref")
    if not isinstance(ref, (str, int)) or isinstance(ref, bool) or not str(ref).strip() or len(str(ref)) > 100:
        raise hand.Refused("ref names what the payment is for (a bounty id, an invoice)", rule="ref")
    to = body.get("to")
    if not solana.is_address(to):
        raise hand.Refused("to is not a valid Solana address", rule="address")
    if to in caps.USDC_MINTS.values():
        raise hand.Refused("to is a token mint, not a wallet", rule="address")
    return Payment(to, asset, raw, purpose, str(ref).strip())


def load_caps() -> caps.Caps:
    try:
        return caps.load(hand.secrets())
    except caps.CapsError as exc:
        raise hand.Refused(f"the wallet's caps are not usable: {exc}", rule="caps") from None


# --- what the core knows ----------------------------------------------------------------


def own_address(core: Core = hand.core) -> str:
    """Teeny's Solana address, from the core's wallet status."""
    status = core("wallet_status", {})
    accounts = status.get("accounts") if isinstance(status, Mapping) else None
    for account in accounts or []:
        if isinstance(account, Mapping) and account.get("chain") == "solana" and solana.is_address(account.get("address")):
            return str(account["address"])
    raise hand.HandError("the core has no Solana wallet; the operator runs bin/wallet-setup once")


def sol_balance(core: Core = hand.core) -> int:
    rows = core("wallet_balances", {})
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, Mapping) and row.get("chain") == "solana":
            if row.get("providerStatus") != "ready":
                raise hand.HandError("the core could not read the SOL balance (its Solana endpoint did not answer)")
            try:
                return int(row["raw"])
            except (KeyError, TypeError, ValueError):
                break
    raise hand.HandError("the core reported no SOL balance")


def balance_of(asset: str, address: str, the_caps: caps.Caps, core: Core = hand.core) -> int:
    if asset == "SOL":
        return sol_balance(core)
    return solana.token_balance(the_caps.cluster, address, the_caps.usdc_mint)


# --- pay --------------------------------------------------------------------------------


def _check(checks: list[dict[str, Any]], rule: str, detail: str) -> None:
    checks.append({"rule": rule, "ok": True, "detail": detail})


def _rules(conn: Any, p: Payment, c: caps.Caps, usd: int, own: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """The rules that read the ledger. Run under the lock, and again inside
    the transaction that writes the pending row."""
    context: dict[str, Any] = {}
    if p.purpose == "bounty":
        context = bounty_store.payable(conn, p.ref, p.to, p.asset, p.raw)
        _check(checks, "bounty", f"bounty {p.ref}: winner public over {bounty_store.challenge_hours()}h, claimed with this address, the prize")
    else:
        vendor = store.vendor_by_address(conn, p.to)
        if vendor is None:
            raise hand.Refused("that address is not in the vendors allowlist", rule="vendor")
        context = {"vendor": vendor["name"]}
        _check(checks, "vendor", f"allowlisted as {vendor['name']}")
    prior = store.active_for_ref(conn, p.purpose, p.ref)
    if prior is not None:
        raise hand.Refused(f"{p.purpose} {p.ref} was already paid or tried", rule="duplicate", payment_id=prior["id"], status=prior["status"])
    _check(checks, "duplicate", "never paid or tried before")
    now = hand.now()
    day = store.spent_since(conn, now - store.DAY)
    if day + usd > c.per_day_micro:
        raise hand.Refused(
            f"that would take the last 24 hours to ${caps.usd(day + usd)}, over the ${caps.usd(c.per_day_micro)} a day cap",
            rule="per_day",
        )
    _check(checks, "per_day", f"${caps.usd(day + usd)} of ${caps.usd(c.per_day_micro)} in 24h")
    week = store.spent_since(conn, now - store.WEEK, p.to)
    if week + usd > c.recipient_week_micro:
        raise hand.Refused(
            f"that would take this address's last 7 days to ${caps.usd(week + usd)}, over the ${caps.usd(c.recipient_week_micro)} cap",
            rule="per_recipient_week",
        )
    _check(checks, "per_recipient_week", f"${caps.usd(week + usd)} of ${caps.usd(c.recipient_week_micro)} to this address in 7d")
    return context


def pay(body: Any, core: Core = hand.core) -> dict[str, Any]:
    try:
        cluster = caps.cluster(hand.secrets())
    except caps.CapsError as exc:
        raise hand.Refused(f"the wallet's caps are not usable: {exc}", rule="caps") from None
    p = parse(body, cluster)
    checks: list[dict[str, Any]] = []
    _check(checks, "asset", f"{p.asset}, not $TINY or another token")
    _check(checks, "address", "a valid Solana address")
    c = load_caps()
    _check(checks, "caps", "set and within their ceilings")
    usd = caps.usd_micro(p.asset, p.raw, c)
    if usd > c.per_tx_micro:
        raise hand.Refused(f"${caps.usd(usd)} is over the ${caps.usd(c.per_tx_micro)} per payment cap", rule="per_tx")
    _check(checks, "per_tx", f"${caps.usd(usd)} of ${caps.usd(c.per_tx_micro)}")
    own = own_address(core)
    if p.to == own:
        raise hand.Refused("that is Teeny's own address", rule="own_address")
    _check(checks, "own_address", "not Teeny's own address")

    summary = {
        "to": p.to,
        "asset": p.asset,
        "amount_raw": str(p.raw),
        "amount": caps.display(p.raw, p.asset),
        "usd": caps.usd(usd),
        "purpose": p.purpose,
        "ref": p.ref,
        "cluster": c.cluster,
    }
    with hand.lock("wallet-pay"):
        conn = store.db()
        try:
            context = _rules(conn, p, c, usd, own, checks)
            balance = balance_of(p.asset, own, c, core)
            if p.raw * 2 > balance:
                raise hand.Refused(
                    f"that is more than half of the {caps.display(balance, p.asset)} {p.asset} the wallet holds",
                    rule="half_balance",
                )
            _check(checks, "half_balance", f"{summary['amount']} of {caps.display(balance, p.asset)} {p.asset}")
            if hand.dry_run():
                return {"dry_run": True, "would_pay": {**summary, **context}, "checks": checks}

            with hand.transaction(conn):
                _rules(conn, p, c, usd, own, [])
                payment_id = store.insert_pending(
                    conn, cluster=c.cluster, asset=p.asset, amount_raw=p.raw, usd_micro=usd, to_address=p.to, purpose=p.purpose, ref=p.ref
                )
            return _send(conn, payment_id, p, summary, context, core)
        finally:
            conn.close()


def _send(conn: Any, payment_id: int, p: Payment, summary: dict[str, Any], context: dict[str, Any], core: Core) -> dict[str, Any]:
    params: dict[str, Any] = {"chain": "solana", "toAddress": p.to, "amountRaw": str(p.raw)}
    if p.asset == "USDC":
        params["assetSymbol"] = "USDC"
    try:
        quote = core("wallet_prepare_transfer", params, timeout=60)
    except hand.CoreError as exc:
        # A quote moves nothing, so a failed prepare is a plain failure.
        store.update(conn, payment_id, status="failed", error=str(exc)[:300])
        raise hand.HandError(f"payment {payment_id}: the core could not prepare it ({exc})") from None
    quote = quote if isinstance(quote, Mapping) else {}
    mismatch = [
        name
        for name, ok in (
            ("toAddress", quote.get("toAddress") == p.to),
            ("amountRaw", str(quote.get("amountRaw")) == str(p.raw)),
            ("assetSymbol", str(quote.get("assetSymbol") or "").upper() == p.asset),
            ("chain", quote.get("chain") in (None, "solana")),
            ("quoteId", bool(quote.get("quoteId"))),
        )
        if not ok
    ]
    if mismatch:
        store.update(conn, payment_id, status="failed", error=f"quote did not match: {', '.join(mismatch)}")
        raise hand.Refused("the core's quote did not match the payment; nothing was executed", rule="quote", payment_id=payment_id, fields=mismatch)
    store.update(conn, payment_id, quote_id=str(quote["quoteId"]))
    try:
        result = core("wallet_execute_prepared", {"quoteId": str(quote["quoteId"]), "confirmed": True}, timeout=120)
    except hand.CoreError as exc:
        if POLICY_REFUSED in str(exc):
            store.update(conn, payment_id, status="refused", error=str(exc)[:300])
            raise hand.Refused("the core's spend policy refused the payment", rule="core_policy", payment_id=payment_id) from None
        store.update(conn, payment_id, status="in_doubt", error=str(exc)[:300])
        raise hand.HandError(
            f"payment {payment_id} is in doubt: the execute was sent and did not come back ({exc}). "
            "It is never retried; check the core's ledger (hands/wallet ledger) and the chain."
        ) from None
    signature = result.get("transactionHash") if isinstance(result, Mapping) else None
    if not isinstance(signature, str) or not signature:
        store.update(conn, payment_id, status="in_doubt", error="the execute answered without a signature")
        raise hand.HandError(f"payment {payment_id} is in doubt: the core answered without a signature. It is never retried.")
    store.update(conn, payment_id, status="paid", signature=signature)
    return {
        "paid": {**summary, **context, "payment_id": payment_id, "signature": signature, "url": solana.explorer_tx(signature, summary["cluster"])},
    }
