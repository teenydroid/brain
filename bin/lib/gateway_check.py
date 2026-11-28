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
import re
import sys
from urllib.parse import urlsplit

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

#: The plugin that owns the memory slot when CortexDB is configured (core/plugins/teeny-cortexdb).
MEMORY_PLUGIN = "teeny-cortexdb"
#: Keys that would be a secret value in a plugin's config: the CortexDB key stays sealed in state/memory-engine.json.
_SECRET_KEY_PARTS = ("apikey", "api_key", "bearer", "password", "secret", "token")
_LOOPBACK = ("localhost", "::1")


def _safe_endpoint(url: str) -> bool:
    """https anywhere, plain http only on this machine: the rule the core's assertSafeEndpoint holds."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme == "https":
        return bool(host)
    if parts.scheme != "http":
        return False
    return host in _LOOPBACK or re.fullmatch(r"127(\.\d{1,3}){3}", host) is not None


def _walk(doc, path: str):
    node = doc
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _secret_keys(node, path="") -> list[str]:
    """Paths in a plugin's config whose key names a secret and whose value is not a SecretRef."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            low = key.lower()
            # "budgetTokens"-style counts are not secrets: only a key that is, or ends in, a secret name.
            named = any(low == part or low.endswith(part) for part in _SECRET_KEY_PARTS) and not low.endswith("tokens")
            if named and isinstance(value, str) and value:
                found.append(here)
            else:
                found.extend(_secret_keys(value, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_secret_keys(value, f"{path}[{i}]"))
    return found


def memory_plugin(doc: dict) -> list[str]:
    """What the memory slot gets wrong: it is OpenClaw's memory-core (and its dreaming job), or Teeny's plugin set up loosely."""
    wrong = []
    plugins = doc.get("plugins") if isinstance(doc.get("plugins"), dict) else {}
    slot = _walk(doc, "plugins.slots.memory")
    entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    allow = plugins.get("allow") if isinstance(plugins.get("allow"), list) else []
    if slot is _MISSING:
        wrong.append(f"plugins.slots.memory is not set (want \"none\" or {json.dumps(MEMORY_PLUGIN)}: otherwise OpenClaw runs memory-core and its dreaming job)")
    elif slot not in ("none", MEMORY_PLUGIN):
        wrong.append(f"plugins.slots.memory is {json.dumps(slot)} (want \"none\" or {json.dumps(MEMORY_PLUGIN)})")
    core = entries.get("memory-core")
    if not isinstance(core, dict) or core.get("enabled") is not False:
        wrong.append("plugins.entries.memory-core.enabled is not false: its dreaming job would run outside brain/cron.toml")
    if slot == MEMORY_PLUGIN:
        entry = entries.get(MEMORY_PLUGIN)
        if MEMORY_PLUGIN not in allow:
            wrong.append(f"plugins.allow does not name {MEMORY_PLUGIN}, which owns the memory slot")
        if not isinstance(entry, dict) or entry.get("enabled") is not True:
            wrong.append(f"plugins.entries.{MEMORY_PLUGIN}.enabled is not true, but it owns the memory slot")
        paths = _walk(doc, "plugins.load.paths")
        if not isinstance(paths, list) or not any(isinstance(p, str) and p.rstrip("/").endswith("/" + MEMORY_PLUGIN) for p in paths):
            wrong.append(f"plugins.load.paths does not load core/plugins/{MEMORY_PLUGIN}")
        config = entry.get("config") if isinstance(entry, dict) else None
        if not isinstance(config, dict):
            wrong.append(f"plugins.entries.{MEMORY_PLUGIN}.config is not set")
        else:
            for found in _secret_keys(config):
                wrong.append(f"plugins.entries.{MEMORY_PLUGIN}.config.{found} holds a secret value: the key stays sealed in state/memory-engine.json")
            url = config.get("url")
            if not (isinstance(url, str) and _safe_endpoint(url)):
                wrong.append(f"plugins.entries.{MEMORY_PLUGIN}.config.url is not https or loopback: no cloud default, no plain http off this machine")
    elif MEMORY_PLUGIN in allow or MEMORY_PLUGIN in entries:
        wrong.append(f"{MEMORY_PLUGIN} is in the plugins config but does not own plugins.slots.memory")
    return wrong


#: The exporter the core loads when it traces turns (core/src/gateway/otel.ts).
OTEL_PLUGIN = "diagnostics-otel"
_OTEL_ENDPOINTS = ("endpoint", "tracesEndpoint", "metricsEndpoint", "logsEndpoint")
# The core's OTEL_PLAIN_HOSTS: compose services reachable over plain http. Named, not any dotless host: Docker hands
# a name it cannot resolve to the host's DNS, where a search domain could send it off the box.
_OTEL_PLAIN_HOSTS = ("otel-collector",)


def _safe_otel_endpoint(url: str) -> bool:
    """The core's safeOtelEndpoint: https, plain http on this machine, or plain http to a compose service in
    _OTEL_PLAIN_HOSTS. No credentials, no query."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 - raises on a bad port
    except ValueError:
        return False
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    if _safe_endpoint(url):
        return True
    return parts.scheme == "http" and host in _OTEL_PLAIN_HOSTS


def diagnostics(doc: dict) -> list[str]:
    """What the trace export gets wrong: spans (which can carry whole prompts) sent off the box in the clear, or a
    secret written into the file as an export header. The collector holds Langfuse's key, never the Gateway."""
    wrong = []
    otel = _walk(doc, "diagnostics.otel")
    if not isinstance(otel, dict):
        return wrong
    for key in _OTEL_ENDPOINTS:
        url = otel.get(key)
        if url is not None and not (isinstance(url, str) and _safe_otel_endpoint(url)):
            wrong.append(f"diagnostics.otel.{key} is not https, loopback or the otel-collector service: spans would leave the box in the clear")
    if otel.get("headers") not in (None, {}):
        wrong.append("diagnostics.otel.headers is set: an export credential belongs to the collector, never the Gateway's config")
    return wrong


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
    wrong.extend(memory_plugin(doc))
    wrong.extend(diagnostics(doc))
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
        slot = _walk(doc, "plugins.slots.memory")
        print(f"{len(LOCKDOWN)} lockdown flags hold; no channels, no bindings, no secret value in the file; memory slot {slot}")
        return 0
    if len(argv) == 2 and argv[1] == "audit":
        status, detail = audit(sys.stdin.read())
        print(f"{status} {detail}")
        return 1 if status == "fail" else 0
    print("usage: gateway_check.py lockdown CONFIG | audit < audit.json", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
