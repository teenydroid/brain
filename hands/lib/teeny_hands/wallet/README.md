# hands/wallet

Teeny's own wallet: Solana and EVM accounts derived from one recovery phrase,
under hard caps. The keys never leave the core: OpenHuman's `web3` domain
holds the phrase and signs, and this hand decides whether to ask it to. Verbs
and flags are in [`docs/hands.md`](../../../../docs/hands.md), "wallet".

Money moves through `pay.py` (a transfer), `convert.py` (a swap or bridge
between Teeny's own accounts) and `x402.py` (a request that may pay for itself).
Each checks the caps in `caps.py`, records an attempt as `pending` in
`state/hands.db` before the core is asked, and honours `DRY_RUN`. The core
checks the same caps again from `[wallet_policy]`, which `bin/lib/wallet_config.py`
writes from `secrets/teeny.env` at every boot.

| File | What it does |
|---|---|
| `cli.py` | The verbs (reads such as `address`, `balance`, `caps`, `ledger`, `costs`; acts `pay`, `swap`, `bridge`, `x402 fetch`; jobs `buy-tiny`, `inflows scan`, `watch`, `sales run`; operator lists `bills`, `vendors`). |
| `caps.py` | The hard caps and the addresses the wallet knows by heart; read by the hand, by `bin/lib/wallet_config.py` and by the doctor so they cannot disagree. |
| `chains.py` | The pinned chains and assets, and the EVM reads. |
| `store.py` | The wallet's tables in `state/hands.db` (payments and the rest). |
| `pay.py` | `pay`: the only way the wallet moves money out. |
| `convert.py` | `swap` and `bridge` through LI.FI or deBridge. Tags the request with the provider, which `spine/src/wallet_backend.rs` reads. |
| `lifi.py` | Checks the LI.FI quote the core returns before it is signed. |
| `jupiter.py` | Jupiter's public quote API, read only (what a USDC to $TINY swap would give). |
| `buy.py` | `buy-tiny`: turn a share of what Teeny earned into $TINY. Buys only. |
| `x402.py` | `x402 fetch`: one HTTP request that pays for itself on a 402, under the caps. |
| `solana.py` | Solana addresses and the token balance reads the core does not do. |
| `reads.py` | Read verbs and the operator's lists (bills, vendors). |
| `accounts.py` | The inflow ledger and Teeny's public accounts. Moves nothing. |
| `senses.py` | On-chain senses: wallet and coin events delivered to the inbox as stimuli. Read only. |
| `sales.py` | Paid build requests: Teeny sells a build for USDC and may refund within `evals/prices.toml`. |
| `paywall.py` | The x402-priced resource Teeny serves (`bin/paywall`). |

Tests are in `hands/tests/` (the core is faked).
