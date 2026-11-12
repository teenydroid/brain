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
  WALLET_MAX_* in its own atomic units (SOL at WALLET_SOL_USD). With no
  usable caps the list is empty, and an empty list refuses every capped
  execute: the wallet cannot move anything.

Prints, for bin/spine to eval, the core's Solana settings: the cluster
(OPENHUMAN_SOLANA_CLUSTER, devnet until WALLET_CLUSTER=mainnet) and the RPC
endpoint (OPENHUMAN_WALLET_RPC_SOLANA, from SOLANA_RPC_URL), and unsets the
OPENHUMAN_WALLET_POLICY_* overrides so nothing in the environment can widen
what this file wrote. It also pins the core's x402 budget (what the wallet
may pay for HTTP 402 calls such as hands/x's twitr.sh reads and writes):
OPENHUMAN_X402_PER_REQUEST_MAX, _DAILY_MAX and _MONTHLY_MAX, in USDC atomic
units, from X402_MAX_PER_REQUEST_USD, X402_MAX_PER_DAY_USD and
X402_MAX_PER_MONTH_USD. Always exported, so the core's looser defaults ($1,
$10, $100) never apply; a value that is unset or unreadable is the default
below and one over its ceiling is the ceiling. A typo never lifts a cap. The caps logic is hands/wallet's own
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
from teeny_hands.wallet import caps  # noqa: E402

OVERRIDES = (
    "OPENHUMAN_WALLET_POLICY_AUTOMATION_ENABLED",
    "OPENHUMAN_WALLET_POLICY_APPLY_TO_INTERACTIVE",
    "OPENHUMAN_WALLET_POLICY_ASSETS",
)


#: (teeny.env key, core variable, default dollars, ceiling dollars)
X402_BUDGET = (
    ("X402_MAX_PER_REQUEST_USD", "OPENHUMAN_X402_PER_REQUEST_MAX", "0.10", "1"),
    ("X402_MAX_PER_DAY_USD", "OPENHUMAN_X402_DAILY_MAX", "2", "20"),
    ("X402_MAX_PER_MONTH_USD", "OPENHUMAN_X402_MONTHLY_MAX", "30", "200"),
)


def x402_budget(env: dict[str, str]) -> dict[str, int]:
    """The core's x402 budget in USDC atomic units (micro-dollars)."""
    budget: dict[str, int] = {}
    for key, variable, default, ceiling in X402_BUDGET:
        fallback = caps.dollars_to_micro(default, key)
        try:
            micro = caps.dollars_to_micro(env.get(key) or default, key)
        except caps.CapsError:
            micro = fallback
        if micro <= 0:
            micro = fallback
        budget[variable] = min(micro, caps.dollars_to_micro(ceiling, key))
    return budget


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


def _drop(text: str, header: str) -> str:
    """Removes every table with this header: the header line up to the next."""
    pattern = re.compile(r"^" + re.escape(header) + r"[ \t]*\n(?:(?!\[).*\n?)*", re.MULTILINE)
    return pattern.sub("", text)


def write(config: Path, loaded: caps.Caps | None) -> None:
    text = config.read_text() if config.exists() else ""
    for header in ("[[wallet_policy.assets]]", "[wallet_policy]"):
        text = _drop(text, header)
    text = text.rstrip("\n")
    text = (text + "\n\n" if text else "") + tables(loaded)
    tomllib.loads(text)  # never leave the core a file it cannot read
    config.write_text(text)


def env_lines(env: dict[str, str], cluster: str) -> str:
    lines = [f"export OPENHUMAN_SOLANA_CLUSTER={shlex.quote(cluster)}"]
    rpc = (env.get("SOLANA_RPC_URL") or "").strip()
    if rpc:
        lines.append(f"export OPENHUMAN_WALLET_RPC_SOLANA={shlex.quote(rpc)}")
    for variable, atomic in x402_budget(env).items():
        lines.append(f"export {variable}={atomic}")
    lines.append("unset " + " ".join(OVERRIDES))
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--check":
        try:
            _, detail = check(read_env(Path(argv[2])))
        except caps.CapsError as exc:
            print(f"wallet keys are set but the caps are not usable: {exc}", file=sys.stderr)
            return 1
        print(detail)
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
    write(Path(argv[1]), loaded)
    if failure is not None:
        print(f"wallet keys are set but the caps are not usable: {failure}; the wallet policy refuses everything", file=sys.stderr)
        return 1
    sys.stdout.write(env_lines(env, cluster))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
