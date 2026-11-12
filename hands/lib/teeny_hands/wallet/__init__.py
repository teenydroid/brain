"""hands/wallet: Teeny's own Solana wallet, under hard caps (docs/hands.md, "wallet").

The keys never leave the core: OpenHuman holds the encrypted recovery
phrase and signs. This hand decides whether a payment may go out (caps.py,
pay.py), keeps its own ledger, and reads balances, receipts and costs.

  caps    the caps and the mints it knows (also read by bin/lib/wallet_config.py and bin/doctor)
  solana  addresses, Solscan links, token balances (USDC, $TINY) over Solana JSON-RPC
  store   wallet_payments, bills, vendors
  pay     the rules, then prepare and execute through the core
  reads   address, balance, tiny, ledger, costs, receipts, the operator's lists
  cli     the verbs
"""
