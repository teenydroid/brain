"""hands/keychain: Teeny's site passwords, sealed (docs/hands.md, "keychain").
Frozen to Teeny.

  store  the `keychain` table in state/keychain.db: create, list, open
  cli    the verbs

A password is made here, sealed with AES-256-GCM under KEYCHAIN_KEY
(secrets/keychain.env) and typed only by the browser, in place of the
operator agent's `{{keychain:<site>}}` placeholder, on that site's own pages
(bin/keychain-fill, OpenHuman's `[browser] keychain_command`). No model, log,
ledger or prompt ever holds one.
"""
