"""The inflow ledger and Teeny's public accounts (docs/roadmap.md, S1).

Nothing here moves funds, signs or asks the core to. It reads the chain
(Solana JSON-RPC: signatures and transactions, the same reads `buy-tiny`
makes) and this hand's own tables.

  inflows scan    J  every deposit to Teeny's own Solana address since the
                     last scan, one row each in `wallet_inflows`: USDC to its
                     token accounts, and SOL to the address itself, from
                     confirmed, successful transactions Teeny did not sign.
                     A deposit from a team address (WALLET_TEAM_ADDRESSES,
                     else TINY_BUY_TEAM_ADDRESSES) is `team`; any other is a
                     `tip` until something says what it paid for (a `sale`
                     when it pays for a request, S3; a `bounty` the operator
                     tags); with no team list set, every deposit is
                     `unclassified`, never counted as earned. A memo on the
                     deposit is a stranger's text: it goes through the
                     guard's inbound screen and is kept only when allowed.
  inflows list    R  the ledger, newest first (memos never printed here)
  inflows tag     O  the operator says what one deposit was
  accounts        R  the profit and loss over the last N days (7)
  accounts close  J  the week's page, frozen into `wallet_accounts` once:
                     the pinned `accounts` job (bin/accounts, Mondays). The
                     receipts job publishes closed pages (bin/lib/receipts.py)

The page's every line is in whole cents, each rounded once from the ledger
it comes from, and every total is the sum of the lines above it, so the
page adds up to the cent by construction and each line can be checked
against `hands/wallet costs` and `hands/wallet inflows list`.

What it does not count, and says so in `not_counted`: network fees, SOL
received in dollars (SOL deposits are listed in SOL: the fixed
WALLET_SOL_USD is set high on purpose and would flatter the income), and
anything on the EVM networks.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Iterable, Mapping

from .. import hand
from . import caps, reads, solana, store
from .pay import own_address

Json = dict[str, Any]

CATEGORIES = ("team", "tip", "sale", "bounty", "unclassified")
#: What the operator may tag a deposit as. `unclassified` is only ever the
#: absence of a team list.
TAGS = ("team", "tip", "sale", "bounty")
EARNED = ("tip", "sale", "bounty")
#: A deposit's memo, beyond which it is cut.
MEMO_CHARS = 500
MEMO_PROGRAMS = ("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr", "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo")
#: The most transactions one scan reads, and the most signature pages per
#: account. More wait for the next scan, from where this one stopped.
MAX_TXS = 200
MAX_PAGES = 10
PAGE = 100
FIRST_WINDOW_DAYS = 30
#: A bill counts as the server when its name starts with one of these (the
#: vps hand names its bills `<provider> <id>`); any other bill is a tool.
SERVER_BILLS = ("vultr", "eqvps", "server", "vps")

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_inflows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signature   TEXT NOT NULL,
    chain       TEXT NOT NULL DEFAULT 'solana',
    cluster     TEXT NOT NULL,
    asset       TEXT NOT NULL CHECK (asset IN ('SOL', 'USDC')),
    amount_raw  INTEGER NOT NULL CHECK (amount_raw > 0),
    usd_micro   INTEGER NOT NULL CHECK (usd_micro >= 0),
    sender      TEXT,
    at          REAL NOT NULL,
    seen_at     REAL NOT NULL,
    category    TEXT NOT NULL CHECK (category IN ('team', 'tip', 'sale', 'bounty', 'unclassified')),
    memo        TEXT,
    memo_held   INTEGER NOT NULL DEFAULT 0,
    ref         TEXT,
    note        TEXT,
    stimulus    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (signature, asset)
);
CREATE INDEX IF NOT EXISTS wallet_inflows_at ON wallet_inflows (at);
CREATE TABLE IF NOT EXISTS wallet_scan (
    name        TEXT PRIMARY KEY,
    through     REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wallet_accounts (
    week        TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    page        TEXT NOT NULL
);
"""


