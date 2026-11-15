"""The verbs of hands/dripsplan.

  R  validate <config>   read a YAML or JSON config, print ok with counts
                         or one error line per problem, exit 1 on any
  R  emit <config>       the ordered call plan as byte-deterministic JSON
                         with a human echo, dry run only, nothing signed

This hand holds no key, opens no socket and signs nothing, ever.
"""
from __future__ import annotations

import sys

from teeny_hands.dripsplan import config


def main(argv):
    """`dripsplan validate <config>` or `dripsplan emit <config>`."""
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 2 if not argv else 0
    return config.main(argv)


def run():
    """Entry point for `python -m teeny_hands.dripsplan.cli`."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    run()
