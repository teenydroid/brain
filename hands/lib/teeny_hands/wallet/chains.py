"""The chains and assets the wallet knows, pinned, and the EVM reads.

One recovery phrase, held by the core, derives every account (bin/wallet-setup
stores them all): one Solana address and one EVM address, the same on every
EVM network. The hand pays, swaps and bridges only between the assets below.
A token that is not pinned here is not one Teeny holds, whatever a body or a
quote calls it.

  solana     SOL, USDC          (devnet or mainnet, WALLET_CLUSTER)
  base       ETH, USDC          (mainnet only)
  arbitrum   ETH, USDC
  ethereum   ETH, USDC
  optimism   ETH, USDC

EVM networks exist for the wallet only on mainnet: under WALLET_CLUSTER=devnet
every EVM payment, swap and bridge is refused (rule `cluster`), and neither
swap provider (LI.FI, deBridge) quotes devnet.

The core reports native ETH; USDC on an EVM network is read here with one
`eth_call` (`balanceOf`) to EVM_RPC_URL_<NETWORK> from secrets/teeny.env,
else the network's public endpoint. The same URL is given to the core as
OPENHUMAN_WALLET_RPC_<NETWORK> (bin/lib/wallet_config.py). An endpoint URL can
carry a provider's API key, so no error here names it.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .. import hand

SOLANA = "solana"


@dataclass(frozen=True)
class Network:
    key: str
    evm_network: str | None  # the core's evmNetwork, None for Solana
    debridge_id: int
    native: str
    usdc: dict[str, str]  # cluster -> token address
    rpc_default: str
    explorer: str
    env: str  # the RPC setting: hands read it, the core gets it as OPENHUMAN_WALLET_RPC_*
    core_env: str


#: The address LI.FI and deBridge both give a chain's native asset.
EVM_NATIVE = "0x0000000000000000000000000000000000000000"
SOL_NATIVE = "11111111111111111111111111111111"

NETWORKS: dict[str, Network] = {
    "base": Network(
        "base", "base_mainnet", 8453, "ETH", {"mainnet": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"},
        "https://mainnet.base.org", "https://basescan.org", "EVM_RPC_URL_BASE", "OPENHUMAN_WALLET_RPC_BASE",
    ),
    "arbitrum": Network(
        "arbitrum", "arbitrum_one", 42161, "ETH", {"mainnet": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"},
        "https://arb1.arbitrum.io/rpc", "https://arbiscan.io", "EVM_RPC_URL_ARBITRUM", "OPENHUMAN_WALLET_RPC_ARBITRUM",
    ),
    "ethereum": Network(
        "ethereum", "ethereum_mainnet", 1, "ETH", {"mainnet": "0xA0b86991c6218b36c1d19D4a2E9Eb0cE3606eB48"},
        "https://ethereum-rpc.publicnode.com", "https://etherscan.io", "EVM_RPC_URL_ETHEREUM", "OPENHUMAN_WALLET_RPC_EVM",
    ),
    "optimism": Network(
        "optimism", "optimism_mainnet", 10, "ETH", {"mainnet": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85"},
        "https://mainnet.optimism.io", "https://optimistic.etherscan.io", "EVM_RPC_URL_OPTIMISM", "OPENHUMAN_WALLET_RPC_OPTIMISM",
    ),
}

CHAINS = (SOLANA, *NETWORKS)

#: deBridge's chain id for Solana.
DEBRIDGE_SOLANA = 7565164

#: The contracts a deBridge EVM transaction may be sent to (and so the only
#: spenders an ERC-20 approval may name): DlnSource, for a DLN bridge order,
#: and the deBridge router a same-chain swap goes through. Anything else in
#: `tx.to` is refused before a signature.
DEBRIDGE_EVM_CONTRACTS = frozenset(
    {
        "0xef4fb24ad0916217251f553c0596f8edc630eb66",  # DlnSource
        "0x663dc15d3c1ac63ff12e45ab68fea3f0a883c251",  # DeBridgeRouter (crosschain forwarder)
    }
)

DECIMALS = {"SOL": 9, "ETH": 18, "USDC": 6}

_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
TIMEOUT_SECONDS = 20.0


def is_evm(chain: str) -> bool:
    return chain in NETWORKS


def assets(chain: str) -> tuple[str, ...]:
    return ("SOL", "USDC") if chain == SOLANA else (NETWORKS[chain].native, "USDC")


def is_evm_address(text: object) -> bool:
    """0x and 40 hex digits, and not the zero address. Mixed case is taken as
    written: the core checks an EIP-55 checksum when it builds the transfer."""
    return isinstance(text, str) and bool(_EVM_ADDRESS.fullmatch(text)) and int(text, 16) != 0


def same_address(chain: str, a: object, b: object) -> bool:
    """EVM addresses compare without case; Solana's are case-sensitive."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return a.lower() == b.lower() if is_evm(chain) else a == b


