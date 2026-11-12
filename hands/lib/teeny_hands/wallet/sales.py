"""Paid build requests (docs/roadmap.md, S3): something Teeny sells to people.

A person asks Teeny to build something (any channel `hands/requests` reads),
then sends USDC to Teeny's wallet with the request's number in the memo
("request 12", "#12"). The prices are frozen config, `evals/prices.toml`.

  match    J  every new USDC deposit whose memo names a request: at or over
              the price it is a `sale` of that request (`request_payments`),
              and the request is paid, so `hands/requests pick` takes it
              before any unpaid one (`paid_first`). Under the price it stays
              a tip. A deposit for a request that was skipped or failed, or
              a paid request that later fails or is skipped, is due a refund.
  refund   J  pays the refunds due, each through `hands/wallet pay` with
              purpose `refund`: back to the address that sent the deposit,
              the deposit's exact amount, once, under the wallet's four caps
              and the core's policy, and under the refund limits in
              evals/prices.toml. `in_doubt` is never retried.
  run      J  match, then refund: the pinned `paid-requests` job

The memo is a stranger's text that passed the inbound guard (accounts.py).
Code reads one number out of it and nothing else: it never names an amount,
an address or anything to do. A refund goes to the deposit's on-chain
sender, never to an address anyone wrote down.

Off unless PAID_REQUESTS=1 (matching) and REQUEST_REFUNDS=1 (refunds) in
secrets/teeny.env; each refused `off` until then.
"""

from __future__ import annotations

import re
import sqlite3
import tomllib
from pathlib import Path
from typing import Any, Mapping

from .. import hand
from . import accounts, caps, store

Json = dict[str, Any]

PRICES_FILE = Path(__file__).resolve().parents[4] / "evals" / "prices.toml"
#: The request number in a memo: "request 12", "req #12", "#12". The first one wins.
MEMO_REQUEST = re.compile(r"(?:\brequest\s*#?\s*|\breq\s*#?\s*|#)(\d{1,9})\b", re.IGNORECASE)
#: request_payments statuses. `paid` holds the request's place; the refund
#: ones follow a request Teeny could not build.
STATUSES = ("paid", "under_price", "refund_due", "refunded", "refund_in_doubt")
#: Request statuses Teeny will not build from: a deposit for one is refunded.
NOT_BUILT = ("skipped", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS request_payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  INTEGER NOT NULL,
    inflow_id   INTEGER NOT NULL UNIQUE,
    signature   TEXT NOT NULL,
    sender      TEXT NOT NULL,
    amount_raw  INTEGER NOT NULL CHECK (amount_raw > 0),
    at          REAL NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('paid', 'under_price', 'refund_due', 'refunded', 'refund_in_doubt')),
    payment_id  INTEGER,
    note        TEXT,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS request_payments_request ON request_payments (request_id, status);
