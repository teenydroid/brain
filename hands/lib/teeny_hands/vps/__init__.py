"""hands/vps: Teeny's own servers on EQVPS (docs/hands.md, "vps").

EQVPS sells VPSes to agents over a REST API: an account is one bearer token,
a prepaid balance is topped up through a crypto checkout, and an order is
paid from that balance. This hand is the only holder of the token. It may
spend only what the operator's caps in secrets/teeny.env allow, it never
pays a checkout itself, and nothing it prints carries the token or a root
password.

  client   where EQVPS is, the token, the shapes it answers in
  actions  products, topup, order, service, servers, register
  cli      the verbs
"""
