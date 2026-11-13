"""Teeny pays its own server (docs/roadmap.md, S5).

  invoice add   O  the operator records a server invoice: provider, its id,
                   the dollars, the chain and the address to pay in USDC,
                   and when it is due. The address must already be an
                   allowlisted vendor on that chain (`hands/wallet vendors`):
                   recording an invoice cannot introduce a new recipient.
  invoices      R  the invoices and what became of each
  pay-due       J  every invoice due within DUE_AHEAD_DAYS, paid once through
                   `hands/wallet pay` with purpose `bill` and ref
                   `vps:<provider>:<invoice>`, under the wallet's four caps,
                   the core's policy, and the bill limits in
                   evals/prices.toml. The pinned `pay-bills` job, daily.

Every paid bill is a `wallet_payments` row like any payment, so it gets a
receipt the same way: `hands/wallet receipts` lists it for the
wallet-receipt playbook, and bin/receipts publishes it (`wallet.paid`,
purpose `bill`).

An invoice is `due`, then `paid` (with the payment), or `in_doubt` (the
execute went out and did not come back: never retried, a person reads the
core's ledger and the chain). A refusal (a cap, a limit) leaves it `due`
and moves nothing. Off until VPS_PAY_BILLS=1: `pay-due` is skipped (exit 0)
and `pay` refuses a bill (`off`).

Why the operator records the invoice: neither provider's API states a
crypto invoice's address and amount in a form this hand can read (Vultr is
funded by card or BitPay in its console; EQVPS's top-up is a checkout page).
The money still only moves from here, by code, under the caps.
"""

from __future__ import annotations

import datetime as _dt
import re
import sqlite3
from typing import Any

from .. import hand
from . import client

Json = dict[str, Any]

