"""Shared by the hands/wallet and hands/bounty tests: a scratch TEENY_ROOT, a
fake core and a fake Solana RPC, both JSON-RPC servers on 127.0.0.1.

Nothing here talks to a real core or chain. Each fake answers by method from
a table a test fills in, and remembers every call in order, so a test can
assert which methods ran (and that none that moves funds did).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from x.support import SECRETS as X_SECRETS  # noqa: F401 - puts hands/lib on sys.path
from teeny_hands import hand

logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
HOUR = 3600.0
DAY = 86400.0

OWN = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
WINNER = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
VENDOR = "4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T"
STRANGER = "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
TINY = "2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump"
USDC_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
USDC_MAINNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

CAPS = {
    "WALLET_CLUSTER": "devnet",
    "WALLET_MAX_PER_TX_USD": "50",
    "WALLET_MAX_PER_DAY_USD": "100",
    "WALLET_MAX_PER_RECIPIENT_WEEK_USD": "60",
    "WALLET_SOL_USD": "200",
}

#: What moves funds. A dry run, or a refusal, must never send either.
MOVING = ("openhuman.wallet_prepare_transfer", "openhuman.wallet_execute_prepared")


class FakeRpc:
    """A JSON-RPC server. `answers[method]` is a value (the result), a
    callable of params (its return is the result, or it raises
    RpcFailure), or a list of those used in turn (the last repeats)."""

    def __init__(self) -> None:
        self.answers: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []
        self.delay: dict[str, float] = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.calls.append(body)
                status, reply = fake._answer(body)
                if fake.delay.get(body.get("method")):
                    time.sleep(fake.delay[body["method"]])
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def methods(self) -> list[str]:
        return [c.get("method") for c in self.calls]

    def params(self, method: str) -> list[dict[str, Any]]:
        return [c.get("params") for c in self.calls if c.get("method") == method]

    def _answer(self, body: dict[str, Any]) -> tuple[int, Any]:
        method = body.get("method")
        answer = self.answers.get(method)
        if isinstance(answer, list) and answer and isinstance(answer[0], (Failure, HttpStatus)) or callable(answer) or (
            isinstance(answer, list) and answer and callable(answer[0])
        ):
            pass
        if isinstance(answer, Sequence):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if method not in self.answers:
            return 200, {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601, "message": f"unknown method {method}"}}
        if callable(answer) and not isinstance(answer, (Failure, HttpStatus)):
            answer = answer(body.get("params"))
        if isinstance(answer, HttpStatus):
            return answer.status, {"error": "fake"}
        if isinstance(answer, Failure):
            return 200, {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32000, "message": answer.message}}
        return 200, {"jsonrpc": "2.0", "id": body.get("id"), "result": answer}


class Sequence(list):
    """Answers used in turn; the last one repeats."""


class Failure:
    def __init__(self, message: str) -> None:
        self.message = message


class HttpStatus:
    def __init__(self, status: int) -> None:
        self.status = status


def status_with(address: str = OWN) -> dict[str, Any]:
    return {
        "configured": True,
        "onboardingCompleted": True,
        "consentGranted": True,
        "secretStored": True,
        "source": "generated",
        "mnemonicWordCount": 24,
        "accounts": [
            {"chain": "evm", "address": "0x" + "1" * 40, "derivationPath": "m/44'/60'/0'/0/0"},
            {"chain": "solana", "address": address, "derivationPath": "m/44'/501'/0'/0'"},
        ],
        "updatedAtMs": 1,
    }


def balances(sol_raw: int, status: str = "ready") -> list[dict[str, Any]]:
    return [
        {"chain": "evm", "evmNetwork": "ethereum_mainnet", "address": "0x" + "1" * 40, "assetSymbol": "ETH", "decimals": 18, "raw": "0", "formatted": "0", "providerStatus": "ready"},
        {"chain": "solana", "address": OWN, "assetSymbol": "SOL", "decimals": 9, "raw": str(sol_raw), "formatted": "x", "providerStatus": status},
    ]


def token_accounts(raw: int, mint: str = USDC_DEVNET) -> dict[str, Any]:
    return {
        "context": {"slot": 1},
        "value": [
            {
                "pubkey": "TokenAcct1111111111111111111111111111111111",
                "account": {"data": {"parsed": {"info": {"mint": mint, "owner": OWN, "tokenAmount": {"amount": str(raw), "decimals": 6}}}}},
            }
        ],
    }


def quote_for(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "quoteId": "q-1",
        "kind": "token_transfer" if params.get("assetSymbol") else "native_transfer",
        "chain": "solana",
        "fromAddress": OWN,
        "toAddress": params["toAddress"],
        "assetSymbol": params.get("assetSymbol") or "SOL",
        "amountRaw": params["amountRaw"],
        "amountFormatted": "x",
        "estimatedFeeRaw": "5000",
        "status": "awaiting_confirmation",
        "createdAtMs": 1,
        "expiresAtMs": 2,
        "notes": [],
    }


def executed(params: dict[str, Any]) -> dict[str, Any]:
    return {"quoteId": params["quoteId"], "status": "broadcasted", "chain": "solana", "transactionHash": "5igSig" + "A" * 80, "transaction": {}}


class Scratch(unittest.TestCase):
    """A scratch root with the caps set, DRY_RUN off, the judge off, and the
    two fakes wired in: the core through OPENHUMAN_CORE_URL, the chain
    through SOLANA_RPC_URL."""

    settings: dict[str, str | None] = {}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "TEENY_HANDS_DEBUG"):
            os.environ.pop(key, None)
        self.at(T0)
        self.core = FakeRpc()
        self.chain = FakeRpc()
        self.addCleanup(self.core.close)
        self.addCleanup(self.chain.close)
        os.environ["OPENHUMAN_CORE_URL"] = self.core.url
        os.environ["OPENHUMAN_CORE_TOKEN"] = "core-token-FAKE"
        self.core.answers.update(
            {
                "openhuman.wallet_status": status_with(),
                "openhuman.wallet_balances": balances(10 * 10**9),
                "openhuman.wallet_prepare_transfer": quote_for,
                "openhuman.wallet_execute_prepared": executed,
                "openhuman.wallet_ledger_list": [],
            }
        )
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(1000 * 10**6)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {**CAPS, "DRY_RUN": "0", "SAFETY_JUDGE": "0", "SOLANA_RPC_URL": self.chain.url}
        values.update(self.settings)
        values.update(overrides)
        (self.root / "secrets" / "teeny.env").write_text("".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
        hand._ENV_CACHE = None

    def at(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(seconds)

    def moved(self) -> list[str]:
        return [m for m in self.core.methods() if m in MOVING]


Answer = Callable[[dict[str, Any]], Any]
