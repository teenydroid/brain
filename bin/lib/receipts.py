"""Teeny's public receipts: proof of what it did, never what it read or thought.

    python3 bin/lib/receipts.py [--preview DIR]
    python3 bin/lib/receipts.py reveal SEQ

The repository (teenydroid/diary, RECEIPTS_REPO) is a hash-chained,
append-only ledger. Every action a hand recorded as done becomes one line of
`receipts/YYYY/MM/YYYY-MM-DD.jsonl` (the UTC day it was published):

    {"at": "2026-11-11T06:07:19Z", "commit": "<sha256>", "kind": "x.post",
     "prev": "<sha256 of the previous line>", "ref": {"id": "1856..."}, "seq": 42}

- `ref` holds only identifiers anyone can check somewhere else: an X post id,
  a Solana transaction signature, a GitHub repository and commit, a Moltbook
  post id, agent or submolt. Each field is an enum, an integer or matches a strict pattern; a
  row with anything else is withheld, never published, and counted in the
  run's output.
- `commit` is sha256(salt || the hand's whole ledger row), with a 32-byte
  random salt kept in hands.db (`receipts_issued`). It reveals nothing, but
  binds the receipt to the full private record: `reveal SEQ` prints the salt
  and the record so an operator can choose to prove one receipt in full.
  Actions with nothing public to point at are a commitment alone: mail and
  Telegram messages, which are private, so no address, chat, person, chat
  kind, message id or word of them is published.
- `prev` chains every line to the one before it, and `head.json` names the
  last line's seq and hash, so a rewritten or deleted receipt breaks the
  chain. `verify.py` in the repository checks it with no dependencies.

Every surface is covered, each from the ledger its hand writes once the
action went out (never under DRY_RUN, never when refused or held): x_posts and
x_likes; moltbook_posts and moltbook_comments (live statuses) and
moltbook_actions (upvotes, follows, subscriptions); gh_actions; shipped
build_jobs; paid wallet_payments and bought wallet_tiny_buys; bounties; mail_sent; telegram_sent. The mind
loop's state/tick/runs.jsonl is never read: it is what the mind meant to do,
dry runs and refusals included, and every action it took that went out is
already in a ledger, so reading it too would count one twice.

Nothing here is free text. No post text, titles, summaries, diary prose,
prompts, logs, mentions or anybody else's words are ever written, so nothing
the model read (a secret, or an injected instruction asking to be repeated)
has a path into the repository. The publisher is plain code with no model in
it, and it reads only the ledgers hands write after an action succeeded.

Live, before appending, the published chain is verified from the first line;
a chain that does not verify (someone else pushed, or history was edited)
refuses the run rather than build on it. Only `receipts/`, `head.json`,
`stats/`, `wallet.json` and `accounts/` are ever staged, the staged diff still goes through
the secret gate (every value in secrets/*.env of 12 characters or more, and
the usual key shapes), and a push that fails resets the checkout to the remote so the next run rebuilds.

Two more files ride the same push, so the public site has numbers it can
check rather than defaults:

    stats/latest.json, stats/YYYY-MM-DD.json   the newest `stats` snapshot
      {"date": "2026-11-12", "github": {"public_repos": 4, "stars": 9},
       "moltbook": {"karma": 77}, "x": {"followers": 120, "posts": 301}}
    wallet.json                                the wallet's public address
      {"address": "<base58 Solana public key>", "cluster": "mainnet"}

The stats are the rows of the newest snapshot date (hands/stats writes them
from what X, Moltbook and GitHub report), only for the sources and metrics in
STATS_ALLOWED, and only whole numbers from 0 to 2**53 - 1; any other row is
dropped. The address is what the core's wallet_status reports for Solana,
only if it is a base58 public key; nothing else of the wallet is read. A
snapshot with nothing allowed in it, or a core that does not answer, leaves
the files already published as they are. Live, a run with no new receipts
still pushes when either file changed, and nothing at all when neither did.

With ACCOUNTS_PUBLISH=1 the closed weekly profit and loss pages ride along
too (docs/roadmap.md, S1), one file each, written once and never rewritten:

    accounts/2026-W46.json   {"week", "from", "to", "costs": {"models",
      "server", "tools", "total"}, "income": {"tip", "sale", "bounty",
      "refunds", "total"}, "net", "team", "unclassified", "paid_out", "tiny_bought",
      "sol_received_lamports", "inflows": [{"signature", "at", "asset",
      "amount_raw", "usd", "category"}], "not_counted", "team_known", ...}

They are closed by `hands/wallet accounts close` (the pinned `accounts`
job) and rebuilt here field by field: dollars as `0.00`, enums, timestamps
and Solana signatures only; a page with anything else, or not complete, is
withheld whole. No sender address and no memo is ever published.

Under DRY_RUN (the default) a fresh chain is written to state/receipts-preview/
and nothing touches git or hands.db. Live, the repository is cloned to
state/receipts-repo/ and pushed with RECEIPTS_GITHUB_TOKEN (or
DIARY_GITHUB_TOKEN, or GITHUB_TOKEN) through askpass (teeny_hands.git).

Prints one JSON object: {"ok", "dry_run", "issued", "withheld", "stats", "wallet", "pushed", ...}.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import secrets as rng
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "hands" / "lib"))

from teeny_hands import git, hand  # noqa: E402
from teeny_hands.wallet import caps as wallet_caps, pay as wallet_pay, solana  # noqa: E402

DEFAULT_REPO = "teenydroid/diary"
GENESIS = "0" * 64
HEAD_FILE = "head.json"
RECEIPTS_DIR = "receipts"
STATS_DIR = "stats"
WALLET_FILE = "wallet.json"
#: Moltbook statuses that mean the post or comment is up.
MOLTBOOK_LIVE = ("posted", "verified")

#: Shapes of a credential, whatever the env files hold.
KEY_SHAPES = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{30,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
)
MIN_SECRET = 12

ISSUED_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts_issued (
    key     TEXT PRIMARY KEY,
    seq     INTEGER NOT NULL UNIQUE,
    kind    TEXT NOT NULL,
    salt    TEXT NOT NULL,
    record  TEXT NOT NULL,
    at      REAL NOT NULL
);
"""

