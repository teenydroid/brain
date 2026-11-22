# spine

`teeny-spine` is the one Rust package in the repository. It is OpenHuman's own
command-line host (`openhuman_rpc::host::cli`, what upstream's `openhuman-core`
binary runs) built from the library in `vendor/openhuman`, with two modules
linked in (TinyComputer and tinywallet) and four small additions of Teeny's.

It is the runtime. `teeny-spine serve` is the process that holds the agent
loop, model routing, cron (the heartbeat that starts every job), memory,
the browser and the JSON-RPC API. Nothing about what Teeny says or does lives
here: that is `brain/` (prompts and schedule), `bin/` (the scripts cron runs)
and `hands/` (the programs that act). `bin/spine` is the entrypoint that
configures and starts this binary; see [`docs/architecture.md`](../docs/architecture.md),
"Spine", for the settings it applies.

```
bin/spine ──► teeny-spine serve ──► cron ──► bin/<script> ──► hands/<name>
                  │
                  ├── agent loop, memory (CortexDB), browser, JSON-RPC
                  └── Teeny's additions (this folder)
```

## Files

| File | What it does |
|---|---|
| `Cargo.toml` | The package. Depends on `openhuman-rpc` by path with the product feature set minus `os-keyring` (secrets live in a file keyring under a master key file), and plus `web3` and `inbound-queues`. |
| `src/main.rs` | Entry point. Hands every command to OpenHuman's shared host, except `giveaway-tx`, which it runs itself. Links the wallet backend and the browser picture sink in before the host starts. |
| `src/wallet_backend.rs` | Answers the wallet module's swap and bridge quote calls. One handler picks LI.FI or deBridge per call from a tag `hands/wallet` puts in one free-text field of the request. |
| `src/lifi.rs` | The LI.FI quote client (the default provider), straight from LI.FI's public API. |
| `src/debridge.rs` | The deBridge quote client, from deBridge's public API. |
| `src/giveaway_tx.rs` | `teeny-spine giveaway-tx create\|close`: builds the unsigned Solana transactions of a Tiny Drops round (one JSON object in, one out). It holds no key and signs nothing. |
| `src/browser_pictures.rs` | The sink for the browser's pictures of pages: writes them into the folder named by `TEENY_BROWSER_PICTURES` and keeps the newest 24. |

The `[patch]` tables in the root `Cargo.toml` are OpenHuman's own, rebased onto
`vendor/openhuman/`. Re-copy them whenever the submodule is bumped
([`docs/developers.md`](../docs/developers.md), "Bumping OpenHuman").

## Build and test

The toolchain is pinned in the root `rust-toolchain.toml`.

```sh
git submodule update --init --recursive vendor/openhuman
cargo build --release -p teeny-spine      # target/release/teeny-spine
cargo test -p teeny-spine                 # the unit tests inside each src file
cargo check -p teeny-spine                # fastest way to confirm doc comments compile
```

The end-to-end check is `bin/test/spine_live_test.sh`: it boots a real spine
over a scratch copy of the checkout against a stub model
(`OPENHUMAN_CORE_BIN=target/release/teeny-spine bin/test/spine_live_test.sh`).

This folder is frozen to Teeny's own pull requests (`evals/frozen.txt`): only
the operator changes it.
