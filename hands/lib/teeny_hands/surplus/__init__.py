"""hands/surplus: Teeny's spare SOL, swept to the surplus wallet as USDC on
Base (docs/hands.md, "surplus").

  sweep.py     the pinned surplus-sweep job: bridge SOL above a reserve
               straight into USDC on Base at the surplus wallet
               (wallet.convert's pay_to, LI.FI only), and pay USDC on Base
               above the x402 rail's reserve to it (wallet.pay, purpose vendor)
  settings.py  the SURPLUS_* keys, read and checked; shared with bin/doctor

No payment path of its own: every dollar goes through hands/wallet's rules
and caps (a direct bridge is a `wallet_payments` row too), and the wallet
signs inside the core.
"""
