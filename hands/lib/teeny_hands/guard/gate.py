"""The gate every message passes through twice: once on the way in, once on
the way out.

Inbound, a prohibited message is not answered: not declined, not
deflected, not processed. The drafter never sees it, nothing about it is
remembered, and the journal records that something was held and why,
without the text.

Outbound, the account's own draft is held to a stricter reading
(profanity counts, and every link is checked against the allowlist), and
the caller gets back either clean text to publish or a reason not to. A
clean question can still get a dirty answer, from a model with a bad day
or a web page with a plan.

Checks run cheapest first and each is sufficient to stop the turn: links
are cut, the lexicon is read, and only then is the judge asked.
A port of `crates/teeny-safety/src/gate.rs` (itself
`legacy/src/safety/gate.ts`), with `check_outbound`, the seam the X
Publisher guard called (the legacy `x_post` tool's checks).

`gate_inbound` and `gate_outbound` are the Rust's `SafetyGate::inbound` and
`outbound`; `inbound` and `check_outbound` are what hands/x calls.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .. import hand
from . import holds
from .judge import failed as judge_failed
from .judge import judge
from .links import AllowlistEntry, LinkFilterResult, configured_allowlist, filter_links, parse_allowlist
from .screen import ARTICLE, INBOUND, NARRATIVE, OUTBOUND, screen_text
from .style import MAX_POST_LENGTH, apply_house_style, count_emoji, post_length

log = logging.getLogger(__name__)

# How long approved links are cached before the log is asked again.
APPROVED_LINKS_TTL = 60.0

# `MAX_EMOJI_PER_POST`'s default and bounds, as the Rust config read it.
DEFAULT_MAX_EMOJI = 1
MAX_EMOJI_LIMIT = 10

# The hard length limits, per surface and field, in UTF-16 code units. Over
# one is refused, never cut: the model rewrites it. A surface not named here
# has X's 280 on `text`; a long-form surface's `text` is its body.
LIMITS: dict[str, dict[str, int]] = {
    "x": {"text": MAX_POST_LENGTH},
    "moltbook": {"title": 300, "body": 10000},
    # A repo's description and a commit message are GitHub's too (hands/gh).
    "github": {"title": 256, "body": 20000, "description": 350, "message": 5000},
    "mail": {"subject": 200, "body": 10000},
    "telegram": {"text": 4096},
}

# Where a title, a body or a subject is written rather than a post: the emoji
# cap there is LONG_FORM_MAX_EMOJI per field, not MAX_EMOJI_PER_POST, and a
# leading @handle is an address, not a tag X adds back.
LONG_FORM_SURFACES = ("github", "moltbook", "mail")
LONG_FORM_MAX_EMOJI = 3


@dataclass(frozen=True)
class Held:
    """Why a text was held."""

    # A lexicon category, the judge's category, `links`, or `unknown`.
    category: str
    # For the log and the journal. Never contains the text.
    reason: str
    # Which check held it: screen, judge or links.
    by: str
    # The guard_holds row, when one was written.
    hold_id: int | None = None

    @property
    def judge_failed(self) -> bool:
        """Held only because the judge could not judge (unreachable, or an
        unreadable answer), not for anything it read: asked again later, the
        same text may pass."""
        return self.by == "judge" and judge_failed(self.reason)


@dataclass
class OutboundResult:
    """An outbound check: the decision, and the text with disallowed links cut."""

    # None when clean.
    held: Held | None
    # What to publish. Only meaningful when clean.
    text: str
    # What was changed, for logging. Empty when the draft was already clean.
    fixes: list[str] = field(default_factory=list)
    # The hold written for this check, if any: the held draft, or the cut
    # links of a draft that was otherwise clean.
    hold_id: int | None = None


@dataclass(frozen=True)
class Verdict:
    """What may happen to a text."""

    # "allow": publish `text`, the house style applied and the gate passed.
    # "hold": the gate held it and recorded it for review. Do not rephrase it.
    # "refuse": it cannot go out as written (too long, too many emoji, a link
    # off the allowlist); the model may rewrite it and try again.
    kind: str
    text: str
    # Why, for hold and refuse; "" for allow.
    reason: str
    category: str | None = None
    hold_id: int | None = None
    # On a hold: the judge failed to judge it (Held.judge_failed).
    judge_failed: bool = False


# --- settings --------------------------------------------------------------------


def judge_enabled() -> bool:
    """`SAFETY_JUDGE`, default on. Off without the judge is the Rust's
    `SafetyGateOptions.judge = None`: the lexicon and the allowlist alone.
    Only a clear off turns it off; a value it cannot read leaves the judge
    on, because a typo must not switch half the filter off."""
    value = hand.setting("SAFETY_JUDGE")
    return value is None or value.strip().lower() not in ("0", "false", "no", "off")


def max_emoji_per_post() -> int:
    """`MAX_EMOJI_PER_POST`, a whole number 0..=10, default 1. The Rust refused
    to start on a bad value; a hand has no start, so a bad value is the default."""
    raw = hand.setting("MAX_EMOJI_PER_POST")
    if raw is None:
        return DEFAULT_MAX_EMOJI
    try:
        value = float(raw.strip())
    except ValueError:
        return DEFAULT_MAX_EMOJI
    if not value.is_integer() or not 0 <= value <= MAX_EMOJI_LIMIT:
        return DEFAULT_MAX_EMOJI
    return int(value)


def default_max_emoji(surface: str) -> int:
    """The emoji cap a check uses when the caller names none: a few on a
    long-form surface, `MAX_EMOJI_PER_POST` everywhere else."""
    return LONG_FORM_MAX_EMOJI if surface in LONG_FORM_SURFACES else max_emoji_per_post()


def surface_fields(surface: str) -> tuple[str, ...]:
    """The fields a surface takes, `text` always among them."""
    named = tuple(LIMITS.get(surface, {}))
    return named if "text" in named else ("text", *named)


def field_limit(surface: str, name: str) -> int:
    """The hard length limit on one field of one surface. `text` on a surface
    that has no `text` is its body; on a surface with neither, X's 280."""
    limits = LIMITS.get(surface, {})
    if name in limits:
        return limits[name]
    if name == "text":
        return limits.get("body", MAX_POST_LENGTH)
    raise hand.HandError(f"surface {surface} has no field {name!r} (it takes {', '.join(surface_fields(surface))})")


