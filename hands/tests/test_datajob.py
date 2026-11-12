"""bin/lib/datajob.py (docs/roadmap.md, A17): a data job Teeny wrote runs
jailed, fed the playbook's earlier inputs, and its JSON is the input. A job
that tries the network is killed; one that reaches for secrets/ or writes
outside its scratch folder is refused; nothing it writes lasts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import datajob  # noqa: E402
from teeny_hands import hand  # noqa: E402
from teeny_hands.build import verify  # noqa: E402

JAILABLE = verify.landlock_abi() >= 1 and datajob.seccomp_supported()
needs_jail = unittest.skipUnless(JAILABLE, "needs Landlock and seccomp")


class DataJobCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / "teeny.env").write_text("SENTINEL_KEY=sentinel-secret-value-123\n")
        self.brain = self.root / "brain"
        (self.brain / "datajobs").mkdir(parents=True)
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def job(self, name, code):
        (self.brain / "datajobs" / f"{name}.py").write_text(code)

    def runs(self):
        path = self.root / "state" / "datajobs" / "runs.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class RunTest(DataJobCase):
    @needs_jail
    def test_its_input_is_the_earlier_inputs_and_its_json_the_output(self):
        self.job("trend", "import json, sys\nd = json.load(sys.stdin)\n"
                          "s = [p['followers'] for p in d['stats']]\n"
                          "print(json.dumps({'rise': s[-1] - s[0], 'days': len(s)}))\n")
        got = datajob.run(self.brain, "trend", json.dumps({"stats": [{"followers": 10}, {"followers": 14}]}))
        self.assertEqual(got, {"ok": True, "exit": 0, "output": {"rise": 4, "days": 2}})
        [row] = self.runs()
        self.assertEqual((row["name"], row["ok"], row["killed"]), ("trend", True, False))
        self.assertNotIn("rise", json.dumps(row))  # never its output

    @needs_jail
    def test_a_job_that_tries_the_network_is_killed(self):
        self.job("phone-home", "import socket\ns = socket.socket()\ns.connect(('1.1.1.1', 443))\nprint('{}')\n")
        got = datajob.run(self.brain, "phone-home", "{}")
        self.assertFalse(got["ok"])
        self.assertTrue(got["killed"], got)
        self.assertIn("network", got["error"])
        self.assertTrue(self.runs()[-1]["killed"])

    @needs_jail
    def test_udp_and_ipv6_are_killed_too_and_a_caught_error_does_not_save_it(self):
        self.job("dns", "import socket\ntry:\n    socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)\n"
                        "except Exception:\n    pass\nprint('{}')\n")
        got = datajob.run(self.brain, "dns", "{}")
        self.assertTrue(got.get("killed"), got)

    @needs_jail
    def test_a_unix_socket_is_not_the_network(self):
        self.job("local", "import socket, json\na, b = socket.socketpair()\na.send(b'x')\n"
                          "print(json.dumps({'got': b.recv(1).decode()}))\n")
        self.assertEqual(datajob.run(self.brain, "local", "{}")["output"], {"got": "x"})

    @needs_jail
    def test_secrets_and_the_checkout_are_out_of_reach_and_nothing_written_lasts(self):
        secret = self.root / "secrets" / "teeny.env"
        self.job("snoop", "import json, pathlib\nout = {}\n"
                          f"try:\n    out['secret'] = pathlib.Path({str(secret)!r}).read_text()\n"
                          "except PermissionError:\n    out['secret'] = 'refused'\n"
                          f"try:\n    pathlib.Path({str(self.brain / 'SOUL.md')!r}).write_text('mine')\n"
                          "    out['brain'] = 'written'\nexcept PermissionError:\n    out['brain'] = 'refused'\n"
                          "pathlib.Path('scratch.txt').write_text('temporary')\nout['scratch'] = 'written'\n"
                          "print(json.dumps(out))\n")
        got = datajob.run(self.brain, "snoop", "{}")
        self.assertEqual(got["output"], {"secret": "refused", "brain": "refused", "scratch": "written"})
        self.assertNotIn("sentinel", json.dumps(got))
        self.assertFalse((self.brain / "SOUL.md").exists())
        self.assertEqual(list((self.root / "state").glob("datajob-*")), [])  # the scratch folder is gone

    @needs_jail
    def test_bad_output_a_crash_and_a_timeout_fail_the_run(self):
        self.job("words", "print('not json')\n")
        self.job("crash", "raise SystemExit(3)\n")
        self.job("slow", "import time\ntime.sleep(30)\n")
        self.assertIn("not JSON", datajob.run(self.brain, "words", "{}")["error"])
        self.assertIn("exited 3", datajob.run(self.brain, "crash", "{}")["error"])
        self.assertIn("stopped after 1 s", datajob.run(self.brain, "slow", "{}", timeout=1)["error"])

    def test_names_and_sizes_are_checked_before_anything_runs(self):
        self.assertIn("bad data job name", datajob.run(self.brain, "../secrets", "{}")["error"])
        self.assertIn("no data job", datajob.run(self.brain, "missing", "{}")["error"])
        self.job("big", "#" * (datajob.MAX_SCRIPT + 1))
        self.assertIn("KB", datajob.run(self.brain, "big", "{}")["error"])
        self.job("ok", "print(1)\n")
        self.assertIn("input is over", datajob.run(self.brain, "ok", "x" * (datajob.MAX_STDIN + 1))["error"])
        self.assertEqual(self.runs(), [])

    def test_without_the_jail_nothing_runs(self):
        self.job("ok", "print(1)\n")
        real = verify.landlock_abi
        verify.landlock_abi = lambda: 0
        try:
            got = datajob.run(self.brain, "ok", "{}")
        finally:
            verify.landlock_abi = real
        self.assertIn("never runs unjailed", got["error"])


class PlaybookInputTest(DataJobCase):
    def test_a_playbook_names_a_data_job_that_exists(self):
        import playbook

        self.job("trend", "print(1)\n")
        (self.brain / "playbooks").mkdir()
        (self.brain / "playbooks" / "p.md").write_text(
            '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nname = "trend"\nrun = "datajob:trend"\n+++\ngo\n')
        book = playbook.load(self.brain, "p")
        self.assertEqual(book["inputs"][0]["argv"], ["datajob", "trend"])
        (self.brain / "playbooks" / "q.md").write_text(
            '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "datajob:nope"\n+++\ngo\n')
        with self.assertRaises(playbook.PlaybookError):
            playbook.load(self.brain, "q")
        (self.brain / "playbooks" / "r.md").write_text(
            '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "datajob:../x"\n+++\ngo\n')
        with self.assertRaises(playbook.PlaybookError):
            playbook.load(self.brain, "r")