"""


def db():
    conn = accounts.db()
    conn.executescript(SCHEMA)
    return conn


def prices(path: Path | None = None) -> Json:
    """The frozen price list and refund limits, in micro-dollars. A missing
    or bad value is an error: frozen config never fails open."""
    data = tomllib.loads((path or PRICES_FILE).read_text())
    try:
        out = {
            "build_request": caps.dollars_to_micro(data["prices"]["build_request_usdc"], "build_request_usdc"),
            "refund_max": caps.dollars_to_micro(data["refunds"]["max_per_refund_usd"], "max_per_refund_usd"),
            "refund_day": caps.dollars_to_micro(data["refunds"]["max_per_day_usd"], "max_per_day_usd"),
        }
    except (KeyError, TypeError, caps.CapsError) as exc:
        raise hand.HandError(f"evals/prices.toml: {exc}") from None
    if any(v <= 0 for v in out.values()):
        raise hand.HandError("evals/prices.toml: every price and limit is above zero")
    return out


def price_list() -> Json:
    p = prices()
    return {"build_request_usdc": caps.usd(p["build_request"]), "refund_max_usd": caps.usd(p["refund_max"]),
            "refunds_per_day_usd": caps.usd(p["refund_day"]),
            "how": "send at least the price in USDC to Teeny's Solana address with the request's number in the memo, "
                   "for example \"request 12\""}


def request_in(memo: str | None) -> int | None:
    found = MEMO_REQUEST.search(memo or "")
    return int(found.group(1)) if found else None


def _request(conn, request_id: int):
    try:
        return conn.execute("SELECT id, status FROM build_requests WHERE id = ?", (request_id,)).fetchone()
    except Exception:  # noqa: BLE001 - no build_requests table yet: no request
        return None


def _set(conn, row_id: int, status: str, note: str | None = None, payment_id: int | None = None) -> None:
    conn.execute("UPDATE request_payments SET status = ?, note = COALESCE(?, note), payment_id = COALESCE(?, payment_id),"
                 " updated_at = ? WHERE id = ?", (status, note, payment_id, hand.now(), row_id))


def match() -> Json:
    """Ties new deposits to the requests their memos name, and marks the
    refunds now due. Scans first (read only)."""
    if not hand.flag("PAID_REQUESTS", False):
        raise hand.Refused("paid build requests are off: PAID_REQUESTS=1 in secrets/teeny.env", rule="off")
    price = prices()["build_request"]
    accounts.scan()
    matched: list[Json] = []
    conn = db()
    try:
        rows = conn.execute(
            "SELECT * FROM wallet_inflows WHERE asset = 'USDC' AND category = 'tip' AND memo IS NOT NULL AND ref IS NULL"
            " AND id NOT IN (SELECT inflow_id FROM request_payments) ORDER BY at, id").fetchall()
        for row in rows:
            request_id = request_in(row["memo"])
            if request_id is None or not row["sender"]:
                continue
            request = _request(conn, request_id)
            if request is None:
                continue
            if int(row["amount_raw"]) < price:
                status = "under_price"
            elif request["status"] in NOT_BUILT:
                status = "refund_due"
            else:
                status = "paid"
            with hand.transaction(conn):
                conn.execute(
                    "INSERT INTO request_payments (request_id, inflow_id, signature, sender, amount_raw, at, status, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (request_id, row["id"], row["signature"], row["sender"], row["amount_raw"], row["at"], status, hand.now()))
                if status != "under_price":
                    conn.execute("UPDATE wallet_inflows SET category = 'sale', ref = ? WHERE id = ?", (f"request:{request_id}", row["id"]))
            matched.append({"request": request_id, "signature": row["signature"], "status": status})
        # A paid request Teeny then could not build: its money goes back.
        marks = ",".join("?" * len(NOT_BUILT))
        with hand.transaction(conn):
            due = conn.execute(
                f"UPDATE request_payments SET status = 'refund_due', note = 'the request was not built', updated_at = ?"
                f" WHERE status = 'paid' AND request_id IN (SELECT id FROM build_requests WHERE status IN ({marks}))",
                (hand.now(), *NOT_BUILT)).rowcount
    finally:
        conn.close()
    return {"matched": matched, "refunds_due": due}


def paid_waiting(conn) -> list[int]:
    """Pending requests with a deposit at the price: built first."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT r.id FROM build_requests r JOIN request_payments p ON p.request_id = r.id"
            " WHERE r.status = 'pending' AND p.status = 'paid' ORDER BY p.at, r.id").fetchall()
    except Exception:  # noqa: BLE001 - no request_payments yet: nothing paid
        return []
    return [int(r[0]) for r in rows]


def is_paid(conn, request_id: int) -> bool:
    try:
        return conn.execute("SELECT 1 FROM request_payments WHERE request_id = ? AND status = 'paid'", (request_id,)).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


# --- refunds ---------------------------------------------------------------------------------------


def refunded_since(conn, since: float) -> int:
    """Micro-dollars of refunds that may have moved in the window."""
    marks = ",".join("?" * len(store.COUNTED))
    return int(conn.execute(
        f"SELECT COALESCE(SUM(usd_micro), 0) FROM wallet_payments WHERE purpose = 'refund' AND status IN ({marks}) AND created_at > ?",
        (*store.COUNTED, since)).fetchone()[0])