# --- the allowlist ---------------------------------------------------------------

# Operator approvals, per checkout: TEENY_ROOT -> (entries, when read).
_approved: dict[str, tuple[list[AllowlistEntry], float | None]] = {}


def allowlist() -> list[AllowlistEntry]:
    """The built-in list and `LINK_ALLOWLIST`, plus whatever an operator has
    approved since, read on a short cache so an approval taken in one process
    reaches a long-running one without a restart. A log that cannot be read
    leaves the approved set as it was, never empties it."""
    key = str(hand.root())
    entries, at = _approved.get(key, ([], None))
    if at is None or time.monotonic() - at > APPROVED_LINKS_TTL:
        try:
            entries = parse_allowlist(holds.allowed_links())
        except Exception as error:  # noqa: BLE001 - keep the last set, whatever broke
            log.warning("safety: could not read approved links; keeping the last set: %s", error)
        _approved[key] = (entries, time.monotonic())
    return [*configured_allowlist(), *entries]


def refresh_approved_links() -> None:
    """Forgets the cached approvals, so the next check reads the log."""
    for key, (entries, _) in list(_approved.items()):
        _approved[key] = (entries, None)


# --- the checks ------------------------------------------------------------------


def screen(text: str, direction: str) -> bool:
    """The lexicon alone, synchronous, for scrubbing context lines cheaply.
    True when the text is clean."""
    return screen_text(text, direction) is None


def _record(**entry: Any) -> int | None:
    """Writes a hold for review. Never fails: the decision is already made."""
    try:
        return holds.record(**entry)["id"]
    except Exception as error:  # noqa: BLE001 - a broken log must not undo a hold
        log.warning("safety: could not record a hold: %s", error)
        return None


def _ask(text: str, direction: str) -> Held | None:
    if not judge_enabled() or not text.strip():
        return None
    verdict = judge(text, direction)
    if verdict.verdict == "clean":
        return None
    category = verdict.category or "unknown"
    level = logging.INFO if direction == INBOUND else logging.WARNING
    log.log(level, "safety: held by the judge (%s, %s): %s", direction, category, verdict.reason)
    return Held(category, verdict.reason or "the judge held it", "judge")


