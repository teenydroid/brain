"""hands/moltbook study: what wins on Moltbook this week, from the hot and
top feeds and six submolts, deterministic and cached six hours; and how
Teeny's own last writes did."""

from __future__ import annotations

from datetime import datetime, timezone

from .support import DAY, HOUR, MINUTE, T0, Scratch
from teeny_hands.moltbook import rules, study


def iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


def a_post(pid: str, score: int, age: float = HOUR, submolt: str = "general", content: str = "body") -> dict:
    return {"id": pid, "title": f"title {pid}", "content": content, "score": score, "upvotes": score, "downvotes": 0,
            "comment_count": score * 2, "created_at": iso(T0 - age), "verification_status": "verified",
            "author": {"name": f"author-{pid}", "karma": 9000}, "submolt": {"name": submolt, "display_name": "x"}}


class StudyTest(Scratch):
    def feeds(self, global_posts: list, by_submolt: dict | None = None) -> None:
        self.mb.route("GET", "/posts", {"success": True, "posts": global_posts})
        for name in study.SUBMOLTS:
            posts = (by_submolt or {}).get(name, [])
            self.mb.route("GET", f"/submolts/{name}/feed", {"success": True, "posts": posts})

    def test_top_is_the_best_fifteen_of_the_week_from_hot_and_top(self):
        posts = [a_post(f"p{n}", n) for n in range(20)]
        posts.append(a_post("old", 999, age=8 * DAY))
        posts.append(a_post("p19", 19))  # the same post in both feeds counts once
        posts.append(a_post("long", 50, content="x" * 1000))
        self.feeds(posts)
        out = study.study()
        self.assertEqual([p["id"] for p in out["top"]], ["long", *[f"p{n}" for n in range(19, 5, -1)]])
        self.assertEqual(len(out["top"]), 15)
        self.assertEqual(out["top"][1], {
            "id": "p19", "title": "title p19", "submolt": "general", "author": "author-p19", "score": 19,
            "comment_count": 38, "created_at": iso(T0 - HOUR), "content": "body"})
        self.assertEqual(len(out["top"][0]["content"]), study.CONTENT_CHARS)
        sorts = sorted(r.query["sort"] for r in self.mb.requests("GET", "/posts"))
        self.assertEqual(sorts, ["hot", "top"])
        self.assertIn("data", out["note"])

    def test_by_submolt_is_the_best_five_of_each(self):
        self.feeds([], {"memory": [a_post(f"m{n}", n, submolt="memory") for n in range(8)],
                        "security": [a_post("s1", 3, submolt="security")]})
        out = study.study()
        self.assertEqual(list(out["by_submolt"]), list(study.SUBMOLTS))
        self.assertEqual([p["id"] for p in out["by_submolt"]["memory"]], ["m7", "m6", "m5", "m4", "m3"])
        self.assertEqual([p["id"] for p in out["by_submolt"]["security"]], ["s1"])
        self.assertEqual(out["by_submolt"]["agents"], [])

    def test_it_is_cached_six_hours(self):
        self.feeds([a_post("p1", 1)])
        study.study()
        calls = len(self.mb.seen)
        self.at(T0 + 5 * HOUR)
        study.study()
        self.assertEqual(len(self.mb.seen), calls)
        self.at(T0 + 6 * HOUR + MINUTE)
        study.study()
        self.assertEqual(len(self.mb.seen), 2 * calls)

    def test_a_feed_that_failed_is_reported_and_nothing_is_cached(self):
        self.feeds([a_post("p1", 1)])
        self.mb.route("GET", "/submolts/builds/feed", {"success": False, "error": "no such submolt"}, status=404)
        out = study.study()
        self.assertEqual(out["errors"], ["builds hot: Moltbook answered 404: no such submolt",
                                         "builds top: Moltbook answered 404: no such submolt"])
        self.assertEqual([p["id"] for p in out["top"]], ["p1"])
        self.at(T0 + 10 * MINUTE)  # past the feeds' own five minutes
        study.study()
        self.assertEqual(len(self.mb.requests("GET", "/posts")), 4)

    def test_a_429_stops_the_reading(self):
        self.mb.route("GET", "/posts", {"success": False, "error": "slow down"}, status=429)
        out = study.study()
        self.assertEqual(len(self.mb.seen), 1)
        self.assertTrue(out["rate_limited"])

    def test_own_is_teenys_last_ten_writes_with_their_latest_metrics(self):
        self.feeds([])
        conn = rules.db()
        for n in range(7):
            conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES (?, ?, 'general', ?, 'verified')",
                         (f"p{n}", T0 - DAY + n * HOUR, f"post {n}"))
            conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, 'px', ?, 'posted')",
                         (f"c{n}", T0 - DAY + n * HOUR + MINUTE))
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES (NULL, ?, 'general', 'x', 'refused')", (T0,))
        conn.executemany(
            "INSERT INTO moltbook_post_metrics (kind, target_id, checkpoint, at, score, comment_count, verification_status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [("post", "p6", 1, T0 - 10 * HOUR, 1, 0, "pending"), ("post", "p6", 24, T0, 7, 3, "verified")],
        )
        own = study.study()["own"]
        self.assertEqual([o["id"] for o in own], ["c6", "p6", "c5", "p5", "c4", "p4", "c3", "p3", "c2", "p2"])
        self.assertEqual(own[1], {"kind": "post", "id": "p6", "title": "post 6", "submolt": "general",
                                  "at": iso(T0 - DAY + 6 * HOUR), "status": "verified",
                                  "metrics": {"checkpoint": 24, "at": iso(T0), "score": 7, "comment_count": 3,
                                              "verification_status": "verified"}})
        self.assertEqual((own[0]["kind"], own[0]["post_id"], own[0]["metrics"]), ("comment", "px", None))

    def test_own_summary_is_per_format_and_submolt_over_seven_and_fourteen_days(self):
        self.feeds([])
        conn = rules.db()
        posts = (  # id, days ago, submolt, format, status, score, comments, verification
            ("a", 1, "general", "claim", "verified", 9, 12, "verified"),
            ("b", 2, "general", "claim", "verified", 5, 4, "verified"),
            ("c", 3, "memory", "claim", "verified", 7, 6, "verified"),
            ("d", 4, "memory", "audit", "verified", 30, 20, "verified"),
            ("e", 5, "general", None, "verified", 0, 1, "verified"),
            ("hid", 1, "general", "audit", "verification_failed", 0, 0, "failed"),
            ("old", 10, "builds", "finding", "verified", 50, 9, "verified"),
            ("gone", 20, "builds", "finding", "verified", 99, 9, "verified"),
            ("young", 0.01, "general", "claim", "verified", 0, 0, None),  # not measured yet
        )
        for pid, days, submolt, fmt, status, score, comments, ver in posts:
            conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status, format) VALUES (?, ?, ?, ?, ?, ?)",
                         (pid, T0 - days * DAY, submolt, f"title {pid}", status, fmt))
            if ver:
                conn.execute("INSERT INTO moltbook_post_metrics (kind, target_id, checkpoint, at, score, comment_count,"
                             " verification_status) VALUES ('post', ?, 24, ?, ?, ?, ?)", (pid, T0, score, comments, ver))
        for cid, fmt, score in (("c1", "threads", 2), ("c2", "threads", 0), ("c3", "own-thread", 1)):
            conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status, format) VALUES (?, 'px', ?, 'verified', ?)",
                         (cid, T0 - DAY, fmt))
            conn.execute("INSERT INTO moltbook_post_metrics (kind, target_id, checkpoint, at, score, comment_count,"
                         " verification_status) VALUES ('comment', ?, 1, ?, ?, 0, 'verified')", (cid, T0, score))
        got = study.study()["own_summary"]
        week = got["7d"]["posts"]
        self.assertEqual(week["by_format"], [
            {"format": "audit", "n": 1, "hidden": 1, "median_score": 30, "best_score": 30, "median_comments": 20,
             "worst": {"id": "d", "score": 30, "title": "title d"}},
            {"format": "claim", "n": 3, "median_score": 7, "best_score": 9, "median_comments": 6,
             "worst": {"id": "b", "score": 5, "title": "title b"}},
            {"format": "(untagged)", "n": 1, "median_score": 0, "best_score": 0, "median_comments": 1,
             "worst": {"id": "e", "score": 0, "title": "title e"}},
        ])
        self.assertEqual([(g["submolt"], g["n"], g["median_score"]) for g in week["by_submolt"]],
                         [("memory", 2, 18.5), ("general", 3, 5)])
        self.assertEqual([(g["format"], g["n"], g["median_score"]) for g in got["7d"]["comments"]["by_format"]],
                         [("threads", 2, 1), ("own-thread", 1, 1)])  # a tie goes to the larger n
        self.assertEqual([g["format"] for g in got["14d"]["posts"]["by_format"]], ["finding", "audit", "claim", "(untagged)"])
        lines = study.summary_lines(got)
        self.assertEqual(lines[0], 'Moltbook posts by format, last 7d: audit n=1 med 30 best 30 cmt 20 hidden 1;'
                                   ' claim n=3 med 7 best 9 cmt 6; (untagged) n=1 med 0 best 0 cmt 1; worst (untagged) "title e"')

    def test_own_summary_with_no_writes_is_empty_tables(self):
        self.feeds([])
        got = study.study()["own_summary"]
        self.assertEqual(got["7d"], {"posts": {"by_format": [], "by_submolt": []}, "comments": {"by_format": []}})
        self.assertEqual(study.summary_lines(got), [])
