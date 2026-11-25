# Runbook: x402 endpoint go-live

Written by Teeny (the autonomous agent this repository runs), 2026-11-25,
revised the same day after review (two greptile P1s, three P2s, five
coderabbit findings; every claim below re-verified against the repo source
and the live box). It lives in `docs/` because `deploy/**` is frozen to
Teeny's PRs (`evals/frozen.txt`); the operator may fold it into
`deploy/README.md`. Every step here is operator hands; Teeny verifies.

## Where it stands (measured 2026-11-25)

- `x402.teeny.enamakel.com` does not resolve: `getent hosts` exits 2,
  `curl` exits 000 (NXDOMAIN). The apex `teeny.enamakel.com` resolves
  (Cloudflare) and serves 200.
- TLS is the real gap, not just DNS. The edge wildcard `*.enamakel.com`
  covers exactly one label (SANs: `*.enamakel.com`, `enamakel.com`), so it
  serves the apex but NOT `x402.teeny.enamakel.com` (two labels deep).
  The origin vhost expects a different certificate anyway:
  `/etc/letsencrypt/live/teeny.enamakel.com-sites/` (a `*.teeny.enamakel.com`
  wildcard), which per the vhost header certbot issues only by DNS
  challenge.
- In the repo, ready: the `paywall` service (profile `paywall`,
  127.0.0.1:3130), the exact-name vhost block in
  `deploy/nginx/sites.teeny.enamakel.com.conf`, the `X402_ENDPOINT` switch,
  the price (`stats_endpoint_usdc = "0.01"` in `evals/prices.toml`).
- One wiring gap a doc cannot close: the paywall container must reach the
  core for `own_address()` (the payTo address). The checked-in default
  route is `http://127.0.0.1:7788` (`hands/lib/teeny_hands/hand.py`,
  `OPENHUMAN_CORE_URL`), which inside the paywall container points at the
  container itself, not at the core (the core lives in cortexdb's network
  namespace; `deploy/compose.yaml` gives `xhook` its own namespace for
  exactly this isolation). The operator supplies the core URL and token to
  this container (env or `secrets/`); until then `/stats` answers 500, not
  402. This runbook names the gap; it does not guess the secret.

## Go live

0. Prerequisites (none of this is installed by default):
   - The sites vhost installed and its certificate issued (the vhost header
     has the exact commands):
     `sudo certbot certonly --dns-cloudflare --dns-cloudflare-credentials /root/cf.ini -d '*.teeny.enamakel.com'`
     then copy + symlink the conf, `sudo nginx -t && sudo systemctl reload nginx`.
   - Cloudflare edge coverage for the EXACT name: the zone wildcard
     `*.enamakel.com` does not cover a two-label name. Either extend the
     edge certificate to `*.teeny.enamakel.com` (Cloudflare advanced/total
     certificate), or make the record DNS-only so visitors hit the origin
     certificate directly.
1. DNS: `x402.teeny.enamakel.com` → the origin (an A record holding the
   apex's address, or a CNAME to it). Proxied only with the edge coverage
   from step 0; otherwise DNS-only.
2. Secrets (`secrets/teeny.env`): `X402_ENDPOINT=1`, `X402_FACILITATOR_URL`
   (https; `X402_FEE_PAYER` when the facilitator pays the network fee), and
   the core route for this container (the gap above). The example block is
   at the end of `deploy/teeny.env.example`.
3. Start or recreate:
   `docker compose -f deploy/compose.yaml --profile paywall up -d --force-recreate paywall`
   Secrets are read once per process (`_ENV_CACHE` in `hand.py`): editing
   the file while the container is up changes nothing without a recreate.
4. Verify, in order:
   - `getent hosts x402.teeny.enamakel.com` — an address, not NXDOMAIN.
   - `curl -sI https://x402.teeny.enamakel.com/stats` — expect HTTP 402
     with the payment requirements in the body (x402 v1, scheme `exact`,
     the network, `payTo`, the price). A TLS error means step 0; a 502
     means nginx cannot reach the paywall container (step 3) — and read
     the body anyway: a facilitator failure also answers 502 with an error
     body, and restarting paywall does not fix an unreachable facilitator.
   - The 402 proves routing, TLS and the paywall process. It proves
     nothing about settlement: the 402 fires before the facilitator is
     contacted. The paid path is tested by the first real sale; watch for
     it with `hands/wallet inflows list` (a row with category `sale`, ref
     `x402:stats`). `hands/wallet sales list` is the wrong tool here: it
     reads `request_payments` (paid build requests) and never shows x402
     sales; the x402 row itself lands in `x402_sales` (`state/hands.db`).
5. Take-down: `X402_ENDPOINT=0` plus the same `up -d --force-recreate`
   (a bare edit switches nothing; the running container keeps its cached
   1). A stopped container leaves nginx answering 502 for the name; remove
   the DNS record too if the name should stop resolving.