def refundable(conn, ref: str, to: str, asset: str, raw: int) -> Json:
    """pay's rules for purpose `refund` (pay.py): the ref names a refund
    due, to the address that sent the deposit, for its exact amount, in
    USDC, within the frozen refund limits. Each refused with its rule."""
    if not hand.flag("REQUEST_REFUNDS", False):
        raise hand.Refused("refunds are off: REQUEST_REFUNDS=1 in secrets/teeny.env", rule="off")
    found = re.fullmatch(r"request-payment:(\d{1,12})", ref)
    try:
        row = conn.execute("SELECT * FROM request_payments WHERE id = ?", (int(found.group(1)),)).fetchone() if found else None
    except sqlite3.OperationalError:  # no request_payments yet: nothing is due
        row = None
    if row is None or row["status"] != "refund_due":
        raise hand.Refused("that is not a refund that is due", rule="refund")
    if row["sender"] != to:
        raise hand.Refused("a refund goes back to the address that sent the deposit, only", rule="refund_address")
    if asset != "USDC" or int(row["amount_raw"]) != raw:
        raise hand.Refused("a refund is the deposit's exact amount in USDC", rule="refund_amount")
    limits = prices()
    if raw > limits["refund_max"]:
        raise hand.Refused(f"refunds above ${caps.usd(limits['refund_max'])} are a person's to make", rule="refund_max")
    day = refunded_since(conn, hand.now() - store.DAY)
    if day + raw > limits["refund_day"]:
        raise hand.Refused(f"that would take the last 24 hours of refunds over ${caps.usd(limits['refund_day'])}", rule="refund_day")
    return {"refund_of": row["signature"], "request": int(row["request_id"])}


def refund() -> Json:
    """Pays every refund due, oldest first, through `pay`. A refusal leaves
    it due for the next run (nothing moved); `in_doubt` is marked and never
    retried; a refund already tried is never tried again (pay's `duplicate`)."""
    if not hand.flag("REQUEST_REFUNDS", False):
        raise hand.Refused("refunds are off: REQUEST_REFUNDS=1 in secrets/teeny.env", rule="off")
    from . import pay

    conn = db()
    try:
        due = conn.execute("SELECT * FROM request_payments WHERE status = 'refund_due' ORDER BY at, id").fetchall()
    finally:
        conn.close()
    out: list[Json] = []
    for row in due:
        body = {"to": row["sender"], "amount_usdc": caps.display(int(row["amount_raw"]), "USDC"), "purpose": "refund",
                "ref": f"request-payment:{row['id']}"}
        try:
            got = pay.pay(body)
        except hand.Refused as refused:
            out.append({"id": row["id"], "refused": refused.reason, **refused.detail})
            if refused.detail.get("rule") == "duplicate":
                _mark(row["id"], "refund_in_doubt" if refused.detail.get("status") == "in_doubt" else "refunded",
                      payment_id=refused.detail.get("payment_id"))
            continue
        except hand.HandError as error:
            # In doubt (or a prepare that failed). Read the ledger to know which.
            conn = db()
            try:
                paid = conn.execute("SELECT id, status FROM wallet_payments WHERE purpose = 'refund' AND ref = ? ORDER BY id DESC LIMIT 1",
                                    (body["ref"],)).fetchone()
            finally:
                conn.close()
            if paid is not None and paid["status"] == "in_doubt":
                _mark(row["id"], "refund_in_doubt", note="in doubt: a person reads the ledger and the chain", payment_id=paid["id"])
            out.append({"id": row["id"], "error": str(error)[:300]})
            continue
        if got.get("dry_run"):
            out.append({"id": row["id"], "would_refund": got["would_pay"]})
            continue
        _mark(row["id"], "refunded", payment_id=got["paid"]["payment_id"])
        out.append({"id": row["id"], "refunded": got["paid"]["signature"]})
    return {"refunds": out}


def _mark(row_id: int, status: str, note: str | None = None, payment_id: int | None = None) -> None:
    conn = db()
    try:
        with hand.transaction(conn):
            _set(conn, row_id, status, note, payment_id)
    finally:
        conn.close()


def run() -> Json:
    """The pinned job: match, then refund, each only when switched on."""
    out: Json = {}
    for name, step in (("match", match), ("refund", refund)):
        try:
            out[name] = step()
        except hand.Refused as refused:
            out[name] = {"refused": refused.reason, **refused.detail}
    return out


def payments(max_rows: int = 50) -> Json:
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM request_payments ORDER BY id DESC LIMIT ?", (max_rows,)).fetchall()
    finally:
        conn.close()
    return {"payments": [{**store.row_dict(r), "usd": caps.usd(int(r["amount_raw"]))} for r in rows]}