DUE_AHEAD_DAYS = 3
DAY = 86400.0
STATUSES = ("due", "paid", "in_doubt")
INVOICE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vps_invoices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,
    invoice     TEXT NOT NULL,
    usd_micro   INTEGER NOT NULL CHECK (usd_micro > 0),
    chain       TEXT NOT NULL,
    pay_to      TEXT NOT NULL,
    due_at      REAL NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('due', 'paid', 'in_doubt')),
    payment_id  INTEGER,
    note        TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE (provider, invoice)
);
"""


def db() -> sqlite3.Connection:
    return hand.open_db(SCHEMA)


def ref_of(provider: str, invoice: str) -> str:
    return f"vps:{provider}:{invoice}"


def limits() -> Json:
    from ..wallet import caps, sales

    data = sales.prices_raw()
    try:
        out = {"per_bill": caps.dollars_to_micro(data["bills"]["max_per_bill_usd"], "max_per_bill_usd"),
               "per_month": caps.dollars_to_micro(data["bills"]["max_per_month_usd"], "max_per_month_usd")}
    except (KeyError, TypeError, caps.CapsError) as exc:
        raise hand.HandError(f"evals/prices.toml: {exc}") from None
    if min(out.values()) <= 0:
        raise hand.HandError("evals/prices.toml: the bill limits are above zero")
    return out


def add(provider: str, invoice: str, usd: str, chain: str, pay_to: str, due: str, note: str | None) -> Json:
    from ..wallet import caps, chains
    from ..wallet import store as wallet_store

    if provider not in client.PROVIDERS:
        raise hand.Refused(f"--provider is one of {', '.join(client.PROVIDERS)}", rule="provider")
    if not INVOICE_ID.fullmatch(invoice or ""):
        raise hand.Refused("an invoice id is 1-64 letters, digits, dots, dashes or underscores", rule="invoice")
    if chain not in chains.CHAINS:
        raise hand.Refused(f"--chain is one of {', '.join(chains.CHAINS)}", rule="chain")
    try:
        micro = caps.dollars_to_micro(usd, "--usd")
        when = _dt.datetime.fromisoformat(due).replace(tzinfo=_dt.timezone.utc).timestamp()
    except (caps.CapsError, ValueError):
        raise hand.Refused("--usd is dollars and --due a date (YYYY-MM-DD)", rule="invoice") from None
    if micro <= 0:
        raise hand.Refused("--usd is above zero", rule="invoice")
    conn = wallet_store.db()
    try:
        vendor = wallet_store.vendor_by_address(conn, pay_to, chain)
    finally:
        conn.close()
    if vendor is None:
        raise hand.Refused("that address is not an allowlisted vendor on that chain (hands/wallet vendors add)", rule="vendor")
    conn = db()
    try:
        with hand.transaction(conn):
            if conn.execute("SELECT 1 FROM vps_invoices WHERE provider = ? AND invoice = ?", (provider, invoice)).fetchone():
                raise hand.Refused("that invoice is already recorded", rule="duplicate")
            cur = conn.execute(
                "INSERT INTO vps_invoices (provider, invoice, usd_micro, chain, pay_to, due_at, status, note, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'due', ?, ?, ?)",
                (provider, invoice, micro, chain, pay_to, when, note, hand.now(), hand.now()))
    finally:
        conn.close()
    return {"invoice": {"id": int(cur.lastrowid), "provider": provider, "invoice": invoice, "usd": caps.usd(micro),
                        "chain": chain, "vendor": vendor["name"], "due": due}}


def listing() -> Json:
    from ..wallet import caps

    conn = db()
    try:
        rows = conn.execute("SELECT * FROM vps_invoices ORDER BY due_at DESC, id DESC LIMIT 50").fetchall()
    finally:
        conn.close()
    return {"invoices": [{**{k: r[k] for k in r.keys() if r[k] is not None and k != "usd_micro"}, "usd": caps.usd(r["usd_micro"])}
                         for r in rows]}


def payable(conn: sqlite3.Connection, ref: str, to: str, chain: str, asset: str, raw: int) -> Json:
    """pay's rules for purpose `bill` (wallet/pay.py): the ref names a due
    invoice, to its recorded address on its chain, in USDC, for exactly its
    amount, the address still an allowlisted vendor, within the frozen bill
    limits. Each refused with its rule."""
    from ..wallet import chains
    from ..wallet import store as wallet_store

    if not hand.flag("VPS_PAY_BILLS", False):
        raise hand.Refused("paying bills is off: VPS_PAY_BILLS=1 in secrets/teeny.env", rule="off")
    found = re.fullmatch(r"vps:([a-z]+):([A-Za-z0-9._-]{1,64})", ref)
    try:
        row = conn.execute("SELECT * FROM vps_invoices WHERE provider = ? AND invoice = ?", found.groups()).fetchone() if found else None
    except sqlite3.OperationalError:
        row = None
    if row is None or row["status"] != "due":
        raise hand.Refused("that is not a server invoice that is due", rule="bill")
    if row["chain"] != chain or not chains.same_address(chain, row["pay_to"], to):
        raise hand.Refused("a bill is paid to the address on its invoice, on its chain, only", rule="bill_address")
    if asset != "USDC" or int(row["usd_micro"]) != raw:
        raise hand.Refused("a bill is its invoice's exact amount in USDC", rule="bill_amount")
    if wallet_store.vendor_by_address(conn, to, chain) is None:
        raise hand.Refused("the invoice's address is no longer an allowlisted vendor", rule="vendor")
    lim = limits()
    if raw > lim["per_bill"]:
        raise hand.Refused("that bill is over the most one bill may be; a person pays it", rule="bill_max")
    marks = ",".join("?" * len(wallet_store.COUNTED))
    month = int(conn.execute(
        f"SELECT COALESCE(SUM(usd_micro), 0) FROM wallet_payments WHERE purpose = 'bill' AND status IN ({marks}) AND created_at > ?",
        (*wallet_store.COUNTED, hand.now() - 30 * DAY)).fetchone()[0])
    if month + raw > lim["per_month"]:
        raise hand.Refused("that would take 30 days of bills over the frozen limit", rule="bill_month")
    return {"invoice": row["invoice"], "provider": row["provider"]}


def _set(row_id: int, status: str, payment_id: int | None = None, note: str | None = None) -> None:
    conn = db()
    try:
        with hand.transaction(conn):
            conn.execute("UPDATE vps_invoices SET status = ?, payment_id = COALESCE(?, payment_id), note = COALESCE(?, note),"
                         " updated_at = ? WHERE id = ?", (status, payment_id, note, hand.now(), row_id))
    finally:
        conn.close()


def pay_due() -> Json:
    """Pays every invoice due within DUE_AHEAD_DAYS, once each."""
    from ..wallet import caps, pay
    from ..wallet import store as wallet_store

    if not hand.flag("VPS_PAY_BILLS", False):
        return {"skipped": "paying bills is off (VPS_PAY_BILLS=1 in secrets/teeny.env turns it on)"}
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM vps_invoices WHERE status = 'due' AND due_at <= ? ORDER BY due_at, id",
                            (hand.now() + DUE_AHEAD_DAYS * DAY,)).fetchall()
    finally:
        conn.close()
    out: list[Json] = []
    for row in rows:
        ref = ref_of(row["provider"], row["invoice"])
        body = {"to": row["pay_to"], "chain": row["chain"], "amount_usdc": caps.display(int(row["usd_micro"]), "USDC"),
                "purpose": "bill", "ref": ref}
        try:
            got = pay.pay(body)
        except hand.Refused as refused:
            out.append({"invoice": row["invoice"], "refused": refused.reason, **refused.detail})
            if refused.detail.get("rule") == "duplicate":
                status = "in_doubt" if refused.detail.get("status") == "in_doubt" else "paid"
                _set(row["id"], status, refused.detail.get("payment_id"))
            continue
        except hand.HandError as error:
            conn = wallet_store.db()
            try:
                last = conn.execute("SELECT id, status FROM wallet_payments WHERE purpose = 'bill' AND ref = ? ORDER BY id DESC LIMIT 1",
                                    (ref,)).fetchone()
            finally:
                conn.close()
            if last is not None and last["status"] == "in_doubt":
                _set(row["id"], "in_doubt", last["id"], "in doubt: a person reads the core's ledger and the chain")
            out.append({"invoice": row["invoice"], "error": str(error)[:300]})
            continue
        if got.get("dry_run"):
            out.append({"invoice": row["invoice"], "would_pay": got["would_pay"]})
            continue
        _set(row["id"], "paid", got["paid"]["payment_id"])
        out.append({"invoice": row["invoice"], "paid": got["paid"]["signature"], "url": got["paid"]["url"]})
    return {"bills": out}
