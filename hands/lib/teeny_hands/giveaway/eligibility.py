"""Who may win a round, decided by code and published with a reason per
entrant (docs/drops-schema.md, `entrants`).

Local checks, on what the webhook already stored (no X call):

  - the guard did not hold the reply (`x_mentions.status` held, or a hold id),
    and it was screened at all;
  - the account is not Teeny's own;
  - the account is not classed as an agent (hands/world's `classify`);
  - the account is at least MIN_AGE_DAYS old and has at least MIN_FOLLOWERS,
    when the webhook said so (unknown is settled later, below);
  - the reply is not a near-duplicate of an earlier reply by another account
    (hands/guard's word-set Jaccard, NEAR_DUPLICATE, on what is left once
    the tag, addresses, @handles, links and the entry template's words are
    taken out, and only when both have at least DUPLICATE_MIN_WORDS words: a
    bare "drop #3 <address>" entry, as the /rewards page pre-fills it, is
    not a copy of another);
  - it bound an address.

The checks a drawn candidate still needs (the follow, and the age and
followers the webhook did not give) come from `x_follows` first, then one
batched `hands/x users` call, within GIVEAWAY_X_LOOKUPS_PER_ROUND (`late`).
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..guard import repeats
from ..wallet import caps as wallet_caps, solana
from . import caps

MIN_AGE_DAYS = 90
MIN_FOLLOWERS = 25
NEAR_DUPLICATE = repeats.NEAR_DUPLICATE
DUPLICATE_MIN_WORDS = 4
#: The words of an entry's template, never evidence of copying.
TEMPLATE_WORDS = frozenset({"drop", "enter", "entering", "entry", "wallet", "address", "sol", "solana", "tiny",
                            "$tiny", "teenydroid", "@teenydroid", "mine", "here", "the", "and", "for", "you"})

OK = "ok"
HELD = "held by the guard"
UNSCREENED = "not screened by the guard yet"
OWN = "teeny's own account"
AGENT = "classed as an agent"
YOUNG = f"account under {MIN_AGE_DAYS} days"
FEW_FOLLOWERS = f"under {MIN_FOLLOWERS} followers"
DUPLICATE = "near-duplicate of an earlier reply"
NO_ADDRESS = "no address"
OWN_ADDRESS = "address is teeny's own"
NOT_FOLLOWING = "does not follow @teenydroid"
UNVERIFIABLE = "unverifiable: the round's x lookups are spent"
NOT_FOUND = "account not found on x"
NO_AGE = "account age unknown"

_ADDRESS = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
_HANDLE = re.compile(r"@\w+")
_LINK = re.compile(r"https?://\S+")
_TAG = re.compile(r"\bdrop\s*#\d+", re.IGNORECASE)


def never_bound() -> set[str]:
    """Addresses no entry binds: the mints and programs a reply might quote,
    and the giveaway wallet itself."""
    out = {caps.TINY_MINT, caps.REWARDS_PROGRAM, caps.TOKEN_2022, caps.TOKEN_PROGRAM,
           *wallet_caps.USDC_MINTS.values()}
    from .. import hand

    authority = hand.setting("GIVEAWAY_AUTHORITY")
    if authority:
        out.add(authority)
    return out


def addresses(text: str, exclude: Iterable[str] = ()) -> set[str]:
    """The valid Solana addresses in `text`, less `exclude`."""
    skip = set(exclude)
    return {m for m in _ADDRESS.findall(text or "") if solana.is_address(m) and m not in skip}


def bind(texts: Iterable[tuple[str, str]], exclude: Iterable[str]) -> tuple[str, str] | None:
    """(address, post id) from the first `(post id, text)` that carries
    exactly one address; None when none does. A reply's words beyond its own
    address change nothing: "pick me", "send to X" only bind X when X is the
    one address in it."""
    skip = set(exclude)
    for post_id, text in texts:
        found = addresses(text, skip)
        if len(found) == 1:
            return found.pop(), post_id
    return None


def content_words(text: str) -> set[str]:
    stripped = _LINK.sub(" ", _TAG.sub(" ", _ADDRESS.sub(" ", text or "")))
    stripped = _HANDLE.sub(" ", stripped)
    return {w for w in repeats.words(stripped) if w not in TEMPLATE_WORDS}


def duplicates(entries: Iterable[Mapping[str, Any]]) -> set[int]:
    """Entry ids whose reply is a near-duplicate of an earlier entry's (by
    created_at, then id). The first of a cluster keeps its place."""
    seen: list[set[str]] = []
    out: set[int] = set()
    for entry in sorted(entries, key=lambda e: (e["created_at"], e["id"])):
        words = content_words(entry["text"])
        if len(words) < DUPLICATE_MIN_WORDS:
            continue
        if any(len(words & old) / len(words | old) >= NEAR_DUPLICATE for old in seen):
            out.add(entry["id"])
        seen.append(words)
    return out


def epoch(stamp: Any) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        moment = _dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.timestamp()


@dataclass
class Facts:
    """What is known of one account: None is unknown."""

    created_at: float | None = None
    followers: int | None = None
    follows: bool | None = None
    found: bool = True

    def merge(self, created_at: Any = None, followers: Any = None, follows: Any = None) -> None:
        if self.created_at is None and epoch(created_at) is not None:
            self.created_at = epoch(created_at)
        if self.followers is None and isinstance(followers, int) and not isinstance(followers, bool):
            self.followers = followers
        if self.follows is None and isinstance(follows, bool):
            self.follows = follows

    def needs_lookup(self) -> bool:
        return self.found and (self.created_at is None or self.followers is None or self.follows is None)


def account_reason(facts: Facts, at: float, final: bool) -> str | None:
    """Age and followers against the rules, as of `at` (the deadline). With
    `final` an unknown is a failure; without, it waits for the late check."""
    if not facts.found:
        return NOT_FOUND
    if facts.created_at is not None and at - facts.created_at < MIN_AGE_DAYS * 86400:
        return YOUNG
    if facts.followers is not None and facts.followers < MIN_FOLLOWERS:
        return FEW_FOLLOWERS
    if final:
        if facts.created_at is None:
            return NO_AGE
        if facts.followers is None:
            return FEW_FOLLOWERS
        if facts.follows is not True:
            return NOT_FOLLOWING
    elif facts.follows is False:
        return NOT_FOLLOWING
    return None


def local_reason(entry: Mapping[str, Any], *, own_handle: str, agent: bool, duplicate: bool,
                 facts: Facts, deadline: float) -> str | None:
    """The first local rule `entry` fails, or None."""
    if entry["handle"].lower() == own_handle:
        return OWN
    if entry["held"]:
        return HELD
    if entry["unscreened"]:
        return UNSCREENED
    if agent:
        return AGENT
    early = account_reason(facts, deadline, final=False)
    if early:
        return early
    if duplicate:
        return DUPLICATE
    if not entry["address"]:
        return NO_ADDRESS
    return None
