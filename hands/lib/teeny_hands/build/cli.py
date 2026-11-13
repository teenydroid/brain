"""The verbs of hands/build (docs/hands.md, "build"). `desk` and `ship`
run from cron (bin/build-ship); the builder agent only reads and writes the
desk files.

  A  next                     claim a job: the builder's open one, the oldest picked
                              build request, or today's daily job; creates its folder
  A  done                     stdin {job_id, status: ok|failed, summary, tests, repo?}
  J  desk                     settle the builder's report (state/workbench/desk/build-done.json)
                              with `done`, then write its next job to state/workbench/desk/build.json
  R  jobs [--status S] [--limit N]   the jobs, newest first
  J  ship                     push every ok job to GITHUB_USER/<repo> through hands/gh
                              (create the repository when missing); a self job's branch
                              to GITHUB_USER/brain as a pull request; DRY_RUN applies
  A  self                     stdin {text}: a change Teeny proposes to itself, a pending
                              build request (source self) picked like any other
  A  upstream                 stdin {gap, text}: one gap from docs/oh-gaps.md to close in OpenHuman,
                              a build request (source upstream) built in a fork and opened as
                              one disclosed pull request; off unless BUILD_UPSTREAM=1
  R  gaps                     the gaps of docs/oh-gaps.md, each with whether it can be proposed
  A  idea                     stdin {title, who, source, pitch}: the one build idea Teeny keeps a UTC
                              day; the next daily job builds it. A second one the same day is refused
  R  ideas [--limit N]        today's idea (or none), and the ideas kept, newest first
  J  self-sync                fetch Teeny's own clone (state/workbench/brain) and fast-forward
                              it to origin/main when nothing unshipped is on it; recorded in
                              state/self/sync.json
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import ideas, jobs, selfclone, upstream


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
    verb("desk", lambda a, _: jobs.desk(), "settle the builder's report and lay out its next job")
    verb("ship", lambda a, _: jobs.ship(), "push finished jobs to GitHub")
    verb("babysit", lambda a, _: jobs.babysit(), "put Teeny's own red pull requests back on the builder's desk")
    verb("self", lambda a, body: jobs.propose_self(body), "propose a change to Teeny itself (JSON on stdin)",
         body=True)
    verb("upstream", lambda a, body: upstream.propose(body),
         "propose closing one OpenHuman gap upstream (JSON on stdin)", body=True)
    verb("gaps", lambda a, _: upstream.listing(), "the OpenHuman gaps, and which can be proposed")
    verb("idea", lambda a, body: ideas.keep(body), "keep today's build idea (JSON on stdin)", body=True)
    kept = verb("ideas", lambda a, _: ideas.listing(a.limit), "today's idea, and the ideas kept")
    kept.add_argument("--limit", type=_limit, default=30)
    verb("self-sync", lambda a, _: selfclone.sync(), "sync Teeny's own clone with origin/main")
    return root


def main(argv: list[str]) -> int:
    return hand.run("build", parser(), argv)
