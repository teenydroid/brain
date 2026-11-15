"""The verbs of hands/keychain (docs/hands.md, "keychain"). Frozen to Teeny.

  A  create --site S --handle H   a new strong password for that account, sealed in
                                  state/keychain.db; the account lands in `accounts`.
                                  Answers the site and the handle, never the password
  R  list                         sites and handles, never a password
  J  secret --host H [--handle H] the password for the site H is on, on stdout, for the
                                  browser's fill only: bin/keychain-fill, which the core
                                  runs after it checked the page's host, sets FILL_FLAG.
                                  Refused without it and on a terminal (a person uses
                                  `reveal`)
  O  reveal --site S [--handle H] the password, on a terminal only, so the operator can
                                  take an account over
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from .. import hand
from . import store


def _create(args: argparse.Namespace) -> dict[str, Any]:
    from .. import websites
    from ..tasks import queue

    site = store.site(args.site)
    if websites.denied(f"https://{site}/", websites.load_deny()):
        raise hand.Refused(f"{site} is on deploy/browser-deny.txt: the browser never opens it", rule="denied",
                           site=site)
    out = store.create(site, args.handle)
    queue.record_account(out["site"], out["handle"], args.why or "made with hands/keychain create", task_id=0)
    return out


def _tty(stream: Any) -> bool:
    try:
        return os.isatty(stream.fileno())
    except (AttributeError, OSError, ValueError):
        return False


#: Set only by bin/keychain-fill, the command the core runs for the browser's
#: fill. A playbook input runs `hands/<name> ...` with no environment of its
#: own (and bin/lib/playbook.py lets it run only `list` here), so it cannot.
FILL_FLAG = "TEENY_KEYCHAIN_FILL"


def _secret(args: argparse.Namespace) -> int:
    if os.environ.get(FILL_FLAG) != "1" or _tty(sys.stdout):
        print("keychain: `secret` is for the browser's fill (bin/keychain-fill); a person uses `reveal`",
              file=sys.stderr)
        return 2
    try:
        word = store.secret_for(args.host, args.handle)
    except (hand.HandError, hand.Refused) as exc:
        print(f"keychain: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(word)
    sys.stdout.flush()
    return 0


def _reveal(args: argparse.Namespace) -> int:
    if not (_tty(sys.stdout) and _tty(sys.stdin)):
        print("keychain: `reveal` prints to a terminal only", file=sys.stderr)
        return 2
    try:
        handle, word = store._open(store.site(args.site), store.handle(args.handle) if args.handle else None)  # noqa: SLF001
    except (hand.HandError, hand.Refused) as exc:
        print(f"keychain: {exc}", file=sys.stderr)
        return 1
    print(f"{handle} on {store.site(args.site)}: {word}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hands/keychain", description="Teeny's site passwords, sealed.")
    verbs = root.add_subparsers(dest="verb")
    creating = verbs.add_parser("create", help="a new password for one account")
    creating.add_argument("--site", required=True)
    creating.add_argument("--handle", required=True)
    creating.add_argument("--why", default=None)
    creating.set_defaults(handler=lambda a, _: _create(a), takes_body=False)
    verbs.add_parser("list", help="sites and handles").set_defaults(handler=lambda a, _: store.listing(),
                                                                     takes_body=False)
    return root


def main(argv: list[str]) -> int:
    # The two verbs that print a password do not go through hand.run, whose
    # answer is JSON for a caller to read back.
    if argv and argv[0] in ("secret", "reveal"):
        sub = argparse.ArgumentParser(prog=f"hands/keychain {argv[0]}")
        if argv[0] == "secret":
            sub.add_argument("--host", required=True)
        else:
            sub.add_argument("--site", required=True)
        sub.add_argument("--handle", default=None)
        args = sub.parse_args(argv[1:])
        return _secret(args) if argv[0] == "secret" else _reveal(args)
    return hand.run("keychain", parser(), argv)
