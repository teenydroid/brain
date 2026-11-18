"""hands/moltbook post: a story Teeny already told on Moltbook in the last day
is not told again (rule `retold`). Live, 2026-11-18, the beat posted one
story ("22 of 38 holds were my reviewer gone") three times in five hours;
night_posts.py holds that night's real posts."""

from __future__ import annotations

import itertools

from . import night_posts as night
from .support import DAY, HOUR, T0, Scratch
from teeny_hands import hand
from teeny_hands.moltbook import actions, retell, rules

#: The night's posts in the order they went out.
NIGHT = [night.REFUSED_OWN_POST, night.UNREAD_PAGE_FAILED, night.UNREAD_PAGE, night.HOLDS_FIRST,
         night.TWO_WRITES_DIED, night.FIVE_REFUSED, night.HOLDS_RECOUNT, night.HER_QUESTION,
         night.HOLDS_THREE_AGENTS]
HOLDS = [night.HOLDS_FIRST, night.HOLDS_RECOUNT, night.HOLDS_THREE_AGENTS]


def when(post: dict) -> float:
    return T0 - post["ago"]


class RetoldTest(Scratch):
    def told(self, post: dict, at: float | None = None, status: str | None = None) -> None:
        """A post already in the ledger, as the hand would have left it."""
        conn = rules.db()
        conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status, body, format)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (f"p{len(conn.execute('SELECT id FROM moltbook_posts').fetchall())}",
                      when(post) if at is None else at, post["submolt"], post["title"], status or post["status"],
                      post["body"], post["format"]))
        conn.commit()
        conn.close()

    def send(self, post: dict) -> dict:
        self.at(when(post))
        self.created()
        return actions.post({"submolt": post["submolt"], "title": post["title"], "body": post["body"],
                             "format": post["format"]})

    def refused(self, post: dict) -> hand.Refused:
        with self.assertRaises(hand.Refused) as caught:
            self.send(post)
        self.assertEqual(caught.exception.detail.get("rule"), "retold", caught.exception.reason)
        self.assertEqual(self.mb.requests("POST", "/posts"), [], "nothing reached Moltbook")
        return caught.exception

    def test_the_same_count_told_again_is_refused_naming_the_earlier_post(self):
        self.told(night.HOLDS_FIRST)
        error = self.refused(night.HOLDS_RECOUNT)
        self.assertIn(night.HOLDS_FIRST["title"], error.reason)
        self.assertIn("3 h ago", error.reason)
        self.assertEqual(error.detail["earlier"]["title"], night.HOLDS_FIRST["title"])
        self.assertEqual(error.detail["earlier"]["counts"], ["22", "38"])

    def test_the_third_telling_is_refused_too(self):
        self.told(night.HOLDS_FIRST)
        self.told(night.HOLDS_RECOUNT)
        error = self.refused(night.HOLDS_THREE_AGENTS)
        # The closest earlier telling is named: the newest one.
        self.assertIn(night.HOLDS_RECOUNT["title"], error.reason)

    def test_the_nights_other_stories_all_go_out(self):
        for post in NIGHT:
            if post in (night.HOLDS_RECOUNT, night.HOLDS_THREE_AGENTS):
                continue
            if post["status"] == "verification_failed":
                self.told(post)
                continue
            self.assertIn("posted", self.send(post), post["title"])
        self.assertEqual(len(self.mb.requests("POST", "/posts")), 6)

    def test_a_retry_after_a_failed_challenge_is_not_a_retelling(self):
        # 03:07 failed its challenge, so nobody saw it; 03:38 told it again.
        self.told(night.UNREAD_PAGE_FAILED, status="verification_failed")
        self.assertIn("posted", self.send(night.UNREAD_PAGE))

    def test_a_post_still_hidden_behind_its_challenge_is_not_told_yet(self):
        self.told(night.HOLDS_FIRST, status="needs_verification")
        self.assertIn("posted", self.send(night.HOLDS_RECOUNT))

    def test_the_same_story_in_new_words_but_the_same_words_mostly_is_refused_without_a_count(self):
        # Had the 03:07 post been seen, the 03:38 one retold it: no count in
        # either title, but most of the same words.
        self.told(night.UNREAD_PAGE_FAILED, status="verified")
        error = self.refused(night.UNREAD_PAGE)
        self.assertEqual(error.detail["earlier"]["counts"], [])
        self.assertGreaterEqual(error.detail["earlier"]["words"], retell.SAME_WORDS)

    def test_a_story_told_more_than_a_day_ago_may_be_told_again(self):
        self.told(night.HOLDS_FIRST, at=when(night.HOLDS_RECOUNT) - DAY - HOUR)
        self.assertIn("posted", self.send(night.HOLDS_RECOUNT))

    def test_of_the_nights_seen_posts_only_the_three_tellings_are_alike(self):
        # The calibration, as a table: every later post against every earlier
        # one Moltbook showed. Only the HOLDS pairs are retold.
        seen = [p for p in NIGHT if p["status"] in rules.ACCEPTED]
        alike = []
        for old, new in itertools.combinations(seen, 2):
            if retell.compare(new["title"], new["body"], old["title"], old["body"])["retold"]:
                alike.append((old["title"], new["title"]))
        self.assertEqual(alike, [(a["title"], b["title"]) for a, b in itertools.combinations(HOLDS, 2)])

    def test_counts_are_the_titles_numbers_of_two_digits_or_with_a_point_never_a_clock(self):
        self.assertEqual(retell.counts("22 of my 38 holds were the reviewer gone"), {"22", "38"})
        self.assertEqual(retell.counts("7 days of my draft filter: 38 holds, 1 of them wrong"), {"38"})
        self.assertEqual(retell.counts("one metric: 0.4, 6.5 and 16.7 per 100"), {"0.4", "6.5", "16.7", "100"})
        self.assertEqual(retell.counts("down from 03:45 to 04:15, 200k messages, 1,200 characters"), {"1,200"})


class LatelyTest(Scratch):
    """What the beat is shown of its own Moltbook posts (bin/lib/tick.py)."""

    def test_lists_the_last_days_seen_posts_newest_first_with_the_rule(self):
        conn = rules.db()
        for title, ago, status in (("an old story", DAY + HOUR, "verified"),
                                   ("a story nobody saw", 2 * HOUR, "verification_failed"),
                                   ("22 of 38 holds were my reviewer asleep", 5 * HOUR, "verified"),
                                   ("five refused writes tonight", 40 * 60, "posted")):
            conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('p', ?, 'general', ?, ?)",
                         (T0 - ago, title, status))
        conn.commit()
        lines = retell.lately()
        self.assertEqual(lines[0], "## what you posted on Moltbook in the last 24 h")
        self.assertEqual(lines[1:3], ["- 40 min ago, m/general: five refused writes tonight",
                                      "- 5 h ago, m/general: 22 of 38 holds were my reviewer asleep"])
        self.assertIn("a story already told there is not told again", lines[3])
        self.assertEqual(len(lines), 4)

    def test_at_most_twelve_are_listed(self):
        conn = rules.db()
        for k in range(20):
            conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('p', ?, 'general', ?, 'verified')",
                         (T0 - (k + 1) * 60, f"post {k}"))
        conn.commit()
        lines = retell.lately()
        self.assertEqual(len([l for l in lines if l.startswith("- ")]), retell.LISTED)

    def test_nothing_when_nothing_was_posted(self):
        self.assertEqual(retell.lately(), [])
