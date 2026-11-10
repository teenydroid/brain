"""hands/gh's reads: the client, the cache, the policy heuristic and the ship
log, against the fake GitHub."""

from __future__ import annotations

from .support import TOKEN, Scratch
from teeny_hands.gh import contributing, reads
from teeny_hands.gh.client import GitHubApi, GitHubError, split_repo


class ClientTest(Scratch):
    def test_sends_the_token_as_a_bearer(self):
        self.me()
        self.assertEqual(reads.whoami()["login"], "teenydroid")
        self.assertEqual(self.gh.requests("GET", "/user")[0].headers["authorization"], f"Bearer {TOKEN}")

    def test_an_error_names_neither_the_url_nor_the_token(self):
        self.gh.route("GET", "/user", {"message": "Bad credentials"}, status=401)
        with self.assertRaises(GitHubError) as caught:
            GitHubApi.from_settings().whoami()
        message = str(caught.exception)
        self.assertEqual(message, "GitHub answered 401: Bad credentials")
        self.assertNotIn(TOKEN, message)
        self.assertNotIn("127.0.0.1", message)
        self.assertIsNone(caught.exception.__cause__)

    def test_needs_the_token(self):
        self.configure(GITHUB_TOKEN=None)
        with self.assertRaises(Exception) as caught:
            reads.whoami()
        self.assertIn("GITHUB_TOKEN is not set", str(caught.exception))

    def test_only_repositories_reach_a_path(self):
        for bad in ("a", "a/b/c", "../b", "a/..", "a/b.git", "-a/b", "a/b c", 5):
            with self.assertRaises(Exception, msg=bad):
                split_repo(bad)
        self.assertEqual(split_repo("tinyhumansai/openhuman"), ("tinyhumansai", "openhuman"))


class ReadsTest(Scratch):
    def test_whoami_says_whether_the_token_is_teenys(self):
        self.me("someoneelse")
        out = reads.whoami()
        self.assertEqual((out["login"], out["expected"], out["matches"]), ("someoneelse", "teenydroid", False))

    def test_reads_are_cached(self):
        self.me()
        reads.whoami()
        reads.whoami()
        self.assertEqual(len(self.gh.requests("GET", "/user")), 1)

    def test_the_cache_can_be_switched_off(self):
        self.configure(GH_CACHE_ENABLED="0")
        self.me()
        reads.whoami()
        reads.whoami()
        self.assertEqual(len(self.gh.requests("GET", "/user")), 2)

    def test_repo(self):
        self.gh.route("GET", "/repos/a/b", {"full_name": "a/b", "owner": {"login": "a"}, "default_branch": "main", "token": "x"})
        out = reads.repo("a/b")["repo"]
        self.assertEqual((out["full_name"], out["owner"], out["default_branch"]), ("a/b", "a", "main"))
        self.assertNotIn("token", out)
        self.assertIsNone(reads.repo("a/missing")["repo"])

    def test_issues_leave_out_pull_requests_and_pass_the_label(self):
        self.gh.route(
            "GET",
            "/repos/a/b/issues",
            [
                {"number": 1, "title": "bug", "labels": [{"name": "good first issue"}], "user": {"login": "x"}},
                {"number": 2, "title": "a pr", "pull_request": {}},
            ],
        )
        out = reads.issues("a/b", "good first issue", 5)["issues"]
        self.assertEqual([(i["number"], i["labels"]) for i in out], [(1, ["good first issue"])])
        query = self.gh.requests("GET", "/repos/a/b/issues")[0].query
        self.assertEqual((query["labels"], query["per_page"], query["state"]), ("good first issue", "5", "open"))

    def test_pr(self):
        self.gh.route(
            "GET",
            "/repos/a/b/pulls/7",
            {"number": 7, "state": "open", "merged": False, "head": {"label": "teenydroid:fix"}, "base": {"ref": "main"}},
        )
        out = reads.pr("a/b", 7)["pr"]
        self.assertEqual((out["number"], out["head"], out["base"]), (7, "teenydroid:fix", "main"))

    def test_activity_is_the_ship_log(self):
        self.gh.route(
            "GET",
            "/search/issues",
            {"items": [{"number": 3, "title": "fix", "repository_url": "https://api.github.com/repos/a/b",
                        "pull_request": {"merged_at": "2026-09-13T00:00:00Z"}}]},
        )
        self.gh.route(
            "GET",
            "/search/commits",
            {"items": [{"sha": "abcdef1234567890", "repository": {"full_name": "teenydroid/hello"},
                        "commit": {"message": "first\n\nmore", "committer": {"date": "2026-09-13"}}}]},
        )
        out = reads.activity(7)
        self.assertEqual(out["since"], "2026-09-07")
        self.assertEqual(out["prs"][0]["repo"], "a/b")
        self.assertEqual(out["merged"][0]["merged_at"], "2026-09-13T00:00:00Z")
        self.assertEqual(out["commits"][0], {"repo": "teenydroid/hello", "sha": "abcdef123456", "message": "first",
                                             "date": "2026-09-13", "html_url": None})
        queries = [s.query["q"] for s in self.gh.requests("GET", "/search/issues")]
        self.assertIn("author:teenydroid type:pr created:>=2026-09-07", queries)


class PolicyTest(Scratch):
    def assess(self, text):
        return contributing.assess({"CONTRIBUTING.md": text})

    def test_a_ban_forbids(self):
        for text in (
            "We do not accept AI-generated pull requests.",
            "Contributions written by LLMs will be closed without review.",
            "No ChatGPT or Copilot output, please.",
            "Bots are banned from opening issues.",
            "Pull requests created by autonomous agents are prohibited.",
        ):
            self.assertTrue(self.assess(text)["forbids_ai"], text)

    def test_a_welcome_allows(self):
        verdict = self.assess("AI-assisted contributions are welcome, as long as you disclose them.")
        self.assertEqual((verdict["verdict"], verdict["forbids_ai"]), ("allows", False))

    def test_silence_is_not_a_ban(self):
        verdict = self.assess("Run the tests before you open a pull request. Be kind.")
        self.assertEqual((verdict["verdict"], verdict["forbids_ai"]), ("silent", False))
        self.assertEqual(contributing.assess({})["verdict"], "silent")

    def test_ambiguity_forbids(self):
        verdict = self.assess("Please read our AI policy before contributing.")
        self.assertEqual((verdict["verdict"], verdict["forbids_ai"]), ("forbids", True))
        self.assertEqual(verdict["evidence"][0]["reads"], "unclear")

    def test_one_ban_outweighs_a_welcome(self):
        text = "AI tools are welcome for research.\n\nAI-generated code is not accepted."
        self.assertTrue(self.assess(text)["forbids_ai"])

    def test_reads_every_policy_file_from_github(self):
        self.file("a/b", "CONTRIBUTING.md", "Run the tests.")
        self.file("a/b", ".github/AI_POLICY.md", "We don't accept LLM-generated contributions.")
        out = reads.contributing("a", "b")
        self.assertEqual(out["repo"], "a/b")
        self.assertTrue(out["forbids_ai"])
        self.assertEqual(out["files"], [".github/AI_POLICY.md", "CONTRIBUTING.md"])
        self.assertEqual(out["evidence"][0]["file"], ".github/AI_POLICY.md")