def gate_inbound(text: str, context: dict[str, Any] | None = None) -> Held | None:
    """Whether a message from somebody else is something the account
    processes at all: None when it is. With a context (`surface` and where it
    came from), a hold is recorded for review."""
    found = screen_text(text, INBOUND)
    if found is not None:
        log.info("safety: inbound held by the screen (%s, %s)", found.category, found.term)
        held: Held | None = Held(found.category, f'contains "{found.term}"', "screen")
    else:
        held = _ask(text, INBOUND)
    if held is not None and context is not None:
        kind = context.get("kind") or ("task" if context.get("surface") == "task" else "message")
        hold_id = _record(
            direction=INBOUND,
            kind=kind,
            context=context,
            category=held.category,
            reason=held.reason,
            by=held.by,
            text=text,
        )
        held = dataclasses.replace(held, hold_id=hold_id)
    return held


def _direction(surface: str | None) -> str:
    """The screen profile for a surface: the serial's posts and the articles
    written from its lore are read under the narrative profile; every other
    draft under the strict one."""
    return {"narrative": NARRATIVE, "article": ARTICLE}.get(surface or "", OUTBOUND)


def _screen_outbound(text: str, surface: str | None, judge: bool = True) -> tuple[LinkFilterResult, Held | None]:
    """Links cut, then the lexicon and (unless `judge` is False) the judge on
    what is left: the text with its links filtered, and why it is held (None
    when it is not). Writes nothing; the caller records the hold."""
    links = filter_links(text, allowlist())
    cut = bool(links.removed)
    if cut:
        log.warning("safety: cut links from an outbound draft: %s", links.removed)
    return links, _outbound_decision(links.text, cut, _direction(surface), judge)


def gate_outbound(text: str, context: dict[str, Any] | None = None) -> OutboundResult:
    """The account's own words, checked before they leave. The text handed
    back has its links filtered; the caller publishes that. A cut link is
    a hold of its own even when the rest goes out, because it is the
    decision a reviewer most often wants to overturn."""
    fixes: list[str] = []
    links, held = _screen_outbound(text, context.get("surface") if context else None)
    cut = bool(links.removed)
    if cut:
        fixes.append(f"cut {len(links.removed)} link(s) not on the allowlist")
    hold_id = None
    if context is not None:
        removed = links.removed if cut else None
        if held is not None:
            hold_id = _record(
                direction=OUTBOUND,
                kind=context.get("kind") or "draft",
                context=context,
                category=held.category,
                reason=held.reason,
                by=held.by,
                text=text,
                links=removed,
            )
            held = dataclasses.replace(held, hold_id=hold_id)
        elif cut:
            hold_id = _record(
                direction=OUTBOUND,
                kind="link",
                context=context,
                category="links",
                reason="; ".join(r["reason"] for r in links.removed),
                by="links",
                text=text,
                links=removed,
            )
    return OutboundResult(held, links.text, fixes, hold_id)


def _outbound_decision(cleaned: str, links_cut: bool, direction: str, judge: bool = True) -> Held | None:
    found = screen_text(cleaned, direction)
    if found is not None:
        log.warning("safety: outbound held by the screen (%s, %s)", found.category, found.term)
        return Held(found.category, f'contains "{found.term}"', "screen")
    # Nothing left to say once the links are gone is not a message to send.
    if links_cut and not any(c.isascii() and c.isalnum() for c in cleaned):
        return Held("links", "the draft was only a disallowed link", "links")
    return _ask(cleaned, direction) if judge else None


# --- what hands/x calls ------------------------------------------------------------


def _context(surface: str, context: dict[str, Any] | None) -> dict[str, Any]:
    """The hold context: the caller's details, with `surface` (the argument
    wins over any in the dict) checked against the hold log's surfaces."""
    if surface not in holds.SURFACES:
        raise hand.HandError(f"surface must be one of {', '.join(holds.SURFACES)}, not {surface!r}")
    return {**(context or {}), "surface": surface}


def inbound(text: str, *, surface: str = "x", context: dict[str, Any] | None = None) -> Verdict:
    """Screens third-party text before a model sees it: "allow" or "hold",
    and a hold is recorded for review."""
    held = gate_inbound(text, _context(surface, context))
    if held is None:
        return Verdict("allow", text, "")
    return Verdict("hold", "", held.reason, held.category, held.hold_id)


