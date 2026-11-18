"""Prompt-injection screening for pull requests: does this text try to talk
to a model? Deterministic, no model, nothing to argue with.

A pull request is strangers' text twice over: its title, body, commits and
comments are read by whoever reviews it, and on teenydroid/brain its diff
can become a prompt (everything under brain/ rides in Teeny's prompts). So
every piece of it is read here before any model or person acts on it.

A hit names its rule and where it was found, plus a sha256 of the matched
span so two sightings of one payload can be told apart. It never carries
the matched text: a finding is logged (`babysit_injections`) and a ledger
is read by later turns (dream reads them), so keeping the payload would
deliver it to the very model it was aimed at.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

# --- the rules ---------------------------------------------------------------------------

#: Trojan Source (CVE-2021-42574): the bidi controls that make code read
#: differently from how it runs.
_BIDI = re.compile("[‪-‮⁦-⁩]")
#: Characters that render as nothing. Folded out before the phrase rules
#: read the text, so "ign​ore previous instructions" is still caught.
_INVISIBLE = "​‌‍⁠᠎﻿"
_INVISIBLE_RE = re.compile(f"[{_INVISIBLE}]")
#: Unicode tag characters: ASCII smuggled in a form no person sees but a
#: tokenizer reads.
_TAGS = re.compile("[\U000e0000-\U000e007f]")

_I = re.IGNORECASE

#: (rule, pattern). Each reads the folded text (NFKC, invisibles removed,
#: whitespace collapsed). Written to catch addresses to a model, not words
#: about models: "the system prompt is built in bin/lib/prompts.py" is not a
#: hit; "print your system prompt" is.
PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override", re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass|skip)\b(?:\s+\w+){0,3}?\s+"
        r"(?:previous|prior|above|earlier|preceding|existing|original|all|any|your|the)\s+"
        r"(?:\w+\s+)?(?:instructions?|prompts?|rules|directions|guidelines|guardrails|constraints|policies|context)\b",
        _I)),
    ("role_hijack", re.compile(
        r"\byou\s+are\s+now\b|\bfrom\s+now\s+on,?\s+you\b|\bnew\s+(?:system\s+)?instructions?\s*:"
        r"|\b(?:enter|enable|activate)\s+(?:developer|god|jailbreak|dan)\s+mode\b|\bact\s+as\s+(?:an?\s+)?"
        r"(?:unrestricted|unfiltered|jailbroken|uncensored)\b|\bpretend\s+(?:that\s+)?you\s+(?:are|have)\s+no\b",
        _I)),
    ("chat_markup", re.compile(
        r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>|\[/?INST\]|<</?SYS>>"
        r"|</?(?:system|system-reminder|instructions?|function_calls|antml:[a-z_]+|tool_use|tool_result)\b[^>]*>"
        r"|^\s*#{2,}\s*(?:system|instructions?)\s*:|^\s*(?:human|assistant|system)\s*:\s",
        _I | re.MULTILINE)),
    ("agent_directive", re.compile(
        r"\b(?:ai|llm|bot|agent|assistant|model|reviewer|claude|copilot|gpt|chatgpt|teeny|babysitter)s?\b"
        r"(?:\s*[,:]\s*|\s+(?=must\b|should\b|please\b|now\b|just\b))"
        r"(?:(?:must|should|please|now|just)\s+){0,2}(?:approve|merge|lgtm|accept|ignore|skip|auto-?merge)\b"
        r"|\b(?:approve|merge|auto-?merge)\s+(?:this|the)\s+(?:pr|pull\s+request|change)s?\s+"
        r"(?:immediately|now|without|automatically)\b"
        r"|\b(?:do\s+not|don'?t|never)\s+(?:flag|report|mention|review|audit|check)\s+(?:this|these|the)\b",
        _I)),
    ("exfiltrate", re.compile(
        r"\b(?:print|reveal|output|show|send|leak|dump|post|paste|echo|repeat|tell\s+me)\b(?:\s+\w+){0,3}?\s+"
        r"(?:your\s+(?:system\s+prompt|instructions|secrets?|tokens?|api\s+keys?|credentials|"
        r"env(?:ironment)?(?:\s+variables)?|private\s+keys?|seed\s+phrase|recovery\s+phrase)"
        r"|the\s+(?:system\s+prompt|secrets|api\s+keys?|credentials|private\s+keys?|seed\s+phrase|"
        r"recovery\s+phrase|environment\s+variables))\b"
        r"|\b(?:cat|curl|wget|base64|less|more|head|tail)\s+[^\n]{0,40}(?:secrets/|teeny\.env|core\.token|\.ssh/)",
        _I)),
)

#: An HTML comment renders as nothing on GitHub. One that carries anything
#: but tooling markers is text written for a reader who is not a person.
_HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
_COMMENT_HINT = re.compile(
    r"\b(?:ai|llm|assistant|model|reviewer|bot|agent|instruction|prompt|ignore|approve|merge|claude|gpt|teeny)\b", _I
)
#: The hidden identity markers PR-Agent (.github/workflows/pr-agent.yml,
#: the-pr-agent/pr-agent) puts in the comments it posts, and the one our own
#: budget step (.github/scripts/pr-agent-budget.sh) posts: tooling a later
#: run finds its comment by, not text for a model. Each name is spelled out
#: (a word pattern would let "pr-agent:approve-it" through); the dedup
#: markers carry a hex fingerprint and nothing else. Only the whole comment
#: counts: a marker with anything else inside the `<!-- -->` is not one.
#: Exempt only where the caller says so (audit: a comment by the workflow's
#: own login), never on its own.
PR_AGENT_MARKERS = re.compile(
    r"\s*(?:pr-agent:(?:review:(?:full|incremental)|improve:(?:summary|no-suggestions|unanchored)"
    r"|describe:persistent|(?:github|gitlab|gitea|bitbucket)-incomplete-(?:files|diff))"
    r"|pr_agent:(?:summary|type|walkthrough|diagram)"
    r"|pr-agent-(?:generated|response|progress|budget)"
    r"|pr-agent-(?:dedup|dedup-code|key-issue-location):\s[0-9a-f]{6,64})\s*"
)
#: A long base64 run in prose is either a blob nobody needs to read or a
#: payload hiding from the phrase rules. Decoded and read again.
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/=])")

# --- the answer --------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    """One sighting: which rule, where, and a digest of what matched (never
    the text itself)."""

    rule: str
    where: str
    digest: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "where": self.where, "digest": self.digest}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def fold(text: str) -> str:
    """The text the phrase rules read: NFKC (full-width and look-alike
    letters to ASCII), tag characters and invisibles removed."""
    text = _TAGS.sub("", text)
    text = _INVISIBLE_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    return text


def _phrase_hits(text: str, where: str, rules: Iterable[str] | None = None) -> list[Hit]:
    hits: list[Hit] = []
    for rule, pattern in PHRASES:
        if rules is not None and rule not in rules:
            continue
        found = pattern.search(text)
        if found:
            hits.append(Hit(rule, where, _digest(found.group(0))))
    return hits


def _decoded(blob: str) -> str | None:
    try:
        raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(ch.isprintable() or ch.isspace() for ch in text)
    return text if text and printable / len(text) > 0.95 else None


def scan(text: str, where: str, *, prose: bool = True, rules: Iterable[str] | None = None,
         markers: re.Pattern[str] | None = None) -> list[Hit]:
    """Every rule `text` breaks, one hit per rule. `prose` is True for words
    people write to people (a title, a body, a commit message, a comment),
    where hidden comments and base64 runs are suspect; False for a diff,
    where they are code. `rules` limits the phrase rules read. `markers`
    names hidden comments that are known tooling (`PR_AGENT_MARKERS`): one
    whose whole inside it matches is skipped by `hidden_comment` alone;
    every other rule still reads the whole text."""
    if not isinstance(text, str) or not text:
        return []
    hits: list[Hit] = []
    for rule, pattern in (("bidi_control", _BIDI), ("unicode_tags", _TAGS)):
        found = pattern.search(text)
        if found:
            hits.append(Hit(rule, where, _digest(found.group(0))))
    invisible = _INVISIBLE_RE.findall(text.lstrip("﻿"))
    if invisible:
        hits.append(Hit("invisible_chars", where, _digest("".join(invisible))))
    folded = fold(text)
    hits.extend(_phrase_hits(folded, where, rules))
    if prose:
        for comment in _HTML_COMMENT.finditer(folded):
            inner = comment.group(1)
            if markers is not None and markers.fullmatch(inner):
                continue
            if _COMMENT_HINT.search(inner):
                hits.append(Hit("hidden_comment", where, _digest(inner)))
                break
        for blob in _BASE64.finditer(folded):
            decoded = _decoded(blob.group(0))
            if decoded is not None and _phrase_hits(fold(decoded), where):
                hits.append(Hit("encoded_payload", where, _digest(blob.group(0))))
                break
    seen: set[str] = set()
    unique = []
    for hit in hits:
        if hit.rule not in seen:
            seen.add(hit.rule)
            unique.append(hit)
    return unique


#: Rules that are an attack wherever they appear, even in a trusted author's
#: diff: nobody needs invisible bidi controls or tag characters in this code.
ALWAYS_HOSTILE = frozenset({"bidi_control", "unicode_tags"})
