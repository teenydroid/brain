# core

The TypeScript core: the process `bin/spine` runs (`bin/teeny-core serve`, Node
24). It answers the `openhuman.*` JSON-RPC every script and hand calls, and it
supervises an **OpenClaw Gateway** (the `openclaw` npm package, pinned in
`package.json`) that runs the agents' turns, the browser and the scheduled
automations. It replaced the Rust host (`spine/`) and the vendored OpenHuman library.

Nothing about what Teeny says or does lives here: that is `brain/` (prompts and
schedule), `bin/` (the scripts cron runs) and `hands/` (the programs that act).
See [`docs/architecture.md`](../docs/architecture.md), "Spine", for the boot
order, the Gateway's lockdown and every setting.

```
bin/spine ──► bin/teeny-core serve ──► RPC host (127.0.0.1:7788)
                  │                      ├── agents, settings, cost, memory, wallet, cron API
                  └── OpenClaw Gateway ◄─┘   agent turns, browser, automations
                         (child process, loopback, token in its environment only)
```

## Files

| Path | What it does |
| --- | --- |
| `src/main.ts` | Entry point. `serve`, `giveaway-tx create\|close` and `openclaw ARGS...` (the bundled OpenClaw CLI on the core's own state and config). `wireCron()` is the one place cron is hooked up. |
| `src/server/` | The HTTP/JSON-RPC server, the method registry (`registry.ts`), the config and credential methods. |
| `src/gateway/` | The Gateway: the config the core generates (`config.ts`), the supervisor, the agent runner (`client.ts`) and the CLI passthrough. |
| `src/agents/` | Agent definitions from `brain/agents/*.toml` as OpenClaw agents, model refs, the cost log, the chat methods. |
| `src/config/` | Paths, the token, the sealed settings store, Chrome discovery. |
| `src/cron/` | `cron_*` over OpenClaw automations. |
| `src/memory/` | `memory_*` (CortexDB), and `plugin.ts`, the config the memory plugin gets. |
| `src/wallet/` | The wallet signer, spend policy and ledger, x402, swaps and bridges, `giveaway-tx`. Its own README says more. |
| `src/playbooks/` | The playbook parser and the agent-turn renderer. |
| `plugins/teeny-guardrails/` | The OpenClaw plugin that holds every tool call to its agent's definition. |
| `plugins/teeny-cortexdb/` | The OpenClaw plugin that owns the memory slot: recall into each turn, capture after it, and the `memory_*` tools (derived from `@cortexdb/openclaw` 0.1.4, Apache-2.0; see its NOTICE). |
| `scripts/` | `gen-skills.ts` (one OpenClaw skill per hand), `migrate-keyring.ts` (the operator's one-shot move from OpenHuman's keyring), `remove-openhuman.sh` (the removal commit). |
| `test/` | vitest. `test/contract/` is what the Python callers expect of each RPC method. |

## Build and test

Node 24.16 or later (the pinned OpenClaw needs it).

```sh
cd core
npm ci
npm run build       # dist/ (what bin/teeny-core runs)
npm test            # vitest
npm run typecheck && npm run lint
```

In the image (`docker build -t teeny-spine .`) the build is a `node:24` stage and
the core lives in `/opt/teeny-core`. The end-to-end check is
`bin/test/spine_live_test.sh`, which boots a real core and Gateway through
`bin/spine` over a scratch copy of the checkout against a stub model; run it in
the image (its header has the command).

This folder is frozen to Teeny's own pull requests (`evals/frozen.txt`): only
the operator changes it.
