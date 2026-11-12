"""The verbs of hands/gh (docs/hands.md, "gh"). Reads (R) go through the
cache and are safe to give a drafting agent; acting verbs (A) take a JSON
body on stdin and only go through `actions`, which runs the guard, the rules
and DRY_RUN itself.

  R  whoami                              the token's account, and whether it is GITHUB_USER
  R  repo OWNER/REPO                     one repository
  R  issues OWNER/REPO [--label L] [--max N]   open issues (not pull requests)
  R  pr OWNER/REPO NUMBER                one pull request
  R  contributing OWNER/REPO             whether its policy forbids AI contributions
  R  checks OWNER/REPO NUMBER            a pull request's state and its red, pending and green checks
  R  activity [--days N]                 Teeny's own PRs, merged PRs and commits
  A  create-repo   {name, description?, private?}       under GITHUB_USER only
  A  push          {workdir, repo, branch, message}     from state/workbench/, to an own repo
  A  open-pr       {repo, head, base, title, body}      disclosure footer on others' repos
  A  comment       {repo, number, body}
  A  fork          {repo}
  A  open-issue    {repo, title, body}                  on an own repo only
  A  close-issue   {repo, number, comment?}             on an own repo only
     ingest                              notifications and new stars into the mind's inbox,
                                         screened by the inbound guard (off unless GH_INBOUND=1)
  R  notifications [--max N]             what ingest read, and what became of each
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from .. import hand
from . import actions, notifications, reads
from .client import split_repo


def _whoami(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return reads.whoami()


def _repo(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return reads.repo(args.repo)


def _issues(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return reads.issues(args.repo, args.label, args.max)


def _pr(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return reads.pr(args.repo, args.number)


def _contributing(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    owner, name = split_repo(args.repo)
    return reads.contributing(owner, name)


def _activity(args: argparse.Namespace, _: Any) -> Mapping[str, Any]:
    return reads.activity(args.days)


def _bounded(low: int, high: int, what: str):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number

    return parse


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/gh", description="The only holder of the GitHub token.")
    verbs = root.add_subparsers(dest="verb")

    def verb(name: str, handler: Any, help: str, body: bool = False) -> argparse.ArgumentParser:
        sub = verbs.add_parser(name, help=help)
        sub.set_defaults(handler=handler, takes_body=body)
        return sub

    verb("whoami", _whoami, "the token's account")
    verb("repo", _repo, "one repository").add_argument("repo", metavar="OWNER/REPO")
    listing = verb("issues", _issues, "open issues")
    listing.add_argument("repo", metavar="OWNER/REPO")
    listing.add_argument("--label", default=None)
    listing.add_argument("--max", type=_bounded(1, 100, "--max"), default=20)
    one = verb("pr", _pr, "one pull request")
    one.add_argument("repo", metavar="OWNER/REPO")
    one.add_argument("number", type=_bounded(1, 10**9, "NUMBER"))
    checked = verb("checks", lambda a, _: reads.checks(a.repo, a.number), "a pull request's checks")
    checked.add_argument("repo", metavar="OWNER/REPO")
    checked.add_argument("number", type=_bounded(1, 10**9, "NUMBER"))
    verb("contributing", _contributing, "whether a repo's policy forbids AI contributions").add_argument(
        "repo", metavar="OWNER/REPO"
    )
    verb("activity", _activity, "Teeny's own recent GitHub activity").add_argument(
        "--days", type=_bounded(1, 90, "--days"), default=7
    )
    verb("ingest", lambda args, _: notifications.ingest(), "read notifications and new stars into the inbox")
    verb("notifications", lambda args, _: notifications.recent(args.max), "what ingest read").add_argument(
        "--max", type=_bounded(1, 200, "--max"), default=20
    )
    for name, handler, help in (
        ("create-repo", actions.create_repo, "a new repository under GITHUB_USER (JSON on stdin)"),
        ("push", actions.push, "commit and push a workbench folder to an own repo (JSON on stdin)"),
        ("open-pr", actions.open_pr, "open a pull request (JSON on stdin)"),
        ("comment", actions.comment, "comment on an issue or pull request (JSON on stdin)"),
        ("fork", actions.fork, "fork a repository (JSON on stdin)"),
        ("open-issue", actions.open_issue, "open an issue on an own repository (JSON on stdin)"),
        ("close-issue", actions.close_issue, "close an issue on an own repository (JSON on stdin)"),
    ):
        verb(name, lambda args, body, handler=handler: handler(body), help, body=True)
    return root


def main(argv: list[str]) -> int:
    return hand.run("gh", parser(), argv)
