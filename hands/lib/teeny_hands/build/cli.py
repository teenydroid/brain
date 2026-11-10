"""The verbs of hands/build (docs/hands.md, "build"). The builder agent
reaches `next` and `done` through hands/mcp; `ship` runs from cron
(bin/build-ship).

  B  next                     claim a job: the builder's open one, the oldest picked
                              build request, or today's daily job; creates its folder
  B  done                     stdin {job_id, status: ok|failed, summary, tests, repo?}
  R  jobs [--status S] [--limit N]   the jobs, newest first
  J  ship                     push every ok job to GITHUB_USER/<repo> through hands/gh
                              (create the repository when missing); DRY_RUN applies
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import jobs


def _limit(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 500:
        raise argparse.ArgumentTypeError("--limit is 1..500")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/build", description="The builder's work queue.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("next", lambda a, _: jobs.next_job(), "claim the next job for the builder")
    verb("done", lambda a, body: jobs.done(body), "finish the claimed job (JSON on stdin)", body=True)
    listing = verb("jobs", lambda a, _: jobs.listing(a.status, a.limit), "the jobs, newest first")
    listing.add_argument("--status", choices=jobs.STATUSES, default=None)
    listing.add_argument("--limit", type=_limit, default=50)
    verb("ship", lambda a, _: jobs.ship(), "push finished jobs to GitHub")
    return root


def main(argv: list[str]) -> int:
    return hand.run("build", parser(), argv)