def db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def team_addresses(env: Mapping[str, str] | None = None) -> frozenset[str] | None:
    """The team's and the operator's addresses, or None when none is set.
    Money from them is never counted as earned."""
    env = env if env is not None else hand.secrets()
    raw = (env.get("WALLET_TEAM_ADDRESSES") or env.get("TINY_BUY_TEAM_ADDRESSES") or "").replace(",", " ")
    team = frozenset(part for part in raw.split() if part)
    if not team:
        return None
    if any(not solana.is_address(a) for a in team):
        raise hand.Refused("WALLET_TEAM_ADDRESSES holds something that is not a Solana address", rule="team")
    return team


# --- reading the chain ---------------------------------------------------------------------------


def _signatures(cluster: str, address: str, since: float) -> tuple[list[tuple[float, str]], bool]:
    """(blockTime, signature) of successful transactions touching `address`
    after `since`, and whether there were more than MAX_PAGES pages."""
    out: list[tuple[float, str]] = []
    before: str | None = None
    for _ in range(MAX_PAGES):
        options: Json = {"limit": PAGE, "commitment": "confirmed"}
        if before:
            options["before"] = before
        page = solana.rpc(cluster, "getSignaturesForAddress", [address, options])
        if not isinstance(page, list):
            raise hand.HandError("Solana RPC getSignaturesForAddress sent no list")
        for entry in page:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("signature"), str):
                raise hand.HandError("Solana RPC sent a signature it could not read")
            at = entry.get("blockTime")
            if not isinstance(at, (int, float)) or at <= since:
                return out, False
            if entry.get("err") is None:
                out.append((float(at), entry["signature"]))
        if len(page) < PAGE:
            return out, False
        before = page[-1]["signature"]
    return out, True


