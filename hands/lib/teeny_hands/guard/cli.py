"""hands/guard's verbs (docs/hands.md, "guard").

  check     stdin {"text", "surface"?, "kind"?, "replying_to"?, "max_emoji"?, "context"?}
            -> {"ok": true, "text"}; held -> refused "held" with the category
            and hold id; refused (rewrite it) -> refused <why>, rewrite true
  check --fields title,body
            stdin {"title", "body", "surface", "kind"?, "max_emoji"?, "context"?}
            -> {"ok": true, "fields": {"title", "body"}}, each rewritten; a
            failure names its `field`, and the draft writes at most one hold
  inbound   stdin {"text", "surface"?, "context"?} -> {"ok": true}, or held
  style     stdin {"text", "replying_to"?, "max_emoji"?} -> {"text", "length", "emoji"}
  holds list [--status S] [--limit N] | approve ID [--note T] | reject ID [--note T]
  holds release ID [--note T]
  links list | approve HOST[/PATH]
  repeats [--days N] -> {"repeats", "by_surface"}: near-duplicate posts the
            posting rules refused over the last N days (default 30)

`approve` records the decision and carries out what needs no other hand
(allowlisting a cut link). `release` approves a withheld draft by publishing
it: the hold is claimed atomically (`holds.claim_release`), published through
the hand that owns its surface (today X, through `teeny_hands.x.publish`,
with the posting rules and DRY_RUN; the guard itself is not asked again,
because the operator's approval is the decision it deferred), and on failure
put back to pending with the error (`holds.unclaim_release`). Under DRY_RUN
a release claims nothing and says what it would publish.
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from .. import hand
from . import gate, holds
from .links import parse_allowlist
from .style import apply_house_style, count_emoji, post_length


def _body(body: Any, *keys: str) -> dict[str, Any]:
    """The JSON body, with a string `text`, and nothing but `keys` besides."""
    if not isinstance(body, dict):
        raise hand.HandError("stdin must be a JSON object with a text")
    if not isinstance(body.get("text"), str):
        raise hand.HandError("text must be a string")
    unknown = set(body) - {"text", *keys}
    if unknown:
        raise hand.HandError(f"unknown field(s): {', '.join(sorted(unknown))}")
    return body


def _optional(body: dict[str, Any], key: str, kind: type, what: str) -> Any:
    value = body.get(key)
    if value is not None and (not isinstance(value, kind) or isinstance(value, bool)):
        raise hand.HandError(f"{key} must be {what}")
    return value


def _max_emoji(body: dict[str, Any]) -> int | None:
    value = _optional(body, "max_emoji", int, "a whole number")
    if value is not None and value < 0:
        raise hand.HandError("max_emoji must not be negative")
    return value


def check(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    if args.fields is not None:
        return _check_fields(args.fields, body)
    body = _body(body, "surface", "kind", "replying_to", "max_emoji", "context")
    verdict = gate.check_outbound(
        body["text"],
        surface=_optional(body, "surface", str, "a string") or "x",
        kind=_optional(body, "kind", str, "a string") or "post",
        replying_to=_optional(body, "replying_to", str, "a string"),
        max_emoji=_max_emoji(body),
        context=_optional(body, "context", dict, "an object"),
    )
    if verdict.kind == "hold":
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id)
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True)
    return {"text": verdict.text}


def _field_names(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names or len(set(names)) != len(names):
        raise hand.HandError("--fields is a comma-separated list of distinct field names")
    return names


def _check_fields(spec: str, body: Any) -> Mapping[str, Any]:
    names = _field_names(spec)
    if not isinstance(body, dict):
        raise hand.HandError(f"stdin must be a JSON object with {', '.join(names)}")
    unknown = set(body) - {*names, "surface", "kind", "max_emoji", "context"}
    if unknown:
        raise hand.HandError(f"unknown field(s): {', '.join(sorted(unknown))}")
    for name in names:
        if not isinstance(body.get(name), str):
            raise hand.HandError(f"{name} must be a string")
    verdict = gate.check_fields(
        {name: body[name] for name in names},
        surface=_optional(body, "surface", str, "a string") or "x",
        kind=_optional(body, "kind", str, "a string") or "post",
        max_emoji=_max_emoji(body),
        context=_optional(body, "context", dict, "an object"),
    )
    if verdict.kind == "hold":
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id, field=verdict.field)
    if verdict.kind == "refuse":
        raise hand.Refused(verdict.reason, rewrite=True, field=verdict.field)
    return {"fields": verdict.fields}


def inbound(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    body = _body(body, "surface", "context")
    verdict = gate.inbound(
        body["text"],
        surface=_optional(body, "surface", str, "a string") or "x",
        context=_optional(body, "context", dict, "an object"),
    )
    if verdict.kind == "hold":
        raise hand.Refused("held", category=verdict.category, hold_id=verdict.hold_id)
    return {}


def style(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    body = _body(body, "replying_to", "max_emoji")
    max_emoji = _max_emoji(body)
    styled = apply_house_style(
        body["text"],
        max_emoji=gate.max_emoji_per_post() if max_emoji is None else max_emoji,
        replying_to=_optional(body, "replying_to", str, "a string"),
    )
    return {"text": styled.text, "length": post_length(styled.text), "emoji": count_emoji(styled.text)}


def holds_list(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    return {"holds": holds.list_holds(args.status, args.limit), "counts": holds.counts()}


def _review(decision: str):
    def review(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
        reviewed = holds.review_hold(args.id, decision, args.note, refresh_links=gate.refresh_approved_links)
        if reviewed is None:
            raise hand.HandError(f"no hold {args.id}")
        return {"hold": reviewed}

    return review


def _release_x(held: dict[str, Any]) -> str:
    """Publishes a withheld X draft: a reply when its context names the post
    it answered, an own post otherwise."""
    from ..x import publish  # the X keys are hands/x's; only a release needs them

    context = held["context"]
    kind = context.get("post_kind") or "post"
    if kind == "reply":
        post_id = context.get("post_id")
        if not post_id:
            raise hand.Refused("the held reply does not say which post it answered", rule="release")
        out = publish.reply(str(post_id), {"text": held["text"]}, approved=True)
        return f"published on X as {out['id']} (a reply to {post_id})"
    if kind != "post":
        raise hand.Refused(f"a held {kind} cannot be released yet; only posts and replies", rule="release")
    out = publish.post({"text": held["text"]}, approved=True)
    return f"published on X as {out['id']}"


# The hand that publishes a released draft, by its surface. A surface not
# here (github, moltbook, mail until their hands release) is refused before
# anything is claimed.
RELEASERS = {"x": _release_x}


def holds_release(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    held = holds.get(args.id)
    if held is None:
        raise hand.HandError(f"no hold {args.id}")
    if held["kind"] != "draft":
        raise hand.Refused(f"hold {args.id} is a {held['kind']}, not a withheld draft; approve or reject it", rule="release")
    surface = held["context"].get("surface")
    releaser = RELEASERS.get(surface)
    if releaser is None:
        raise hand.Refused(f"nothing releases a {surface} draft yet; approve or reject it", rule="release")
    if held["status"] != "pending":
        return {"hold": held}
    if hand.dry_run():
        return {"dry_run": True, "would_release": args.id, "surface": surface, "text": held["text"]}
    reviewed = holds.review_hold(args.id, "approved", args.note, release=releaser)
    return {"hold": reviewed}


def review_run(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    """The hold reviewer's pass (review.py; docs/roadmap.md, G4)."""
    from . import review

    return review.run(args.limit)


