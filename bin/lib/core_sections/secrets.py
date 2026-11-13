"""How the core keeps the secrets in its own config (`[secrets]`).

  [secrets]  encrypt = false: the model key sits in .openhuman/config.toml
             (mode 600, outside the brain) the way it already sits in
             secrets/spine.env.
             unattended_unlock = true: the core answers its own config
             secret master grant ("Unlock configuration secrets").

Why unattended: TinySecurity releases the config secret master only against a
one-time grant a person approves, and a headless spine has nobody to ask. The
grant parks, times out after about 30 s and is refused, and whatever needed it
fails: `bin/wallet-setup` could not encrypt the new recovery phrase
(`encrypt_secret`: "the approval to unlock configuration secrets was not
answered in time"). OpenHuman honours `unattended_unlock` only when the
operator supplied the keyring master key to a file keyring, which bin/spine
does (OPENHUMAN_KEYRING_MASTER_KEY_FILE=secrets/openhuman.key,
OPENHUMAN_KEYRING_BACKEND=file): whoever can read that key can already decrypt
the keyring, so a person approving each unlock adds nothing here. The grant
still goes through the core's gate and is audited as before.

Why not encrypted: until unattended unlock existed, every config load with
encryption on parked for that approval, and every agent turn reached the
model without its key (HTTP 401) or failed with "Config loading timed out".
The key is no more exposed this way: the same key is already in plain text in
secrets/spine.env, which bin/spine reads on every boot. Turning `encrypt` back
on is now possible and is a separate change.

This only governs secret fields in config.toml and the master grant. The
keyring (brain/dev-keychain.json, encrypted under secrets/openhuman.key) is a
separate store and this setting does not touch it.
"""

from __future__ import annotations

from pathlib import Path


def tables(root: Path) -> dict[str, str]:
    del root
    return {"[secrets]": "encrypt = false\nunattended_unlock = true\n"}