def _token_accounts(cluster: str, owner: str, mint: str) -> list[str]:
    result = solana.rpc(cluster, "getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    accounts = result.get("value") if isinstance(result, Mapping) else None
    if not isinstance(accounts, list):
        raise hand.HandError("Solana RPC getTokenAccountsByOwner sent no account list")
    return sorted(str(a.get("pubkey")) for a in accounts if isinstance(a, Mapping) and a.get("pubkey"))


def _keys(tx: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    keys = (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys")) or []
    return [k if isinstance(k, Mapping) else {"pubkey": str(k)} for k in keys]


def _token_deltas(tx: Mapping[str, Any], mint: str) -> dict[str, int]:
    meta = tx.get("meta") or {}
    out: dict[str, int] = {}
    for sign, key in ((-1, "preTokenBalances"), (1, "postTokenBalances")):
        for row in meta.get(key) or []:
            if not isinstance(row, Mapping) or row.get("mint") != mint:
                continue
            try:
                amount = int(row["uiTokenAmount"]["amount"])
            except (KeyError, TypeError, ValueError):
                raise hand.HandError("Solana RPC sent a token balance it could not read") from None
            owner = str(row.get("owner"))
            out[owner] = out.get(owner, 0) + sign * amount
    return out


def _lamport_deltas(tx: Mapping[str, Any]) -> dict[str, int]:
    meta = tx.get("meta") or {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    out: dict[str, int] = {}
    for index, key in enumerate(_keys(tx)):
        if index < len(pre) and index < len(post):
            try:
                out[str(key.get("pubkey"))] = int(post[index]) - int(pre[index])
            except (TypeError, ValueError):
                raise hand.HandError("Solana RPC sent a balance it could not read") from None
    return out


def _memo(tx: Mapping[str, Any]) -> str | None:
    for ins in (((tx.get("transaction") or {}).get("message") or {}).get("instructions")) or []:
        if not isinstance(ins, Mapping):
            continue
        if ins.get("program") == "spl-memo" or ins.get("programId") in MEMO_PROGRAMS:
            text = ins.get("parsed")
            if isinstance(text, str) and text.strip():
                text = text.strip()
                return text if len(text) <= MEMO_CHARS else text[:MEMO_CHARS]
    return None


def _screen(memo: str | None, signature: str) -> tuple[str | None, int]:
    """A stranger's memo, through the guard's inbound screen: (the memo, 0)
    when allowed, (None, 1) when held. It is data from then on, never an
    instruction: nothing here reads it, and the mind sees it as the text of
    a stimulus."""
    if memo is None:
        return None, 0
    from ..guard import gate

    verdict = gate.inbound(memo, surface="wallet", context={"kind": "message", "signature": signature})
    if verdict.decision != "allow":
        return None, 1
    return memo, 0


def read_deposit(tx: Mapping[str, Any], own: str, usdc_mint: str) -> list[Json]:
    """The deposits to `own` in one transaction: USDC and SOL legs, each with
    its sender. None when Teeny signed it (its own payment, swap or buy)."""
    if not isinstance(tx, Mapping) or (tx.get("meta") or {}).get("err") is not None:
        return []
    keys = _keys(tx)
    signers = [str(k.get("pubkey")) for k in keys if k.get("signer")]
    if own in signers:
        return []
    legs: list[Json] = []
    tokens = _token_deltas(tx, usdc_mint)
    gained = tokens.get(own, 0)
    if gained > 0:
        senders = [o for o, d in sorted(tokens.items(), key=lambda kv: kv[1]) if d < 0]
        legs.append({"asset": "USDC", "amount_raw": gained, "usd_micro": gained,
                     "sender": senders[0] if senders else (signers[0] if signers else None), "signers": signers})
    lamports = _lamport_deltas(tx).get(own, 0)
    if lamports > 0:
        legs.append({"asset": "SOL", "amount_raw": lamports, "usd_micro": 0,
                     "sender": signers[0] if signers else None, "signers": signers})
    return legs


def _category(leg: Mapping[str, Any], team: frozenset[str] | None) -> str:
    if team is None:
        return "unclassified"
    if leg.get("sender") in team or set(leg.get("signers") or ()) & team:
        return "team"
    return "tip"


def _through(conn) -> float:
    row = conn.execute("SELECT through FROM wallet_scan WHERE name = 'inflows'").fetchone()
    return float(row["through"]) if row else hand.now() - FIRST_WINDOW_DAYS * store.DAY


def scan() -> Json:
    """Records every deposit since the last scan; returns the new rows.
    Reads only: nothing is signed, and the core is asked only for the
    wallet's own address."""
    cluster = reads._cluster()
    team = team_addresses()
    own = own_address()
    mint = caps.USDC_MINTS[cluster]
    with hand.lock("wallet-inflows", timeout=60):
        conn = db()
        try:
            since = _through(conn)
            found: dict[str, float] = {}
            truncated = False
            for address in [own, *_token_accounts(cluster, own, mint)]:
                sigs, more = _signatures(cluster, address, since)
                truncated = truncated or more
                for at, sig in sigs:
                    found.setdefault(sig, at)
            ordered = sorted(found.items(), key=lambda kv: (kv[1], kv[0]))
            if len(ordered) > MAX_TXS:
                ordered, truncated = ordered[:MAX_TXS], True
            new: list[Json] = []
            through = since
            for sig, at in ordered:
                if conn.execute("SELECT 1 FROM wallet_inflows WHERE signature = ?", (sig,)).fetchone():
                    through = max(through, at)
                    continue
                tx = solana.rpc(cluster, "getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
                legs = read_deposit(tx, own, mint) if isinstance(tx, Mapping) else []
                memo, held = _screen(_memo(tx), sig) if legs else (None, 0)
                for leg in legs:
                    row = {"signature": sig, "cluster": cluster, "asset": leg["asset"], "amount_raw": leg["amount_raw"],
                           "usd_micro": leg["usd_micro"], "sender": leg["sender"], "at": at, "seen_at": hand.now(),
                           "category": _category(leg, team), "memo": memo, "memo_held": held}
                    with hand.transaction(conn):
                        cur = conn.execute(
                            f"INSERT OR IGNORE INTO wallet_inflows ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                            tuple(row.values()))
                    if cur.rowcount:
                        new.append(_public(row, int(cur.lastrowid)))
                through = max(through, at)
            # A truncated scan stops where it read to; the rest is the next one's.
            with hand.transaction(conn):
                conn.execute("INSERT INTO wallet_scan (name, through, updated_at) VALUES ('inflows', ?, ?)"
                             " ON CONFLICT (name) DO UPDATE SET through = excluded.through, updated_at = excluded.updated_at",
                             (through, hand.now()))
        finally:
            conn.close()
    return {"new": new, "count": len(new), "through": _iso(through), "truncated": truncated, "team_known": team is not None}


def _iso(at: float) -> str:
    return _dt.datetime.fromtimestamp(at, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _public(row: Mapping[str, Any], row_id: int | None = None) -> Json:
    """One deposit for the operator or a stimulus: never the memo itself."""
    out: Json = {
        "id": row_id if row_id is not None else row.get("id"),
        "signature": row["signature"], "at": _iso(float(row["at"])), "asset": row["asset"],
        "amount": caps.display(int(row["amount_raw"]), row["asset"]), "amount_raw": str(row["amount_raw"]),
        "category": row["category"], "sender": row.get("sender"),
        "has_memo": bool(row.get("memo")), "memo_held": bool(row.get("memo_held")),
        "url": solana.explorer_tx(row["signature"], row["cluster"]),
    }
    if row["asset"] == "USDC":
        out["usd"] = caps.usd(int(row["usd_micro"]))
    for key in ("ref", "note"):
        if row.get(key):
            out[key] = row[key]
    return out


def inflows(max_rows: int = 50) -> Json:
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM wallet_inflows ORDER BY at DESC, id DESC LIMIT ?", (max_rows,)).fetchall()
        through = conn.execute("SELECT through FROM wallet_scan WHERE name = 'inflows'").fetchone()
    finally:
        conn.close()
    return {"inflows": [_public(dict(r)) for r in rows], "through": _iso(float(through["through"])) if through else None}


def tag(signature: str, category: str, note: str | None, ref: str | None = None) -> Json:
    """The operator says what a deposit was: team money, a tip, a sale, a
    bounty Teeny won. Every leg of that transaction is tagged."""
    if category not in TAGS:
        raise hand.Refused(f"--category is one of {', '.join(TAGS)}", rule="category")
    conn = db()
    try:
        with hand.transaction(conn):
            gone = conn.execute("UPDATE wallet_inflows SET category = ?, note = COALESCE(?, note), ref = COALESCE(?, ref)"
                                " WHERE signature = ?", (category, note, ref, signature)).rowcount
    finally:
        conn.close()
    if not gone:
        raise hand.Refused("no deposit with that signature is in the ledger", rule="unknown")
    return {"tagged": signature, "category": category, "legs": gone}


# --- the page -----------------------------------------------------------------------------------


def _cents(micro: int) -> int:
    """Micro-dollars as whole cents, rounded half up (caps.usd's rounding)."""
    return (micro + 5_000) // 10_000 if micro >= 0 else -((-micro + 5_000) // 10_000)


def _dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"


def _is_server(name: str) -> bool:
    return name.strip().lower().split(" ", 1)[0] in SERVER_BILLS or name.strip().lower().startswith("server")


def page(days: int = 7) -> Json:
    """The profit and loss over the last `days`. Costs from `costs`' own
    sources (reads.cost_parts), income from the inflow ledger, both over
    the same window."""
    parts = reads.cost_parts(days)
    since, until = float(parts["since"]), hand.now()
    why: list[str] = []
    if "models" not in parts:
        why.append("models")
    if "x402" not in parts:
        why.append("x402")

    server = tools_bills = 0
    for bill in parts["bills"]:
        if _is_server(bill["name"]):
            server += _cents(bill["share_micro"])
        else:
            tools_bills += _cents(bill["share_micro"])
    lines = {
        "models": _cents(parts["models"]["micro"]) if "models" in parts else 0,
        "server": server,
        "tools": tools_bills + (_cents(parts["x402"]["micro"]) if "x402" in parts else 0),
    }
    cost_total = sum(lines.values())

    conn = db()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM wallet_inflows WHERE at > ? AND at <= ? ORDER BY at, id", (since, until))]
        through = conn.execute("SELECT through FROM wallet_scan WHERE name = 'inflows'").fetchone()
        bought = store.bought_since(conn, since) - store.bought_since(conn, until)
    finally:
        conn.close()
    scanned = float(through["through"]) if through else None
    # The scan's cursor is the newest transaction it read, so a quiet wallet
    # lags; what matters is that a scan ran after the window closed.
    scan_ran = None
    conn = db()
    try:
        ran = conn.execute("SELECT updated_at FROM wallet_scan WHERE name = 'inflows'").fetchone()
        scan_ran = float(ran["updated_at"]) if ran else None
    finally:
        conn.close()
    if scan_ran is None or scan_ran < until - 3600:
        why.append("inflows")

    income = {c: 0 for c in EARNED}
    apart = {"team": 0, "unclassified": 0}
    sol = {c: 0 for c in CATEGORIES}
    listed = []
    for row in rows:
        cents = _cents(int(row["usd_micro"])) if row["asset"] == "USDC" else 0
        if row["asset"] == "USDC":
            if row["category"] in income:
                income[row["category"]] += cents
            else:
                apart[row["category"]] += cents
        else:
            sol[row["category"]] += int(row["amount_raw"])
        listed.append({"signature": row["signature"], "at": _iso(float(row["at"])), "asset": row["asset"],
                       "amount_raw": str(row["amount_raw"]), "usd": _dollars(cents) if row["asset"] == "USDC" else None,
                       "category": row["category"]})
    income_total = sum(income.values())
    paid = parts["paid_out"]
    return {
        "from": _iso(since), "to": _iso(until), "days": days,
        "costs": {**{k: _dollars(v) for k, v in lines.items()}, "total": _dollars(cost_total)},
        "income": {**{k: _dollars(v) for k, v in income.items()}, "total": _dollars(income_total)},
        "net": _dollars(income_total - cost_total),
        "team": _dollars(apart["team"]),
        "unclassified": _dollars(apart["unclassified"]),
        "sol_received_lamports": {k: str(v) for k, v in sol.items() if v},
        "paid_out": {k: _dollars(_cents(v["micro"])) for k, v in sorted(paid.items())},
        "tiny_bought": _dollars(_cents(bought)),
        "model_calls": parts["models"]["calls"] if "models" in parts else None,
        "inflows": listed,
        "inflows_through": _iso(scanned) if scanned else None,
        "team_known": _team_known(),
        "not_counted": ["network fees", "SOL in dollars", "EVM networks"],
        "complete": not why,
        **({"incomplete": why} if why else {}),
    }


def _team_known() -> bool:
    try:
        return team_addresses() is not None
    except hand.Refused:
        return False


def week_of(at: float) -> str:
    """The ISO week (2026-W46) that ended at the Monday on or before `at`."""
    day = _dt.datetime.fromtimestamp(at, _dt.timezone.utc).date()
    monday = day - _dt.timedelta(days=day.weekday())
    year, week, _ = (monday - _dt.timedelta(days=7)).isocalendar()
    return f"{year}-W{week:02d}"


def close() -> Json:
    """Freezes last week's page into `wallet_accounts`, once. Run on a Monday
    (UTC) by the pinned job, so the 7 days it covers are the week it is
    named for, give or take the minutes past midnight it ran (the page
    carries its exact `from` and `to`). An incomplete page (a cost source
    or the inflow scan missing) is not closed: an unread cost would look
    like profit."""
    now = hand.now()
    if _dt.datetime.fromtimestamp(now, _dt.timezone.utc).weekday() != 0:
        raise hand.Refused("a week is closed on Mondays (UTC) only", rule="not_monday")
    week = week_of(now)
    conn = db()
    try:
        if conn.execute("SELECT 1 FROM wallet_accounts WHERE week = ?", (week,)).fetchone():
            return {"closed": False, "week": week, "why": "already closed"}
    finally:
        conn.close()
    got = page(7)
    if not got["complete"]:
        raise hand.Refused("the week's page is incomplete", rule="incomplete", missing=got["incomplete"])
    got["week"] = week
    conn = db()
    try:
        with hand.transaction(conn):
            conn.execute("INSERT OR IGNORE INTO wallet_accounts (week, created_at, page) VALUES (?, ?, ?)",
                         (week, now, json.dumps(got, sort_keys=True, separators=(",", ":"))))
    finally:
        conn.close()
    return {"closed": True, "week": week, "page": got}


def closed_pages(conn=None) -> list[Json]:
    """Every closed page, oldest first, for the receipts job."""
    own = conn is None
    conn = conn or db()
    try:
        rows = conn.execute("SELECT week, page FROM wallet_accounts ORDER BY week").fetchall()
    finally:
        if own:
            conn.close()
    return [json.loads(r["page"]) for r in rows]


def stored(conn, rows: Iterable[Any]) -> None:  # pragma: no cover - kept for symmetry with other hands
    pass
