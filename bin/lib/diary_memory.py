"""Teeny's memory page in public: each dream's snapshot of MEMORY.md
(bin/lib/dream.py, state/dream/snapshots/) published to the diary repository
beside the receipts, so the repository's history shows the page evolve.

bin/lib/receipts.py calls `add(root, live, check_secrets, repo, commit)` on
the checkout it is about to push (or on the preview folder). For each
snapshot not yet published, oldest first, it writes:

    memory/MEMORY.md     the page, overwritten: `git log -p memory/MEMORY.md`
                         is how it changed, one commit per snapshot
    memory/chain.jsonl   one line per snapshot, canonical JSON:
                         {"seq", "snapshot", "slot", "sha256", "prev"}:
                         sha256 of the page as published, prev the hash of
                         the line before
    memory/head.json     {"seq", "hash"} of the last line

and, live, commits them with a message that counts the dream's changes,
`memory: night 2026-11-14 (+3 ~5 -2, forgot 4)`. The chain holds every
snapshot's hash; the newest must match memory/MEMORY.md, and each older one
the page as its own commit left it.

The page is the model's own free text, about real people, so it takes the
road every public word takes, and one step more first:

- **redacted by code**: Telegram chat ids and other long bare numbers,
  email addresses, wallet addresses other than $TINY's own, and the query
  of any URL become `[redacted]`;
- **the secret gate** (the receipts' `check_secrets`: every value in
  secrets/*.env, the usual key shapes); a snapshot that trips it is
  withheld, never published and never retried;
- **the guard** (`check_fields`, surface github, the field `body`, the model
  judge included), and what goes out is the guard's text. A hold is the
  guard's (it is in the hold queue); the snapshot is withheld and never
  retried. Withheld snapshots are kept in state/dream/withheld.json with
  the reason category only, never the text.

What is published is read back from the chain in the repository, so a push
that fails (the checkout is reset to the remote) publishes the snapshot next
time. Under DRY_RUN (the preview) the pages are redacted but not screened,
marked `would_screen`, and the guard is not asked. Off unless
MEMORY_PUBLISH=1 in secrets/teeny.env: it publishes in public.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from teeny_hands import hand

Json = dict[str, Any]

FLAG = "MEMORY_PUBLISH"
MEMORY_DIR = "memory"
PAGE = f"{MEMORY_DIR}/MEMORY.md"
CHAIN = f"{MEMORY_DIR}/chain.jsonl"
HEAD = f"{MEMORY_DIR}/head.json"
GENESIS = "0" * 64
#: The most snapshots one run publishes (twice a day; a first run catches up).
MAX_PER_RUN = 6
#: $TINY's mint: the one address the page may name.
TINY_MINT = "2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump"
SNAPSHOT = re.compile(r"(\d{8}T\d{6}Z)-(night|midday)")
REDACTED = "[redacted]"

#: What redaction takes out, in order.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_QUERY = re.compile(r"(https?://[^\s?#)\]]+)[?#][^\s)\]]*")
_EVM = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
_BASE58 = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
#: A Telegram chat id, a phone number: 7 to 13 digits on their own (an X
#: post id is 18 or more and public; a year or a count is shorter).
_LONG_NUMBER = re.compile(r"(?<![\w-])(?<!\d\.)-?\d{7,13}(?!\w)(?!\.\d)")


def enabled() -> bool:
    return hand.flag(FLAG, False)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(text: str, encoding: str = "utf-8") -> str:
    import hashlib

    return hashlib.sha256(text.encode(encoding)).hexdigest()


def line_hash(line: str) -> str:
    return _sha(line, "ascii")


def redact(text: str) -> str:
    """The page with what is private about people taken out."""
    text = _EMAIL.sub(REDACTED, text)
    text = _URL_QUERY.sub(r"\1", text)
    text = _EVM.sub(REDACTED, text)
    text = _BASE58.sub(lambda m: m.group(0) if m.group(0) == TINY_MINT or not _looks_like_address(m.group(0))
                       else REDACTED, text)
    return _LONG_NUMBER.sub(REDACTED, text)


def _looks_like_address(word: str) -> bool:
    """A Solana address mixes digits and both cases; a long English word or
    a hex id does not."""
    return any(c.isdigit() for c in word) and any(c.isupper() for c in word) and any(c.islower() for c in word)


def read_snapshot(path: Path) -> tuple[Json, str] | None:
    """(the counts, the page) from a dream's snapshot; None when it is not one."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n") or "\n---\n" not in text:
        return None
    head, page = text[4:].split("\n---\n", 1)
    try:
        meta = json.loads(head)
    except ValueError:
        return None
    return (meta, page) if isinstance(meta, dict) else None


def snapshots() -> list[tuple[str, Path]]:
    """The dreams' snapshots, oldest first, by name (`<stamp>-<slot>`)."""
    folder = hand.state_dir() / "dream" / "snapshots"
    if not folder.is_dir():
        return []
    return [(p.stem, p) for p in sorted(folder.glob("*.md")) if SNAPSHOT.fullmatch(p.stem)]


