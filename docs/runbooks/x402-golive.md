# Runbook: x402 endpoint go-live

Written by Teeny (the autonomous agent this repository runs), 2026-11-25.
It lives in `docs/` because `deploy/**` is frozen to Teeny's pull requests
(`evals/frozen.txt`); the operator may want to fold it into
`deploy/README.md`.

## Where it stands (measured 2026-11-25 ~11:20Z)

Goal #1 (first x402 inflow by ~2026-11-26) is blocked on a single missing
piece: DNS.

- `x402.teeny.enamakel.com` does not resolve: `getent hosts` exits 2,
  `curl https://x402.teeny.enamakel.com/` exits 000 (NXDOMAIN).
- The apex `teeny.enamakel.com` resolves (Cloudflare edge) and `/rewards`
  serves 200.
- The edge certificate is a wildcard for `*.enamakel.com`, so visitor-side
  TLS for the x402 name needs nothing new.
- Everything else is already in the repo and built: the `paywall` compose
  service (profile `paywall`, 127.0.0.1:3130), the proxying server block for
  the name in `nginx/sites.teeny.enamakel.com.conf`, the `X402_ENDPOINT`
  switch, price `stats_endpoint_usdc = "0.01"` in `evals/prices.toml`.

The endpoint sells `GET https://x402.teeny.enamakel.com/stats` (docs/hands.md,
"The paid endpoint"; docs/roadmap.md, S4).

## Go live

1. Add the DNS record at the zone (Cloudflare, proxied like the apex):

   ```
   x402.teeny.enamakel.com   CNAME   teeny.enamakel.com   (Proxied)
   ```

   An A record holding the same address as the apex's works too.

2. In `secrets/teeny.env` set `X402_ENDPOINT=1` and `X402_FACILITATOR_URL`
   (https only; `X402_FEE_PAYER` when the facilitator pays the network fee
   and names its address). `deploy/teeny.env.example` shows the three lines
   near the end of the x402 block.

3. Start the service:

   ```sh
   docker compose -f deploy/compose.yaml --profile paywall up -d paywall
   ```

4. Verify, from anywhere:

   ```sh
   getent hosts x402.teeny.enamakel.com           # an address, not NXDOMAIN
   curl -i https://x402.teeny.enamakel.com/stats  # 402 with the payment requirements
   ```

   A 502 with the name resolving means the `paywall` service is not up
   (step 3). The 402 body carries the requirements (x402 v1, scheme
   `exact`, network, `payTo`, the price); a paid request settles through
   the facilitator, is read back from the chain, and writes one
   `x402_sales` row and one inflow row (category `sale`, ref `x402:stats`)
   in `state/hands.db`. `hands/wallet sales list` shows the rows.

## Off switch and take-down

`X402_ENDPOINT=0` (the default) answers 404 from the box even once the name
resolves. To take it down: set the flag back to 0 and
`docker compose -f deploy/compose.yaml --profile paywall down paywall`.
