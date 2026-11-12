"""bin/lib/wallet_config.py: the core's [wallet_policy] from the hand's own
caps, apply_to_interactive always on, closed when the caps are not usable."""

from __future__ import annotations

import importlib.util
import io
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from wallet.support import CAPS, USDC_DEVNET, USDC_MAINNET

SCRIPT = Path(__file__).resolve().parents[3] / "bin" / "lib" / "wallet_config.py"
spec = importlib.util.spec_from_file_location("wallet_config", SCRIPT)
wallet_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wallet_config)


class WalletConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.config = self.dir / "config.toml"
        self.env = self.dir / "teeny.env"

    def run_it(self, env: dict[str, str]):
        self.env.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = wallet_config.main(["wallet_config.py", str(self.config), str(self.env)])
        return code, out.getvalue(), err.getvalue(), tomllib.loads(self.config.read_text())

    def test_caps_become_a_capped_policy_that_applies_to_rpc_executes(self):
        code, out, _, doc = self.run_it(CAPS)
        self.assertEqual(code, 0)
        policy = doc["wallet_policy"]
        self.assertIs(policy["apply_to_interactive"], True)
        self.assertIs(policy["automation_enabled"], True)
        sol, usdc = policy["assets"]
        self.assertEqual((sol["chain"], sol["symbol"]), ("solana", "SOL"))
        self.assertNotIn("token_address", sol)
        # $50 / $100 / $60 at $200 a SOL, in lamports, rounded down.
        self.assertEqual((sol["per_tx_max_raw"], sol["daily_max_raw"], sol["recipient_weekly_max_raw"]), (250_000_000, 500_000_000, 300_000_000))
        self.assertEqual((usdc["symbol"], usdc["token_address"]), ("USDC", USDC_DEVNET))
        self.assertEqual((usdc["per_tx_max_raw"], usdc["daily_max_raw"], usdc["recipient_weekly_max_raw"]), (50_000_000, 100_000_000, 60_000_000))
        self.assertIn("export OPENHUMAN_SOLANA_CLUSTER=devnet\n", out)
        self.assertIn("unset OPENHUMAN_WALLET_POLICY_AUTOMATION_ENABLED OPENHUMAN_WALLET_POLICY_APPLY_TO_INTERACTIVE OPENHUMAN_WALLET_POLICY_ASSETS", out)
        self.assertNotIn("OPENHUMAN_WALLET_RPC_SOLANA", out)

    def test_mainnet_pins_mainnet_usdc_and_passes_the_rpc(self):
        code, out, _, doc = self.run_it({**CAPS, "WALLET_CLUSTER": "mainnet", "SOLANA_RPC_URL": "https://rpc.example/?api-key=k k"})
        self.assertEqual(code, 0)
        self.assertEqual(doc["wallet_policy"]["assets"][1]["token_address"], USDC_MAINNET)
        self.assertIn("export OPENHUMAN_SOLANA_CLUSTER=mainnet\n", out)
        self.assertIn("export OPENHUMAN_WALLET_RPC_SOLANA='https://rpc.example/?api-key=k k'\n", out)

    def test_no_wallet_keys_is_a_closed_policy(self):
        code, out, _, doc = self.run_it({"DRY_RUN": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(doc["wallet_policy"], {"automation_enabled": False, "apply_to_interactive": True, "assets": []})
        self.assertIn("export OPENHUMAN_SOLANA_CLUSTER=devnet", out)

    def test_unusable_caps_write_a_closed_policy_and_fail(self):
        for broken in ({**CAPS, "WALLET_MAX_PER_DAY_USD": "0"}, {**CAPS, "WALLET_SOL_USD": ""}, {**CAPS, "WALLET_MAX_PER_TX_USD": "100000"}):
            code, out, err, doc = self.run_it(broken)
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("not usable", err)
            self.assertEqual(doc["wallet_policy"]["assets"], [])
            self.assertIs(doc["wallet_policy"]["automation_enabled"], False)

    def test_replaces_its_tables_and_keeps_the_rest(self):
        self.config.write_text(
            'api_url = "x"\n\n[wallet_policy]\nautomation_enabled = true\napply_to_interactive = false\n\n'
            '[[wallet_policy.assets]]\nchain = "solana"\nsymbol = "BONK"\nper_tx_max_raw = 1\n\n'
            '[[wallet_policy.assets]]\nchain = "evm"\nsymbol = "ETH"\n\n[inbound.sources."x:mentions"]\nagent_id = "teeny-mentions"\n'
        )
        _, _, _, doc = self.run_it(CAPS)
        self.assertEqual(doc["api_url"], "x")
        self.assertEqual(doc["inbound"]["sources"]["x:mentions"]["agent_id"], "teeny-mentions")
        self.assertEqual([a["symbol"] for a in doc["wallet_policy"]["assets"]], ["SOL", "USDC"])
        self.assertIs(doc["wallet_policy"]["apply_to_interactive"], True)
        # Twice is the same as once.
        first = self.config.read_text()
        self.run_it(CAPS)
        self.assertEqual(self.config.read_text(), first)

    def test_check(self):
        self.env.write_text("WALLET_CLUSTER=devnet\n")
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(wallet_config.main(["x", "--check", str(self.env)]), 1)
        self.assertIn("WALLET_MAX_PER_TX_USD is not set", err.getvalue())
        self.env.write_text("".join(f"{k}={v}\n" for k, v in CAPS.items()))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(wallet_config.main(["x", "--check", str(self.env)]), 0)
        self.assertIn("$50.00 a payment", out.getvalue())
