"""The verbs of hands/mail (docs/hands.md, "mail").

  R  whoami                                  the key's scope and Teeny's inbox
  R  list [--unread] [--max N] [--label L]   recent mail, newest first
  R  get ID                                  one message
  R  thread ID                               a whole thread
  R  codes [--since-minutes N] [--from-domain D]   verification codes and allowlisted links
  J  ingest [--max N] [--no-offer]           new mail, screened, offered to the core (mail:inbox)
  J  queue [--status S]                      the intake and what became of each message
  J  mark ID --status S [--note T]           what became of a message after its draft
  J  requests [--status S]                   build requests noticed in what people wrote
  A  reply  {message_id, body}               within an existing thread, once
  A  send   {to, subject, body}              to contacts only
  A  label  {message_id, add?, remove?}
  O  contacts add EMAIL --kind vendor|winner|collaborator [--note T] | contacts list | contacts remove EMAIL
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from .. import hand
from .. import requests as build_requests
from . import actions, inbox, reads


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def _contacts(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    if args.action == "list":
        return actions.contacts_list()
    if not args.email:
        raise hand.HandError(f"contacts {args.action} needs EMAIL")
    if args.action == "remove":
        return actions.contacts_remove(args.email)
    if not args.kind:
        raise hand.HandError("contacts add needs --kind")
    return actions.contacts_add(args.email, args.kind, args.note)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/mail", description="The only holder of the AgentMail key.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("whoami", lambda a, _: reads.whoami(), "the key's scope and the inbox")
    listing = verb("list", lambda a, _: reads.listing(a.unread, a.max, a.label), "recent mail")
    listing.add_argument("--unread", action="store_true")
    listing.add_argument("--max", type=_bounded(1, 100, "--max"), default=20)
    listing.add_argument("--label", default=None)
    verb("get", lambda a, _: reads.get(a.id), "one message").add_argument("id", metavar="ID")
    verb("thread", lambda a, _: reads.thread(a.id), "a thread").add_argument("id", metavar="ID")
    found = verb("codes", lambda a, _: reads.recent_codes(a.since_minutes, a.from_domain), "verification codes and links")
    found.add_argument("--since-minutes", type=_bounded(1, 1440, "--since-minutes"), default=30)
    found.add_argument("--from-domain", default=None)

    intake = verb("ingest", lambda a, _: inbox.ingest(a.max, offer=not a.no_offer), "new mail into the queue")
    intake.add_argument("--max", type=_bounded(1, 100, "--max"), default=20)
    intake.add_argument("--no-offer", action="store_true")
    verb("queue", lambda a, _: inbox.queue(a.status), "the mail intake").add_argument("--status", default=None)
    marking = verb("mark", lambda a, _: inbox.mark(a.id, a.status, a.note), "what became of a message")
    marking.add_argument("id", metavar="ID")
    marking.add_argument("--status", required=True, choices=inbox.MARKS)
    marking.add_argument("--note", default=None)
    verb("requests", lambda a, _: build_requests.listing(a.status), "build requests").add_argument("--status", default=None)

    for name, handler, help in (
        ("reply", actions.reply, "reply within a thread (JSON on stdin)"),
        ("send", actions.send, "mail a contact (JSON on stdin)"),
        ("label", actions.label, "change a message's labels (JSON on stdin)"),
    ):
        verb(name, lambda args, body, handler=handler: handler(body), help, body=True)

    people = verb("contacts", _contacts, "who Teeny may mail first (operator)")
    people.add_argument("action", choices=("add", "list", "remove"))
    people.add_argument("email", nargs="?", metavar="EMAIL")
    people.add_argument("--kind", choices=actions.CONTACT_KINDS, default=None)
    people.add_argument("--note", default=None)
    return root


def main(argv: list[str]) -> int:
    return hand.run("mail", parser(), argv)
