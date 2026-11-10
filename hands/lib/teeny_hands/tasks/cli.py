"""The verbs of hands/tasks (docs/hands.md, "tasks"). The operator agent
reaches `next` and `done` through hands/mcp.

  A  add                       stdin {goal, start_url, done_when, kind?}; the start page
                               must be on deploy/browser-allow.txt
  B  next                      claim a task: the operator's open one, else the oldest pending
  B  done                      stdin {task_id, outcome: ok|failed|needs_human, notes,
                               screenshots?: [paths]}; images are copied under
                               state/media/tasks/<id>/ for hands/x upload
  R  list [--status S] [--limit N]   the tasks, newest first
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import queue


def _limit(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 500:
        raise argparse.ArgumentTypeError("--limit is 1..500")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/tasks", description="The browser operator's queue.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    adding = verb("add", lambda a, body: queue.add(body, source=a.source), "queue a browser task (JSON on stdin)",
                  body=True)
    adding.add_argument("--source", default=None, help="who asked: a playbook name, 'operator', ...")
    verb("next", lambda a, _: queue.next_task(), "claim the next task for the operator")
    verb("done", lambda a, body: queue.done(body), "finish the claimed task (JSON on stdin)", body=True)
    listing = verb("list", lambda a, _: queue.listing(a.status, a.limit), "the tasks, newest first")
    listing.add_argument("--status", choices=queue.STATUSES, default=None)
    listing.add_argument("--limit", type=_limit, default=50)
    return root


def main(argv: list[str]) -> int:
    return hand.run("tasks", parser(), argv)
