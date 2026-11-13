# What Teeny needs from OpenHuman

References are `upstream/main` at `6741ea4b41`, with
`$C = crates/openhuman-core/src`. None of these blocks the design in
[`architecture.md`](architecture.md). Each has a workaround in Teeny's scripts
or hands that the upstream change would delete, and they're ranked by how much
Teeny-side code that would remove.

| # | Gap | Teeny's workaround today | Proposed upstream change |
|---|---|---|---|
| G1 ([#8118](https://github.com/tinyhumansai/openhuman/issues/8118)) | **Solved** (openhuman#8648), but capped by G12. Shell cron jobs had a fixed 120 s limit; `[cron] shell_timeout_secs` (default 120) sets it. | `bin/spine` sets `shell_timeout_secs = 1800` (`bin/lib/core_sections/automation.py`); past 300 s G12 applies. | Done. A per-job timeout would still let one slow job have a longer leash than the rest. |
| G2 ([#8119](https://github.com/tinyhumansai/openhuman/issues/8119)) | **Cron jobs can't be declared in a file.** They're created only through `cron.add` (RPC or the agent tool), so the schedule isn't version-controlled. | `bin/seed-cron` reconciles `brain/cron.toml` against `cron.list` at every boot. | Load `<brain>/cron.toml` at boot as operator-declared jobs, owned by the file and pruned when they're removed from it. |
| G3 ([#8120](https://github.com/tinyhumansai/openhuman/issues/8120)) | **Channel replies have no outbound hook.** A Telegram turn's text goes to the chat with no step to filter, rewrite or block it. `afterAgentResponse` only observes (`vendor/tinyagents/crates/tinyagents-hooks/src/command/types.rs`). | Teeny uses no OpenHuman channel. Every outside surface is a hand that runs the guard itself. | A `beforeChannelSend` hook that can deny or rewrite, with the same contract as `preToolUse`. |
| G4 ([#8121](https://github.com/tinyhumansai/openhuman/issues/8121)) | **Read-only tools need MCP, and MCP is always `Execute`.** A no-shell agent can only reach a script through MCP, and every MCP call registers as `Execute` whatever the server's `readOnlyHint` says (`$C/tools/impl/network/mcp.rs:319`). So OpenHuman's gate can't tell a read verb from a write, and Teeny's safety rests only on the MCP catalog and the agents' allowlists. | No agent that reads strangers' text has any tool. The scheduled agents use desk files (G11). | Workspace-declared command tools (`tools/<name>.toml`: command, JSON schema, permission level, external effect) registered like built-ins. |
| G5 ([#8122](https://github.com/tinyhumansai/openhuman/issues/8122)) | **No direct Langfuse export.** Pushing traces needs the TinyHumans backend proxy. Locally there's only an NDJSON file (`[observability.agent_tracing]`, `$C/config/schema/observability.rs`). | Not used yet. Each mind beat is logged to `state/tick/runs.jsonl`. | `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` export from the core. |
| G6 ([#8123](https://github.com/tinyhumansai/openhuman/issues/8123)) | **`openhuman-core call` runs in-process.** It invokes the method in a new process against the same storage (`$C/core/cli.rs:419`) instead of reaching the running `serve`. | `bin/oh` posts JSON-RPC to `127.0.0.1:7788` with `OPENHUMAN_CORE_TOKEN`. | `call --url` / `OPENHUMAN_CORE_URL` to send the call to a running core. |
| G7 ([#7000](https://github.com/tinyhumansai/openhuman/issues/7000)) | **Browser control is unproven headless.** It's the TinyComputer module from `tinyhumansai/tinydesktop` (the submodule is still at `vendor/tinycomputer`; see #7000). The core image ships without Chromium. | Teeny's image adds Chromium and sets `chrome_path`. The browser operator runs headless on the allowed websites only, and a payment checkpoint ends its task. | Close #7000. Optionally ship a `-browser` image variant. |
| G8 ([#8102](https://github.com/tinyhumansai/openhuman/issues/8102)) | **The Telegram channel's allowlist isn't persisted after pairing** (#8102). | Not used (see G3). | Fix #8102. |
| G9 ([#8124](https://github.com/tinyhumansai/openhuman/issues/8124)) | **`beforeSubmitPrompt` doesn't fire on `agent_chat`.** It runs only on the web-chat path (`$C/web_chat/ops/start_chat.rs:313`), so a hook can't screen text that a script sends to an agent. | Every intake hand (`x ingest`, the webhook, `mail ingest`, `moltbook inbox`) runs `hands/guard inbound` before anything reaches a model. | Fire `beforeSubmitPrompt` for every turn entry point (`agent_chat`, channels, cron). |
| G10 ([#8125](https://github.com/tinyhumansai/openhuman/issues/8125)) | **No proven way for a hand to file a labelled capture into memory.** Labelled `Memory::store`/`search` exist only in-process (#7486). From a script there's only RPC: `memory_learn` and `memory_brain_ingest`. | The mind's `remember` intent and the nightly diary entry use `memory_learn`. `brain/MEMORY.md` is the memory that must survive anything. | Expose labelled `memory.store` and `memory.search` over RPC (the #7486 facade). |
| G11 (resolved, [#8654](https://github.com/tinyhumansai/openhuman/pull/8654)) | **A serve host drops every configured MCP server's tools when TinySecurity is on.** `mcp::registry::tools_safe_for_agent` scans each remote tool definition through TinySecurity using `CoreContext::current_embedder_config()`, which only an embedding host (the desktop app) sets; under `openhuman-core serve` (a `HostKind::Cli` context) it is `None`, the scan counts as `scan.unavailable`, and every tool is dropped (measured on `1864c68c52`: `[mcp] dropped a remote tool that tripped the input-validation scan server="hands" ... reason=scan.unavailable`). | The builder and the browser operator take and finish their work through desk files under `state/workbench/desk/` that a cron job settles with the hand (`architecture.md` §6a), instead of `hands/mcp`. | Landed in tinyhumansai/openhuman#8654: MCP tool scanning falls back to the loaded config when no embedder config is scoped, so a serve host keeps its MCP tools. |
| G12 | **A cron shell job is bound by TinyBox's fixed module call timeout, not `shell_timeout_secs`.** Cron runs each shell job as one `run_native_shell` call through the TinyBox module client, built `with_timeout(MODULE_CALL_TIMEOUT)` = 300 s (`$C/modules/tinybox/operations.rs:19`, at the vendored pin `f329a1476c`). A job past 300 s fails as "TinyBox native shell execution failed" while its process runs on unsupervised; cron retries it (the retry found `bin/tick`'s lock held and exited `skipped`), and the real outcome never reaches cron history. | Jobs that can pass 300 s run detached: `bin/tick --detach` and `bin/reflect --detach` start the work in its own session through `bin/lib/detach.py` and return at once; the work logs to `state/<job>/` and takes its own lock, so a call while it runs says `busy`. The other pinned shell jobs are bounded well under 300 s and stay in the foreground. | Let a cron shell call last as long as `[cron] shell_timeout_secs` (pass the job's timeout, plus a grace, as the module call timeout), so a long job can again run in the foreground and cron records what it did. |
| G13 | **A session turn is paused at `max_iterations` even after a final answer.** `$C/agent/session_host/driver/execute.rs:164` (at `f329a1476c`) runs session turns with `pause_at_cap: true`; the `CapPauser` (`vendor/tinyagents/crates/tinyagents-runtime/src/turn_driver/driver.rs:432`) sends a pause after the cap's last model call even when that call was the answer, and `settle_natural_finish` (`tinyagents-harness/src/agent_loop/turn_control.rs:77`) drains it and returns `Paused`. At `max_iterations = 1` every turn is "hosted turn paused before completion", `[host] agent run failed`, and recorded as a failure (the experience store and memory sink would tag every beat `turn_failure`), though the answer comes back. | The tool-less agents (`teeny`, `act`, `judge`, `teeny-mentions`, `teeny-mail`) run at `max_iterations = 2`; the second iteration only absorbs the pause. `bin/lib/agent_check.py` (`NO_TOOLS_MAX_ITERATIONS`) holds them to exactly 2. | Pause at the cap only when the turn still has work to do (a pending tool call), never after a natural finish; then drop the agents back to 1. |
| G14 | **Memory never reconciles a fact that changed.** A `memory_learn` item is deduped only by an exact fingerprint, with no supersede or valid-until (`vendor/tinymemory/crates/tinymemory-api/src/item/mod.rs`); `Correction` is only a label. Learnings and memory-block lines carry no date (`tinymemory-tools/src/recall/gather.rs` dates only conversation hits), so "promise #1 open" and "promise #1 kept" come back side by side and the model cannot tell which is newer. | Every `memory_learn` from `bin/tick` and `bin/reflect` starts with its date, the journal puts what the hands did before what the mind claimed, and `brain/MEMORY.md` (rewritten item by item, retired items archived) is the record that wins. | Date every recalled item in the pack and the memory block, and let a learn name the item it supersedes (or carry a subject key whose newest item wins). |

## Landed upstream

- Scheduled actions (openhuman#8648). A cron job whose agent sets
  `scheduled_actions = true` and names its tools explicitly runs as
  `TrustedAutomation { CronActions }`: its root run may use those tools
  without a person's approval, while the autonomy tier, `workspace_only`,
  TinySecurity's floor, the sandbox, the allowed websites and the browser's
  payment checkpoint still apply. `[browser] unattended_actions` lists the
  clicks and typing any cron turn may take. Teeny uses it for exactly two
  agents, the builder and the browser operator
  ([`architecture.md`](architecture.md) §6a). Every other scheduled job is
  still a shell job, and every agent that reads strangers' text is still
  tool-less. The read-only rule stays the default, and the opt-in is per
  agent rather than a blanket allowlist.
- Wallet policy (openhuman#8649). `[wallet_policy]` and a spend ledger
  shared by every core in a workspace. The spine builds the `web3` domain
  with tinywallet linked in (`spine/Cargo.toml`); the policy itself belongs
  to the wallet hand.
- MCP tool scanning on a serve host (openhuman#8654, gap G11). The scan
  no longer drops every configured MCP server's tools under `openhuman-core
  serve`. The desk-file handoff stays as the builder's and the browser
  operator's path for now; `hands/mcp` is no longer blocked on the core.

## Deliberately not asked for

- An `[cron] unattended_tools` allowlist that would let every cron agent turn
  take external actions. The rule at
  `$C/agent/tinyagents/host/security_gate.rs:532` is the right default for a
  public agent, and the design relies on it. Scripts act and models draft;
  the per-agent opt-in above covers the two agents built to act.
- Secret injection for `http_request`. Hands hold the secrets, so the model
  never needs a credentialed HTTP tool.
