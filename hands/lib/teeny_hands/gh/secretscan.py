"""What a push would publish that looks like a secret.

A push from Teeny's workbench publishes every file git would add and every
commit already made there, so all of that is read: the paths (an env file,
a key, anything under secrets/ or state/) and the contents (the shapes of
the tokens Teeny could plausibly have in reach). One finding refuses the
whole push and names the file and the rule, never the secret itself.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

# Paths that never leave the box, matched against every component and the
# whole path.
_PATH_RULES: tuple[tuple[str, str], ...] = (
    (".env*", "an env file"),
    ("*.pem", "a PEM key"),
    ("id_*", "an SSH key"),
    ("*.key", "a key file"),
)
_DIR_RULES: tuple[tuple[str, str], ...] = (
    ("secrets", "the secrets folder"),
    ("state", "the state folder"),
    (".ssh", "an SSH folder"),
)

_CONTENT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "a GitHub token (ghp_)"),
    (re.compile(r"\bgh[osur]_[A-Za-z0-9]{20,}"), "a GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "a GitHub token (github_pat_)"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"), "an API key (sk-)"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "a Slack token (xox)"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "an AWS access key (AKIA)"),
    (re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"), "a private key"),
    (re.compile(r"\bam_[A-Za-z0-9]{24,}"), "an AgentMail key"),
)


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "rule": self.rule}


def path_finding(path: str) -> Finding | None:
    """Why a path may not be published, or None."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    for part in parts[:-1]:
        for name, rule in _DIR_RULES:
            if part == name:
                return Finding(path, rule)
    if parts:
        leaf = parts[-1]
        for pattern, rule in _PATH_RULES:
            # .pub halves of SSH keys are public.
            if fnmatch.fnmatch(leaf, pattern) and not (pattern == "id_*" and leaf.endswith(".pub")):
                return Finding(path, rule)
        if leaf in ("teeny.env", "core.token", "openhuman.key"):
            return Finding(path, "a Teeny secret file")
    return None


def content_finding(path: str, text: str) -> Finding | None:
    """Why a file's (or a patch's) text may not be published, or None."""
    for pattern, rule in _CONTENT_RULES:
        if pattern.search(text):
            return Finding(path, rule)
    return None


def scan(paths: list[str], contents: dict[str, str]) -> list[Finding]:
    """Every path finding, then every content finding, one per path."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for path in paths:
        found = path_finding(path)
        if found is not None and path not in seen:
            findings.append(found)
            seen.add(path)
    for path, text in contents.items():
        if path in seen:
            continue
        found = content_finding(path, text)
        if found is not None:
            findings.append(found)
            seen.add(path)
    return findings
