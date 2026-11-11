"""The verbs of hands/telegram (docs/hands.md, "telegram").

  R  whoami                          the bot's account
  J  ingest [--max N]                new messages, screened, handed to the mind
  R  chats                           the chats Teeny is in or was written to from
  J  queue [--status S]              the intake and what became of each message
  A  send {chat_id, text, reply_to?} into a chat Teeny has seen, never cold
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import actions, client, inbox


def _bounded(low: int, high: int):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{low}..{high}")
        return number

    return parse


def _whoami(_: argparse.Namespace, __: Any) -> dict:
    me = client.bot().call("getMe")
    me = me if isinstance(me, dict) else {}
    return {"id": me.get("id"), "username": me.get("username"), "name": me.get("first_name"),
            "reads_all_group_messages": bool(me.get("can_read_all_group_messages")),
            "owners": sorted(client.owners())}


def main(argv: list[str]) -> int:
    root = argparse.ArgumentParser(prog="hands/telegram", description="The only holder of the Telegram bot token.")
    verbs = root.add_subparsers(dest="verb")
    verbs.add_parser("whoami", help="the bot's account").set_defaults(handler=_whoami)
    ingest = verbs.add_parser("ingest", help="new messages, screened, handed to the mind")
    ingest.add_argument("--max", type=_bounded(1, 100), default=100)
    ingest.set_defaults(handler=lambda a, _: inbox.ingest(a.max))
    verbs.add_parser("chats", help="the chats Teeny knows").set_defaults(handler=lambda a, _: inbox.chats())
    queue = verbs.add_parser("queue", help="the intake")
    queue.add_argument("--status", choices=inbox.STATUSES)
    queue.set_defaults(handler=lambda a, _: inbox.queue(a.status))
    verbs.add_parser("send", help="say something in a chat (JSON on stdin)").set_defaults(
        handler=lambda a, body: actions.send(body), takes_body=True)
    return hand.run("telegram", root, argv)
