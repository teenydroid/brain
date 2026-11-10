"""hands/moltbook: the only holder of Teeny's Moltbook key (docs/hands.md, "moltbook").

Moltbook is a Reddit-like network for AI agents (https://www.moltbook.com,
API described by its skill.md, v1.12.0).

  client     the API base (always www) and the transport, which never follows a redirect
  challenge  the verification challenge, answered only when it reads with confidence
  reads      the read verbs, through the read cache (`moltbook_cache`)
  rules      the ledger, Moltbook's published limits kept locally, our own caps,
             collaboration consent, the crypto rule, and the verification lockout
  actions    the only path to a write: guard, rules, DRY_RUN, Moltbook, the ledger
  cli        the verbs
"""
