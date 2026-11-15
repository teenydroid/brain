"""hands/deploy (docs/roadmap.md, A11): a fixture build is published to a
temp root and its URL recorded; anything but static files, a form, a remote
script, words the guard holds, a build that did not ship or a name that is
reserved is refused; DRY_RUN copies nothing; off until DEPLOY=1."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from teeny_hands import hand  # noqa: E402
from teeny_hands.deploy import publish  # noqa: E402

DEPLOY = HERE.parents[1] / "deploy"
JOBS = """CREATE TABLE IF NOT EXISTS build_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
  request_id INTEGER, slug TEXT NOT NULL UNIQUE, repo TEXT NOT NULL, text TEXT NOT NULL, workdir TEXT NOT NULL,
  status TEXT NOT NULL, claimed_at REAL NOT NULL)"""


class DeployTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self.sites = self.root / "www"
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "TEENY_NOW"):
            os.environ.pop(key, None)
        self.configure()
        conn = hand.db()
        conn.execute(JOBS)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **over: str | None) -> None:
        values = {"DRY_RUN": "0", "DEPLOY": "1", "DEPLOY_ROOT": str(self.sites), "DEPLOY_DOMAIN": "teeny.example.com", **over}
        (self.root / "secrets" / "teeny.env").write_text("".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
        hand._ENV_CACHE = None

    def job(self, slug: str = "req-12-crab-counter", status: str = "shipped", kind: str = "request",
            files: dict[str, str | bytes] | None = None, folder: str = "site") -> int:
        work = self.root / "state" / "workbench" / slug
        files = files if files is not None else {"index.html": "<h1>Crab counter</h1><script src=\"app.js\"></script>",
                                                 "app.js": "console.log('crabs')", "img/crab.png": b"\x89PNG..."}
        for name, body in files.items():
            path = work / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body if isinstance(body, bytes) else body.encode())
        (work / "server.py").write_text("print('never published')")
        conn = hand.db()
        cur = conn.execute("INSERT INTO build_jobs (kind, slug, repo, text, workdir, status, claimed_at) VALUES (?, ?, ?, 'x', ?, ?, 1)",
                           (kind, slug, slug, f"state/workbench/{slug}", status))
        conn.commit()
        conn.close()
        return int(cur.lastrowid)

    def refused(self, job_id: int, rule: str) -> None:
        with self.assertRaises(hand.Refused) as no:
            publish.publish(job_id)
        self.assertEqual(no.exception.detail["rule"], rule)
        self.assertFalse((self.sites / "req-12-crab-counter").exists())

    def test_a_shipped_build_is_published_and_its_url_recorded(self):
        out = publish.publish(self.job())
        self.assertEqual(out["published"]["url"], "https://req-12-crab-counter.teeny.example.com/")
        live = self.sites / "req-12-crab-counter"
        self.assertEqual(sorted(p.relative_to(live).as_posix() for p in live.rglob("*") if p.is_file()),
                         ["app.js", "img/crab.png", "index.html"])
        self.assertFalse((live / "server.py").exists())
        (row,) = publish.listing()["deploys"]
        self.assertEqual((row["site"], row["status"], row["files"]), ("req-12-crab-counter", "published", 3))

    def test_republishing_swaps_the_whole_site(self):
        job = self.job()
        publish.publish(job)
        site = self.root / "state" / "workbench" / "req-12-crab-counter" / "site"
        (site / "app.js").unlink()
        (site / "index.html").write_text("<p>v2</p>")
        publish.publish(job)
        live = self.sites / "req-12-crab-counter"
        self.assertEqual(sorted(p.name for p in live.rglob("*") if p.is_file()), ["crab.png", "index.html"])
        self.assertEqual([p.name for p in self.sites.iterdir()], ["req-12-crab-counter"])

    def test_server_code_and_other_files_are_refused(self):
        for name in ("api.php", "run.py", "go.sh", "x.cgi", ".htaccess", "app.wasm"):
            with self.subTest(name=name):
                self.refused(self.job(slug=f"req-1-{name.strip('.').replace('.', '-')}",
                                      files={"index.html": "<p>hi</p>", name: "x"}), "static")

    def test_a_symlink_is_refused(self):
        job = self.job()
        (self.root / "state" / "workbench" / "req-12-crab-counter" / "site" / "secrets").symlink_to(self.root / "secrets")
        self.refused(job, "static")

    def test_size_limits_hold(self):
        self.configure(DEPLOY_MAX_FILE_BYTES="1000")
        self.refused(self.job(files={"index.html": "<p>" + "a" * 2000 + "</p>"}), "size")

    def test_a_form_or_a_remote_script_is_refused(self):
        self.refused(self.job(slug="req-1-form", files={"index.html": "<form action='https://evil.example'>"}), "form")
        self.refused(self.job(slug="req-2-cdn", files={"index.html": "<script src='https://cdn.example/x.js'></script>"}),
                     "remote_script")

    def test_words_the_guard_holds_are_never_published(self):
        self.refused(self.job(files={"index.html": "<p>kill yourself</p>"}), "held")

    def test_only_a_shipped_build_of_a_safe_name(self):
        self.refused(self.job(slug="req-3-wip", status="claimed"), "status")
        self.refused(self.job(slug="self-4-x", kind="self"), "kind")
        self.refused(self.job(slug="wallet"), "name")
        self.refused(self.job(slug="req-5-noindex", files={"about.html": "<p>hi</p>"}), "no_index")
        self.refused(self.job(slug="req-6-nosite", folder="src"), "no_site")
        self.refused(999, "unknown")

    def test_dry_run_copies_nothing(self):
        self.configure(DRY_RUN="1")
        out = publish.publish(self.job())
        self.assertEqual(out["would_publish"]["files"], 3)
        self.assertFalse(self.sites.exists())
        self.assertEqual(publish.listing()["deploys"], [])

    def test_off_until_deploy_is_set(self):
        self.configure(DEPLOY=None)
        self.refused(self.job(), "off")

    def test_the_operator_takes_a_site_down(self):
        publish.publish(self.job())
        run = subprocess.run([sys.executable, str(DEPLOY), "unpublish", "req-12-crab-counter", "--note", "asked to"],
                             capture_output=True, text=True, env=os.environ)
        self.assertEqual(json.loads(run.stdout)["unpublished"], "req-12-crab-counter")
        self.assertFalse((self.sites / "req-12-crab-counter").exists())
