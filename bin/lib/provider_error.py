"""What kind of model-provider failure a failed draft turn was.

    python3 bin/lib/provider_error.py < ERROR_TEXT

Prints `<class> <lasting|passing>`, or nothing when the text is not a
provider failure (exits 0 either way). bin/act runs it on a failed draft
turn's stderr and puts the class in the run's line in runs.jsonl
(`failure: "provider"`, `provider: <class>`); bin/lib/health.py raises the
playbook alarm from those lines, and bin/lib/catch_up.py makes up a slot
they lost.

On 2026-11-14, from about 06:10 to 09:15 UTC, every draft failed with
"local-openai returned HTTP 402 (insufficient_allowance)". The core's cron
retries a failed shell job at once, so each run was tried three times in
twenty seconds and failed three times. A `lasting` class (no money, a
refused key) fails the same way seconds later: bin/act records it once and
exits 0, so cron does not retry it. A `passing` one (a rate limit, a 5xx, a
cut stream, a timeout, a refused connection) may clear in seconds, and
keeps cron's retry.
"""

from __future__ import annotations

import re
import sys

#: (class, pattern), first match wins. Read against the core's error text,
#: e.g. "model error: local-openai returned HTTP 402 (insufficient_allowance)".
CLASSES = (
    ("payment", r"\b402\b|insufficient[_ ](?:allowance|credits?|balance|funds|quota)|payment required"),
    ("auth", r"\b40[13]\b|invalid[_ ]api[_ ]key|api key not set|unauthori[sz]ed|forbidden|authentication failed"),
    ("rate_limit", r"\b429\b|rate[_ -]?limit|too many requests"),
    ("timeout", r"timed out|timeout|deadline exceeded"),
    ("connection", r"connection (?:refused|reset|closed)|could not connect|failed to connect|unreachable"
                   r"|no route to host|name resolution|dns error"),
    ("server", r"\b5\d\d\b|bad gateway|service unavailable|internal server error|overloaded"
               r"|stream ended|completion signal"),
)
#: The classes a retry seconds later cannot clear.
LASTING = ("payment", "auth")

_COMPILED = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in CLASSES)


def classify(text: str) -> str | None:
    """The provider failure class of an error text, or None when it is not one."""
    for name, pattern in _COMPILED:
        if pattern.search(text):
            return name
    return None


def main() -> int:
    kind = classify(sys.stdin.read())
    if kind:
        print(kind, "lasting" if kind in LASTING else "passing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
