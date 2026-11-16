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


class ConfigCase(unittest.TestCase):
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


class WalletConfigTest(ConfigCase):
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

    def test_check_names_who_quotes_swaps_and_bridges(self):
        self.env.write_text("".join(f"{k}={v}\n" for k, v in {**CAPS, "WALLET_MAX_CONVERT_PER_DAY_USD": "20"}.items()))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(wallet_config.main(["x", "--check", str(self.env)]), 0)
        self.assertIn("swaps and bridges $20.00 a day through LI.FI (bridges) and deBridge (swaps)", out.getvalue())
        self.env.write_text("".join(f"{k}={v}\n" for k, v in {**CAPS, "WALLET_CONVERT_PROVIDER": "dln"}.items()))
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(wallet_config.main(["x", "--check", str(self.env)]), 1)
        self.assertIn("WALLET_CONVERT_PROVIDER", err.getvalue())

    def test_the_providers_api_keys_reach_the_spine_only_when_set(self):
        _, out, _, _ = self.run_it(CAPS)
        self.assertNotIn("LIFI_API_KEY", out)
        self.assertNotIn("DEBRIDGE_API_KEY", out)
        _, out, _, _ = self.run_it({**CAPS, "LIFI_API_KEY": "lk-1", "DEBRIDGE_API_KEY": "dk 2"})
        self.assertIn("export LIFI_API_KEY=lk-1\n", out)
        self.assertIn("export DEBRIDGE_API_KEY='dk 2'\n", out)


#: What the writer put in the file for wallet.support's CAPS on devnet before
#: the giveaway wallet existed. Index 0's policy must stay exactly this.
INDEX_0_DEVNET = """[wallet_policy]
automation_enabled = true
apply_to_interactive = true

[[wallet_policy.assets]]
chain = "solana"
symbol = "SOL"
per_tx_max_raw = 250000000
daily_max_raw = 500000000
recipient_weekly_max_raw = 300000000

[[wallet_policy.assets]]
chain = "solana"
symbol = "USDC"
token_address = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
per_tx_max_raw = 50000000
daily_max_raw = 100000000
recipient_weekly_max_raw = 60000000
"""

DEVNET_MINT = "9pan9bMn5HatX4EJdBwg9VgCa7Uz5HL8N1m5D3NdXejP"
REWARDS = "REWArDioXgQJ2fZKkfu9LCLjQfRwYWVVfsvcsR5hoXi"
TINY = "2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump"
GIVEAWAY = {
    "GIVEAWAYS": "1",
    "GIVEAWAY_POOL_MAX_TINY": "100000",
    "GIVEAWAY_SEASON_MAX_TINY": "250000.5",
    "GIVEAWAY_ROUNDS_PER_WEEK": "2",
    "GIVEAWAY_MAX_PER_WINNER_TINY": "40000",
    "GIVEAWAY_CLAWBACK_DAYS": "30",
    "GIVEAWAY_X_LOOKUPS_PER_ROUND": "1",
    "GIVEAWAY_CLUSTER": "devnet",
    "GIVEAWAY_DEVNET_MINT": DEVNET_MINT,
}


