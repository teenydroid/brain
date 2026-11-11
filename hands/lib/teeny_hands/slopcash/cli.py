"""The verbs of hands/slopcash (docs/hands.md, "slopcash"). Reads (R) are
public slop.cash and GitHub reads through the read cache, safe to give a
drafting agent. Operator verbs (O) sign in to slop.cash in process (the
operator opens a GitHub link printed on stderr), so they are for a person at
the box and never for an agent; a playbook can neither output nor run them.

  R  project [ID]                              status, authority, why paused and the next step, reward, revision
  R  job [ID]                                  the listing: headline, description, contribution, repository
  R  jobs [--steward LOGIN]                    every project LOGIN (GITHUB_USER, teenydroid) stewards
  R  revisions [ID]                            the project's revision history
  R  submissions [--state open|closed|all] [--max N] [--repo OWNER/REPO]
                                               pull requests on the project's repositories, with Slopbot's latest review
  R  submission NUMBER [--repo OWNER/REPO]     one pull request: files, checks, every Slopbot comment, the gate facts
  O  post-job --file PATH [--reason R] [--confirm]
                                               edit the listing (name, eyebrow, headline, description, links,
                                               contribution); a preview unless --confirm, and under DRY_RUN
  O  check [--scan] [--confirm]                run slop.cash's setup checks (activates the project when they pass)
  O  setup                                     the signed-in account's projects and setup

ID defaults to SLOPCASH_PROJECT (teeny-s-brain). Reviewing a pull request on
GitHub stays with `hands/gh comment`: this hand posts nothing to GitHub.
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import hand
from . import actions, reads


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def _submissions(args: argparse.Namespace, _: Any) -> Any:
    out = reads.submissions(args.state, args.max)
    if args.repo:
        wanted = args.repo.lower()
        out["submissions"] = [s for s in out["submissions"] if str(s.get("repo", "")).lower() == wanted]
    return out


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/slopcash", description="Teeny's project on slop.cash.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=False)
        return sub

    verb("project", lambda a, _: reads.project(a.id), "the project and why it is paused").add_argument("id", nargs="?", metavar="ID")
    verb("job", lambda a, _: reads.job(a.id), "the listing as posted").add_argument("id", nargs="?", metavar="ID")
    verb("jobs", lambda a, _: reads.jobs(a.steward), "every project a steward has").add_argument("--steward", default=None)
    verb("revisions", lambda a, _: reads.revisions(a.id), "the revision history").add_argument("id", nargs="?", metavar="ID")
    listing = verb("submissions", _submissions, "pull requests and Slopbot's reviews")
    listing.add_argument("--state", default="open", choices=reads.STATES)
    listing.add_argument("--max", type=_bounded(1, 100, "--max"), default=20)
    listing.add_argument("--repo", default=None)
    one = verb("submission", lambda a, _: reads.submission(a.number, a.repo), "one pull request in full")
    one.add_argument("number", type=_bounded(1, 10**9, "NUMBER"), metavar="NUMBER")
    one.add_argument("--repo", default=None)

    post = verb("post-job", lambda a, _: actions.post_job(a.file, a.reason, a.confirm), "edit the listing (operator)")
    post.add_argument("--file", required=True, metavar="PATH")
    post.add_argument("--reason", default=None)
    post.add_argument("--confirm", action="store_true")
    check = verb("check", lambda a, _: actions.check(a.scan, a.confirm), "run the setup checks (operator)")
    check.add_argument("--scan", action="store_true")
    check.add_argument("--confirm", action="store_true")
    verb("setup", lambda a, _: actions.setup(), "the signed-in account's projects (operator)")
    return root


def main(argv: list[str]) -> int:
    return hand.run("slopcash", parser(), argv)
