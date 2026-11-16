"""Shared by the hands/reddit tests: a scratch TEENY_ROOT with the real deny
list, a test allowlist of subreddits, DRY_RUN off and the guard's model judge
off. Nothing here reaches Reddit or a browser: the hand only queues desk
tasks, and the tests settle them the way the desk does."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from teeny_hands import hand

logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

REPO = Path(__file__).resolve().parents[3]
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0

THREAD = "https://www.reddit.com/r/AI_Agents/comments/abc123/how_do_you_retry_tool_calls/"
COMMENT = "https://old.reddit.com/r/AI_Agents/comments/abc123/how_do_you_retry_tool_calls/def456/"
ELSEWHERE = "https://old.reddit.com/r/pics/comments/zzz999/a_cat/"
RULES = "1. No spam. 2. Disclose bots and AI accounts in the profile. 3. No self-promotion more than once a week."


class Scratch(unittest.TestCase):
    settings: dict[str, str] = {}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        (self.root / "deploy").mkdir()
        for name in ("browser-allow.txt", "browser-deny.txt"):
            shutil.copy(REPO / "deploy" / name, self.root / "deploy" / name)
        (self.root / "deploy" / "reddit-subreddits.txt").write_text("# test list\nAI_Agents\nLLMDevs  # builders\n")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        for key in ("DRY_RUN", "TEENY_NOW", "TEENY_HANDS_DEBUG"):
            os.environ.pop(key, None)
        self.at(T0)
        self.configure()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str | None) -> None:
        values: dict[str, str | None] = {"DRY_RUN": "0", "SAFETY_JUDGE": "0", "AGENTMAIL_INBOX": "teeny@agentmail.to"}
        values.update(self.settings)
        values.update(overrides)
        text = "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def at(self, seconds: float) -> None:
        os.environ["TEENY_NOW"] = str(seconds)

    def keychain(self) -> None:
        import base64

        (self.root / "secrets" / "keychain.env").write_text(
            "KEYCHAIN_KEY=" + base64.b64encode(os.urandom(32)).decode() + "\n")

    # --- the world the hand reads ------------------------------------------------------------

    def account(self, handle: str = "teenydroid", days_ago: float = 30, karma_comment: int | None = 120) -> None:
        """A Reddit account a signup recorded `days_ago`, and a stats read."""
        from teeny_hands.reddit import rules
        from teeny_hands.tasks import queue

        now = hand.now()
        self.at(now - days_ago * DAY)
        queue.record_account("www.reddit.com", handle, "Teeny's own Reddit account", 1)
        self.at(now)
        if karma_comment is not None:
            conn = rules.db()
            conn.execute("INSERT OR REPLACE INTO reddit_account (handle, karma_post, karma_comment, age_days, checked_at)"
                         " VALUES (?, 5, ?, ?, ?)", (handle, karma_comment, days_ago, now))
            conn.close()

    def rules_read(self, sub: str = "AI_Agents", text: str = RULES) -> None:
        from teeny_hands.reddit import rules

        conn = rules.db()
        conn.execute("INSERT OR REPLACE INTO reddit_rules (subreddit, rules, task_id, captured_at) VALUES (?, ?, 0, ?)",
                     (sub, text, hand.now()))
        conn.close()

    def seen(self, link: str, author: str = "someone") -> str:
        from teeny_hands.reddit import rules

        parsed = rules.permalink(link)
        conn = rules.db()
        conn.execute("INSERT OR REPLACE INTO reddit_seen (target, permalink, subreddit, title, author, task_id, seen_at)"
                     " VALUES (?, ?, ?, 't', ?, 0, ?)", (parsed["target"], parsed["url"], parsed["subreddit"], author,
                                                         hand.now()))
        conn.close()
        return parsed["target"]

    def ready(self) -> None:
        """An account old enough to post, AI_Agents' rules read."""
        self.account()
        self.rules_read()

    # --- the desk --------------------------------------------------------------------------------

    def tasks(self) -> list[dict]:
        from teeny_hands.tasks import queue

        return list(reversed(queue.listing()["tasks"]))

    def rows(self) -> list[dict]:
        from teeny_hands.reddit import rules

        conn = rules.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM reddit_actions ORDER BY id")]
        finally:
            conn.close()

    def finish(self, task_id: int, outcome: str = "ok", notes: str = "done", reddit: dict | None = None) -> dict:
        """The operator takes the task and reports on it, as through the desk."""
        from teeny_hands.tasks import queue

        offered = queue.next_task()["task"]
        self.assertEqual(offered["task_id"], task_id)
        body = {"task_id": task_id, "outcome": outcome, "notes": notes}
        if reddit is not None:
            body["reddit"] = reddit
        return queue.done(body)
