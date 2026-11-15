"""The verbs of hands/tasks (docs/hands.md, "tasks"). `desk` runs from cron
(bin/tasks-desk); the operator agent only reads and writes the desk files.

  A  add                       stdin {goal, start_url, done_when, kind?}; the start page is
                               any public https page off deploy/browser-deny.txt; from the
                               mind or a playbook, off the reviewed list, a reputed link
  A  next                      claim a task: the operator's open one, else the oldest pending
  A  done                      stdin {task_id, outcome: ok|failed|needs_human, notes,
                               screenshots?: [paths], account?: {handle}}; images are copied under
                               state/media/tasks/<id>/ for hands/x upload; a signup that ended
                               ok names its account, which lands in `accounts`
  J  desk                      settle the operator's report (state/workbench/desk/task-done.json)
                               with `done`, then write its next task to state/workbench/desk/task.json
  R  list [--status S] [--limit N]   the tasks, newest first
  R  accounts                  the accounts Teeny signed up for: site, handle, why, when
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
    verb("desk", lambda a, _: queue.desk(), "settle the operator's report and lay out its next task")
    verb("accounts", lambda a, _: queue.accounts(), "the accounts signup tasks recorded")
    listing = verb("list", lambda a, _: queue.listing(a.status, a.limit), "the tasks, newest first")
    listing.add_argument("--status", choices=queue.STATUSES, default=None)
    listing.add_argument("--limit", type=_limit, default=50)
    return root


def main(argv: list[str]) -> int:
    return hand.run("tasks", parser(), argv)
