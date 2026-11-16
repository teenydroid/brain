"""The core's wallet settings, from secrets/teeny.env, written before a boot.

    python3 bin/lib/wallet_config.py CONFIG_TOML TEENY_ENV   write, print the env lines
    python3 bin/lib/wallet_config.py --check TEENY_ENV       bin/doctor: are the caps sane?

Writes `[wallet_policy]` and its `[[wallet_policy.assets]]` into the core's
config.toml, replacing them whole (everything else in the file, which the
core writes itself, is left as it is):

- `apply_to_interactive = true`. Every execute that reaches the core over
  JSON-RPC is "interactive" to it, and an interactive execute is uncapped
  unless this is set. hands/wallet executes over JSON-RPC, so without it the
  core's caps would not apply to Teeny's payments at all.
- `automation_enabled = true` only when the caps are set and sane; the
  assets are SOL and USDC (its mint pinned for the cluster), each capped at
  WALLET_MAX_* in its own atomic units (SOL at WALLET_SOL_USD); on mainnet
  also USDC on each EVM network the hand knows (contract pinned) and, with
  WALLET_ETH_USD set, ETH there. With no
  usable caps the list is empty, and an empty list refuses every capped
  execute: the wallet cannot move anything.

Then, only when GIVEAWAYS is on and the GIVEAWAY_* caps are sane and name the
core's cluster, `[wallet_policy.accounts.solana_1]`: what the giveaway wallet
(Solana index 1) may sign through `wallet.prepare_program`, from
hands/giveaway's own caps (`teeny_hands/giveaway/caps.py`, `policy_account`):
the Rewards program alone, and the round's mint at GIVEAWAY_POOL_MAX_TINY a
transaction and GIVEAWAY_SEASON_MAX_TINY a day, in atomic units. Without the
section the core refuses every transaction index 1 is asked to sign. The
index-0 tables above are written exactly as they are without it (a test pins
them byte for byte), and a giveaway problem never closes them: it is said on
stderr and `--check`, and only the section is left out.

Prints, for bin/spine to eval, the core's Solana settings: the cluster
(OPENHUMAN_SOLANA_CLUSTER, devnet until WALLET_CLUSTER=mainnet) and the RPC
endpoints (OPENHUMAN_WALLET_RPC_SOLANA from SOLANA_RPC_URL, and each
OPENHUMAN_WALLET_RPC_<NETWORK> from EVM_RPC_URL_<NETWORK>); the x402
module's own budget (OPENHUMAN_X402_PER_REQUEST_MAX, _DAILY_MAX, _MONTHLY_MAX)
pinned to WALLET_MAX_X402_* in USDC atomic units, or to one atomic unit when
x402 is off, so the module refuses what the hand would; LIFI_API_KEY and
DEBRIDGE_API_KEY when set, for the spine's swap and bridge quotes (each only
raises that provider's rate limit); and unsets the
OPENHUMAN_WALLET_POLICY_* overrides so nothing in the environment can widen
what this file wrote. The caps logic is hands/wallet's own
(teeny_hands/wallet/caps.py), so the hand and the core cannot disagree.

Exits 1 when wallet keys are set but the caps are not usable (the file is
still written, closed), and `--check` exits 1 for the same reason.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hands" / "lib"))

from teeny_hands.hand import _parse_env_file  # noqa: E402
from teeny_hands.giveaway import caps as giveaway_caps  # noqa: E402
from teeny_hands.wallet import caps, chains  # noqa: E402

OVERRIDES = (
    "OPENHUMAN_WALLET_POLICY_AUTOMATION_ENABLED",
    "OPENHUMAN_WALLET_POLICY_APPLY_TO_INTERACTIVE",
    "OPENHUMAN_WALLET_POLICY_ASSETS",
)


def read_env(path: Path) -> dict[str, str]:
    try:
        return _parse_env_file(path.read_text())
    except FileNotFoundError:
        return {}


def check(env: dict[str, str]) -> tuple[caps.Caps | None, str]:
    """(caps, detail): caps None when the wallet is not configured or its
    caps are not usable; detail says which."""
    if not caps.configured(env):
        return None, "no wallet keys set; the wallet cannot move anything"
    loaded = caps.load(env)  # raises CapsError
    return loaded, (
        f"{loaded.cluster}: ${caps.usd(loaded.per_tx_micro)} a payment, ${caps.usd(loaded.per_day_micro)} a day,"
        f" ${caps.usd(loaded.recipient_week_micro)} per recipient a week, SOL at ${caps.usd(loaded.sol_usd_micro)}"
        + (f", ETH at ${caps.usd(loaded.eth_usd_micro)}" if loaded.eth_usd_micro is not None else "")
        + (f", swaps and bridges ${caps.usd(loaded.convert_day_micro)} a day through {loaded.convert_provider or 'LI.FI (bridges) and deBridge (swaps)'}"
           if loaded.convert_day_micro is not None else "")
        + (f", x402 ${caps.usd(loaded.x402_request_micro)} a request and ${caps.usd(loaded.x402_day_micro)} a day"
           if loaded.x402_request_micro is not None else "")
    )


def _toml(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))


def tables(loaded: caps.Caps | None) -> str:
    out = "[wallet_policy]\n"
    out += f"automation_enabled = {_toml(loaded is not None)}\n"
    out += "apply_to_interactive = true\n"
    if loaded is None:
        out += "assets = []\n"
        return out
    for asset in caps.policy_assets(loaded):
        out += "\n[[wallet_policy.assets]]\n"
        out += "".join(f"{key} = {_toml(value)}\n" for key, value in asset.items())
    return out


def account_tables(section: dict | None) -> str:
    """`[wallet_policy.accounts.solana_1]` and its mints, or nothing."""
    if section is None:
        return ""
    name = f"wallet_policy.accounts.{giveaway_caps.POLICY_ACCOUNT}"
    out = f"\n[{name}]\n"
    out += "allowed_programs = [" + ", ".join(_toml(p) for p in section["allowed_programs"]) + "]\n"
    for mint in section["mints"]:
        out += f"\n[[{name}.mints]]\n"
        out += "".join(f"{key} = {_toml(value)}\n" for key, value in mint.items())
    return out


#: Every per-account table, whatever the account: the writer owns them all.
_ACCOUNT_TABLES = re.compile(r"^\[\[?wallet_policy\.accounts\.[^\]\n]*\]\]?[ \t]*\n(?:(?!\[).*\n?)*", re.MULTILINE)


def _drop(text: str, header: str) -> str:
    """Removes every table with this header: the header line up to the next."""
    pattern = re.compile(r"^" + re.escape(header) + r"[ \t]*\n(?:(?!\[).*\n?)*", re.MULTILINE)
    return pattern.sub("", text)


def write(config: Path, loaded: caps.Caps | None, giveaway: dict | None = None) -> None:
    text = config.read_text() if config.exists() else ""
    text = _ACCOUNT_TABLES.sub("", text)
    for header in ("[[wallet_policy.assets]]", "[wallet_policy]"):
        text = _drop(text, header)
    text = text.rstrip("\n")
    text = (text + "\n\n" if text else "") + tables(loaded) + account_tables(giveaway)
    tomllib.loads(text)  # never leave the core a file it cannot read
    config.write_text(text)


def giveaway_section(env: dict[str, str], cluster: str) -> tuple[dict | None, str]:
    """(section, detail). CapsError when GIVEAWAYS is on and the section
    cannot be written."""
    section = giveaway_caps.policy_account(env, cluster)
    if section is None:
        return None, "giveaways off; the giveaway wallet signs nothing"
    mint = section["mints"][0]
    return section, (f"giveaway wallet (solana index 1): {giveaway_caps.display(mint['per_tx_max_raw'])} a round and"
                     f" {giveaway_caps.display(mint['daily_max_raw'])} a day of {mint['mint']}, through the Rewards program only")


def x402_budget(loaded: caps.Caps | None) -> tuple[int, int, int]:
    """The module's x402 budget, in USDC atomic units: the hand's caps, or a
    single unit (the module reads zero as unset) when x402 is off."""
    if loaded is None or loaded.x402_request_micro is None or loaded.x402_day_micro is None:
        return 1, 1, 1
    return loaded.x402_request_micro, loaded.x402_day_micro, loaded.x402_day_micro * 31


def env_lines(env: dict[str, str], cluster: str, loaded: caps.Caps | None = None) -> str:
    lines = [f"export OPENHUMAN_SOLANA_CLUSTER={shlex.quote(cluster)}"]
    rpc = (env.get("SOLANA_RPC_URL") or "").strip()
    if rpc:
        lines.append(f"export OPENHUMAN_WALLET_RPC_SOLANA={shlex.quote(rpc)}")
    for network in chains.NETWORKS.values():
        url = (env.get(network.env) or "").strip()
        if url:
            lines.append(f"export {network.core_env}={shlex.quote(url)}")
    # The spine's swap and bridge providers (spine/src/lifi.rs, debridge.rs):
    # an optional key only raises the provider's rate limit.
    for key in ("LIFI_API_KEY", "DEBRIDGE_API_KEY"):
        value = (env.get(key) or "").strip()
        if value:
            lines.append(f"export {key}={shlex.quote(value)}")
    per_request, daily, monthly = x402_budget(loaded)
    lines.append(f"export OPENHUMAN_X402_PER_REQUEST_MAX={per_request}")
    lines.append(f"export OPENHUMAN_X402_DAILY_MAX={daily}")
    lines.append(f"export OPENHUMAN_X402_MONTHLY_MAX={monthly}")
    lines.append("unset " + " ".join(OVERRIDES))
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--check":
        env = read_env(Path(argv[2]))
        try:
            _, detail = check(env)
        except caps.CapsError as exc:
            print(f"wallet keys are set but the caps are not usable: {exc}", file=sys.stderr)
            return 1
        try:
            _, giveaway = giveaway_section(env, caps.cluster(env))
        except (caps.CapsError, giveaway_caps.CapsError) as exc:
            print(f"GIVEAWAYS is on but the giveaway wallet's policy cannot be written: {exc}", file=sys.stderr)
            return 1
        print(f"{detail}; {giveaway}")
        return 0
    if len(argv) != 3:
        print("usage: wallet_config.py CONFIG_TOML TEENY_ENV | --check TEENY_ENV", file=sys.stderr)
        return 2
    env = read_env(Path(argv[2]))
    failure = None
    try:
        loaded, _ = check(env)
    except caps.CapsError as exc:
        loaded, failure = None, exc
    try:
        cluster = caps.cluster(env)
    except caps.CapsError as exc:
        cluster, failure = "devnet", failure or exc
    try:
        giveaway, _ = giveaway_section(env, cluster)
    except giveaway_caps.CapsError as exc:
        giveaway = None
        print(f"GIVEAWAYS is on but the giveaway wallet's policy cannot be written: {exc}; it signs nothing", file=sys.stderr)
    write(Path(argv[1]), loaded, giveaway)
    if failure is not None:
        print(f"wallet keys are set but the caps are not usable: {failure}; the wallet policy refuses everything", file=sys.stderr)
        return 1
    sys.stdout.write(env_lines(env, cluster, loaded))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
