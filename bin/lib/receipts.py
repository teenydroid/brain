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
  post id. Each field is an enum, an integer or matches a strict pattern; a
  row with anything else is withheld, never published, and counted in the
  run's output.
- `commit` is sha256(salt || the hand's whole ledger row), with a 32-byte
  random salt kept in hands.db (`receipts_issued`). It reveals nothing, but
  binds the receipt to the full private record: `reveal SEQ` prints the salt
  and the record so an operator can choose to prove one receipt in full.
  Actions with nothing public to point at (mail sent) are a commitment alone.
- `prev` chains every line to the one before it, and `head.json` names the
  last line's seq and hash, so a rewritten or deleted receipt breaks the
  chain. `verify.py` in the repository checks it with no dependencies.

Nothing here is free text. No post text, titles, summaries, diary prose,
prompts, logs, mentions or anybody else's words are ever written, so nothing
the model read (a secret, or an injected instruction asking to be repeated)
has a path into the repository. The publisher is plain code with no model in
it, and it reads only the ledgers hands write after an action succeeded.

Live, before appending, the published chain is verified from the first line;
a chain that does not verify (someone else pushed, or history was edited)
refuses the run rather than build on it. Only `receipts/` and `head.json` are
ever staged, the staged diff still goes through the secret gate (every value
in secrets/*.env of 12 characters or more, and the usual key shapes), and a
push that fails resets the checkout to the remote so the next run rebuilds.

Under DRY_RUN (the default) a fresh chain is written to state/receipts-preview/
and nothing touches git or hands.db. Live, the repository is cloned to
state/receipts-repo/ and pushed with RECEIPTS_GITHUB_TOKEN (or
DIARY_GITHUB_TOKEN, or GITHUB_TOKEN) through askpass (teeny_hands.git).

Prints one JSON object: {"ok", "dry_run", "issued", "withheld", "pushed", ...}.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
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

DEFAULT_REPO = "teenydroid/diary"
GENESIS = "0" * 64
HEAD_FILE = "head.json"
RECEIPTS_DIR = "receipts"
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
CLUSTERS = ("mainnet-beta", "devnet", "testnet")
ASSETS = ("SOL", "USDC")
PURPOSES = ("bounty", "vendor")
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
        "wallet.paid": "signature", "bounty.opened": "id", "bounty.picked": "id",
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


def publish(actions: list[Action]) -> Json:
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
        if not fresh:
            return {"issued": 0, "head": seq, "pushed": 0}
        today = _iso(hand.now())[:10]
        issued = append(repo, fresh, seq, prev, today)
        _git(repo, "add", "--", RECEIPTS_DIR, HEAD_FILE)
        try:
            check_secrets(_git(repo, "diff", "--cached", "--unified=0"))
        except hand.Refused as why:
            _git(repo, "reset", "-q", "--hard", f"origin/{branch}")
            raise hand.Refused("the receipts carry a secret, nothing committed", why=why.reason) from None
        _git(repo, "commit", "-q", "-m", f"receipts: {issued[0]['seq']}-{issued[-1]['seq']}")
        try:
            _git(repo, "push", "-q", "origin", f"HEAD:{branch}", token=token)
        except hand.HandError:
            _git(repo, "reset", "-q", "--hard", f"origin/{branch}")
            raise
        conn = _issued_db()
        try:
            with hand.transaction(conn):
                conn.executemany(
                    "INSERT OR REPLACE INTO receipts_issued (key, seq, kind, salt, record, at)"
                    " VALUES (:key, :seq, :kind, :salt, :record, :at)", issued)
        finally:
            conn.close()
    return {"issued": len(issued), "head": issued[-1]["seq"], "pushed": 1}


def preview(root: Path, actions: list[Action]) -> Json:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    issued = append(root, [a for a in actions if a.key], 0, GENESIS, _iso(hand.now())[:10])
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
        finally:
            conn.close()
        if args.preview or hand.dry_run():
            root = Path(args.preview) if args.preview else hand.state_dir() / "receipts-preview"
            hand.emit({"ok": True, "dry_run": True, "preview": str(root), "withheld": withheld, "pushed": 0,
                       **preview(root, actions)})
            return 0
        hand.emit({"ok": True, "dry_run": False, "withheld": withheld, **publish(actions)})
        return 0
    except hand.Refused as refused:
        hand.emit({"ok": False, "refused": refused.reason, **refused.detail})
        return 2
    except hand.HandError as error:
        hand.emit({"ok": False, "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
