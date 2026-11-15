"""The public diary (docs/roadmap.md, A14): each finished day's diary page,
published beside the receipts in the diary repository, through the guard,
hash-chained like the receipts.

bin/lib/receipts.py calls `add(root, live)` on the checkout it is about to
push (or on the preview folder). It writes:

    days/YYYY-MM-DD.md   the page, as the guard handed it back
    days/chain.jsonl     one line per page, canonical JSON:
                         {"date", "prev", "seq", "sha256"}: sha256 of the
                         page's bytes, prev the hash of the line before
    days/head.json       {"seq", "hash"} of the last line

so a page edited or deleted later breaks the chain, which anyone can check
with the receipts' own `canonical` and `line_hash` rules.

The receipts themselves never carry free text. A diary page is nothing but
free text, the model's own, so it takes the road every public word takes:

- only a finished day (a date before today, UTC) from state/diary/, written
  by bin/reflect; at most MAX_PER_RUN pages a run, oldest first;
- the secret gate first (the receipts' `check_secrets`: every value in
  secrets/*.env, the usual key shapes); a page that trips it is withheld,
  never published and never retried;
- then the guard (`check_fields`, surface github, the field `body`, the
  model judge included), and what goes out is the guard's text. A hold is
  the guard's (it is in the hold queue); the page is withheld and never
  retried. Withheld dates are kept in state/diary-days.json with the reason
  category only, never the text.

Under DRY_RUN (the preview) the pages are written to the preview folder
unscreened, marked `would_screen`, and the guard is not asked, so a
rehearsal leaves no hold behind. Off unless DIARY_PUBLISH=1 in
secrets/teeny.env: it publishes in public.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from teeny_hands import hand

Json = dict[str, Any]

FLAG = "DIARY_PUBLISH"
DAYS_DIR = "days"
CHAIN = f"{DAYS_DIR}/chain.jsonl"
HEAD = f"{DAYS_DIR}/head.json"
GENESIS = "0" * 64
#: The most pages one run publishes (a first run catches up a week at a time).
MAX_PER_RUN = 7
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def enabled() -> bool:
    return hand.flag(FLAG, False)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def page_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _today() -> str:
    return dt.datetime.fromtimestamp(hand.now(), dt.timezone.utc).strftime("%Y-%m-%d")


def source_pages() -> list[tuple[str, Path]]:
    """The finished days bin/reflect wrote, oldest first."""
    folder = hand.state_dir() / "diary"
    today = _today()
    found = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        if DATE.fullmatch(path.stem) and path.stem < today:
            found.append((path.stem, path))
    return found


def verify(root: Path) -> tuple[int, str, set[str]]:
    """(last seq, last hash, dates published). Raises hand.Refused on any
    break: a line out of order, a page whose bytes moved, a head that
    disagrees."""
    seq, prev, dates = 0, GENESIS, set()
    chain = root / CHAIN
    lines = chain.read_text(encoding="ascii").splitlines() if chain.is_file() else []
    for number, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
        except ValueError:
            raise hand.Refused("the days chain does not verify", at=f"chain.jsonl:{number}") from None
        if (not isinstance(entry, dict) or canonical(entry) != line or entry.get("seq") != seq + 1
                or entry.get("prev") != prev or not DATE.fullmatch(str(entry.get("date")))):
            raise hand.Refused("the days chain does not verify", at=f"chain.jsonl:{number}")
        page = root / DAYS_DIR / f"{entry['date']}.md"
        if not page.is_file() or page_hash(page.read_text(encoding="utf-8")) != entry.get("sha256"):
            raise hand.Refused("the days chain does not verify", at=f"{entry['date']}.md")
        seq, prev = entry["seq"], line_hash(line)
        dates.add(entry["date"])
    head = root / HEAD
    if seq or head.is_file():
        try:
            stated = json.loads(head.read_text(encoding="ascii"))
        except (OSError, ValueError):
            stated = None
        if stated != {"seq": seq, "hash": prev}:
            raise hand.Refused("the days chain does not verify", at="head.json")
    return seq, prev, dates


def _withheld_path() -> Path:
    return hand.state_dir() / "diary-days.json"


def _withheld() -> dict[str, Json]:
    try:
        value = json.loads(_withheld_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _withhold(date: str, why: Json) -> None:
    record = _withheld()
    record[date] = {**why, "at": hand.now()}
    path = _withheld_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _screen(text: str, date: str, repo: str) -> Json:
    """The guard's say on one page: {"kind": "allow", "text"} or why not.
    Never the page's text in anything but an allow."""
    from teeny_hands.guard.gate import check_fields

    verdict = check_fields({"body": text}, surface="github", kind="diary",
                           context={"repo": repo, "page": f"{DAYS_DIR}/{date}.md"})
    if verdict.kind == "allow" and isinstance(verdict.fields, dict) and isinstance(verdict.fields.get("body"), str):
        return {"kind": "allow", "text": verdict.fields["body"]}
    if verdict.kind == "hold":
        return {"kind": "held", "category": verdict.category, "hold_id": verdict.hold_id}
    return {"kind": "refused"}


def add(root: Path, live: bool, check_secrets: Callable[[str], None], repo: str = "teenydroid/diary") -> Json:
    """Appends the finished days not yet in ROOT's chain. `live` False is
    the preview: unscreened, nothing remembered. `check_secrets` is the
    receipts' secret gate (it takes a diff: only `+` lines count)."""
    if not enabled():
        return {"skipped": f"off ({FLAG} in secrets/teeny.env)"}
    try:
        seq, prev, published = verify(root)
    except hand.Refused as broken:
        # The receipts still go out; the days wait for a person.
        return {"refused": broken.reason, **broken.detail}
    withheld = _withheld() if live else {}
    out: Json = {"published": [], "withheld": []}
    if not live:
        out["would_screen"] = True
    lines = []
    for date, path in source_pages():
        if len(out["published"]) >= MAX_PER_RUN:
            break
        if date in published or date in withheld:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        try:
            check_secrets("\n".join("+" + line for line in text.splitlines()))
        except hand.Refused:
            out["withheld"].append({"date": date, "why": "secret"})
            if live:
                _withhold(date, {"why": "secret"})
            continue
        if live:
            screened = _screen(text, date, repo)
            if screened["kind"] != "allow":
                why = {"why": screened["kind"], **({"category": screened["category"]} if screened.get("category") else {})}
                out["withheld"].append({"date": date, **why})
                _withhold(date, why)
                continue
            text = screened["text"]
        page = root / DAYS_DIR / f"{date}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(text, encoding="utf-8")
        seq += 1
        line = canonical({"seq": seq, "date": date, "sha256": page_hash(text), "prev": prev})
        lines.append(line)
        prev = line_hash(line)
        out["published"].append(date)
    if lines:
        with (root / CHAIN).open("a", encoding="ascii") as fh:
            fh.write("".join(f"{line}\n" for line in lines))
        (root / HEAD).write_text(canonical({"seq": seq, "hash": prev}) + "\n", encoding="ascii")
    out["head"] = seq
    return out
