"""hands/keychain: passwords sealed with AES-256-GCM under the key in
secrets/keychain.env, never printed except to the browser's fill (`secret`,
on a pipe) or to a person's terminal (`reveal`), and never in a ledger."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent.parent / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import hand  # noqa: E402
from teeny_hands.keychain import store  # noqa: E402

REPO = HERE.parents[2]
KEYCHAIN = REPO / "hands" / "keychain"
T0 = 1_789_369_200.0


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        (self.root / "deploy").mkdir()
        (self.root / "deploy" / "browser-deny.txt").write_text((REPO / "deploy" / "browser-deny.txt").read_text())
        (self.root / "deploy" / "browser-allow.txt").write_text((REPO / "deploy" / "browser-allow.txt").read_text())
        self.key = base64.b64encode(os.urandom(32)).decode()
        (self.root / "secrets" / "keychain.env").write_text(f"KEYCHAIN_KEY={self.key}\n")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def run_keychain(self, *argv, tty=False):
        return subprocess.run([sys.executable, str(KEYCHAIN), *argv], capture_output=True, text=True,
                              env={**os.environ}, stdin=subprocess.DEVNULL)

    def password(self, site, handle=None):
        return store._open(site, handle)[1]  # noqa: SLF001


class StoreTest(Scratch):
    def test_create_seals_a_strong_password_and_answers_no_secret(self):
        out = store.create("Example.com", "teeny")
        self.assertEqual(out, {"site": "example.com", "handle": "teeny", "created": True})
        word = self.password("example.com")
        self.assertGreaterEqual(len(word), 20)
        for kind in (str.islower, str.isupper, str.isdigit):
            self.assertTrue(any(kind(c) for c in word))
        raw = (self.root / "state" / "keychain.db").read_bytes()
        self.assertNotIn(word.encode(), raw)
        self.assertEqual(oct((self.root / "state" / "keychain.db").stat().st_mode & 0o777), "0o600")

    def test_creating_twice_keeps_the_first_password(self):
        store.create("example.com", "teeny")
        first = self.password("example.com")
        with self.assertRaises(hand.Refused):
            store.create("example.com", "teeny")
        self.assertEqual(self.password("example.com"), first)

    def test_a_row_moved_to_another_site_or_another_key_does_not_open(self):
        store.create("example.com", "teeny")
        conn = sqlite3.connect(self.root / "state" / "keychain.db")
        conn.execute("UPDATE keychain SET site = 'evil.io'")
        conn.commit()
        conn.close()
        with self.assertRaises(hand.HandError):
            self.password("evil.io")
        store.create("other.org", "teeny")
        (self.root / "secrets" / "keychain.env").write_text(
            "KEYCHAIN_KEY=" + base64.b64encode(os.urandom(32)).decode() + "\n")
        with self.assertRaises(hand.HandError):
            self.password("other.org")

    def test_no_key_or_a_bad_key_refuses_and_the_environment_cannot_hand_one_in(self):
        (self.root / "secrets" / "keychain.env").unlink()
        os.environ["KEYCHAIN_KEY"] = self.key
        with self.assertRaises(hand.HandError):
            store.create("example.com", "teeny")
        (self.root / "secrets" / "keychain.env").write_text("KEYCHAIN_KEY=short\n")
        with self.assertRaises(hand.HandError):
            store.create("example.com", "teeny")

    def test_the_browser_gets_the_password_of_the_site_the_page_host_is_on(self):
        store.create("example.com", "teeny")
        word = self.password("example.com")
        self.assertEqual(store.secret_for("accounts.example.com"), word)
        for host in ("example.com.evil.io", "notexample.com", "evil.io"):
            with self.subTest(host=host), self.assertRaises(hand.Refused):
                store.secret_for(host)

    def test_bad_sites_and_handles(self):
        for site, handle in (("localhost", "teeny"), ("example.com", "has spaces"), ("", "teeny")):
            with self.subTest(site=site, handle=handle), self.assertRaises(hand.HandError):
                store.create(site, handle)


class CliTest(Scratch):
    def test_create_and_list_never_print_a_password_and_record_the_account(self):
        made = self.run_keychain("create", "--site", "example.com", "--handle", "teeny")
        self.assertEqual(made.returncode, 0, made.stderr)
        word = self.password("example.com")
        listed = self.run_keychain("list")
        for out in (made, listed):
            self.assertNotIn(word, out.stdout + out.stderr)
        self.assertEqual(json.loads(listed.stdout)["accounts"][0]["handle"], "teeny")
        conn = sqlite3.connect(self.root / "state" / "hands.db")
        rows = conn.execute("SELECT site, handle, task_id FROM accounts").fetchall()
        conn.close()
        self.assertEqual(rows, [("example.com", "teeny", 0)])

    def test_a_denied_site_gets_no_password(self):
        refused = self.run_keychain("create", "--site", "x.com", "--handle", "teeny")
        self.assertEqual(refused.returncode, 2)
        self.assertFalse((self.root / "state" / "keychain.db").exists() and store.listing()["accounts"])

    def test_secret_answers_on_a_pipe_for_the_page_host(self):
        self.run_keychain("create", "--site", "example.com", "--handle", "teeny")
        got = self.run_keychain("secret", "--host", "www.example.com")
        self.assertEqual((got.returncode, got.stdout), (0, self.password("example.com")))
        self.assertEqual(self.run_keychain("secret", "--host", "example.com.evil.io").returncode, 1)

    def test_reveal_refuses_anything_but_a_terminal(self):
        self.run_keychain("create", "--site", "example.com", "--handle", "teeny")
        got = self.run_keychain("reveal", "--site", "example.com")
        self.assertEqual(got.returncode, 2)
        self.assertNotIn(self.password("example.com"), got.stdout + got.stderr)


if __name__ == "__main__":
    unittest.main()
