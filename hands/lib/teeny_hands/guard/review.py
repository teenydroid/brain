"""A second model reads the guard's outbound holds before a person does
(docs/roadmap.md, G4; docs/governance.md).

The guard is blunt on purpose: "hell yeah" is profanity to the lexicon, and
a hold waits for the operator. That daily approval is the last human act in
Teeny's day. This reviewer takes the easy cases off the operator and leaves
the hard ones:

- **Only what the frozen config allows.** `evals/guard-review.toml` lists the
  categories a reviewer may ever release (`releasable`). On top of it, and
  whatever that file says, NEVER_RELEASE always waits for a person: people
  (a reply after a rejection), self-harm, violence, weapons, hate,
  extremism, sexual content, a disallowed link, a judge verdict with no
  category; and money, a draft that mentions it (MONEY), in any category.
- **Only an outbound draft** of Teeny's own, still pending, younger than
  `max_age_hours`, on a surface something can publish a released draft to
  (the guard's `RELEASERS`: X today). Inbound messages are the re-screen's
  (rescreen.py), never this.
- **Only a different model.** `GUARD_REVIEW_MODEL` must be set and differ
  from the model that drafted (`SPINE_MODEL`, secrets/spine.env) and from the
  guard's judge (`SAFETY_JUDGE_MODEL`, else the same). A supervisor on the
  drafter's model shares its blind spots (docs/research/self-governance.md,
  "Who watches"). No way to tell the drafter's model is no review.
- **The reviewer can only release.** Its answer is `release` or `keep`;
  anything else, an unreadable answer or a failed call, keeps the hold for a
  person. It never rejects, never approves a category, never edits the text.
  A release goes out through the same path as an operator's `holds release`
  (`holds.review_hold` with the guard's releaser), so the posting rules
  still apply.
- **Every review is logged** in `guard_reviews` (hold id, category, outcome,
  whether sampled, the reviewer's model, never the text or the reason), and
  a random `sample` (a tenth) of the releases is marked for the operator to
  read afterwards (`hands/guard reviews --sampled`).

Off unless `GUARD_REVIEW=1` in secrets/teeny.env. Under DRY_RUN the reviewer
is asked and its answer logged as `would_release`, and nothing is claimed or
published. The pinned `guard-review` job runs it.
"""

from __future__ import annotations

import json
import logging
import random
import re
import tomllib
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import hand
from . import holds
from .judge import _answer
from .screen import INBOUND

log = logging.getLogger(__name__)

#: Never released by a model, whatever evals/guard-review.toml says.
NEVER_RELEASE = ("rejection", "self-harm", "violence", "weapons", "hate", "extremism", "sexual", "links", "unknown",
                 "none")
#: A draft that talks about money waits for a person, in any category.
MONEY = re.compile(
    r"\$\s?\d|\b\d[\d,.]*\s?(?:usd|usdc|sol|eth|dollars?|bucks)\b|\b(?:usdc|wallet|pay|paid|payment|send|sent|"
    r"transfer|airdrop|tip|tips|refund|invoice|seed phrase|private key|buy|sell)\b|[1-9A-HJ-NP-Za-km-z]{32,44}",
    re.IGNORECASE)