def check_outbound(
    text: str,
    *,
    surface: str = "x",
    kind: str = "post",
    replying_to: str | None = None,
    max_emoji: int | None = None,
    context: dict[str, Any] | None = None,
) -> Verdict:
    """The checks a posting tool runs before anything goes out, in the legacy
    `x_post` order: the hard limits are refused so the model learns from
    them, the rest of the house style is applied silently, and the result
    goes through `gate_outbound` with the draft's context.

    `kind` is what is being published ("post", "reply", ...); a hold of it
    is a `draft` hold with the kind kept in its context, unless it names a
    hold kind itself (`prompt` for a picture). `max_emoji` None is the
    surface's default (`default_max_emoji`). The length limit is the
    surface's on `text` (`field_limit`): 280 on X and every surface that
    names none."""
    hold_context = _context(surface, context)
    if max_emoji is None:
        max_emoji = default_max_emoji(surface)
    limit = field_limit(surface, "text")
    if kind in holds.KINDS:
        hold_context["kind"] = kind
    else:
        hold_context["post_kind"] = kind
    if replying_to:
        hold_context["replying_to"] = replying_to

    refused = _hard_limits("text", text, limit, max_emoji)
    if refused is not None:
        return Verdict("refuse", "", refused)
    styled = apply_house_style(
        text,
        max_emoji=max_emoji,
        max_length=limit,
        replying_to=replying_to,
        strip_handles=surface not in LONG_FORM_SURFACES,
    )
    result = gate_outbound(styled.text, hold_context)
    if result.held is not None:
        return Verdict("hold", "", _HELD.format(category=result.held.category), result.held.category, result.held.hold_id,
                       result.held.judge_failed)
    if result.fixes:
        return Verdict("refuse", "", _OFF_LIST.format(what="the text"))
    return Verdict("allow", result.text, "")


_HELD = "this account does not publish that ({category}). Do not rephrase it; say something else or say nothing."

#: A person named in a draft: an @handle (GitHub's and X's shape).
_HANDLE = re.compile(r"(?<![\w.@/])@[A-Za-z0-9](?:[A-Za-z0-9_-]{0,38})\b")
#: The category of a draft held for naming a person after a rejection
#: (evals/invariants.md, no-reprisal; docs/roadmap.md, P4).
REJECTION = "rejection"


def names_a_person(text: str, people: list[str]) -> bool:
    """Whether `text` names anyone: an @handle, or one of `people` (logins
    the caller knows were involved) as a word of its own."""
    if _HANDLE.search(text):
        return True
    for person in people:
        name = person.strip().lstrip("@")
        if name and re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text, re.IGNORECASE):
            return True
    return False


def _rejection_people(context: Mapping[str, Any]) -> list[str] | None:
    """The people involved when the draft answers a rejection (the caller
    put `rejection` in the context), else None."""
    rejection = context.get("rejection")
    if not isinstance(rejection, Mapping):
        return None
    people = rejection.get("people")
    return [p for p in people if isinstance(p, str)] if isinstance(people, list) else []
_OFF_LIST = (
    "{what} links to a site that is not on the allowlist. This account only links to its own sites,"
    " code and community. Drop the link and call the tool again."
)


def _hard_limits(name: str, value: str, limit: int, max_emoji: int) -> str | None:
    """Why `value` is refused as written (over its length or the emoji cap),
    or None. Refused rather than fixed, so the model learns the limit."""
    what = "the text" if name == "text" else f"the {name}"
    length = post_length(value)
    if length > limit:
        return f"{what} is {length} characters, over the {limit} limit."
    emoji = count_emoji(value)
    if emoji > max_emoji:
        per = "per post" if name == "text" else f"per {name}"
        return f"{what} has {emoji} emoji and this account allows at most {max_emoji} {per}. Rewrite it and call the tool again."
    return None


@dataclass(frozen=True)
class FieldsVerdict:
    """What may happen to a draft of several fields (a title and a body).

    `kind` is as `Verdict`'s. On "allow", `fields` holds every field as it
    may be published (house style applied, the gate passed). On "hold" or
    "refuse", `field` names the field that failed and `fields` is empty."""

    kind: str
    fields: dict[str, str]
    reason: str
    category: str | None = None
    hold_id: int | None = None
    field: str | None = None
    # On a hold: the judge failed to judge it (Held.judge_failed).
    judge_failed: bool = False


