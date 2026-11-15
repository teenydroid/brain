"""hands/dripsplan: plan and validate a Drips funding config, offline.

v1 is config-layer only: it reads a config, validates it, and emits the
ordered call plan a signer would run. It never signs, never touches the
network, and never reads a key. Drips facts beyond this file (npm package
name, split-weight unit) are unverified, so the SDK stays out by design.

  config   read and validate a Drips config (YAML or JSON)
  plan     the ordered calls: create the Drip List, set splits, fund
  cli      validate, emit
"""
