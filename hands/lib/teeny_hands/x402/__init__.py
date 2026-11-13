"""hands/x402: Teeny's x402 rail (docs/hands.md, "x402").

Two jobs on top of hands/wallet, both in-process and neither with a payment
path of its own:

  fund.py      keeps USDC on Base above a floor, converting from the pinned
               SOL, ETH and USDC only through wallet.convert (every swap and
               bridge rule and cap still applies)
  search.py    web search and page contents from Exa (and Firecrawl's x402
               search, off by default), every request through wallet.x402.fetch
               (the host allowlist, the x402 caps, the x402_requests ledger)
  settings.py  the X402_* keys, read and checked; shared with bin/doctor

No key is held here: x402 pays per request from the wallet, and the wallet
signs inside the core.
"""