def check_fields(
    fields: Mapping[str, str],
    *,
    surface: str,
    kind: str = "post",
    max_emoji: int | None = None,
    context: dict[str, Any] | None = None,
    judge: bool = True,
) -> FieldsVerdict:
    """`check_outbound` for a draft of several fields, as GitHub, Moltbook
    and mail take them: every field gets the surface's hard limits (refused,
    never fixed), the house style, then links, the lexicon and the judge.
    Any field that fails fails the draft, and a draft writes at most one
    hold: the whole draft, as JSON of its fields, with `fields` (their names)
    and `field` (the one held) in its context. This is the guard's Python API
    for hands that publish long-form (`teeny_hands.gh`, and those to come);
    `hands/guard check --fields a,b` is the same on the command line.

    `fields` maps a field name the surface takes (`surface_fields`) to its
    text, in the order they are checked. `max_emoji` None is the surface's
    default (`default_max_emoji`), applied to each field.

    `judge=False` skips the model judge; the limits, the house style, the
    links and the lexicon still run. It is for text a reviewed operator
    script wrote from fixed sentences, never a model's or a stranger's
    words, and only from Python: no verb on the command line sets it.
    bin/lib/health.py uses it, because the alarm it files is about the
    judge's own model not answering."""
    hold_context = _context(surface, context)
    if not fields:
        raise hand.HandError("check_fields needs at least one field")
    for name, value in fields.items():
        if not isinstance(value, str):
            raise hand.HandError(f"{name} must be a string")
        field_limit(surface, name)  # an unknown field is an error, not a pass
    if max_emoji is None:
        max_emoji = default_max_emoji(surface)
    if kind in holds.KINDS:
        hold_context["kind"] = kind
    else:
        hold_context["post_kind"] = kind
    hold_context["fields"] = list(fields)

    # The hard limits first, every field, before anything is asked or written.
    for name, value in fields.items():
        if not value.strip():
            return FieldsVerdict("refuse", {}, f"the {name} is empty. Write it and call the tool again.", field=name)
        refused = _hard_limits(name, value, field_limit(surface, name), max_emoji)
        if refused is not None:
            return FieldsVerdict("refuse", {}, refused, field=name)

    styled: dict[str, str] = {}
    for name, value in fields.items():
        styled[name] = apply_house_style(
            value,
            max_emoji=max_emoji,
            max_length=field_limit(surface, name),
            strip_handles=surface not in LONG_FORM_SURFACES,
        ).text

    # After a rejection (a closed pull request of Teeny's, a ban), a draft
    # that names anybody is held for a person: Teeny never answers a
    # rejection by going after someone (evals/invariants.md, no-reprisal).
    people = _rejection_people(hold_context)
    if people is not None:
        for name, value in styled.items():
            if names_a_person(value, people):
                hold_id = _record(
                    direction=OUTBOUND,
                    kind=hold_context.get("kind") or "draft",
                    context={**hold_context, "field": name},
                    category=REJECTION,
                    reason="names a person in an answer to a rejection (no-reprisal)",
                    by="screen",
                    text=_joined(styled),
                )
                return FieldsVerdict("hold", {}, _HELD.format(category=REJECTION), REJECTION, hold_id, name)

    cleaned: dict[str, str] = {}
    removed: list[dict[str, str]] = []
    cut_in: str | None = None
    for name, value in styled.items():
        links, held = _screen_outbound(value, surface, judge)
        if held is not None:
            hold_id = _record(
                direction=OUTBOUND,
                kind=hold_context.get("kind") or "draft",
                context={**hold_context, "field": name},
                category=held.category,
                reason=held.reason,
                by=held.by,
                text=_joined(styled),
                links=[*removed, *links.removed] or None,
            )
            return FieldsVerdict("hold", {}, _HELD.format(category=held.category), held.category, hold_id, name,
                                 held.judge_failed)
        if links.removed:
            removed.extend(links.removed)
            cut_in = cut_in or name
        cleaned[name] = links.text
    if removed:
        hold_id = _record(
            direction=OUTBOUND,
            kind="link",
            context={**hold_context, "field": cut_in},
            category="links",
            reason="; ".join(r["reason"] for r in removed),
            by="links",
            text=_joined(styled),
            links=removed,
        )
        return FieldsVerdict("refuse", {}, _OFF_LIST.format(what=f"the {cut_in}"), "links", hold_id, cut_in)
    return FieldsVerdict("allow", cleaned, "")


def _joined(fields: Mapping[str, str]) -> str:
    """A draft of several fields as one hold text: JSON, so a release can
    read every field back exactly."""
    return json.dumps(dict(fields), ensure_ascii=False)