def reviews_list(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    from . import review

    return {"reviews": review.listing(args.sampled, args.limit)}


def links_list(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    return {"links": holds.approved_link_rows()}


def links_approve(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    entry = args.entry.strip().lower()
    parsed = parse_allowlist([entry])
    if not parsed or not parsed[0].host:
        raise hand.HandError("give a host, or host/path, to allow")
    holds.allow_link(entry, None)
    gate.refresh_approved_links()
    return {"entry": entry}


def repeats_count(args: argparse.Namespace, body: Any) -> Mapping[str, Any]:
    from . import repeats

    return repeats.count(args.days)


def _days(value: str) -> int:
    days = int(value)
    if not 1 <= days <= 365:
        raise argparse.ArgumentTypeError("--days is 1..365")
    return days


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hands/guard", description="Teeny's content filter.")
    verbs = p.add_subparsers(dest="verb")

    check_p = verbs.add_parser("check")
    check_p.add_argument("--fields", metavar="A,B", help="screen these fields of the body, not its text")
    check_p.set_defaults(handler=check, takes_body=True)
    for name, handler in (("inbound", inbound), ("style", style)):
        verbs.add_parser(name).set_defaults(handler=handler, takes_body=True)

    holds_p = verbs.add_parser("holds").add_subparsers(dest="action")
    listing = holds_p.add_parser("list")
    listing.add_argument("--status", choices=holds.STATUSES)
    listing.add_argument("--limit", type=int)
    listing.set_defaults(handler=holds_list)
    for decision, name in (("approved", "approve"), ("rejected", "reject")):
        review = holds_p.add_parser(name)
        review.add_argument("id", type=int)
        review.add_argument("--note")
        review.set_defaults(handler=_review(decision))
    release = holds_p.add_parser("release")
    release.add_argument("id", type=int)
    release.add_argument("--note")
    release.set_defaults(handler=holds_release)

    review_p = verbs.add_parser("review")
    review_p.add_argument("--limit", type=int, default=10)
    review_p.set_defaults(handler=review_run)
    reviews_p = verbs.add_parser("reviews")
    reviews_p.add_argument("--sampled", action="store_true", help="only the releases queued for the operator to read")
    reviews_p.add_argument("--limit", type=int, default=100)
    reviews_p.set_defaults(handler=reviews_list)

    links_p = verbs.add_parser("links").add_subparsers(dest="action")
    links_p.add_parser("list").set_defaults(handler=links_list)
    approve = links_p.add_parser("approve")
    approve.add_argument("entry", metavar="HOST[/PATH]")
    approve.set_defaults(handler=links_approve)

    counted = verbs.add_parser("repeats")
    counted.add_argument("--days", type=_days, default=30)
    counted.set_defaults(handler=repeats_count)
    return p


def main(argv: list[str]) -> int:
    return hand.run("guard", parser(), argv)
