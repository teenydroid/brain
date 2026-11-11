"""bin/diary: Teeny's public diary. One page a day from the entry bin/reflect
wrote and the ledgers of what it posted and shipped; other people's words
never copied in; previews under DRY_RUN; live, a commit pushed only after the
secret gate, and nothing at all when the gate says no."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import diary  # noqa: E402
from teeny_hands import hand  # noqa: E402

# 2026-09-14 in Kolkata: 09:30 and 21:00; then 2026-09-15 08:00.
MON_0930 = 1_789_358_400.0
MON_2100 = 1_789_399_800.0
TUE_0800 = 1_789_439_400.0

SCHEMA = """
CREATE TABLE x_posts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, in_reply_to TEXT,
                      quote_of TEXT, media TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL);
CREATE TABLE x_mentions (id TEXT PRIMARY KEY, author_username TEXT, text TEXT NOT NULL);
CREATE TABLE moltbook_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, at REAL NOT NULL,
                             submolt TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
                             detail TEXT NOT NULL DEFAULT '{}');
CREATE TABLE build_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, status TEXT NOT NULL,
                         summary TEXT, shipped_at REAL, repo_url TEXT, commit_url TEXT);
"""


class DiaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scratch = Path(self.tmp.name)
        self.root = self.scratch / "root"
        (self.root / "secrets").mkdir(parents=True)
        (self.root / "state" / "diary").mkdir(parents=True)
        (self.root / "bin").symlink_to(REPO / "bin")
        self._env = dict(os.environ)
        os.environ.update(TEENY_ROOT=str(self.root), CALENDAR_TZ="Asia/Kolkata", TEENY_NOW=str(TUE_0800),
                          GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        os.environ.pop("DRY_RUN", None)
        self.secrets("")
        conn = hand.db()
        conn.executescript(SCHEMA)
        conn.close()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def secrets(self, text):
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def sql(self, query, *args):
        conn = hand.db()
        conn.execute(query, args)
        conn.close()

    def seed_monday(self):
        (self.root / "state" / "diary" / "2026-09-14.md").write_text("# Monday 14 September 2026\n\na long monday.\n")
        self.sql("INSERT INTO x_posts VALUES ('101', 'post', 'gm tiny humans', NULL, NULL, '[\"m1\"]', ?)", MON_0930)
        self.sql("INSERT INTO x_mentions VALUES ('900', 'ana', 'ana says something private-ish')")
        self.sql("INSERT INTO x_posts VALUES ('102', 'reply', 'thanks fren', '900', NULL, '[]', ?)", MON_2100)
        self.sql("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('mb1', ?, 'general', 'hello [moltbook]', 'posted')", MON_2100)
        self.sql("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('mb2', ?, 'general', 'never made it', 'verification_failed')", MON_2100)
        self.sql("INSERT INTO build_jobs (repo, status, summary, shipped_at, repo_url, commit_url) VALUES"
                 " ('teenydroid/tiny-cli', 'shipped', 'a cli for crabs', ?, 'https://github.com/teenydroid/tiny-cli',"
                 " 'https://github.com/teenydroid/tiny-cli/commit/abc')", MON_2100)
        self.sql("INSERT INTO build_jobs (repo, status, summary) VALUES ('teenydroid/half-done', 'failed', 'tests failed')")

    def run_diary(self, *args):
        proc = subprocess.run([str(REPO / "bin" / "diary"), *args], capture_output=True, text=True, env=os.environ)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])

    # --- the page -------------------------------------------------------------------------------------

    def test_a_day_gets_one_page_with_everything_teeny_did(self):
        self.seed_monday()
        page = diary.render_day("2026-09-14", diary.gather()["2026-09-14"])
        self.assertTrue(page.startswith("# Monday 14 September 2026\n"))
        for bit in ("## diary\n\na long monday.", "## posted", "> gm tiny humans", "https://x.com/teenydroid/status/101",
                    "with an image", "## replied", "1 reply.", "> thanks fren", "to [this post](https://x.com/i/status/900)",
                    "## moltbook", "[hello \\[moltbook\\]](https://www.moltbook.com/post/mb1) in m/general",
                    "## shipped", "[teenydroid/tiny-cli](https://github.com/teenydroid/tiny-cli) ([commit](https://github.com/teenydroid/tiny-cli/commit/abc)): a cli for crabs"):
            self.assertIn(bit, page)
        self.assertNotIn("Monday 14 September 2026\n\na long", page.split("## diary")[0])

    def test_other_peoples_words_and_unfinished_work_stay_out(self):
        self.seed_monday()
        page = diary.render_day("2026-09-14", diary.gather()["2026-09-14"])
        for bit in ("private-ish", "never made it", "half-done", "tests failed"):
            self.assertNotIn(bit, page)

    def test_days_follow_teenys_clock_and_empty_sections_are_left_out(self):
        self.sql("INSERT INTO x_posts VALUES ('103', 'post', 'tuesday gm', NULL, NULL, '[]', ?)", TUE_0800)
        days = diary.gather()
        self.assertEqual(list(days), ["2026-09-15"])
        page = diary.render_day("2026-09-15", days["2026-09-15"])
        self.assertIn("**08:00** IST", page)
        for empty in ("## diary", "## replied", "## moltbook", "## shipped"):
            self.assertNotIn(empty, page)

    # --- dry run ----------------------------------------------------------------------------------------

    def test_dry_run_only_previews_and_needs_no_token(self):
        self.seed_monday()
        code, out = self.run_diary()
        self.assertEqual((code, out["ok"], out["dry_run"], out["days"]), (0, True, True, 1), out)
        preview = self.root / "state" / "diary-preview"
        self.assertTrue((preview / "days" / "2026" / "09" / "2026-09-14.md").is_file())
        self.assertIn("- [2026-09-14](2026/09/2026-09-14.md)", (preview / "days" / "README.md").read_text())
        self.assertFalse((self.root / "state" / "diary-repo").exists())

    def test_the_index_lists_days_newest_first_inside_its_markers(self):
        out = self.scratch / "out"
        (out / "days").mkdir(parents=True)
        (out / "days" / "README.md").write_text(f"# Days\n\nintro stays.\n\n{diary.MARK_START}\n- old\n{diary.MARK_END}\n\nfooter stays.\n")
        self.seed_monday()
        self.sql("INSERT INTO x_posts VALUES ('103', 'post', 'tuesday gm', NULL, NULL, '[]', ?)", TUE_0800)
        diary.write_pages(out, diary.gather())
        index = (out / "days" / "README.md").read_text()
        self.assertIn("intro stays.", index)
        self.assertIn("footer stays.", index)
        self.assertNotIn("- old", index)
        self.assertLess(index.index("2026-09-15"), index.index("2026-09-14"))

    # --- live -------------------------------------------------------------------------------------------

    def remote(self) -> Path:
        bare = self.scratch / "diary.git"
        seed = self.scratch / "seed"
        git = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
        git("init", "-q", "--bare", "-b", "main", str(bare))
        git("init", "-q", "-b", "main", str(seed))
        (seed / "days").mkdir()
        (seed / "days" / "README.md").write_text(f"# Days\n\n{diary.MARK_START}\n{diary.MARK_END}\n")
        (seed / "README.md").write_text("# Teeny's diary\n")
        git("add", "-A", cwd=seed)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "seed", cwd=seed)
        git("push", "-q", str(bare), "main", cwd=seed)
        return bare

    def remote_file(self, bare: Path, path: str) -> str:
        return subprocess.run(["git", "--git-dir", str(bare), "show", f"main:{path}"], capture_output=True, text=True).stdout

    def test_live_pushes_the_pages_once_and_then_has_nothing_to_do(self):
        bare = self.remote()
        self.secrets(f"DRY_RUN=0\nDIARY_REMOTE={bare}\nDIARY_GITHUB_TOKEN=fake-token-for-tests\n")
        self.seed_monday()
        code, out = self.run_diary()
        self.assertEqual((code, out["ok"], out["pushed"]), (0, True, 1), out)
        self.assertIn("a long monday.", self.remote_file(bare, "days/2026/09/2026-09-14.md"))
        self.assertIn("2026-09-14", self.remote_file(bare, "days/README.md"))
        self.assertEqual(self.remote_file(bare, "README.md"), "# Teeny's diary\n")
        config = (self.root / "state" / "diary-repo" / ".git" / "config").read_text()
        self.assertNotIn("fake-token-for-tests", config)
        code, again = self.run_diary()
        self.assertEqual((code, again["pushed"], again["changed"]), (0, 0, []), again)

    def test_live_ignores_the_boxs_git_config_and_hooks(self):
        # The image's or the operator's git config (a hook path, commit
        # signing with no key) must not reach the diary's commit.
        hooks = self.scratch / "hooks"
        hooks.mkdir()
        marker = self.scratch / "hook-ran"
        (hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / "pre-commit").chmod(0o755)
        box = self.scratch / "box.gitconfig"
        box.write_text(f"[core]\n\thooksPath = {hooks}\n[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /nonexistent\n")
        os.environ["GIT_CONFIG_GLOBAL"] = str(box)
        bare = self.remote()
        self.secrets(f"DRY_RUN=0\nDIARY_REMOTE={bare}\nDIARY_GITHUB_TOKEN=fake-token-for-tests\n")
        self.seed_monday()
        code, out = self.run_diary()
        self.assertEqual((code, out["ok"], out["pushed"]), (0, True, 1), out)
        self.assertFalse(marker.exists(), "a hook from the box's git config ran")

    def test_live_commits_as_the_configured_git_identity(self):
        bare = self.remote()
        self.secrets(f"DRY_RUN=0\nDIARY_REMOTE={bare}\nDIARY_GITHUB_TOKEN=fake-token-for-tests\n"
                     "GIT_NAME=Teeny Droid\nGIT_EMAIL=teeny@example.com\n")
        self.seed_monday()
        code, out = self.run_diary()
        self.assertEqual((code, out["ok"]), (0, True), out)
        who = subprocess.run(["git", "--git-dir", str(bare), "log", "-1", "--format=%an <%ae>|%cn <%ce>", "main"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(who, "Teeny Droid <teeny@example.com>|Teeny Droid <teeny@example.com>")

    def test_live_needs_a_token(self):
        self.secrets(f"DRY_RUN=0\nDIARY_REMOTE={self.remote()}\n")
        code, out = self.run_diary()
        self.assertEqual((code, out["ok"]), (1, False))
        self.assertIn("DIARY_GITHUB_TOKEN", out["error"])

    def test_a_secret_in_the_diary_commits_and_pushes_nothing(self):
        bare = self.remote()
        leaked = "surplus-key-abcdef123456"
        self.secrets(f"DRY_RUN=0\nDIARY_REMOTE={bare}\nDIARY_GITHUB_TOKEN=fake-token-for-tests\nSURPLUS_API_KEY={leaked}\n")
        (self.root / "state" / "diary" / "2026-09-14.md").write_text(f"# Monday\n\noops, my key is {leaked}\n")
        code, out = self.run_diary()
        self.assertEqual((code, out["ok"], out["refused"]), (2, False, "the diary carries a secret, nothing committed"), out)
        self.assertNotIn(leaked, json.dumps(out))
        self.assertEqual(self.remote_file(bare, "days/2026/09/2026-09-14.md"), "")
        checkout = self.root / "state" / "diary-repo"
        status = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(status, "")

    def test_key_shapes_are_refused_whatever_the_env_files_hold(self):
        for shaped in ("ghp_" + "a" * 36, "sk-" + "b" * 40, "-----BEGIN OPENSSH PRIVATE KEY-----", "AKIA" + "C" * 16):
            with self.subTest(shaped=shaped[:8]):
                with self.assertRaises(hand.Refused):
                    diary.check_secrets(f"+++ b/days/x.md\n+look: {shaped}\n")
        diary.check_secrets("--- a/days/x.md\n-ghp_" + "a" * 36 + "\n+fine\n")  # removed lines do not count


if __name__ == "__main__":
    unittest.main()