Json = dict[str, Any]

# --- what may be published ------------------------------------------------------------------------------
# Every published value passes one of these. Anything that fails withholds
# the whole receipt: a half-checked one is worse than none.

X_ID = re.compile(r"\d{1,25}")
MOLTBOOK_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
SUBMOLT = re.compile(r"[a-z0-9_-]{1,40}")
#: A Moltbook agent name as the moltbook hand ledgers it (lowercased).
AGENT = re.compile(r"[a-z0-9_-]{1,40}")
REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
SHA = re.compile(r"[0-9a-f]{7,40}")
SOLANA_SIG = re.compile(r"[1-9A-HJ-NP-Za-km-z]{64,90}")
#: The clusters a payment may name: exactly what the wallet hand records
#: (WALLET_CLUSTER as it is, `mainnet` or `devnet`), so none is dropped.
CLUSTERS = wallet_caps.CLUSTERS
ASSETS = ("SOL", "USDC")
PURPOSES = ("bounty", "vendor", "refund", "bill")
MAX_INT = 2**63 - 1


class Withheld(Exception):
    """A row with a value that may not be published."""


def _match(pattern: re.Pattern[str], value: Any) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Withheld
    return value


def _one_of(options: tuple[str, ...], value: Any) -> str:
    if value not in options:
        raise Withheld
    return value


