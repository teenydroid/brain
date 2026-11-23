"""The verbs of hands/rent (docs/hands.md, "rent").

  A  post                {title, brief, done, price_usd, city|remote, ...}: priced by a
                         dry run, held to the caps, posted once
  A  accept              {job, application}: hire one applicant
  A  review              {job, verdict: approve|redo|reject, reason}: approve pays, once
  A  message             {job, text}: to the person hired
  J  poll                applications, work and messages to the mind; auto-accept (bin/rent-poll)
  R  status              the caps, today's spend, open jobs, the RentAHuman wallet
  R  list [--status S]   the jobs, newest first
  R  get ID              one job and what poll has passed on
  R  humans [--skill --city --country --limit N]   who is on RentAHuman
  O  cancel ID [--reason R]   end an unhired bounty

The mind reaches post, accept, review and message through its rent_* intents
(bin/lib/tick.py); no playbook reaches a verb that spends (bin/lib/playbook.py).
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import actions, store

CANCEL_REASONS = ("not_enough_applicants", "applicants_not_fit", "task_no_longer_needed",
                  "budget_timing_requirements_changed", "other")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/rent", description="People Teeny rents on RentAHuman.ai.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("post", lambda a, body: actions.post(body), "post a bounty", body=True)
    verb("accept", lambda a, body: actions.accept(body), "hire an applicant", body=True)
    verb("review", lambda a, body: actions.review(body), "approve and pay, redo or reject", body=True)
    verb("message", lambda a, body: actions.message(body), "write to the person hired", body=True)
    verb("poll", lambda a, _: actions.poll(), "follow every open job (the pinned job)")
    verb("status", lambda a, _: actions.status(), "caps, spend, open jobs, wallet")
    verb("list", lambda a, _: actions.listing(a.status), "the jobs").add_argument("--status", choices=store.STATUSES)
    verb("get", lambda a, _: actions.get(a.id), "one job").add_argument("id", type=int, metavar="ID")
    humans = verb("humans", lambda a, _: actions.humans(a.skill, a.city, a.country, a.limit), "who is on RentAHuman")
    humans.add_argument("--skill")
    humans.add_argument("--city")
    humans.add_argument("--country")
    humans.add_argument("--limit", type=int, choices=range(1, 51), default=10, metavar="1..50")
    cancel = verb("cancel", lambda a, _: actions.cancel(a.id, a.reason), "end an unhired bounty (operator)")
    cancel.add_argument("id", type=int, metavar="ID")
    cancel.add_argument("--reason", choices=CANCEL_REASONS, default="no_longer_needed")
    return root


def main(argv: list[str]) -> int:
    return hand.run("rent", parser(), argv)
