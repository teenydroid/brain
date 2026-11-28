# The private Langfuse

Every agent turn the OpenClaw Gateway runs, traced into a Langfuse on this box:
each run, its model calls (model, tokens, latency, and the prompt and reply),
its tool calls and exec, grouped by session. Behind the `langfuse` profile in
`deploy/compose.yaml`; nothing about it is public.

```
Gateway (in the spine)                 langfuse-net                 langfuse-data (internal)
  diagnostics-otel ──OTLP/HTTP──► otel-collector ──Basic auth──► langfuse-web ◄──► postgres, clickhouse,
  (no credential)                 (holds the key)                    │              redis, minio, worker
                                                          127.0.0.1:3140 (UI, loopback only)
```

## How it fits together

- **The exporter** is OpenClaw's own `diagnostics-otel` plugin. The core
  installs it into the Gateway's state as the official npm package at the
  Gateway's own version (`core/src/gateway/otel.ts`), once, before the
  Gateway starts. It has to be the official install: the Gateway gives its
  diagnostic event stream only to a bundled or verified official
  `diagnostics-otel`, and a copy loaded from a path exports nothing. If the
  install cannot happen within a minute (no registry, say), the core logs
  `turns are not traced` and boots without tracing, well inside the spine's
  120-second boot deadline.
- **The Gateway's config** (`core/src/gateway/config.ts`) gets a
  `diagnostics.otel` block: traces only, every turn sampled, prompts and
  replies on the spans unless `TRACES_CAPTURE=0`, and **no headers**. The
  lockdown (`bin/lib/gateway_check.py`) refuses an export endpoint that
  would leave the box in the clear (only https, loopback, or plain http to
  the `otel-collector` service by name) and any header in the file.
- **The collector** (`otel-collector.yaml`) is the only thing that holds
  Langfuse's project key (`secrets/langfuse-otlp.env`). It drops the core's
  RPC chatter with the Gateway span by span (9 traces in 10 on the live test
  were nothing else), so no agent span is ever lost to a sampling decision,
  and forwards the rest to Langfuse's OTLP endpoint.
- **Langfuse** (web, worker, Postgres, ClickHouse, Redis, MinIO) follows
  Langfuse's own `docker-compose.yml` at the pinned tag. The datastores and
  the worker are only on `langfuse-data`, an internal network with no route
  off the box and no published port. `langfuse-web` is also on
  `langfuse-net` with the collector and the spine; its UI is published on
  `127.0.0.1:3140` only.

## What each call cost, and how much of it was cache

Langfuse's tokens and costs come from Surplus's own bill, not from the
Gateway. The core's model meter (`core/src/gateway/meter.ts`) sits on
loopback between the Gateway and Surplus, and turns each model call into one
`surplus.charge` generation under the Gateway's `openclaw.model.call`:

- **cost**: what Surplus charged for that call (`usage.buyer_cost_micro`),
  the marketplace's discounted price, not a list price;
- **usage**: input split into uncached `input` and `input_cached_tokens`,
  and `output`, as Surplus counted them;
- **metadata**: `cache_hit_pct` (cached ÷ prompt tokens for that call),
  `list_cost_usd` (the same tokens at the serving provider's published rate,
  from Surplus's `GET /v1/prices`, the figure its request log calls
  `direct_cost_micro`), `discount_pct`, and the `provider` that served it.

While the meter is in front of the model, the Gateway reports as service
`teeny-metered`, and the collector then strips its own token counts off
`openclaw.model.call` and drops the per-run `openclaw.model.usage` total, so
nothing is counted twice. With `TEENY_METER=0`, or when the meter cannot bind
its port, the Gateway reports as `teeny` and its counts stay, since there is
no `surplus.charge` to replace them. A charge the collector refuses is retried
twice and then logged as a warning. A project's cost and token totals in Langfuse are Surplus's bill; the
cache hit rate over any window is the sum of `input_cached_tokens` over the
sum of `input` plus `input_cached_tokens` on `surplus.charge`.

The meter also sets each call's `prompt_cache_key` to a hash of its stable
prefix (system prompt and tools), so every run of an agent shares one key
instead of a fresh one per session, and writes Surplus's charge into the
answer as `usage.cost`, which OpenClaw records as the provider-billed cost
(the core's `cost_report` then shows real spend).

## First time on the box

```sh
deploy/langfuse/init.sh                    # mints secrets/langfuse.env and secrets/langfuse-otlp.env, once
docker compose -f deploy/compose.yaml --profile langfuse up -d \
  otel-collector langfuse-web langfuse-worker langfuse-postgres \
  langfuse-clickhouse langfuse-redis langfuse-minio
curl -fsS http://127.0.0.1:3140/api/public/health
```

Name the services: without them, `up` and `down` with the profile also act on
every service without one, the spine and cortexdb included.

Then point the spine at the collector: add
`TRACES_OTLP_URL=http://otel-collector:4318` to `secrets/spine.env`, and
recreate cortexdb and the spine (`up -d cortexdb spine`). Adding
`langfuse-net` to cortexdb recreates it, and the spine with it, the first
time, so check `bin/evolve status` first, as for any spine restart. The
boot log says `tracing the gateway's turns` once the plugin is in; the first
boot installs it (about 15 seconds).

## Looking at it

From a laptop: `ssh -L 3140:127.0.0.1:3140 <box>`, then open
<http://localhost:3140>. Sign in as `teeny@langfuse.local`; the password is
`LANGFUSE_INIT_USER_PASSWORD` in `secrets/langfuse.env`. Sign-up is off. The
project is `turns`: traces, the generations in them (`openclaw.model.call`),
and sessions.

## Turning it off

Remove `TRACES_OTLP_URL` from `secrets/spine.env` and restart the spine: the
Gateway's config loses the exporter and nothing is sent. Then stop Langfuse by
naming its services; its data stays in the `langfuse-*` volumes:

```sh
docker compose -f deploy/compose.yaml --profile langfuse stop \
  otel-collector langfuse-web langfuse-worker langfuse-postgres \
  langfuse-clickhouse langfuse-redis langfuse-minio
```

Not `--profile langfuse down`: `down` takes the whole project with it, the
spine and cortexdb included.

## Upgrading

Bump `langfuse/langfuse` and `langfuse/langfuse-worker` together, and compare
the environment with Langfuse's `docker-compose.yml` at the new tag. ClickHouse,
Postgres, Redis and MinIO move only when Langfuse's file moves them. The
exporter follows the Gateway: a new `openclaw` in `core/package.json` makes the
core install the matching `@openclaw/diagnostics-otel` at its next boot.