def evm_tokens() -> set[str]:
    """Every pinned EVM token contract, lowercased: never a wallet."""
    return {address.lower() for n in NETWORKS.values() for address in n.usdc.values()}


def token_address(chain: str, asset: str, cluster: str, usdc_solana: dict[str, str]) -> str:
    """The providers' address for `asset` on `chain`: the native sentinel or
    the pinned token."""
    if chain == SOLANA:
        return SOL_NATIVE if asset == "SOL" else usdc_solana[cluster]
    network = NETWORKS[chain]
    return EVM_NATIVE if asset == network.native else network.usdc["mainnet"]


def debridge_id(chain: str) -> int:
    return DEBRIDGE_SOLANA if chain == SOLANA else NETWORKS[chain].debridge_id


def explorer_tx(chain: str, signature: str, cluster: str) -> str:
    if chain == SOLANA:
        url = f"https://solscan.io/tx/{signature}"
        return url + ("?cluster=devnet" if cluster == "devnet" else "")
    return f"{NETWORKS[chain].explorer}/tx/{signature}"


def explorer_account(chain: str, address: str, cluster: str) -> str:
    if chain == SOLANA:
        url = f"https://solscan.io/account/{address}"
        return url + ("?cluster=devnet" if cluster == "devnet" else "")
    return f"{NETWORKS[chain].explorer}/address/{address}"


# --- EVM reads ----------------------------------------------------------------------------


def rpc_url(chain: str) -> str:
    network = NETWORKS[chain]
    return hand.setting(network.env) or network.rpc_default


def evm_rpc(chain: str, method: str, params: list[Any]) -> Any:
    """One JSON-RPC call to the network's endpoint; its `result`. Errors name
    the network and the method, never the URL."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(
        rpc_url(chain), data=body, headers={"content-type": "application/json", "user-agent": "teeny-hands-wallet"}, method="POST"
    )
    failure: str | None = None
    payload: Any = None
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        failure = f"{chain} RPC {method} answered HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        failure = f"could not reach {chain} RPC for {method} ({type(getattr(exc, 'reason', exc)).__name__})"
    if failure is not None:
        raise hand.HandError(failure)
    if not isinstance(payload, dict):
        raise hand.HandError(f"{chain} RPC {method} sent something unreadable")
    if payload.get("error"):
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else error
        raise hand.HandError(f"{chain} RPC {method}: {str(message)[:200]}")
    return payload.get("result")


def erc20_balance(chain: str, token: str, owner: str) -> int:
    """`balanceOf(owner)` on `token`, in atomic units."""
    data = "0x70a08231" + owner.lower().removeprefix("0x").rjust(64, "0")
    result = evm_rpc(chain, "eth_call", [{"to": token, "data": data}, "latest"])
    if not isinstance(result, str) or not result.startswith("0x"):
        raise hand.HandError(f"{chain} RPC eth_call sent no balance")
    try:
        return int(result, 16) if result != "0x" else 0
    except ValueError:
        raise hand.HandError(f"{chain} RPC eth_call sent a balance it could not read") from None


def approve_calldata(spender: str, amount: int) -> str:
    """ERC-20 `approve(spender, amount)`: exactly `amount`, never unlimited."""
    if not is_evm_address(spender) or not 0 <= amount < 2**256:
        raise hand.HandError("an approval needs a spender address and an amount")
    return "0x095ea7b3" + spender.lower().removeprefix("0x").rjust(64, "0") + format(amount, "064x")
