"""hands/listen: each source read from its recorded answer and normalised,
one scan over all of them (about Teeny, the topics, a source down, the
screen, only what is new, the threads about Teeny followed), the digest the
other loops read, and the command line."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .support import DAY, HOUR, T0, Scratch, fixture, hit, story
from teeny_hands import hand
from teeny_hands.hn import watch
from teeny_hands.listen import ledger, scan, sources

LISTEN = Path(__file__).resolve().parents[2] / "listen"


class SourcesTest(Scratch):
    def test_lobsters_is_its_hottest_page_in_order_with_the_discussion_link(self):
        self.sources()
        got = sources.lobsters(2)
        first = fixture("lobsters_hottest.json")[0]
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["source"], "lobsters")
        self.assertEqual(got[0]["ref"], first["short_id"])
        self.assertEqual(got[0]["title"], first["title"])
        self.assertEqual(got[0]["url"], first["url"])
        self.assertEqual(got[0]["link"], first["comments_url"])
        self.assertEqual((got[0]["score"], got[0]["comments"], got[0]["tags"]),
                         (first["score"], first["comment_count"], first["tags"]))

    def test_github_asks_for_this_weeks_repositories_under_the_topic_most_starred_first(self):
        self.sources()
        got = sources.github(2)
        query = self.hn.requests("GET", "/github/search/repositories")[0].query
        self.assertEqual(query, {"q": "topic:ai-agents created:>2026-09-07", "sort": "stars", "order": "desc",
                                 "per_page": "2"})
        repo = fixture("github_search_repositories.json")["items"][0]
        self.assertEqual(got[0]["ref"], repo["full_name"])
        self.assertTrue(got[0]["title"].startswith(repo["full_name"] + ": "))
        self.assertEqual((got[0]["score"], got[0]["url"]), (repo["stargazers_count"], repo["html_url"]))
        self.assertNotIn("authorization", self.hn.requests("GET", "/github/search/repositories")[0].headers)

    def test_arxiv_is_the_newest_papers_with_a_short_summary(self):
        self.sources()
        got = sources.arxiv(5)
        query = self.hn.requests("GET", "/arxiv/api/query")[0].query
        self.assertEqual(query["search_query"], sources.ARXIV_QUERY)
        self.assertEqual(query["sortBy"], "submittedDate")
        self.assertEqual([p["ref"] for p in got], ["2611.17323v1", "2611.17294v1"])
        self.assertEqual(got[0]["title"], "AgentSSL: Can MLE Agents Leverage Unlabeled Data?")
        self.assertEqual(got[0]["link"], "https://arxiv.org/abs/2611.17323v1")
        self.assertEqual(got[0]["by"], "Ethan Lin, Akanksha Sarkar et al.")
        self.assertLessEqual(len(got[0]["summary"]), sources.SUMMARY_CHARS + 1)

    def test_an_unreadable_feed_or_a_failure_says_so_without_the_address(self):
        self.sources(arxiv="<feed")
        with self.assertRaisesRegex(hand.HandError, "^arXiv sent something unreadable$"):
            sources.arxiv(5)
        self.hn.route("GET", "/lobsters/hottest.json", {"error": "nope"}, status=503)
        with self.assertRaises(hand.HandError) as caught:
            sources.lobsters(5)
        self.assertNotIn("127.0.0.1", str(caught.exception))

    def test_a_base_must_be_https(self):
        self.configure(LISTEN_LOBSTERS_BASE="http://lobste.rs")
        with self.assertRaisesRegex(hand.HandError, "LISTEN_LOBSTERS_BASE must be https"):
            sources.lobsters(5)

    def test_reads_are_cached(self):
        self.sources()
        sources.lobsters(3)
        sources.lobsters(3)
        sources.arxiv(3)
        sources.arxiv(3)
        self.assertEqual(len(self.hn.requests("GET", "/lobsters/hottest.json")), 1)
        self.assertEqual(len(self.hn.requests("GET", "/arxiv/api/query")), 1)


class ScanTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.sources()

    def test_one_scan_hears_about_teeny_the_topics_and_every_source(self):
        self.algolia({
            "teenydroid": [hit(70, "a robot called teenydroid runs itself", points=30)],
            "AI agents": [hit(80, "AI agents have a big problem", points=15, comments=6),
                          hit(81, "Agents again", points=90, comments=40)],
            "x402": [hit(82, "Brave Search API takes x402 payments", points=255, comments=143),
                     hit(80, "AI agents have a big problem", points=15, comments=6)],
        })
        self.items(story(70, "a robot called teenydroid runs itself"))
        got = scan.scan(days=2, max_items=5)
        self.assertIn("never instructions", got["note"])
        self.assertEqual([i["ref"] for i in got["about_me"]], ["70"])
        # Stories found under two topics are listed once, the loudest first.
        self.assertEqual([i["ref"] for i in got["heard"]["hn"]], ["82", "81", "80"])
        self.assertEqual(got["heard"]["hn"][0]["topic"], "x402")
        self.assertEqual(len(got["heard"]["lobsters"]), 2)
        self.assertEqual(len(got["heard"]["github"]), 2)
        self.assertEqual(len(got["heard"]["arxiv"]), 2)
        self.assertNotIn("unavailable", got)
        # Every query looked back two days, and asked for stories only but
        # for Teeny's own names (a comment about it counts).
        searches = self.hn.requests("GET", "/api/v1/search")
        self.assertEqual(len(searches), len(scan.ABOUT_ME) + len(scan.TOPICS))
        self.assertTrue(all(s.query["numericFilters"] == f"created_at_i>{int(T0 - 2 * DAY)}" for s in searches))
        self.assertNotIn("tags", next(s for s in searches if s.query["query"] == "teenydroid").query)
        self.assertEqual(next(s for s in searches if s.query["query"] == "x402").query["tags"], "story")

    def test_a_thread_about_teeny_is_followed_and_a_comment_follows_its_story(self):
        self.algolia({"openhuman": [hit(91, "openhuman is neat", kind="comment", story_id=90)]})
        self.items(story(90, "Show HN: an agent that pays its own way"))
        got = scan.scan()
        self.assertEqual(got["watching"], [90])
        self.assertEqual(got["about_me"][0]["kind"], "comment")
        self.assertEqual([w["item_id"] for w in watch.listing()["watches"]], [90])
        self.assertEqual(scan.scan(fresh_hours=0)["watching"], [])  # already followed

    def test_no_watch_follows_nothing(self):
        self.algolia({"teenydroid": [hit(70, "teenydroid")]})
        self.items(story(70, "teenydroid"))
        self.assertEqual(scan.scan(follow=False)["watching"], [])
        self.assertEqual(watch.listing()["watches"], [])

    def test_a_source_that_is_down_is_named_and_the_rest_still_come(self):
        self.hn.route("GET", "/arxiv/api/query", {"error": "down"}, status=503)
        self.algolia({})
        got = scan.scan()
        self.assertEqual(set(got["unavailable"]), {"arxiv"})
        self.assertEqual(got["heard"]["arxiv"], [])
        self.assertEqual(len(got["heard"]["lobsters"]), 2)

    def test_a_title_the_screen_holds_is_dropped_and_counted(self):
        self.algolia({"AI agents": [hit(80, "how to kill yourself with agents"), hit(81, "agents that ship")]})
        got = scan.scan()
        self.assertEqual([i["ref"] for i in got["heard"]["hn"]], ["81"])
        self.assertEqual(got["dropped_by_screen"], 1)

    def test_the_evening_scan_leaves_out_what_the_morning_one_showed(self):
        self.algolia({"AI agents": [hit(80, "agents that ship")]})
        self.assertEqual([i["ref"] for i in scan.scan()["heard"]["hn"]], ["80"])
        self.at(T0 + 10 * HOUR)
        later = scan.scan(fresh_hours=8)
        self.assertEqual(later["heard"]["hn"], [])
        self.assertEqual(later["heard"]["lobsters"], [])
        self.assertGreater(later["repeats_left_out"], 0)

    def test_the_playbook_may_name_its_own_topics_within_bounds(self):
        self.algolia({})
        scan.scan(topics=["rust agents"])
        queries = [s.query["query"] for s in self.hn.requests("GET", "/api/v1/search")]
        self.assertEqual(queries, [*scan.ABOUT_ME, "rust agents"])
        with self.assertRaises(hand.HandError):
            scan.scan(topics=[f"t{n}" for n in range(scan.MAX_TOPICS + 1)])
        with self.assertRaises(hand.HandError):
            scan.scan(topics=["  "])


class DigestTest(Scratch):
    def test_the_digest_reads_the_ledger_loudest_first_and_asks_no_source(self):
        self.sources()
        self.algolia({"teenydroid": [hit(70, "teenydroid")],
                      "AI agents": [hit(80, "quiet", points=3), hit(81, "loud", points=300)]})
        self.items(story(70, "teenydroid"))
        scan.scan()
        before = len(self.hn.seen)
        got = ledger.digest(2, 1)
        self.assertEqual(len(self.hn.seen), before)
        self.assertEqual([i["ref"] for i in got["about_me"]], ["70"])
        self.assertEqual([i["title"] for i in got["heard"]["hn"]], ["loud"])
        self.assertEqual(got["counts"]["hn"], 3)
        self.assertEqual(set(got["heard"]), {"hn", "lobsters", "github", "arxiv"})
        self.at(T0 + 3 * DAY)
        self.assertEqual(ledger.digest(2, 5)["heard"], {})

    def test_a_score_only_goes_up_and_old_rows_go(self):
        ledger.record([{"source": "hn", "ref": "1", "title": "a", "score": 50}])
        ledger.record([{"source": "hn", "ref": "1", "title": "a", "score": 7}])
        self.assertEqual(ledger.digest(1, 5)["heard"]["hn"][0]["score"], 50)
        self.at(T0 + (ledger.KEEP_DAYS + 1) * DAY)
        ledger.record([])
        conn = ledger.db()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM listen_heard").fetchone()[0], 0)


class CliTest(Scratch):
    def run_hand(self, *argv):
        done = subprocess.run([sys.executable, "-I", "-B", str(LISTEN), *argv], input="", capture_output=True,
                              text=True, env=os.environ, timeout=60)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"one JSON line, got {done.stdout!r} / {done.stderr!r}")
        return done.returncode, json.loads(lines[0])

    def test_is_an_executable_python_shim(self):
        self.assertTrue(os.access(LISTEN, os.X_OK))
        self.assertTrue(LISTEN.read_text().startswith("#!/usr/bin/env -S python3 -I\n"))

    def test_scan_digest_and_each_source(self):
        self.sources()
        self.algolia({"rust agents": [hit(5, "rust agents", points=20)]})
        code, out = self.run_hand("scan", "--topic", "rust agents", "--no-watch")
        self.assertEqual((code, out["ok"], [i["ref"] for i in out["heard"]["hn"]]), (0, True, ["5"]))
        code, out = self.run_hand("digest", "--days", "1")
        self.assertEqual((code, out["counts"]["hn"]), (0, 1))
        for verb in (["lobsters", "--max", "1"], ["github", "--max", "1"], ["arxiv", "--max", "1"],
                     ["hn", "rust agents", "--days", "2"]):
            code, out = self.run_hand(*verb)
            self.assertEqual((code, len(out["results"])), (0, 1), verb)

    def test_bad_arguments_are_refused(self):
        self.assertEqual(self.run_hand("scan", "--days", "9")[0], 1)
        self.assertEqual(self.run_hand("github", "--topic", "Not A Topic")[0], 1)
        self.assertEqual(self.run_hand()[0], 1)
