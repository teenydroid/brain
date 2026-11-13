"""hands/wallet: Teeny's own wallet, Solana and EVM from one recovery phrase,
under hard caps (docs/hands.md, "wallet").

The keys never leave the core: OpenHuman holds the encrypted recovery
phrase and signs on every chain. This hand decides whether a payment, a
swap, a bridge or a paid request may go out (caps.py, pay.py, convert.py,
x402.py), keeps its own ledger, and reads balances, receipts and costs.

  caps    the caps and the mints it knows (also read by bin/lib/wallet_config.py and bin/doctor)
  chains  the chains and assets it knows (pinned), EVM addresses and reads
  solana  addresses, Solscan links, token balances (USDC, $TINY) over Solana JSON-RPC
  store   wallet_payments, bills, vendors
  pay     the rules, then prepare and execute through the core
  convert swap and bridge between its own accounts, through LI.FI (or deBridge)
  lifi    the LI.FI quote checks convert runs before anything is signed
  x402    one HTTP request that may pay for itself, under its own caps
  buy     buy-tiny: a share of net inflows into $TINY, buy only
  jupiter Jupiter's public quote API, read only
  reads   address, balance, tiny, ledger, costs, receipts, the operator's lists
  cli     the verbs
"""
