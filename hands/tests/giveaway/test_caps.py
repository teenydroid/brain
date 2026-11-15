"""The switch and the caps: every GIVEAWAY_* cap is required, a missing or
zero one refuses every round, and `open` refuses whatever would pass one."""

from __future__ import annotations

import unittest

from giveaway.support import CAPS, DAY, DEADLINE, HOUR, T0, GiveawayCase, iso
from teeny_hands import hand
from teeny_hands.giveaway import caps, rounds, store


class CapsLoadTest(unittest.TestCase):
    def test_every_cap_is_required_and_none_may_be_zero_or_garbage(self):
        good = {k: v for k, v in CAPS.items() if k in caps.CAP_KEYS}
        self.assertEqual(caps.load(good).pool_max, 100_000 * 10**6)
        for key in caps.CAP_KEYS:
            for bad in ("", "0", "-5", "lots"):
                with self.assertRaises(caps.CapsError, msg=f"{key}={bad!r}"):
                    caps.load({**good, key: bad})

    def test_the_caps_must_fit_inside_each_other(self):
        good = {k: v for k, v in CAPS.items() if k in caps.CAP_KEYS}
        with self.assertRaises(caps.CapsError):
            caps.load({**good, "GIVEAWAY_POOL_MAX_TINY": "300000"})
        with self.assertRaises(caps.CapsError):
            caps.load({**good, "GIVEAWAY_MAX_PER_WINNER_TINY": "100001"})
        with self.assertRaises(caps.CapsError):
            caps.load({**good, "GIVEAWAY_CLUSTER": "testnet"})

    def test_amounts_are_exact(self):
        self.assertEqual(caps.to_atomic("1234.5", "x"), 1_234_500_000)
        self.assertEqual(caps.display(1_234_500_000), "1234.5")
        with self.assertRaises(ValueError):
            caps.to_atomic("0.0000001", "x")


class OpenTest(GiveawayCase):
    def refused(self, rule: str, **body):
        with self.assertRaises(hand.Refused) as caught:
            rounds.open_round(self.open_body(**body))
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        return caught.exception

    def test_off_by_default_and_when_switched_off(self):
        self.configure(GIVEAWAYS=None)
        self.refused("giveaways_off")
        self.configure(GIVEAWAYS="0")
        self.refused("giveaways_off")
        self.assertFalse(self.web.writes())

    def test_a_missing_cap_refuses_and_posts_nothing(self):
        for key in caps.CAP_KEYS:
            self.configure(**{key: None})
            self.refused("caps")
            self.configure(**{key: "0"})
            self.refused("caps")
            self.configure(**{key: CAPS[key]})
        self.assertFalse(self.web.writes())

    def test_each_cap(self):
        self.refused("pool_cap", pool_tiny="100004", best_reply_tiny="4", winners=4)
        self.refused("per_winner_cap", pool_tiny="100000", winners=1, best_reply_tiny="0")
        self.refused("per_winner_cap", pool_tiny="90000", winners=3, best_reply_tiny="30000")  # 20k + 30k to one person
        self.refused("split", pool_tiny="100000", winners=3, best_reply_tiny="20000")
        self.refused("amount", best_reply_tiny="100000")
        self.refused("winners", winners=0)
        self.refused("format", format="lottery")
        self.refused("format", format="challenge", winners=1)
        self.refused("deadline", deadline=iso(T0 + 60))
        self.refused("deadline", deadline=iso(T0 + 31 * DAY))
        self.refused("tag", text="a giveaway with no tag")
        self.refused("tag", text="drop #2 is not the next round")
        self.refused("fields", pay_to="someone")
        self.assertFalse(self.web.writes())

    def test_a_deadline_can_be_given_in_hours_from_now(self):
        # A drafting turn is not told the time, so a playbook names the
        # deadline as hours from now and the hand works out the moment.
        self.refused("deadline", deadline=None, deadline_hours=0)
        self.refused("deadline", deadline=None, deadline_hours=24 * 30 + 1)
        self.refused("deadline", deadline=None, deadline_hours="48")
        self.refused("deadline", deadline=iso(DEADLINE), deadline_hours=48)
        out = rounds.open_round(self.open_body(deadline=None, deadline_hours=48))
        self.assertEqual(out["round"]["deadline"], iso(T0 + 48 * 3600))

    def test_open_posts_and_returns_the_tag(self):
        out = rounds.open_round(self.open_body())
        self.assertEqual((out["round"]["tag"], out["round"]["post_id"], out["round"]["status"]), ("drop #1", "5000", "open"))
        self.assertEqual(len(self.web.requests("POST", "/2/tweets")), 1)

    def test_one_round_open_at_a_time_and_the_weekly_and_season_caps(self):
        self.opened()
        self.refused("one_open", text="drop #2 go")
        conn = store.db()
        conn.execute("UPDATE giveaway_rounds SET status = 'drawn'")
        conn.close()
        self.web.route("POST", "/2/tweets", {"data": {"id": "5001"}}, status=201)
        self.at(T0 + 2 * HOUR)
        rounds.open_round(self.open_body(text="drop #2 go", deadline=iso(DEADLINE + DAY)))
        conn = store.db()
        conn.execute("UPDATE giveaway_rounds SET status = 'drawn'")
        conn.close()
        self.at(T0 + 4 * HOUR)
        self.refused("weekly_cap", text="drop #3 go", pool_tiny="40000", winners=2, best_reply_tiny="0")
        self.at(T0 + 8 * DAY)
        self.refused("season_cap", text="drop #3 go", deadline=iso(T0 + 10 * DAY))
        self.assertEqual(rounds.status()["season"]["season_left_tiny"], "50000")

    def test_a_milestone_threshold_must_be_above_today(self):
        from teeny_hands.stats import collect

        conn = collect.db()
        conn.execute("INSERT INTO stats (date, source, metric, value, at) VALUES ('2026-09-14', 'x', 'followers', 800, ?)", (T0,))
        conn.close()
        self.refused("threshold", format="milestone", threshold=800)
        self.refused("threshold", format="milestone")
        self.refused("threshold", threshold=900)
        out = rounds.open_round(self.open_body(format="milestone", threshold=1000))
        self.assertEqual(out["round"]["threshold"], 1000)

    def test_dry_run_records_nothing(self):
        self.configure(DRY_RUN="1")
        out = rounds.open_round(self.open_body())
        self.assertTrue(out["dry_run"])
        self.assertEqual(rounds.status()["rounds"], [])
        self.assertFalse(self.web.writes())


if __name__ == "__main__":
    unittest.main()
