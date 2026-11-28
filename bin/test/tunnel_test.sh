#!/usr/bin/env bash
# The Cloudflare Tunnel is the only way in from the internet to
# teeny.enamakel.com (deploy/cloudflared/config.yml, the `tunnel` service in
# deploy/compose.yaml). Its whole safety is in four facts, and any of them can
# drift in a harmless-looking edit: the one rule that reaches xhook matches
# X's webhook path exactly, nothing else reaches xhook, an unknown host gets a
# 404, and the tunnel shares a network with xhook and the redirect alone, so
# the spine, cortexdb and the Gateway are out of its reach.
#
#   bin/test/tunnel_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

python3 -I - "$repo" <<'PY'
import os, re, sys

import yaml

repo = sys.argv[1]
failures = []


def fail(msg):
    failures.append(msg)


config = yaml.safe_load(open(os.path.join(repo, "deploy/cloudflared/config.yml")))
compose = yaml.safe_load(open(os.path.join(repo, "deploy/compose.yaml")))
services = compose["services"]
rules = config.get("ingress") or []

# The webhook rule: exactly one, on the exact path, and only for our host.
to_xhook = [r for r in rules if str(r.get("service", "")).startswith("http://xhook:")]
if len(to_xhook) != 1:
    fail(f"expected exactly one ingress rule to xhook, found {len(to_xhook)}")
else:
    rule = to_xhook[0]
    if rule.get("path") != "^/api/webhooks/x$":
        fail(f"the xhook rule must match ^/api/webhooks/x$ exactly, not {rule.get('path')!r}")
    if rule.get("hostname") != "teeny.enamakel.com":
        fail(f"the xhook rule must name teeny.enamakel.com, not {rule.get('hostname')!r}")
    pattern = re.compile(rule.get("path") or "")
    for path in ["/api/webhooks/x"]:
        if not pattern.search(path):
            fail(f"the xhook rule does not match {path}")
    for path in ["/health", "/queue", "/webhooks/x", "/api/webhooks/x/../../health", "/api/webhooks/xx", "/x/api/webhooks/x"]:
        if pattern.search(path):
            fail(f"the xhook rule also matches {path}")

# Nothing on the tunnel reaches the spine's side of the stack.
for rule in rules:
    service = str(rule.get("service", ""))
    host = re.sub(r"^https?://", "", service).split(":")[0].split("/")[0]
    if re.match(r"^https?://", service) and host not in ("xhook", "web"):
        fail(f"ingress rule reaches {service}: only xhook and web may be behind the tunnel")

# Unknown hosts are refused.
if not rules or rules[-1] != {"service": "http_status:404"}:
    fail("the last ingress rule must be the catch-all `service: http_status:404`")

# Networks: the tunnel is on `edge` only, and `edge` holds tunnel, xhook and web alone.
def networks(name):
    svc = services.get(name) or {}
    nets = svc.get("networks")
    if nets is None:
        return ["default"] if "network_mode" not in svc else [svc["network_mode"]]
    return list(nets)

if "tunnel" not in services:
    fail("compose has no `tunnel` service")
else:
    if networks("tunnel") != ["edge"]:
        fail(f"the tunnel must be on `edge` only, not {networks('tunnel')}")
    if services["tunnel"].get("ports"):
        fail("the tunnel publishes a port: it only dials out")
    if services["tunnel"].get("profiles"):
        fail("the tunnel is behind a profile: a plain `up` would leave X's webhook down")
on_edge = sorted(n for n in services if "edge" in networks(n))
if on_edge != ["tunnel", "web", "xhook"]:
    fail(f"`edge` must hold tunnel, web and xhook alone, not {on_edge}")
if "default" in networks("xhook"):
    fail("xhook is on `default`, where cortexdb and the spine are")

if failures:
    for f in failures:
        print(f"FAIL: {f}")
    sys.exit(1)
print("ok: the tunnel reaches X's webhook path and the redirect, nothing else")
PY
