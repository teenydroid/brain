# core/src/wallet: the TypeScript wallet signer

This replaces OpenHuman's tinywallet module and the Rust spine hooks
(`spine/src/{wallet_backend,lifi,debridge,giveaway_tx}.rs`). It serves every
`wallet_*`, `web3_*`, `x402_*` method and `encrypt_secret` in
`core/test/contract/` as `RpcMethods` (`index.ts`), with the same names, the same
JSON and the same refusal messages (`WALLET_POLICY_REFUSED:`, `X402_REFUSED:`).

Money moves only here, and only after the caps in `[wallet_policy]` have said yes.
The hands still decide whether to ask at all (`DRY_RUN`, their own caps, the guard);
the core has no `DRY_RUN` of its own and ignores `OPENHUMAN_WALLET_POLICY_*` in the
environment, so the process env can only ever make it do less.

## Files

| File | What it does |
| --- | --- |
| `index.ts` | `methods` (and `createMethods(deps)` for tests). Every handler is wrapped so only a scrubbed message leaves it. |
| `wallet-methods.ts` | status, setup, mnemonic, recovery material, `encrypt_secret`, account, balances, ledger, tx status, prepare/execute transfer. |
| `program-methods.ts`, `solana-program.ts` | `wallet_prepare_program` / `wallet_execute_prepared_program`: decode, summarise, simulate, sign (port of OpenHuman's `web3::wallet::program`). |
| `convert-methods.ts`, `routes/` | `web3_swap_*`, `web3_bridge_*`, `web3_dapp_*`; `routes/{lifi,debridge,index}.ts` are `lifi.rs`, `debridge.rs`, `wallet_backend.rs`. |
| `x402.ts` | `x402_fetch`, `x402_list_payments`. |
| `policy.ts`, `ledger.ts` | the caps and the persistent spend ledger. |
| `crypto.ts`, `derive.ts`, `store.ts` | the keyring envelope, HD derivation, the wallet state and the vault that opens it. |
| `giveaway.ts`, `cli.ts` | `giveaway-tx create|close` (port of `giveaway_tx.rs`) and its command-line entry. |
| `../../scripts/migrate-keyring.ts` | the one-shot, operator-run move from OpenHuman's keyring. |

## Where things live

Everything comes from the `CoreContext` (`ctx.env`, `ctx.stateDir`), never from
`process.env`.

| What | Where |
| --- | --- |
| The wallet | `<stateDir>/wallet/wallet-state.json` (0600, dir 0700) |
| The spend ledger | `<stateDir>/wallet/spend-ledger.json` |
| x402 payments | `<stateDir>/wallet/x402-payments.json` |
| The master key | `OPENHUMAN_KEYRING_MASTER_KEY_FILE` (64 hex; or `OPENHUMAN_KEYRING_MASTER_KEY`), a file group and others cannot write |
| The policy | `TEENY_CORE_CONFIG`, else `dirname($OPENHUMAN_WORKSPACE)/.openhuman/config.toml`, else `<root>/.openhuman/config.toml`: the file `bin/lib/wallet_config.py` writes. Read fresh on every execute. |
| Cluster, RPC | `OPENHUMAN_SOLANA_CLUSTER` (devnet unless `mainnet`), `OPENHUMAN_WALLET_RPC_SOLANA`, `OPENHUMAN_WALLET_RPC_{BASE,ARBITRUM,EVM,OPTIMISM}` (the names `wallet_config.py` exports), `OPENHUMAN_WALLET_RPC_BTC` (Esplora, default blockstream.info) |
| x402 budget | `OPENHUMAN_X402_{PER_REQUEST,DAILY,MONTHLY}_MAX`, USDC atomic units. Unset or zero: every x402 payment is refused. |
| Providers | `TEENY_LIFI_API`, `LIFI_API_KEY`, `TEENY_DEBRIDGE_API`, `DEBRIDGE_API_KEY` (as the Rust spine read them) |

For P1/P5: the context has no config path, so the wiring must export
`TEENY_CORE_CONFIG` (or keep the `.openhuman/` sibling layout) for the policy to be found.
A missing config means an empty policy, which refuses every transfer.

## The keyring: what OpenHuman did, and the format here

OpenHuman, as Teeny ran it (`bin/spine`: `OPENHUMAN_KEYRING_BACKEND=file`,
`OPENHUMAN_KEYRING_MASTER_KEY_FILE=secrets/openhuman.key`, `OPENHUMAN_STORAGE_URL=sqlite:...`):

1. `encrypt_secret` sealed the recovery phrase as `enc2:<hex(nonce(12) || ciphertext || tag(16))>`,
   ChaCha20-Poly1305, no associated data, under a *user master* key kept in the keyring
   as `<user>:secretstore.master_key` (64 hex characters; with the `file` backend that
   is the plaintext `dev-keychain.json` in `OPENHUMAN_WORKSPACE`).
2. `wallet_setup` stored that `enc2:` value in the keyring as `<user>:wallet.mnemonic`.
   With a storage backend configured, the keyring put it in the `secrets` collection of
   the sqlite documents, sealed a second time with
   `HKDF-SHA256(master key file, info = "tinystoragedrivers-secrets/v1:" + scope)`.
   `wallet-state.json` kept only the public accounts (the phrase lived there only when
   no keyring was available).
3. The master key file `openhuman.key` therefore protected the documents, not the phrase directly.

**Here:** the phrase is sealed once, in the *same* `enc2` envelope (`crypto.ts` writes and
reads it byte for byte as OpenHuman's `encrypt_enc2` / `decrypt_enc2`), directly under
the master key file, and kept as `encryptedMnemonic` in `wallet-state.json`, whose
shape is OpenHuman's `StoredWalletState` (camelCase). One root, one file the operator
already protects (`secrets/openhuman.key`, 0600), no plaintext keychain file, no sqlite
secrets table. `encrypt_secret` returns that envelope, so `bin/wallet-setup` works
unchanged. `wallet_setup` refuses a ciphertext that does not open under the master
key or whose accounts do not derive from it (OpenHuman stored whatever it was given).

Keys and the seed exist only inside `withSeed`: decrypt, derive, sign, wipe the buffers.
JavaScript strings cannot be zeroed, so the phrase string lives until the GC takes it;
that is the one place this is weaker than the Rust module, and why nothing ever logs,
returns or throws it (`errors.ts` also scrubs any tracked secret out of an error).

## Methods

See the report that came with this change for the per-method status. In short: every
method in the fixtures is implemented; `x402_fetch` implements the `exact` scheme only
(no `upto`), and `signIn` is refused ("not implemented").

### Prepare and execute

- `wallet_prepare_transfer` validates and stores a quote in memory (5 minutes). Nothing is signed.
- `wallet_execute_prepared` needs `confirmed: true`, takes the quote (single use; it does not survive a restart or a replaced wallet), then **reserves the spend in the ledger under the policy before anything is signed**: a refusal never opens the key. A failure after the reservation is recorded `in_doubt` and counts as spent; a quote is never executed twice.
- `wallet_prepare_program` / `wallet_execute_prepared_program` re-simulate and re-check at execute; the quote lives 60 seconds.
- `web3_*_execute` and `web3_dapp_execute` apply no cap (as in OpenHuman: `hands/wallet` holds those caps), but are single use, expire (90s / 120s), sign only the wallet's own primary account for the four EVM networks and Solana, and are written to the ledger (`kind` swap/bridge/dapp, `policyApplied: false`, never counted against the transfer caps).

### Where this is stricter than OpenHuman

- Every RPC execute is capped. OpenHuman skipped the caps for an "interactive" execute unless `apply_to_interactive` was true; there is no interactive user here.
- A policy file that cannot be read, or has an unknown field, refuses every execute.
- `wallet_derive_recovery_material` never returns the BIP-39 seed (OpenHuman did, for its storage KDF).
- `wallet_setup` verifies the ciphertext and addresses (above), and takes an index-0 account at the giveaway path as the giveaway account: `bin/wallet-setup` copies only `chain`, `address` and `derivationPath`, which drops `index`, and OpenHuman's validation would then refuse the whole setup.

## giveaway-tx

`giveaway.ts` is `giveaway_tx.rs`. `core/test/wallet/fixtures/giveaway_tx_rust.json`
holds what the **unmodified** Rust file answered, for 31 requests (every success and
every error it has), recorded in a throwaway `rust:1.96.1` container by
`fixtures/rust-parity/run.sh`. The test compares stdout byte for byte (serde_json
prints keys sorted, so does this) and exit codes, and the error text for all but four
cases where it holds the prefix only (serde's own words for malformed JSON and an
overflowing number). The one random thing, a fresh seed keypair, is injectable
(`options.randomBytes`) and checked by signature instead. The `@solana/web3.js`
vectors in `hands/tests/giveaway/fixtures/rewards_tx.json` are checked too.

Wiring (P1 owns `main.ts`, P5 the wrapper): `giveawayTxCli(argv)` in `cli.ts` takes what follows
`giveaway-tx` and returns the exit code. In `main.ts`:
`if (argv[0] === "giveaway-tx") process.exit(await giveawayTxCli(argv.slice(1)));`
and `OPENHUMAN_CORE_BIN` (what `hands/giveaway/onchain.py` runs) becomes a wrapper that
execs `node core/dist/main.js "$@"`.

## Migrating the old keyring (the operator runs this)

With the old spine stopped, from the repo root, with Node >= 24.16:

```
node core/scripts/migrate-keyring.ts --check \
  --old-state <old workspace>/state/wallet-state.json \
  --old-workspace <old OPENHUMAN_WORKSPACE> \
  --old-master-key-file secrets/openhuman.key \
  --old-storage-db <the sqlite file of OPENHUMAN_STORAGE_URL> \
  --new-state-dir state
```

`--check` decrypts, proves the phrase by deriving the Solana address the old state
lists, prints the public addresses and writes nothing. Run it again without `--check`
to write `state/wallet/wallet-state.json`. It prints only public addresses, refuses to
overwrite an existing new store (the file is created `wx`), refuses a master key file
others can write, and the old keyring is left untouched. Leave `--old-storage-db` out
when the old core had no storage backend. The tests build a keyring in each of the
three places OpenHuman can have kept the phrase and run the script as a subprocess.

## What was not verified

- Nothing here has touched a real chain. Devnet verification (a transfer, a swap quote, a giveaway create and close) is the operator's step; every RPC and HTTP call in the tests is a mock.
- The tinywallet module's source is not in the repository (a submodule `vendor/openhuman` does not carry), so the transfer quote fields, expiry, fee policy and the x402 internals follow what the hands read and what the host side of OpenHuman shows, not the module's code.
- BTC and Tron addresses are derived (P2WPKH `bc1q...` at `m/44'/0'/0'/0/0`, Tron base58check at `m/44'/195'/0'/0/0`) because OpenHuman's setup demands all four chains; they were not compared with OpenHuman's own output for a known phrase. EVM and Solana are checked against the well-known derivations of the BIP-39 test phrase.
- DNS pinning for x402: the destination is resolved and vetted, then `fetch` resolves it again. A rebinding race is possible; OpenHuman pinned the vetted address.
