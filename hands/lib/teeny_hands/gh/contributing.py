"""Whether a repository's own policy forbids contributions made by an AI.

Teeny opens a pull request on somebody else's project only where it is
welcome, so this reads the files a project states its rules in and answers
conservatively: a sentence that names AI (or an LLM, a bot, generated code)
next to a prohibition forbids; a mention that is clearly a welcome (with or
without a disclosure rule, which Teeny's footer meets) allows; a mention it
cannot read either way is treated as forbidding. Silence is not a ban: a
project that says nothing about AI gets the rest of the rules (one open PR,
the daily cap, the disclosure footer).

This is a keyword-and-phrase heuristic, not a reading. It errs towards "no".
"""

from __future__ import annotations

import re
from typing import Callable

#: Where projects state their rules, in the order they are read.
POLICY_FILES = (
    "AI_POLICY.md",
    ".github/AI_POLICY.md",
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    ".github/CODE_OF_CONDUCT.md",
)

# A sentence about machine-made contributions.
_AI = re.compile(
    r"\b(?:a\.?i\.?|ai[- ](?:generated|assisted|written|authored|tools?|agents?|models?)|artificial intelligence|"
    r"llms?|large language models?|gpt[-\w]*|chatgpt|copilot|claude|gemini|generative|"
    r"machine[- ]generated|auto[- ]?generated|generated (?:code|content|contributions?|pull requests?|prs?)|"
    r"bots?|bot[- ](?:generated|authored|made)|automated (?:contributions?|pull requests?|prs?|submissions?)|"
    r"(?:ai|autonomous|coding) agents?)\b",
    re.IGNORECASE,
)
# What a prohibition sounds like.
_NO = re.compile(
    r"\b(?:not|no|never|don'?t|do not|does not|doesn'?t|won'?t|will not|cannot|can'?t|must not|may not|"
    r"shall not|prohibit\w*|forbid\w*|banned|ban|bans|disallow\w*|reject\w*|refuse\w*|unacceptable|"
    r"not (?:accept|allow|permit|welcome)\w*|will be closed|be closed|closed without|zero[- ]tolerance)\b",
    re.IGNORECASE,
)
# What a welcome sounds like.
_YES = re.compile(
    r"\b(?:welcome[sd]?|allowed|permitted|is fine|are fine|ok(?:ay)?|accept(?:ed|s)?|encourage[sd]?|"
    r"may use|can use|feel free)\b",
    re.IGNORECASE,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n\s*\n|\n(?=\s*(?:[-*+]|\d+\.)\s)")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s and s.strip()]


def assess(files: dict[str, str]) -> dict:
    """The verdict on the policy files found (path -> text):

      {"forbids_ai": bool, "verdict": "forbids" | "allows" | "silent",
       "files": [paths read], "evidence": [{"file", "sentence", "reads"}]}

    `reads` is "forbids", "allows" or "unclear" for each sentence that names
    AI; any "forbids" or "unclear" forbids."""
    evidence: list[dict] = []
    for path, text in files.items():
        for sentence in _sentences(text):
            if not _AI.search(sentence):
                continue
            if _NO.search(sentence):
                reads = "forbids"
            elif _YES.search(sentence):
                reads = "allows"
            else:
                reads = "unclear"
            evidence.append({"file": path, "sentence": sentence[:300], "reads": reads})
    if not evidence:
        verdict = "silent"
    elif any(item["reads"] != "allows" for item in evidence):
        verdict = "forbids"
    else:
        verdict = "allows"
    return {
        "forbids_ai": verdict == "forbids",
        "verdict": verdict,
        "files": list(files),
        "evidence": [item for item in evidence if item["reads"] != "allows" or verdict == "allows"][:10],
    }


def read_policy(fetch: Callable[[str], str | None]) -> dict:
    """Reads every policy file `fetch(path)` returns, then `assess`es them."""
    found: dict[str, str] = {}
    for path in POLICY_FILES:
        text = fetch(path)
        if text is not None:
            found[path] = text
    return assess(found)
