"""bin/wallet-setup: the phrase is generated, encrypted and stored by the
core, shown once on the terminal only (never stdout, never a file), and an
existing wallet is replaced only with --force and a typed confirmation.
The terminal is a pty; the core is faked."""

from __future__ import annotations

import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

from wallet.support import OWN, Scratch, status_with

SETUP = Path(__file__).resolve().parents[3] / "bin" / "wallet-setup"
PHRASE = " ".join(f"word{i:02d}x" for i in range(24))


def accounts():
    return status_with()["accounts"]


class SetupTest(Scratch):
    def setUp(self):
        super().setUp()
        self.core.answers.update(
            {
                "openhuman.wallet_status": {"configured": False, "accounts": []},
                "openhuman.wallet_generate_mnemonic": {"phrase": PHRASE, "wordCount": 24},
                "openhuman.wallet_derive_recovery_material": {"seed": [1, 2, 3], "accounts": accounts()},
                "openhuman.encrypt_secret": "enc:v1:FAKECIPHERTEXT",
                "openhuman.wallet_setup": status_with(),
            }
        )

    def on_tty(self, *argv: str, answers: list[str] = ()) -> tuple[int, str, str]:
        """Runs the script with a pty as its terminal and stdout sent to a
        file; types each answer when the script waits. (exit, terminal, stdout)"""
        stdout_path = self.root / "stdout.txt"
        pid, master = pty.fork()
        if pid == 0:  # the child: stdout to a file, the pty stays its terminal
            fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.dup2(fd, 1)
            os.execve(sys.executable, [sys.executable, "-I", "-B", str(SETUP), *argv], dict(os.environ))
        screen = b""
        pending = list(answers)
        deadline = time.monotonic() + 30
        status = None
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    chunk = b""
                screen += chunk
                if chunk and pending and (screen.rstrip().endswith(b":") or b"Press Enter" in screen.split(b"\n")[-1]):
                    os.write(master, (pending.pop(0) + "\n").encode())
            done, code = os.waitpid(pid, os.WNOHANG)
            if done:
                status = code
                break
        if status is None:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            self.fail(f"wallet-setup did not finish: {screen[-500:]!r}")
        try:
            while True:
                ready, _, _ = select.select([master], [], [], 0.05)
                if not ready:
                    break
                chunk = os.read(master, 4096)
                if not chunk:
                    break
                screen += chunk
        except OSError:
            pass
        os.close(master)
        return os.waitstatus_to_exitcode(status), screen.decode(errors="replace"), stdout_path.read_text()

    def files_hold(self, needle: str) -> list[str]:
        found = []
        for path in self.root.rglob("*"):
            if path.is_file() and needle.encode() in path.read_bytes():
                found.append(str(path))
        return found

    def test_sets_up_and_shows_the_phrase_once_on_the_terminal_only(self):
        code, screen, stdout = self.on_tty(answers=[""])
        self.assertEqual(code, 0, screen + stdout)
        self.assertIn("word00x", screen)
        self.assertIn("word23x", screen)
        self.assertEqual(screen.count("word07x"), 1)
        self.assertIn("\x1b[3J\x1b[2J", screen)  # cleared after Enter
        self.assertNotIn("word", stdout)
        self.assertIn(f"Your own wallet (Solana): `{OWN}`", stdout)
        self.assertEqual(self.files_hold("word07x"), [])
        methods = self.core.methods()
        self.assertEqual(methods, ["openhuman.wallet_status", "openhuman.wallet_generate_mnemonic", "openhuman.wallet_derive_recovery_material",
                                   "openhuman.encrypt_secret", "openhuman.wallet_setup"])
        [setup] = self.core.params("openhuman.wallet_setup")
        self.assertEqual(setup["encryptedMnemonic"], "enc:v1:FAKECIPHERTEXT")
        self.assertEqual((setup["consentGranted"], setup["source"], setup["mnemonicWordCount"]), (True, "generated", 24))
        self.assertNotIn("force", setup)
        self.assertNotIn("word", str(setup))
        self.assertEqual(self.core.params("openhuman.wallet_generate_mnemonic"), [{"wordCount": 24}])

    def test_refuses_without_a_terminal(self):
        done = subprocess.run([sys.executable, "-I", "-B", str(SETUP)], capture_output=True, text=True, env=os.environ,
                              stdin=subprocess.DEVNULL, start_new_session=True, timeout=30)
        self.assertEqual(done.returncode, 1)
        self.assertIn("needs a terminal", done.stderr)
        self.assertEqual(self.core.methods(), [])

    def test_refuses_when_a_wallet_exists(self):
        self.core.answers["openhuman.wallet_status"] = status_with()
        code, screen, stdout = self.on_tty()
        self.assertEqual(code, 1)
        self.assertIn("already has a wallet", screen)
        self.assertEqual(self.core.methods(), ["openhuman.wallet_status"])

    def test_force_needs_the_typed_confirmation(self):
        self.core.answers["openhuman.wallet_status"] = status_with()
        code, screen, _ = self.on_tty("--force", answers=["yes"])
        self.assertEqual(code, 1)
        self.assertIn("not confirmed", screen)
        self.assertEqual(self.core.methods(), ["openhuman.wallet_status"])
        self.core.calls.clear()
        code, screen, _ = self.on_tty("--force", answers=[OWN[:8], ""])
        self.assertEqual(code, 0, screen)
        [setup] = self.core.params("openhuman.wallet_setup")
        self.assertIs(setup["force"], True)
