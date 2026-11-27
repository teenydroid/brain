#!/usr/bin/env python3
"""Checks on the OpenClaw Gateway the core runs, read from what it generated.

    gateway_check.py lockdown CONFIG   the config the core wrote (state/openclaw/openclaw.json)
    gateway_check.py audit             `openclaw security audit --json` on stdin

`lockdown` prints one line and exits 0 when the config holds, 1 with the reasons
when it does not. `audit` prints `ok|warn|fail DETAIL` and exits 1 only for
`fail`: a warning is the operator's to read, not a reason to refuse a boot.

The lockdown is what docs/architecture.md ("The OpenClaw Gateway") promises: no
channel can reach Teeny but through its hands, nothing on the Gateway takes a
request from the network, and nothing phones home. A missing key is a failure,
not a default: the core writes every one of them, and a config that lacks one
was not written by it.
"""
from __future__ import annotations

import json
import sys

#: (path, wanted value, why). A path walks nested objects.
LOCKDOWN = (
    ("gateway.mode", "local", "the Gateway runs locally"),
    ("gateway.bind", "loopback", "the Gateway listens on loopback only"),
    ("gateway.auth.mode", "token", "the Gateway needs its shared token"),
    ("gateway.controlUi.enabled", False, "no control UI"),
    ("gateway.http.endpoints.chatCompletions.enabled", False, "no OpenAI-style chat endpoint"),
    ("gateway.http.endpoints.responses.enabled", False, "no responses endpoint"),
    ("hooks.enabled", False, "no webhooks in"),
    ("update.checkOnStart", False, "no update check at start"),
    ("discovery.mdns.mode", "off", "no mDNS advertising"),
)
_MISSING = object()


def _walk(doc, path: str):
    node = doc
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def lockdown(doc) -> list[str]:
    """What the config gets wrong, one line each; empty when it is locked down."""
    if not isinstance(doc, dict):
        return ["the config is not a JSON object"]
    wrong = []
    for path, wanted, why in LOCKDOWN:
        got = _walk(doc, path)
        if got is _MISSING:
            wrong.append(f"{path} is not set (want {json.dumps(wanted)}: {why})")
        elif got != wanted:
            wrong.append(f"{path} is {json.dumps(got)} (want {json.dumps(wanted)}: {why})")
    channels = doc.get("channels")
    if channels not in (None, {}, []):
        wrong.append("channels is configured: Telegram and the rest stay in the hands, never on the Gateway")
    if doc.get("bindings") not in (None, {}, []):
        wrong.append("bindings is configured: no channel may route to an agent")
    # A secret in the file is a SecretRef (an object naming where to read it), never the value.
    def plain(value):
        return not (isinstance(value, dict) and "source" in value)

    for path in ("gateway.auth.token", "gateway.auth.password", "hooks.token"):
        got = _walk(doc, path)
        if got is not _MISSING and plain(got):
            wrong.append(f"{path} is in the config file as a value: it must be a SecretRef")
    providers = _walk(doc, "models.providers")
    for name, provider in (providers.items() if isinstance(providers, dict) else []):
        key = provider.get("apiKey") if isinstance(provider, dict) else None
        if key is not None and plain(key):
            wrong.append(f"models.providers.{name}.apiKey is in the config file as a value: it must be a SecretRef")
    return wrong


def audit(text: str) -> tuple[str, str]:
    """(status, detail) for `openclaw security audit --json` output."""
    start = text.find("{")
    if start < 0:
        return "fail", "the audit printed no JSON: " + (text.strip()[:200] or "nothing")
    try:
        report = json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return "fail", "the audit printed JSON that does not parse"
    findings = report.get("findings") if isinstance(report, dict) else None
    if not isinstance(findings, list):
        return "fail", "the audit report has no findings list"
    by = {"critical": [], "warn": []}
    info = 0
    for f in findings:
        sev = str(f.get("severity", "")) if isinstance(f, dict) else ""
        if sev in by:
            by[sev].append(str(f.get("checkId", "?")))
        else:
            info += 1
    if by["critical"]:
        return "fail", "critical finding(s): " + ", ".join(sorted(set(by["critical"])))
    if by["warn"]:
        return "warn", "warning(s): " + ", ".join(sorted(set(by["warn"]))) + " (openclaw security audit)"
    return "ok", f"no critical or warning findings ({info} informational)"


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "lockdown":
        try:
            with open(argv[2], encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"the OpenClaw config cannot be read: {exc}")
            return 1
        wrong = lockdown(doc)
        if wrong:
            print("; ".join(wrong))
            return 1
        print(f"{len(LOCKDOWN)} lockdown flags hold; no channels, no bindings, no secret value in the file")
        return 0
    if len(argv) == 2 and argv[1] == "audit":
        status, detail = audit(sys.stdin.read())
        print(f"{status} {detail}")
        return 1 if status == "fail" else 0
    print("usage: gateway_check.py lockdown CONFIG | audit < audit.json", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