class GiveawayPolicyTest(ConfigCase):
    """[wallet_policy.accounts.solana_1]: the giveaway wallet's own section,
    written from hands/giveaway's caps only when they are on and sane."""

    def test_index_0_is_byte_for_byte_what_it_was_with_or_without_giveaways(self):
        self.run_it(CAPS)
        self.assertEqual(self.config.read_text(), INDEX_0_DEVNET)
        self.run_it({**CAPS, **GIVEAWAY})
        text = self.config.read_text()
        self.assertTrue(text.startswith(INDEX_0_DEVNET + "\n[wallet_policy.accounts.solana_1]\n"), text)

    def test_on_writes_the_rewards_program_and_the_round_mint_under_the_caps(self):
        code, out, err, doc = self.run_it({**CAPS, **GIVEAWAY})
        self.assertEqual((code, err), (0, ""))
        section = doc["wallet_policy"]["accounts"]["solana_1"]
        self.assertEqual(section["allowed_programs"], [REWARDS])
        self.assertEqual(section["mints"], [{"mint": DEVNET_MINT, "per_tx_max_raw": 100_000 * 10**6, "daily_max_raw": 250_000_500_000}])
        self.assertIn("export OPENHUMAN_SOLANA_CLUSTER=devnet\n", out)

    def test_mainnet_pays_tiny(self):
        env = {**CAPS, **GIVEAWAY, "WALLET_CLUSTER": "mainnet", "GIVEAWAY_CLUSTER": "mainnet"}
        _, _, _, doc = self.run_it(env)
        self.assertEqual(doc["wallet_policy"]["accounts"]["solana_1"]["mints"][0]["mint"], TINY)

    def test_off_or_unusable_writes_no_section_and_never_closes_index_0(self):
        cases = (
            ({**GIVEAWAY, "GIVEAWAYS": "0"}, None),
            ({k: v for k, v in GIVEAWAY.items() if k != "GIVEAWAYS"}, None),
            ({**GIVEAWAY, "GIVEAWAY_POOL_MAX_TINY": "0"}, "GIVEAWAY_POOL_MAX_TINY"),
            ({**GIVEAWAY, "GIVEAWAY_CLUSTER": "mainnet"}, "the core's wallet is on devnet"),
            ({**GIVEAWAY, "GIVEAWAY_DEVNET_MINT": ""}, "GIVEAWAY_DEVNET_MINT"),
        )
        for giveaway, why in cases:
            code, _, err, doc = self.run_it({**CAPS, **giveaway})
            self.assertEqual(code, 0, giveaway)
            self.assertNotIn("accounts", doc["wallet_policy"], giveaway)
            self.assertEqual(self.config.read_text(), INDEX_0_DEVNET)
            if why:
                self.assertIn(why, err)
            else:
                self.assertEqual(err, "")

    def test_a_section_left_from_before_is_replaced_or_removed(self):
        self.config.write_text('api_url = "x"\n\n[wallet_policy.accounts.solana_1]\nallowed_programs = ["Evil"]\n\n'
                               '[[wallet_policy.accounts.solana_1.mints]]\nmint = "M"\nper_tx_max_raw = 9\ndaily_max_raw = 9\n\n'
                               '[wallet_policy.accounts.solana_7]\nallowed_programs = []\n')
        _, _, _, doc = self.run_it({**CAPS, **GIVEAWAY})
        self.assertEqual(list(doc["wallet_policy"]["accounts"]), ["solana_1"])
        self.assertEqual(doc["wallet_policy"]["accounts"]["solana_1"]["allowed_programs"], [REWARDS])
        first = self.config.read_text()
        self.run_it({**CAPS, **GIVEAWAY})
        self.assertEqual(self.config.read_text(), first)
        _, _, _, doc = self.run_it(CAPS)
        self.assertNotIn("accounts", doc["wallet_policy"])
        self.assertEqual(doc["api_url"], "x")

    def test_check_names_the_giveaway_wallet_and_fails_when_it_is_on_but_broken(self):
        self.env.write_text("".join(f"{k}={v}\n" for k, v in {**CAPS, **GIVEAWAY}.items()))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(wallet_config.main(["x", "--check", str(self.env)]), 0)
        self.assertIn("giveaway wallet (solana index 1): 100000 a round", out.getvalue())
        self.env.write_text("".join(f"{k}={v}\n" for k, v in {**CAPS, **GIVEAWAY, "GIVEAWAY_CLUSTER": "mainnet"}.items()))
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(wallet_config.main(["x", "--check", str(self.env)]), 1)
        self.assertIn("GIVEAWAY_CLUSTER is mainnet", err.getvalue())
