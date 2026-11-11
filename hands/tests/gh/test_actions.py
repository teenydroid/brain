"""hands/gh's acting verbs: the guard on every word, the rules, DRY_RUN, the
ledger, and the push's secret scan, against the fake GitHub and local bare
remotes."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from .support import T0, TOKEN, Scratch, git
from teeny_hands import hand
from teeny_hands.gh import actions
from teeny_hands.guard import holds


def ledger() -> list[dict]:
    conn = hand.db()
    conn.executescript(actions.SCHEMA)
    return [dict(r) for r in conn.execute("SELECT * FROM gh_actions ORDER BY id")]


class CreateRepoTest(Scratch):
    def test_creates_under_teenys_account_and_records_it(self):
        self.me()
        self.gh.route("POST", "/user/repos", {"full_name": "teenydroid/hello", "owner": {"login": "teenydroid"},
                                              "html_url": "https://github.com/teenydroid/hello"}, status=201)
        out = actions.create_repo({"name": "hello", "description": "my first — repo"})
        self.assertEqual(out["created"], "teenydroid/hello")
        self.assertEqual(self.gh.requests("POST", "/user/repos")[0].json(),
                         {"name": "hello", "description": "my first, repo", "private": False})
        self.assertEqual([(r["verb"], r["repo"]) for r in ledger()], [("create-repo", "teenydroid/hello")])

    def test_dry_run_touches_nothing(self):
        self.configure(DRY_RUN="1")
        out = actions.create_repo({"name": "hello"})
        self.assertEqual((out["dry_run"], out["would_create"]), (True, "teenydroid/hello"))
        self.assertEqual(self.gh.seen, [])
        self.assertEqual(ledger(), [])

    def test_refuses_a_token_for_somebody_else(self):
        self.me("someoneelse")
        with self.assertRaises(hand.Refused) as caught:
            actions.create_repo({"name": "hello"})
        self.assertEqual(caught.exception.detail["rule"], "identity")
        self.assertEqual(self.gh.writes(), [])

    def test_a_held_description_publishes_nothing(self):
        self.me()
        with self.assertRaises(hand.Refused) as caught:
            actions.create_repo({"name": "hello", "description": "this is fucking great"})
        self.assertEqual(caught.exception.reason, "held")
        self.assertEqual(self.gh.seen, [])

    def test_bad_bodies(self):
        for body in (None, {}, {"name": "a/b"}, {"name": "x", "private": "no"}, {"name": "x", "owner": "y"}):
            with self.assertRaises(hand.HandError, msg=body):
                actions.create_repo(body)


class PushTest(Scratch):
    def body(self, **more):
        return {"workdir": "state/workbench/hello", "repo": "teenydroid/hello", "branch": "main",
                "message": "first commit", **more}

    def refused(self, body, rule):
        with self.assertRaises(hand.Refused) as caught:
            actions.push(body)
        self.assertEqual(caught.exception.detail.get("rule"), rule)
        return caught.exception

    def test_pushes_a_workbench_folder_to_an_own_repo(self):
        self.me()
        self.workdir(files={"README.md": "# hello\n", "src/main.py": "print('hi')\n"})
        remote = self.remote("teenydroid/hello")
        out = actions.push(self.body())
        self.assertEqual((out["pushed"], out["branch"]), ("teenydroid/hello", "main"))
        log = git("-C", str(remote), "log", "--format=%an <%ae>|%s", "main").strip()
        self.assertEqual(log, "Teeny <teeny@agentmail.to>|first commit")
        self.assertEqual(ledger()[0]["ref"], out["commit"])
        # The token is in no file the push left behind.
        for path in (self.root / "state").rglob("*"):
            if path.is_file():
                self.assertNotIn(TOKEN.encode(), path.read_bytes(), path)

    def test_never_forces(self):
        self.me()
        self.workdir()
        remote = self.remote("teenydroid/hello")
        actions.push(self.body())
        # Somebody else moved the remote on; an unrelated local history cannot overwrite it.
        other = self.workdir("other", {"x.txt": "x"})
        git("init", "-q", "-b", "main", str(other))
        git("-C", str(other), "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-q",
                        "--allow-empty", "-m", "theirs")
        git("-C", str(other), "push", "-q", "-f", str(remote), "main")
        (self.root / "state/workbench/hello/new.txt").write_text("new")
        with self.assertRaises(hand.HandError):
            actions.push(self.body(message="second"))

    def test_dry_run_scans_but_commits_nothing(self):
        self.configure(DRY_RUN="1")
        path = self.workdir()
        out = actions.push(self.body())
        self.assertEqual((out["dry_run"], out["would_push"]), (True, "teenydroid/hello"))
        self.assertFalse((path / ".git").exists())
        self.assertEqual(self.gh.seen, [])

    def test_only_from_the_workbench(self):
        self.workdir()
        (self.root / "secrets" / "x").mkdir()
        self.refused(self.body(workdir="secrets/x"), "workdir")
        self.refused(self.body(workdir="state/workbench/../../secrets/x"), "workdir")
        self.refused(self.body(workdir="state/workbench"), "workdir")
        (self.root / "state/workbench/link").symlink_to(self.root / "secrets")
        self.refused(self.body(workdir="state/workbench/link"), "workdir")

    def test_only_to_an_own_repo(self):
        self.workdir()
        self.refused(self.body(repo="tinyhumansai/openhuman"), "owner")

    def test_refuses_secret_paths_and_names_them(self):
        for rel in (".env", ".env.local", "secrets/teeny.env", "state/hands.db", "deploy/key.pem", "id_ed25519"):
            with self.subTest(rel=rel):
                self.workdir(f"p{abs(hash(rel))}", {"README.md": "hi", rel: "x"})
                error = self.refused(self.body(workdir=f"state/workbench/p{abs(hash(rel))}"), "secrets")
                self.assertEqual(error.detail["findings"][0]["path"], rel)

    def test_refuses_secret_contents_without_repeating_them(self):
        cases = {
            "ghp_" + "a" * 36: "ghp_",
            "github_pat_" + "b" * 30: "github_pat_",
            "sk-" + "c" * 40: "sk-",
            "xoxb-1234567890-abcdef": "xox",
            "AKIA" + "ABCDEFGHIJKLMNOP": "AKIA",
            "-----BEGIN OPENSSH PRIVATE KEY-----": "private key",
        }
        for secret, rule in cases.items():
            with self.subTest(rule=rule):
                name = f"c{abs(hash(secret))}"
                self.workdir(name, {"config.py": f"KEY = '{secret}'\n"})
                error = self.refused(self.body(workdir=f"state/workbench/{name}"), "secrets")
                self.assertEqual(error.detail["findings"][0]["path"], "config.py")
                self.assertNotIn(secret, str(error.detail))

    def test_refuses_a_secret_already_in_the_history(self):
        path = self.workdir()
        git("init", "-q", "-b", "main", str(path))
        (path / "leak.txt").write_text("token = ghp_" + "z" * 36 + "\n")
        git("-C", str(path), "add", "-A")
        git("-C", str(path), "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-q", "-m", "x")
        (path / "leak.txt").unlink()
        error = self.refused(self.body(), "secrets")
        self.assertEqual(error.detail["findings"][0]["path"], "(history)")

    def test_an_ignored_secret_is_not_published_so_not_refused(self):
        self.me()
        path = self.workdir(files={"README.md": "hi", ".gitignore": ".env\n", ".env": "X=ghp_" + "q" * 36})
        git("init", "-q", "-b", "main", str(path))
        self.remote("teenydroid/hello")
        # .gitignore itself matches ".env*"? No: it is ".gitignore". The ignored .env is not published.
        actions.push(self.body())

    def test_a_held_message_pushes_nothing(self):
        self.workdir()
        with self.assertRaises(hand.Refused) as caught:
            actions.push(self.body(message="this is fucking great"))
        self.assertEqual(caught.exception.reason, "held")


class PushBranchTest(Scratch):
    """A branch of a clone whose git directory lives under state/ (Teeny's
    own clone, teeny_hands.build.selfclone), pushed for a pull request."""

    def setUp(self):
        super().setUp()
        self.clone = self.root / "state" / "self" / "brain"
        git("init", "-q", "-b", "main", str(self.clone))
        self.commit({"README.md": "# brain\n", "fixture.py": "KEY = 'ghp_" + "f" * 36 + "'\n"}, "base")
        git("-C", str(self.clone), "update-ref", "refs/remotes/origin/main", "HEAD")
        git("-C", str(self.clone), "checkout", "-q", "-b", "teeny/briefer")
        self.commit({"README.md": "# brain, briefer\n"}, "briefer")
        self.remote_path = self.remote("teenydroid/brain")

    def commit(self, files, message):
        for rel, text in files.items():
            (self.clone / rel).write_text(text)
        git("-C", str(self.clone), "add", "-A")
        git("-C", str(self.clone), "-c", "user.name=Teeny", "-c", "user.email=t@e", "commit", "-q", "-m", message)

    def push(self, **more):
        args = {"git_dir": self.clone / ".git", "repo": "teenydroid/brain", "branch": "teeny/briefer",
                "since": "refs/remotes/origin/main", **more}
        return actions.push_branch(**args)

    def refused(self, rule, **more):
        with self.assertRaises(hand.Refused) as caught:
            self.push(**more)
        self.assertEqual(caught.exception.detail.get("rule"), rule)
        return caught.exception

    def test_pushes_the_branch_and_records_it(self):
        self.me()
        out = self.push()
        head = git("-C", str(self.clone), "rev-parse", "HEAD").strip()
        self.assertEqual((out["pushed"], out["branch"], out["commit"]), ("teenydroid/brain", "teeny/briefer", head))
        self.assertEqual(git("--git-dir", str(self.remote_path), "rev-parse", "teeny/briefer").strip(), head)
        self.assertEqual([(r["verb"], r["ref"]) for r in ledger()], [("push", head)])

    def test_only_a_teeny_branch_never_main(self):
        git("-C", str(self.clone), "branch", "-q", "elsewhere")
        for branch in ("main", "elsewhere"):
            with self.subTest(branch=branch):
                self.refused("branch", branch=branch)
        self.assertEqual(self.gh.seen, [])

    def test_only_to_an_own_repo_and_from_under_state(self):
        self.refused("owner", repo="tinyhumansai/openhuman")
        outside = Path(self.tmp.name) / "elsewhere"
        outside.mkdir()
        self.refused("git_dir", git_dir=outside)

    def test_scans_what_the_branch_adds_not_what_main_already_has(self):
        self.me()
        self.push()  # the fixture on main is not this branch's to vouch for
        self.commit({"config.py": "TOKEN = 'ghp_" + "b" * 36 + "'\n"}, "oops")
        error = self.refused("secrets")
        self.assertIn("config.py", [f["path"] for f in error.detail["findings"]])

    def test_dry_run_pushes_nothing(self):
        self.configure(DRY_RUN="1")
        out = self.push()
        self.assertEqual((out["dry_run"], out["would_push"], out["branch"]), (True, "teenydroid/brain", "teeny/briefer"))
        self.assertIsNone(subprocess.run(["git", "--git-dir", str(self.remote_path), "rev-parse", "-q", "--verify",
                                          "teeny/briefer"], capture_output=True, text=True).stdout.strip() or None)
        self.assertEqual((self.gh.seen, ledger()), ([], []))


class OpenPrTest(Scratch):
    def setUp(self):
        super().setUp()
        self.created = self.gh.route("POST", "/repos/a/b/pulls", {"number": 12, "html_url": "https://github.com/a/b/pull/12"}, status=201)
        self.gh.route("POST", "/repos/teenydroid/hello/pulls", {"number": 3, "html_url": "u"}, status=201)
        self.gh.route("GET", "/search/issues", {"total_count": 0, "items": []})

    def body(self, **more):
        return {"repo": "a/b", "head": "fix-typo", "base": "main", "title": "fix a typo", "body": "one letter.", **more}

    def sent(self):
        return self.created_requests()[-1].json()

    def created_requests(self):
        return self.gh.requests("POST", "/repos/a/b/pulls")

    def test_an_external_pr_carries_the_disclosure_and_teenys_head(self):
        out = actions.open_pr(self.body())
        self.assertEqual((out["number"], out["external"]), (12, True))
        sent = self.sent()
        self.assertEqual(sent["head"], "teenydroid:fix-typo")
        self.assertEqual(sent["body"], "one letter.\n\n" + actions.DISCLOSURE)
        self.assertTrue(actions.DISCLOSURE.startswith("—\nopened by Teeny, an AI agent (github.com/teenydroid), operated by @senamakel."))
        self.assertEqual([(r["verb"], r["external"], r["ref"]) for r in ledger()], [("open-pr", 1, "12")])

    def test_an_own_pr_has_no_footer(self):
        out = actions.open_pr(self.body(repo="teenydroid/hello"))
        self.assertFalse(out["external"])
        self.assertEqual(self.gh.requests("POST", "/repos/teenydroid/hello/pulls")[0].json()["body"], "one letter.")

    def test_refuses_where_ai_is_not_welcome(self):
        self.file("a/b", "CONTRIBUTING.md", "We do not accept AI-generated contributions.")
        with self.assertRaises(hand.Refused) as caught:
            actions.open_pr(self.body())
        self.assertEqual(caught.exception.detail["rule"], "contributing")
        self.assertEqual(self.created_requests(), [])

    def test_refuses_a_second_open_pr_in_one_repo(self):
        self.open_prs("a/b", 1)
        with self.assertRaises(hand.Refused) as caught:
            actions.open_pr(self.body())
        self.assertEqual(caught.exception.detail["rule"], "open_pr")
        self.assertEqual(self.created_requests(), [])

    def test_refuses_over_the_daily_cap(self):
        self.configure(GH_MAX_EXTERNAL_PRS_PER_DAY="1")
        actions.open_pr(self.body())
        self.gh.route("POST", "/repos/c/d/pulls", {"number": 1}, status=201)
        with self.assertRaises(hand.Refused) as caught:
            actions.open_pr(self.body(repo="c/d"))
        self.assertEqual(caught.exception.detail["rule"], "daily_cap")
        # A new UTC day is a new allowance; own repos never count.
        actions.open_pr(self.body(repo="teenydroid/hello"))
        import os
        os.environ["TEENY_NOW"] = str(T0 + 86400)
        actions.open_pr(self.body(repo="c/d"))

    def test_dry_run_runs_the_rules_and_opens_nothing(self):
        self.configure(DRY_RUN="1")
        out = actions.open_pr(self.body())
        self.assertEqual((out["dry_run"], out["would_open_pr"]), (True, "a/b"))
        self.assertTrue(out["body"].endswith(actions.DISCLOSURE))
        self.assertEqual(self.gh.writes(), [])
        self.assertEqual(ledger(), [])

    def test_dry_run_on_an_own_repo_needs_no_token(self):
        self.configure(DRY_RUN="1", GITHUB_TOKEN=None)
        self.assertTrue(actions.open_pr(self.body(repo="teenydroid/hello"))["dry_run"])

    def test_the_guard_reads_title_and_body_together(self):
        with self.assertRaises(hand.Refused) as caught:
            actions.open_pr(self.body(body="this is fucking great"))
        self.assertEqual((caught.exception.reason, caught.exception.detail["field"]), ("held", "body"))
        held = holds.get(caught.exception.detail["hold_id"])
        self.assertEqual((held["context"]["surface"], held["context"]["post_kind"]), ("github", "pr"))
        self.assertEqual(self.gh.seen, [])

    def test_publishes_the_guards_rewrite(self):
        guard = mock.Mock(return_value=SimpleNamespace(kind="allow", fields={"title": "T", "body": "B"}))
        with mock.patch.object(actions, "check_fields", guard):
            actions.open_pr(self.body(repo="teenydroid/hello"))
        self.assertEqual(guard.call_args.kwargs["surface"], "github")
        sent = self.gh.requests("POST", "/repos/teenydroid/hello/pulls")[0].json()
        self.assertEqual((sent["title"], sent["body"]), ("T", "B"))

    def test_refuses_a_head_from_somebody_elses_fork(self):
        with self.assertRaises(hand.Refused):
            actions.open_pr(self.body(head="evil:main"))


class CommentAndForkTest(Scratch):
    def test_comments_through_the_guard(self):
        self.gh.route("POST", "/repos/a/b/issues/4/comments", {"id": 9, "html_url": "u"}, status=201)
        out = actions.comment({"repo": "a/b", "number": 4, "body": "@alice thanks — merged"})
        self.assertEqual(out["number"], 4)
        self.assertEqual(self.gh.requests("POST", "/repos/a/b/issues/4/comments")[0].json(), {"body": "@alice thanks, merged"})
        self.assertEqual(ledger()[0]["verb"], "comment")

    def test_comment_dry_run(self):
        self.configure(DRY_RUN="1")
        self.assertTrue(actions.comment({"repo": "a/b", "number": 4, "body": "hi"})["dry_run"])
        self.assertEqual(self.gh.seen, [])

    def test_forks_others_repos_only(self):
        self.me()
        self.gh.route("POST", "/repos/a/b/forks", {"full_name": "teenydroid/b", "owner": {"login": "teenydroid"}}, status=202)
        self.assertEqual(actions.fork({"repo": "a/b"})["repo"]["full_name"], "teenydroid/b")
        with self.assertRaises(hand.Refused):
            actions.fork({"repo": "teenydroid/b"})


class IssueTest(Scratch):
    def setUp(self):
        super().setUp()
        self.opened = self.gh.route("POST", "/repos/teenydroid/brain/issues",
                                    {"number": 41, "html_url": "https://github.com/teenydroid/brain/issues/41"}, status=201)
        self.gh.route("POST", "/repos/teenydroid/brain/issues/41/comments", {"id": 5, "html_url": "c"}, status=201)
        self.closed = self.gh.route("PATCH", "/repos/teenydroid/brain/issues/41",
                                    {"number": 41, "state": "closed", "html_url": "u"})

    def test_opens_an_issue_on_an_own_repo_through_the_guard(self):
        out = actions.open_issue({"repo": "teenydroid/brain", "title": "the model — is down", "body": "since noon."})
        self.assertEqual((out["opened_issue"], out["number"]), ("teenydroid/brain", 41))
        self.assertEqual(self.gh.requests("POST", "/repos/teenydroid/brain/issues")[0].json(),
                         {"title": "the model, is down", "body": "since noon."})
        self.assertEqual([(r["verb"], r["ref"]) for r in ledger()], [("open-issue", "41")])

    def test_opens_issues_on_own_repos_only(self):
        with self.assertRaises(hand.Refused) as caught:
            actions.open_issue({"repo": "a/b", "title": "t", "body": "b"})
        self.assertEqual(caught.exception.detail["rule"], "owner")
        self.assertEqual(self.gh.seen, [])

    def test_open_issue_dry_run_writes_nothing(self):
        self.configure(DRY_RUN="1", GITHUB_TOKEN=None)
        out = actions.open_issue({"repo": "teenydroid/brain", "title": "t", "body": "b"})
        self.assertEqual((out["dry_run"], out["would_open_issue"]), (True, "teenydroid/brain"))
        self.assertEqual(self.gh.seen, [])
        self.assertEqual(ledger(), [])

    def test_a_held_issue_opens_nothing(self):
        with self.assertRaises(hand.Refused) as caught:
            actions.open_issue({"repo": "teenydroid/brain", "title": "t", "body": "this is fucking great"})
        self.assertEqual(caught.exception.reason, "held")
        self.assertEqual(self.gh.seen, [])

    def test_an_operator_script_may_open_and_close_without_the_judge(self):
        # The judge on and no core to ask: it fails closed, so a judged
        # issue is held, and an unjudged one (the health alarm's) goes.
        self.configure(SAFETY_JUDGE="1")
        issue = {"repo": "teenydroid/brain", "title": "t", "body": "b"}
        with self.assertRaises(hand.Refused):
            actions.open_issue(issue)
        self.assertEqual(actions.open_issue(issue, judge=False)["number"], 41)
        with self.assertRaises(hand.Refused):
            actions.close_issue({"repo": "teenydroid/brain", "number": 41, "comment": "c"})
        actions.close_issue({"repo": "teenydroid/brain", "number": 41, "comment": "c"}, judge=False)
        self.assertEqual(len(self.gh.requests("PATCH", "/repos/teenydroid/brain/issues/41")), 1)

    def test_closes_an_own_issue_with_a_last_comment(self):
        out = actions.close_issue({"repo": "teenydroid/brain", "number": 41, "comment": "back — all good."})
        self.assertEqual((out["closed_issue"], out["number"]), ("teenydroid/brain", 41))
        self.assertEqual(self.gh.requests("POST", "/repos/teenydroid/brain/issues/41/comments")[0].json(),
                         {"body": "back, all good."})
        self.assertEqual(self.gh.requests("PATCH", "/repos/teenydroid/brain/issues/41")[0].json(),
                         {"state": "closed", "state_reason": "completed"})
        self.assertEqual([r["verb"] for r in ledger()], ["comment", "close-issue"])

    def test_closes_without_a_comment(self):
        actions.close_issue({"repo": "teenydroid/brain", "number": 41})
        self.assertEqual(self.gh.requests("POST"), [])
        self.assertEqual(len(self.gh.requests("PATCH", "/repos/teenydroid/brain/issues/41")), 1)

    def test_closes_issues_on_own_repos_only_and_previews_under_dry_run(self):
        with self.assertRaises(hand.Refused):
            actions.close_issue({"repo": "a/b", "number": 1})
        self.configure(DRY_RUN="1")
        out = actions.close_issue({"repo": "teenydroid/brain", "number": 41, "comment": "ok"})
        self.assertEqual((out["dry_run"], out["would_close_issue"], out["comment"]), (True, "teenydroid/brain", "ok"))
        self.assertEqual(self.gh.seen, [])

    def test_issue_bodies(self):
        for body in (None, {}, {"repo": "teenydroid/brain", "title": "t"}, {"repo": "teenydroid/brain", "title": "t", "body": "b", "labels": []}):
            with self.assertRaises(hand.HandError, msg=body):
                actions.open_issue(body)
        for body in ({"repo": "teenydroid/brain"}, {"repo": "teenydroid/brain", "number": 0}, {"repo": "teenydroid/brain", "number": 1, "comment": 5}):
            with self.assertRaises(hand.HandError, msg=body):
                actions.close_issue(body)