OUTCOMES = ("released", "would_release", "kept", "failed")
#: Holds looked at per run, at most.
LIMIT = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guard_reviews (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL NOT NULL,
    hold_id   INTEGER NOT NULL,
    category  TEXT NOT NULL,
    outcome   TEXT NOT NULL,
    sampled   INTEGER NOT NULL DEFAULT 0,
    model     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS guard_reviews_hold ON guard_reviews (hold_id);
"""

RUBRIC = """You review a short public draft that a blunt word filter held. You are not the author.

The filter matched a word from the category named below. Many of its holds are false positives: friendly slang
("hell yeah", "dope"), a word inside a harmless phrase, or a quote of a tool name. Release the draft only if a
careful human moderator would publish it as it stands: it is friendly, it targets nobody, and the matched word is
harmless in context. Keep it if you have any doubt at all. Keeping is always safe; a person will read it.

Answer with one JSON object and nothing else: {"verdict": "release"} or {"verdict": "keep"}."""


def config(root: Path | None = None) -> dict[str, Any]:
    """evals/guard-review.toml: the releasable categories, the sample rate,
    the age limit. Missing or broken releases nothing."""
    path = (root or hand.root()) / "evals" / "guard-review.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {"releasable": (), "sample": 1.0, "max_age_hours": 6.0}
    releasable = tuple(c for c in raw.get("releasable", []) if isinstance(c, str) and c not in NEVER_RELEASE)
    sample = float(raw.get("sample", 0.1))
    return {"releasable": releasable, "sample": min(1.0, max(0.0, sample)),
            "max_age_hours": float(raw.get("max_age_hours", 6))}


def _spine_model() -> str | None:
    try:
        text = (hand.secrets_dir() / "spine.env").read_text(encoding="utf-8")
    except OSError:
        return None
    return hand._parse_env_file(text).get("SPINE_MODEL") or None


def reviewer_model() -> str:
    """The reviewer's model, refused when it is unset or is the drafter's or
    the judge's."""
    model = hand.setting("GUARD_REVIEW_MODEL")
    if not model:
        raise hand.Refused("GUARD_REVIEW_MODEL is not set in secrets/teeny.env", rule="review")
    drafter = _spine_model()
    if not drafter:
        raise hand.Refused("cannot tell the drafter's model (SPINE_MODEL in secrets/spine.env)", rule="review")
    judge = hand.setting("SAFETY_JUDGE_MODEL") or drafter
    if model.strip().lower() in (drafter.strip().lower(), judge.strip().lower()):
        raise hand.Refused("the reviewer's model must differ from the drafter's and the judge's", rule="review")
    return model


def eligible(hold: Mapping[str, Any], cfg: Mapping[str, Any], releasers: Mapping[str, Any], now: float) -> str | None:
    """None when a model may look at this hold; else why a person must."""
    if hold.get("status") != "pending":
        return "decided"
    if hold.get("kind") != "draft" or hold.get("direction") == INBOUND:
        return "not an outbound draft"
    category = hold.get("category")
    if category in NEVER_RELEASE or category not in cfg["releasable"]:
        return "a person's category"
    if MONEY.search(str(hold.get("text") or "")):
        return "money"
    if (hold.get("context") or {}).get("surface") not in releasers:
        return "nothing publishes it"
    at = holds._epoch(hold.get("at")) if hasattr(holds, "_epoch") else _epoch(hold.get("at"))
    if at is None or now - at > cfg["max_age_hours"] * 3600:
        return "too old"
    return None


def _epoch(text: Any) -> float | None:
    import datetime as dt

    try:
        return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def ask(text: str, category: str, model: str) -> str:
    """"release" or "keep": anything but a clear release keeps."""
    message = f"{RUBRIC}\n\nCategory the filter matched: {category}\n\nThe draft:\n<<<\n{text}\n>>>"
    try:
        reply = hand.core("inference_agent_chat_simple", {"message": message, "model_override": model}, timeout=60)
    except Exception:  # noqa: BLE001 - a failed review is a hold kept for a person
        return "failed"
    answer = _answer(reply)
    if isinstance(answer, dict) and answer.get("verdict") == "release" and set(answer) <= {"verdict", "reason"}:
        return "release"
    return "keep"


def _log(hold_id: int, category: str, outcome: str, sampled: bool, model: str) -> None:
    conn = hand.open_db(_SCHEMA)
    try:
        with hand.transaction(conn):
            conn.execute("INSERT INTO guard_reviews (at, hold_id, category, outcome, sampled, model) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (hand.now(), hold_id, category, outcome, int(sampled), model))
    finally:
        conn.close()


def reviewed_ids() -> set[int]:
    conn = hand.open_db(_SCHEMA)
    try:
        return {int(r[0]) for r in conn.execute("SELECT DISTINCT hold_id FROM guard_reviews")}
    finally:
        conn.close()


def run(limit: int = LIMIT, *, rng: random.Random | None = None, releasers: Mapping[str, Callable] | None = None,
        asker: Callable[[str, str, str], str] = ask) -> dict[str, Any]:
    """One pass: each eligible pending hold, asked once."""
    out: dict[str, Any] = {"enabled": hand.flag("GUARD_REVIEW", False), "checked": 0,
                           **{o: 0 for o in OUTCOMES}, "sampled": 0}
    if not out["enabled"]:
        out["skipped"] = "off (GUARD_REVIEW in secrets/teeny.env)"
        return out
    model = reviewer_model()
    cfg = config()
    if releasers is None:
        from .cli import RELEASERS as releasers  # noqa: N811
    rng = rng or random.Random()
    done = reviewed_ids()
    now = hand.now()
    for hold in holds.list_holds("pending", holds.LIST_MAX):
        if out["checked"] >= limit:
            break
        if hold["id"] in done or eligible(hold, cfg, releasers, now) is not None:
            continue
        out["checked"] += 1
        verdict = asker(hold["text"], hold["category"], model)
        if verdict != "release":
            outcome = "failed" if verdict == "failed" else "kept"
            _log(hold["id"], hold["category"], outcome, False, model)
            out[outcome] += 1
            continue
        sampled = rng.random() < cfg["sample"]
        if hand.dry_run():
            _log(hold["id"], hold["category"], "would_release", sampled, model)
            out["would_release"] += 1
        else:
            surface = hold["context"]["surface"]
            try:
                holds.review_hold(hold["id"], "approved", "released by the hold reviewer (G4)",
                                  release=releasers[surface])
            except Exception as error:  # noqa: BLE001 - the hold went back to pending; a person decides
                log.warning("guard review: release of hold %s failed: %s", hold["id"], type(error).__name__)
                _log(hold["id"], hold["category"], "failed", False, model)
                out["failed"] += 1
                continue
            _log(hold["id"], hold["category"], "released", sampled, model)
            out["released"] += 1
        out["sampled"] += int(sampled)
    return out


def listing(sampled_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    """The reviewer's log, newest first: ids, categories and outcomes only."""
    conn = hand.open_db(_SCHEMA)
    try:
        rows = conn.execute("SELECT at, hold_id, category, outcome, sampled, model FROM guard_reviews"
                            + (" WHERE sampled = 1" if sampled_only else "") + " ORDER BY id DESC LIMIT ?",
                            (max(1, limit),)).fetchall()
    finally:
        conn.close()
    return [{"at": holds.iso(r[0]), "hold_id": r[1], "category": r[2], "outcome": r[3], "sampled": bool(r[4]),
             "model": r[5]} for r in rows]


__all__ = ["run", "listing", "eligible", "config", "reviewer_model", "NEVER_RELEASE", "MONEY", "json"]