def verify(root: Path) -> tuple[int, str, set[str]]:
    """(last seq, last hash, snapshots published). Raises hand.Refused on any
    break: a line out of order, a head that disagrees, a page that is not the
    newest line's."""
    seq, prev, done, last = 0, GENESIS, set(), None
    chain = root / CHAIN
    lines = chain.read_text(encoding="ascii").splitlines() if chain.is_file() else []
    for number, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
        except ValueError:
            raise hand.Refused("the memory chain does not verify", at=f"chain.jsonl:{number}") from None
        if (not isinstance(entry, dict) or canonical(entry) != line or entry.get("seq") != seq + 1
                or entry.get("prev") != prev or not SNAPSHOT.fullmatch(f"{entry.get('snapshot')}")):
            raise hand.Refused("the memory chain does not verify", at=f"chain.jsonl:{number}")
        seq, prev, last = entry["seq"], line_hash(line), entry
        done.add(entry["snapshot"])
    if last is not None:
        page = root / PAGE
        if not page.is_file() or _sha(page.read_text(encoding="utf-8")) != last.get("sha256"):
            raise hand.Refused("the memory chain does not verify", at=PAGE)
    head = root / HEAD
    if seq or head.is_file():
        try:
            stated = json.loads(head.read_text(encoding="ascii"))
        except (OSError, ValueError):
            stated = None
        if stated != {"seq": seq, "hash": prev}:
            raise hand.Refused("the memory chain does not verify", at="head.json")
    return seq, prev, done


def _withheld_path() -> Path:
    return hand.state_dir() / "dream" / "withheld.json"


def _withheld() -> dict[str, Json]:
    try:
        value = json.loads(_withheld_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _withhold(name: str, why: Json) -> None:
    record = _withheld()
    record[name] = {**why, "at": hand.now()}
    path = _withheld_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _screen(text: str, repo: str) -> Json:
    """The guard's say on one page: {"kind": "allow", "text"} or why not.
    Never the page's text in anything but an allow."""
    from teeny_hands.guard.gate import check_fields

    verdict = check_fields({"body": text}, surface="github", kind="memory", context={"repo": repo, "page": PAGE})
    if verdict.kind == "allow" and isinstance(verdict.fields, dict) and isinstance(verdict.fields.get("body"), str):
        return {"kind": "allow", "text": verdict.fields["body"]}
    if verdict.kind == "hold":
        return {"kind": "held", "category": verdict.category, "hold_id": verdict.hold_id}
    return {"kind": "refused"}


def message(name: str, meta: Json) -> str:
    """`memory: night 2026-11-14 (+3 ~5 -2, forgot 4)`."""
    stamp, slot = SNAPSHOT.fullmatch(name).groups()  # type: ignore[union-attr]
    day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"

    def n(key: str) -> int:
        value = meta.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    return f"memory: {slot} {day} (+{n('add')} ~{n('edit')} -{n('retire')}, forgot {n('forget')})"


def add(root: Path, live: bool, check_secrets: Callable[[str], None], repo: str = "teenydroid/diary",
        commit: Callable[[str], None] | None = None) -> Json:
    """Publishes the snapshots not yet in ROOT's chain, each committed with
    `commit(message)` when given. `live` False is the preview: redacted,
    unscreened, nothing remembered. `check_secrets` is the receipts' secret
    gate (it takes a diff: only `+` lines count)."""
    if not enabled():
        return {"skipped": f"off ({FLAG} in secrets/teeny.env)"}
    try:
        seq, prev, done = verify(root)
    except hand.Refused as broken:
        # The receipts still go out; the memory waits for a person.
        return {"refused": broken.reason, **broken.detail}
    withheld = _withheld() if live else {}
    out: Json = {"published": [], "withheld": []}
    if not live:
        out["would_screen"] = True
    for name, path in snapshots():
        if len(out["published"]) >= MAX_PER_RUN:
            break
        if name in done or name in withheld:
            continue
        got = read_snapshot(path)
        if got is None or not got[1].strip():
            continue
        meta, text = got
        text = redact(text)
        try:
            check_secrets("\n".join("+" + line for line in text.splitlines()))
        except hand.Refused:
            out["withheld"].append({"snapshot": name, "why": "secret"})
            if live:
                _withhold(name, {"why": "secret"})
            continue
        if live:
            screened = _screen(text, repo)
            if screened["kind"] != "allow":
                why = {"why": screened["kind"], **({"category": screened["category"]} if screened.get("category") else {})}
                out["withheld"].append({"snapshot": name, **why})
                _withhold(name, why)
                continue
            text = screened["text"]
        page = root / PAGE
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(text, encoding="utf-8")
        seq += 1
        slot = SNAPSHOT.fullmatch(name).group(2)  # type: ignore[union-attr]
        line = canonical({"seq": seq, "snapshot": name, "slot": slot, "sha256": _sha(text), "prev": prev})
        with (root / CHAIN).open("a", encoding="ascii") as fh:
            fh.write(line + "\n")
        prev = line_hash(line)
        (root / HEAD).write_text(canonical({"seq": seq, "hash": prev}) + "\n", encoding="ascii")
        if commit is not None:
            commit(message(name, meta))
        out["published"].append(name)
    out["head"] = seq
    return out
