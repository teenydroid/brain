"""hands/vps: Teeny's own servers on Vultr (docs/hands.md, "vps").

Vultr bills servers hourly against the account's credit; the operator opens
the account, funds it (card, PayPal, or crypto through BitPay) and makes the
API key. This hand is the only holder of that key. It may buy only what the
operator's caps in secrets/teeny.env allow, and nothing it prints carries
the key or a root password.

  client   where Vultr is, the key, secrets kept out of answers
  actions  plans, account, servers, server, order, destroy, release
  cli      the verbs
"""
