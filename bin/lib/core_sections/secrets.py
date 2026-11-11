"""How the core keeps the secrets in its own config (`[secrets]`).

  [secrets]  encrypt = false: the model key sits in .openhuman/config.toml
             (mode 600, outside the brain) the way it already sits in
             secrets/spine.env.

Why not encrypted: the core decrypts a config secret only after a person
approves unlocking it, on every config load, and a headless spine has nobody
to ask. With encryption on, every load parks for that approval, times out,
and every agent turn reaches the model without its key (HTTP 401) or fails
with "Config loading timed out". The key is no more exposed this way: the
same key is already in plain text in secrets/spine.env, which bin/spine
reads on every boot.

This only governs secret fields in config.toml. The keyring
(brain/dev-keychain.json, encrypted under secrets/openhuman.key) is a
separate store and this setting does not touch it.

Turn it back on once OpenHuman can unlock config secrets unattended with the
operator's master key file (OPENHUMAN_KEYRING_MASTER_KEY_FILE, which bin/spine
sets).
"""

from __future__ import annotations

from pathlib import Path


def tables(root: Path) -> dict[str, str]:
    del root
    return {"[secrets]": "encrypt = false\n"}