def _int(value: Any, low: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= MAX_INT:
        raise Withheld
    return value


def _repo_and_sha(repo: Any, commit_url: Any) -> Json:
    repo = _match(REPO, repo)
    if not isinstance(commit_url, str):
        raise Withheld
    found = re.fullmatch(rf"https://github\.com/{re.escape(repo)}/commit/([0-9a-f]{{7,40}})", commit_url)
    if not found:
        raise Withheld
    return {"repo": repo, "sha": found.group(1)}


def _detail(row: sqlite3.Row) -> Json:
    try:
        value = json.loads(row["detail"] or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# --- gathering ------------------------------------------------------------------------------------------


class Action:
    """One done thing: its dedupe key, kind, when, public ref and private row."""

    __slots__ = ("key", "kind", "at", "ref", "record")

    def __init__(self, key: str, kind: str, at: float, ref: Json, record: Json):
        self.key, self.kind, self.at, self.ref, self.record = key, kind, at, ref, record


def _rows(conn: sqlite3.Connection, query: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    """A query against a table some other hand creates: no table yet, no rows."""
    try:
        return conn.execute(query, tuple(args)).fetchall()
    except sqlite3.OperationalError as error:
        if "no such table" in str(error) or "no such column" in str(error):
            return []
        raise


def _key(kind: str, ref: Json) -> str | None:
    """The dedupe key a public receipt carries in itself, so a receipt that
    reached the remote is never issued twice even if hands.db lost its row."""
    pick = {
        "x.post": "id", "x.reply": "id", "x.quote": "id", "x.like": "id",
        "moltbook.post": "id", "moltbook.comment": "id",
        "moltbook.follow": "agent", "moltbook.subscribe": "submolt",
        "wallet.paid": "signature", "wallet.bought": "signature", "bounty.opened": "id", "bounty.picked": "id",
    }.get(kind)
    if pick and pick in ref:
        return f"{kind}:{ref[pick]}"
    if kind == "moltbook.upvote" and len(ref) == 1:
        [(field, value)] = ref.items()
        return f"{kind}:{field}:{value}"
    if kind in ("github.push", "build.shipped") and "sha" in ref:
        return f"{kind}:{ref['repo']}@{ref['sha']}"
    if kind in ("github.create-repo", "github.fork") and "repo" in ref:
        return f"{kind}:{ref['repo']}"
    if kind == "github.open-pr" and "number" in ref:
        return f"{kind}:{ref['repo']}#{ref['number']}"
    if kind == "github.comment" and "id" in ref:
        return f"{kind}:{ref['repo']}:{ref['id']}"
    return None


def gather(conn: sqlite3.Connection) -> tuple[list[Action], dict[str, int]]:
    """Every done action the hand ledgers hold, and how many were withheld per kind."""
    actions: list[Action] = []
    withheld: dict[str, int] = {}

    def add(kind: str, at: Any, row: sqlite3.Row, build: Callable[[], Json], key: str | None = None) -> None:
        try:
            ref = build()
            when = float(at)
        except (Withheld, TypeError, ValueError):
            withheld[kind] = withheld.get(kind, 0) + 1
            return
        actions.append(Action(key or _key(kind, ref) or "", kind, when, ref, dict(row)))

    for r in _rows(conn, "SELECT * FROM x_posts ORDER BY created_at, id"):
        kind = {"post": "x.post", "reply": "x.reply", "quote": "x.quote"}.get(r["kind"], "x.other")

        def x_ref(r: sqlite3.Row = r, kind: str = kind) -> Json:
            ref: Json = {"id": _match(X_ID, r["id"])}
            if kind == "x.reply":
                ref["in_reply_to"] = _match(X_ID, r["in_reply_to"])
            elif kind == "x.quote":
                ref["quote_of"] = _match(X_ID, r["quote_of"])
            elif kind != "x.post":
                raise Withheld
            return ref

        add(kind, r["created_at"], r, x_ref)

    for r in _rows(conn, "SELECT * FROM x_likes ORDER BY liked_at, post_id"):
        add("x.like", r["liked_at"], r, lambda r=r: {"id": _match(X_ID, r["post_id"])})

    marks = ",".join("?" * len(MOLTBOOK_LIVE))
    for r in _rows(conn, f"SELECT * FROM moltbook_posts WHERE status IN ({marks}) ORDER BY at, id", MOLTBOOK_LIVE):
        add("moltbook.post", r["at"], r,
            lambda r=r: {"id": _match(MOLTBOOK_ID, r["post_id"]), "submolt": _match(SUBMOLT, r["submolt"])})
    for r in _rows(conn, f"SELECT * FROM moltbook_comments WHERE status IN ({marks}) ORDER BY at, id", MOLTBOOK_LIVE):
        add("moltbook.comment", r["at"], r,
            lambda r=r: {"id": _match(MOLTBOOK_ID, r["comment_id"]), "post": _match(MOLTBOOK_ID, r["post_id"])})
    # Upvotes, follows and subscriptions: the hand ledgers one only after
    # Moltbook took it, never under DRY_RUN.
    simple: dict[str, tuple[str, str, re.Pattern[str]]] = {
        "upvote-post": ("moltbook.upvote", "post", MOLTBOOK_ID),
        "upvote-comment": ("moltbook.upvote", "comment", MOLTBOOK_ID),
        "follow": ("moltbook.follow", "agent", AGENT),
        "subscribe": ("moltbook.subscribe", "submolt", SUBMOLT),
    }
    for r in _rows(conn, "SELECT * FROM moltbook_actions ORDER BY at, id"):
        if r["verb"] not in simple:
            kind = f"moltbook.{r['verb']}"
            withheld[kind] = withheld.get(kind, 0) + 1
            continue
        kind, field, pattern = simple[r["verb"]]
        add(kind, r["at"], r, lambda r=r, f=field, p=pattern: {f: _match(p, r["target"])})

    # A repository Teeny made private stays out entirely: its name, its pushes
    # and the builds shipped to it.
    private = {r["repo"] for r in _rows(conn, "SELECT repo, detail FROM gh_actions WHERE verb = 'create-repo'")
               if _detail(r).get("private")}

    def public_repo(value: Any) -> str:
        repo = _match(REPO, value)
        if repo in private:
            raise Withheld
        return repo

    for r in _rows(conn, "SELECT * FROM gh_actions ORDER BY at, id"):
        verb, detail = r["verb"], _detail(r)
        kind = f"github.{verb}"
        if verb == "create-repo":
            if detail.get("private"):
                continue  # not withheld: never meant to be public
            add(kind, r["at"], r, lambda r=r: {"repo": public_repo(r["repo"])})
        elif verb == "push":
            add(kind, r["at"], r, lambda r=r: {"repo": public_repo(r["repo"]), "sha": _match(SHA, r["ref"])})
        elif verb == "open-pr":
            add(kind, r["at"], r, lambda r=r: {"repo": public_repo(r["repo"]), "number": _int(_as_int(r["ref"]), 1)})
        elif verb == "comment":
            add(kind, r["at"], r, lambda r=r, d=detail: {"repo": public_repo(r["repo"]), "id": _int(_as_int(r["ref"]), 1),
                                                         "number": _int(d.get("number"), 1)})
        elif verb == "fork":
            add(kind, r["at"], r, lambda r=r: {"repo": public_repo(r["repo"])})
        else:
            withheld[kind] = withheld.get(kind, 0) + 1

    for r in _rows(conn, "SELECT * FROM build_jobs WHERE status = 'shipped' AND shipped_at IS NOT NULL ORDER BY shipped_at, id"):
        def build_ref(r: sqlite3.Row = r) -> Json:
            ref = _repo_and_sha(r["repo"], r["commit_url"])
            public_repo(ref["repo"])
            return ref

        add("build.shipped", r["shipped_at"], r, build_ref)

    for r in _rows(conn, "SELECT * FROM wallet_payments WHERE status = 'paid' ORDER BY updated_at, id"):
        add("wallet.paid", r["updated_at"], r, lambda r=r: {
            "signature": _match(SOLANA_SIG, r["signature"]), "cluster": _one_of(CLUSTERS, r["cluster"]),
            "asset": _one_of(ASSETS, r["asset"]), "amount_raw": _int(r["amount_raw"], 1),
            "purpose": _one_of(PURPOSES, r["purpose"])})

    for r in _rows(conn, "SELECT * FROM wallet_tiny_buys WHERE status = 'bought' ORDER BY updated_at, id"):
        add("wallet.bought", r["updated_at"], r, lambda r=r: {
            "signature": _match(SOLANA_SIG, r["signature"]), "cluster": _one_of(CLUSTERS, r["cluster"]),
            "usdc_raw": _int(r["usdc_raw"], 1), "mint": _one_of((wallet_caps.TINY_MINT,), r["output_mint"])})

    for r in _rows(conn, "SELECT * FROM bounties WHERE x_post_id IS NOT NULL ORDER BY created_at, id"):
        add("bounty.opened", r["public_at"] or r["created_at"], r, lambda r=r: {
            "id": _int(r["id"], 1), "prize_raw": _int(r["prize_raw"], 1), "deadline": _int(int(r["deadline"])),
            "x_post": _match(X_ID, r["x_post_id"])})
    for r in _rows(conn, "SELECT * FROM bounties WHERE winner_post_id IS NOT NULL ORDER BY picked_at, id"):
        add("bounty.picked", r["picked_at"] or r["created_at"], r, lambda r=r: {
            "id": _int(r["id"], 1), "x_post": _match(X_ID, r["winner_post_id"])})

    for r in _rows(conn, "SELECT * FROM mail_sent ORDER BY at, id"):
        # Who Teeny mailed and what about stays private: the receipt is a
        # commitment alone, keyed by the ledger row.
        add("mail.sent", r["at"], r, lambda: {}, key=f"mail.sent:{r['id']}")

    for r in _rows(conn, "SELECT * FROM telegram_sent ORDER BY at, id"):
        # Telegram the same way: no chat, person, message id or chat kind,
        # only that one message went out.
        add("telegram.sent", r["at"], r, lambda: {}, key=f"telegram.sent:{r['id']}")

    return actions, withheld


# --- the numbers: the latest stats snapshot and the wallet's address -----------------------------------

#: What may be published from the stats table: each source and its metrics.
STATS_ALLOWED: dict[str, tuple[str, ...]] = {
    "x": ("followers", "following", "posts"),
    "moltbook": ("karma", "followers", "following", "posts", "comments"),
    "github": ("followers", "public_repos", "stars"),
}
#: The largest whole number a JSON reader holds exactly.
MAX_COUNT = 2**53 - 1
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
SOLANA_ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
#: How long the core gets to name the wallet before the run goes on without it.
CORE_TIMEOUT = 30.0


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not float(value).is_integer() or not 0 <= value <= MAX_COUNT:
        return None
    return int(value)


def public_stats(conn: sqlite3.Connection) -> Json | None:
    """The newest snapshot's allowed metrics as whole numbers, with its date; None when nothing is allowed."""
    rows = _rows(conn, "SELECT max(date) AS date FROM stats WHERE date GLOB ?", (DATE_GLOB,))
    date = rows[0]["date"] if rows else None
    if not isinstance(date, str) or not DATE.fullmatch(date):
        return None
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        return None
    sources: dict[str, dict[str, int]] = {}
    for r in _rows(conn, "SELECT source, metric, value FROM stats WHERE date = ? ORDER BY source, metric", (date,)):
        number = _count(r["value"])
        if r["metric"] in STATS_ALLOWED.get(r["source"], ()) and number is not None:
            sources.setdefault(r["source"], {})[r["metric"]] = number
    return {"date": date, **sources} if sources else None


def public_wallet(core: Callable[[str, Json], Any], env: Any) -> Json | None:
    """The wallet's public address and cluster; None when the core does not name a valid one."""
    try:
        cluster = wallet_caps.cluster(env)
        address = wallet_pay.own_address(core)
    except (hand.HandError, wallet_caps.CapsError, ValueError):
        return None
    if not SOLANA_ADDRESS.fullmatch(address) or not solana.is_address(address):
        return None
    return {"address": address, "cluster": cluster}


def _core(method: str, params: Json) -> Any:
    return hand.core(method, params, timeout=CORE_TIMEOUT)


def write_numbers(root: Path, stats: Json | None, wallet: Json | None) -> list[str]:
    """Writes the files there is something for; returns the paths, relative to root."""
    files: dict[str, Json] = {}
    if stats:
        files[f"{STATS_DIR}/latest.json"] = stats
        files[f"{STATS_DIR}/{stats['date']}.json"] = stats
    if wallet:
        files[WALLET_FILE] = wallet
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(value) + "\n", encoding="ascii")
    return list(files)


# --- the weekly accounts (docs/roadmap.md, S1) -----------------------------------------------------------

ACCOUNTS_DIR = "accounts"
WEEK = re.compile(r"\d{4}-W\d{2}")
DOLLARS = re.compile(r"-?\d{1,12}\.\d{2}")
ISO_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
LAMPORTS = re.compile(r"\d{1,20}")
INFLOW_ASSETS = ("SOL", "USDC")
INFLOW_CATEGORIES = ("team", "tip", "sale", "bounty", "unclassified")
COST_LINES = ("models", "server", "tools", "total")
INCOME_LINES = ("tip", "sale", "bounty", "refunds", "total")
NOT_COUNTED = ("network fees", "SOL in dollars", "EVM networks")


def public_accounts(conn: sqlite3.Connection) -> dict[str, Json]:
    """The closed weekly profit-and-loss pages (hands/wallet accounts close),
    rebuilt field by field from what may be published: dollars as `0.00`
    strings, enums, timestamps and transaction signatures. A page with any
    other value is withheld whole. Off unless ACCOUNTS_PUBLISH=1."""
    if not hand.flag("ACCOUNTS_PUBLISH", False):
        return {}
    out: dict[str, Json] = {}
    for row in _rows(conn, "SELECT week, page FROM wallet_accounts ORDER BY week"):
        try:
            page = json.loads(row["page"])
            clean = _public_page(page)
        except (Withheld, ValueError, TypeError, KeyError, AttributeError):
            continue
        out[f"{ACCOUNTS_DIR}/{clean['week']}.json"] = clean
    return out


def _public_page(page: Json) -> Json:
    dollars = lambda value: _match(DOLLARS, value)  # noqa: E731
    clean: Json = {
        "week": _match(WEEK, page["week"]), "from": _match(ISO_AT, page["from"]), "to": _match(ISO_AT, page["to"]),
        "days": _int(page["days"], 1),
        "costs": {k: dollars(page["costs"][k]) for k in COST_LINES},
        "income": {k: dollars(page["income"][k]) for k in INCOME_LINES},
        "net": dollars(page["net"]), "team": dollars(page["team"]), "unclassified": dollars(page["unclassified"]),
        "paid_out": {_one_of(PURPOSES, k): dollars(v) for k, v in page["paid_out"].items()},
        "tiny_bought": dollars(page["tiny_bought"]),
        "sol_received_lamports": {_one_of(INFLOW_CATEGORIES, k): _match(LAMPORTS, v) for k, v in page["sol_received_lamports"].items()},
        "team_known": page["team_known"] is True,
        "not_counted": [_one_of(NOT_COUNTED, v) for v in page["not_counted"]],
        "inflows": [],
    }
    for item in page["inflows"]:
        asset = _one_of(INFLOW_ASSETS, item["asset"])
        clean["inflows"].append({
            "signature": _match(SOLANA_SIG, item["signature"]), "at": _match(ISO_AT, item["at"]), "asset": asset,
            "amount_raw": _match(LAMPORTS, item["amount_raw"]), "category": _one_of(INFLOW_CATEGORIES, item["category"]),
            "usd": dollars(item["usd"]) if asset == "USDC" else None})
    if page.get("complete") is not True:
        raise Withheld
    return clean


def write_accounts(root: Path, pages: dict[str, Json]) -> list[str]:
    """Writes the pages not yet in the repository. A published page is never
    rewritten: the week's numbers are what they were when it closed."""
    written = []
    for name, value in pages.items():
        path = root / name
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(value) + "\n", encoding="ascii")
        written.append(name)
    return written


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


# --- the chain ------------------------------------------------------------------------------------------


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def commitment(salt_hex: str, record: Json) -> str:
    return hashlib.sha256(bytes.fromhex(salt_hex) + canonical(record).encode("ascii")).hexdigest()


def _iso(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def receipt_files(root: Path) -> list[Path]:
    folder = root / RECEIPTS_DIR
    return sorted(folder.glob("*/*/*.jsonl")) if folder.is_dir() else []


def read_chain(root: Path) -> Iterator[Json]:
    for path in receipt_files(root):
        for number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
            if line:
                try:
                    entry = json.loads(line)
                except ValueError:
                    raise hand.Refused("the published chain does not verify", at=f"{path.name}:{number}") from None
                yield {"line": line, "entry": entry, "where": f"{path.name}:{number}"}


def verify_chain(root: Path) -> tuple[int, str, set[str]]:
    """(last seq, last hash, the keys already published). Refuses on any break."""
    seq, prev, keys = 0, GENESIS, set()
    for item in read_chain(root):
        entry, line = item["entry"], item["line"]
        if (not isinstance(entry, dict) or canonical(entry) != line or entry.get("seq") != seq + 1
                or entry.get("prev") != prev):
            raise hand.Refused("the published chain does not verify", at=item["where"])
        seq, prev = entry["seq"], line_hash(line)
        key = _key(str(entry.get("kind")), entry.get("ref") if isinstance(entry.get("ref"), dict) else {})
        if key:
            keys.add(key)
    head = root / HEAD_FILE
    if seq or head.is_file():
        try:
            stated = json.loads(head.read_text(encoding="ascii"))
        except (OSError, ValueError):
            stated = None
        if stated != {"seq": seq, "hash": prev}:
            raise hand.Refused("the published chain does not verify", at=HEAD_FILE)
    return seq, prev, keys


def append(root: Path, actions: list[Action], seq: int, prev: str, today: str) -> list[Json]:
    """Appends the actions as receipts; returns what to remember for each."""
    issued = []
    path = root / RECEIPTS_DIR / today[:4] / today[5:7] / f"{today}.jsonl"
    lines = []
    for action in sorted(actions, key=lambda a: (a.at, a.kind, a.key)):
        seq += 1
        salt = rng.token_hex(32)
        entry = {"seq": seq, "at": _iso(action.at), "kind": action.kind, "ref": action.ref,
                 "commit": commitment(salt, action.record), "prev": prev}
        line = canonical(entry)
        lines.append(line)
        prev = line_hash(line)
        issued.append({"key": action.key, "seq": seq, "kind": action.kind, "salt": salt,
                       "record": canonical(action.record), "at": action.at})
    if lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="ascii") as out:
            out.write("".join(f"{line}\n" for line in lines))
        (root / HEAD_FILE).write_text(canonical({"seq": seq, "hash": prev}) + "\n", encoding="ascii")
    return issued


# --- the secret gate ------------------------------------------------------------------------------------


def secret_values() -> list[str]:
    values: set[str] = set()
    folder = hand.secrets_dir()
    for path in sorted(folder.glob("*.env")) if folder.is_dir() else []:
        try:
            parsed = hand._parse_env_file(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        values.update(v for v in parsed.values() if len(v) >= MIN_SECRET)
    return sorted(values)


def check_secrets(diff: str) -> None:
    """Raises hand.Refused if the diff carries a secret. Only added lines count."""
    added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    if KEY_SHAPES.search(added):
        raise hand.Refused("something shaped like a key")
    for value in secret_values():
        if value in added:
            raise hand.Refused("a value from secrets/")


# --- git ------------------------------------------------------------------------------------------------


def _git(repo: Path | None, *args: str, token: str | None = None) -> str:
    return git.text(repo, *args, token=token, timeout=90)


def _branch() -> str:
    return hand.setting("RECEIPTS_BRANCH") or hand.setting("DIARY_BRANCH") or "main"


def _checkout(token: str) -> Path:
    repo = hand.state_dir() / "receipts-repo"
    # RECEIPTS_REMOTE (a full URL or path) is for tests and mirrors.
    url = (hand.setting("RECEIPTS_REMOTE") or hand.setting("DIARY_REMOTE")
           or f"https://github.com/{hand.setting('RECEIPTS_REPO') or hand.setting('DIARY_REPO') or DEFAULT_REPO}.git")
    branch = _branch()
    if not (repo / ".git").is_dir():
        _git(None, "clone", "-q", "--branch", branch, url, str(repo), token=token)
    else:
        _git(repo, "remote", "set-url", "origin", url)
        _git(repo, "fetch", "-q", "origin", branch, token=token)
        # Only this script writes here, and a run that did not push resets:
        # the remote is the truth.
        _git(repo, "checkout", "-q", branch)
        _git(repo, "reset", "-q", "--hard", f"origin/{branch}")
        _git(repo, "clean", "-qfd")
    return repo


def _issued_db() -> sqlite3.Connection:
    return hand.open_db(ISSUED_SCHEMA)


def publish(actions: list[Action], stats: Json | None = None, wallet: Json | None = None,
            accounts: dict[str, Json] | None = None) -> Json:
    token = hand.setting("RECEIPTS_GITHUB_TOKEN") or hand.setting("DIARY_GITHUB_TOKEN") or hand.setting("GITHUB_TOKEN")
    if not token:
        raise hand.HandError("no token: set RECEIPTS_GITHUB_TOKEN (or GITHUB_TOKEN) in secrets/teeny.env")
    branch = _branch()
    with hand.lock("receipts", timeout=60):
        repo = _checkout(token)
        seq, prev, published = verify_chain(repo)
        conn = _issued_db()
        try:
            published |= {r["key"] for r in conn.execute("SELECT key FROM receipts_issued")}
        finally:
            conn.close()
        fresh = [a for a in actions if a.key and a.key not in published]
        issued = append(repo, fresh, seq, prev, _iso(hand.now())[:10]) if fresh else []
        write_numbers(repo, stats, wallet)
        write_accounts(repo, accounts or {})
        paths = [p for p in (RECEIPTS_DIR, HEAD_FILE, STATS_DIR, WALLET_FILE, ACCOUNTS_DIR) if (repo / p).exists()]
        if paths:
            _git(repo, "add", "--", *paths)
        if not _git(repo, "diff", "--cached", "--name-only").strip():
            return {"issued": 0, "head": seq, "pushed": 0}
        try:
            check_secrets(_git(repo, "diff", "--cached", "--unified=0"))
        except hand.Refused as why:
            _git(repo, "reset", "-q", "--hard", f"origin/{branch}")
            raise hand.Refused("the receipts carry a secret, nothing committed", why=why.reason) from None
        message = f"receipts: {issued[0]['seq']}-{issued[-1]['seq']}" if issued else "receipts: stats, wallet and accounts"
        _git(repo, "commit", "-q", "-m", message)
        try:
            _git(repo, "push", "-q", "origin", f"HEAD:{branch}", token=token)
        except hand.HandError:
            _git(repo, "reset", "-q", "--hard", f"origin/{branch}")
            raise
        if issued:
            conn = _issued_db()
            try:
                with hand.transaction(conn):
                    conn.executemany(
                        "INSERT OR REPLACE INTO receipts_issued (key, seq, kind, salt, record, at)"
                        " VALUES (:key, :seq, :kind, :salt, :record, :at)", issued)
            finally:
                conn.close()
    return {"issued": len(issued), "head": issued[-1]["seq"] if issued else seq, "pushed": 1}


def preview(root: Path, actions: list[Action], stats: Json | None = None, wallet: Json | None = None,
            accounts: dict[str, Json] | None = None) -> Json:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    issued = append(root, [a for a in actions if a.key], 0, GENESIS, _iso(hand.now())[:10])
    write_numbers(root, stats, wallet)
    write_accounts(root, accounts or {})
    return {"issued": len(issued), "head": issued[-1]["seq"] if issued else 0}


def reveal(seq: int) -> Json:
    """The salt and record behind one receipt, for an operator who decides to prove it."""
    conn = _issued_db()
    try:
        row = conn.execute("SELECT * FROM receipts_issued WHERE seq = ?", (seq,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise hand.HandError(f"no receipt {seq} was issued from this box")
    record = json.loads(row["record"])
    return {"seq": seq, "kind": row["kind"], "salt": row["salt"], "record": record,
            "commit": commitment(row["salt"], record)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--preview", metavar="DIR", help="write a fresh chain to DIR and touch nothing else")
    sub = parser.add_subparsers(dest="command")
    show = sub.add_parser("reveal", help="print the salt and record behind one receipt")
    show.add_argument("seq", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "reveal":
            hand.emit({"ok": True, **reveal(args.seq)})
            return 0
        conn = hand.db()
        try:
            actions, withheld = gather(conn)
            stats = public_stats(conn)
            accounts = public_accounts(conn)
        finally:
            conn.close()
        wallet = public_wallet(_core, hand.secrets())
        numbers = {"stats": stats["date"] if stats else None, "wallet": wallet is not None, "accounts": sorted(accounts)}
        if args.preview or hand.dry_run():
            root = Path(args.preview) if args.preview else hand.state_dir() / "receipts-preview"
            hand.emit({"ok": True, "dry_run": True, "preview": str(root), "withheld": withheld, **numbers, "pushed": 0,
                       **preview(root, actions, stats, wallet, accounts)})
            return 0
        hand.emit({"ok": True, "dry_run": False, "withheld": withheld, **numbers, **publish(actions, stats, wallet, accounts)})
        return 0
    except hand.Refused as refused:
        hand.emit({"ok": False, "refused": refused.reason, **refused.detail})
        return 2
    except hand.HandError as error:
        hand.emit({"ok": False, "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
