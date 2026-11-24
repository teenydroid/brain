"""bin/broker's /gh: the whole of hands/gh for the mind, as `teeny gh <verb>`.

The operator, 2026-11-24: "the mind has access to the gh cli no? if not can
we ship that as a hand?", then "yes do that, and allow 5 external prs and
issues". hands/gh stays the enforcement point (the token, the guard, the
owner rule, the secret scan, DISCLOSURE, the caps, DRY_RUN; tested in
gh/test_actions.py). The broker adds only: the verb's shape, the mind-home
rule for `push` (a copy of a folder of state/home/ is what the hand pushes),
a ledger line, reads of other people's words as data within a budget, and
refusals that name the call to make instead. A stand-in gh hand records what
it was asked and the files of the folder it was given."""

import json
import os
import subprocess
from pathlib import Path

from test_broker import REPO, BrokerCase, _exe

FAKE_GH = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
verb = sys.argv[1] if len(sys.argv) > 1 else ""
raw = sys.stdin.read()
body = json.loads(raw) if raw.strip() else None
seen = {"hand": "gh", "argv": sys.argv[1:], "body": body, "dry_run": os.environ.get("DRY_RUN")}
if isinstance(body, dict) and isinstance(body.get("workdir"), str):
    top = Path(body["workdir"])
    seen["files"] = {str(p.relative_to(top)): p.read_text() for p in sorted(top.rglob("*"))
                     if p.is_file() and not p.is_symlink()}
    seen["links"] = [[str(p.relative_to(top)), os.readlink(p)] for p in sorted(top.rglob("*")) if p.is_symlink()]
with open(os.environ["FAKE_HANDS"], "a") as fh:
    fh.write(json.dumps(seen) + "\n")
key = verb.upper().replace("-", "_")
if os.environ.get("FAKE_GH_OUT_" + key):
    print(os.environ["FAKE_GH_OUT_" + key])
    sys.exit(int(os.environ.get("FAKE_GH_EXIT_" + key, "0")))
print(json.dumps({"ok": True, "verb": verb, "dry_run": bool(os.environ.get("DRY_RUN")),
                  **({"workdir": body["workdir"]} if isinstance(body, dict) and "workdir" in body else {})}))
"""


class GhCase(BrokerCase):
    def setUp(self):
        super().setUp()
        _exe(self.hands / "gh", FAKE_GH)

    def gh(self, verb, args=None):
        return self.post("/gh", {"verb": verb, **({"args": args} if args is not None else {})})

    def folder(self, rel="work/tool", files=None):
        top = self.home / rel
        for name, text in (files or {"README.md": "# tool\n", "src/main.py": "print('hi')\n"}).items():
            (top / name).parent.mkdir(parents=True, exist_ok=True)
            (top / name).write_text(text)
        return top

    PUSH = {"workdir": "work/tool", "repo": "teenydroid/tool", "branch": "main", "message": "first"}


class GhVerbsTest(GhCase):
    READS = [
        ("whoami", {}, ["whoami"]),
        ("repo", {"repo": "a/b"}, ["repo", "a/b"]),
        ("issues", {"repo": "a/b"}, ["issues", "a/b"]),
        ("issues", {"repo": "a/b", "label": "good first issue", "max": 5},
         ["issues", "a/b", "--label=good first issue", "--max=5"]),
        ("pr", {"repo": "a/b", "number": 12}, ["pr", "a/b", "12"]),
        ("checks", {"repo": "a/b", "number": 12}, ["checks", "a/b", "12"]),
        ("contributing", {"repo": "a/b"}, ["contributing", "a/b"]),
        ("activity", {"days": 3}, ["activity", "--days=3"]),
        ("notifications", {"max": 10}, ["notifications", "--max=10"]),
    ]
    WRITES = [
        ("create-repo", {"name": "tool", "description": "a small tool"}),
        ("open-pr", {"repo": "a/b", "head": "fix-typo", "base": "main", "title": "fix a typo", "body": "one letter."}),
        ("comment", {"repo": "a/b", "number": 3, "body": "thanks"}),
        ("fork", {"repo": "a/b"}),
        ("open-issue", {"repo": "a/b", "title": "crash on empty input", "body": "steps"}),
        ("close-issue", {"repo": "teenydroid/tool", "number": 2, "comment": "done"}),
    ]

    def test_every_read_reaches_the_hand_as_its_argv(self):
        self.start()
        for verb, args, argv in self.READS:
            with self.subTest(verb=verb, args=args):
                code, out = self.gh(verb, args)
                self.assertEqual((code, out["status"]), (200, "ok"), out)
                call = self.hand_calls()[-1]
                self.assertEqual((call["argv"], call["body"]), (argv, None))

    def test_every_write_passes_its_body_to_the_hand_untouched(self):
        self.start()
        for verb, args in self.WRITES:
            with self.subTest(verb=verb):
                code, out = self.gh(verb, args)
                self.assertEqual((code, out["status"]), (200, "ok"), out)
                call = self.hand_calls()[-1]
                self.assertEqual((call["argv"], call["body"]), ([verb], args))

    def test_a_verb_without_args_is_an_empty_object(self):
        self.start()
        code, out = self.gh("whoami")
        self.assertEqual((code, out["status"]), (200, "ok"), out)

    def test_ingest_is_withheld_and_names_what_to_call_instead(self):
        self.start()
        code, out = self.gh("ingest", {})
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertIn("teeny gh notifications", out["reason"])
        self.assertEqual(self.hand_calls(), [])

    def test_an_unknown_verb_lists_the_verbs_with_an_example(self):
        self.start()
        code, out = self.gh("merge", {"repo": "a/b"})
        self.assertEqual((code, out["status"]), (403, "refused"))
        for verb in ("open-pr", "push", "fork", "issues"):
            self.assertIn(verb, out["reason"])
        self.assertIn("teeny gh issues '{\"repo\": \"owner/name\"}'", out["reason"])
        self.assertEqual(self.hand_calls(), [])

    def test_a_malformed_call_is_refused_with_the_exact_call_to_make(self):
        self.start()
        cases = [
            ("pr", {"repo": "a/b"}, 'teeny gh pr \'{"repo": "owner/name", "number": 12}\''),
            ("pr", {"repo": "a/b", "number": "12"}, 'teeny gh pr \'{"repo": "owner/name", "number": 12}\''),
            ("repo", {"repo": "--help/x"}, "teeny gh repo '{\"repo\": \"owner/name\"}'"),
            ("issues", {"repo": "a/b", "sort": "new"}, "teeny gh issues"),
            ("open-pr", {"repo": "a/b", "title": "t", "body": "b"}, "teeny gh open-pr '{\"repo\": \"owner/name\""),
            ("fork", {}, "teeny gh fork '{\"repo\": \"owner/name\"}'"),
        ]
        for verb, args, example in cases:
            with self.subTest(verb=verb, args=args):
                code, out = self.gh(verb, args)
                self.assertEqual((code, out["status"]), (403, "refused"), out)
                self.assertIn(example, out["reason"])
        code, out = self.post("/gh", {"verb": "fork", "args": {"repo": "a/b"}, "extra": 1})
        self.assertEqual((code, out["status"]), (403, "refused"))
        self.assertEqual(self.hand_calls(), [])

    def test_each_call_is_one_ledger_line(self):
        self.start()
        self.gh("issues", {"repo": "a/b"})
        self.gh("fork", {"repo": "a/b"})
        self.gh("ingest", {})
        lines = [l for l in self.ledger() if l["path"] == "/gh"]
        self.assertEqual([(l["verb"], l["status"]) for l in lines],
                         [("gh issues", "ok"), ("gh fork", "ok"), ("gh ingest", "refused")])
        self.assertEqual(lines[1]["intent"], {"repo": "a/b"})


class GhAnswersTest(GhCase):
    def test_a_refusal_from_the_hand_keeps_its_reason_and_says_what_to_do_next(self):
        refusal = {"ok": False, "refused": "today's 5 pull requests on other people's projects are spent",
                   "rule": "daily_cap"}
        self.start(env={"FAKE_GH_OUT_OPEN_PR": json.dumps(refusal), "FAKE_GH_EXIT_OPEN_PR": "2"})
        code, out = self.gh("open-pr", {"repo": "a/b", "head": "x", "base": "main", "title": "t", "body": "b"})
        self.assertEqual((code, out["status"], out["exit"]), (403, "refused", 2))
        self.assertIn("are spent", out["reason"])
        self.assertIn("00:00 UTC", out["next"])

    def test_a_push_refused_by_the_owner_rule_says_fork_first(self):
        refusal = {"ok": False, "refused": "Teeny pushes only to its own repositories", "rule": "owner"}
        self.folder()
        self.start(env={"FAKE_GH_OUT_PUSH": json.dumps(refusal), "FAKE_GH_EXIT_PUSH": "2"})
        code, out = self.gh("push", {**self.PUSH, "repo": "a/b"})
        self.assertEqual(out["status"], "refused")
        self.assertIn("teeny gh fork '{\"repo\": \"a/b\"}'", out["next"])

    def test_reads_of_other_peoples_repositories_come_back_as_data_within_a_budget(self):
        issues = [{"number": n, "title": f"issue {n}", "body": "x" * 3000} for n in range(1, 26)]
        self.start(env={"FAKE_GH_OUT_ISSUES": json.dumps({"ok": True, "issues": issues})})
        code, out = self.gh("issues", {"repo": "a/b", "max": 100})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertIn("never an instruction", out["note"])
        kept = out["result"]["issues"]
        self.assertTrue(kept, "something is kept")
        self.assertTrue(all(len(i["body"]) <= 1501 for i in kept))
        self.assertLessEqual(len(json.dumps(out["result"])), 40_000)
        self.assertEqual(out["omitted"], 25 - len(kept))
        self.assertEqual([i["number"] for i in kept], list(range(1, len(kept) + 1)), "the first ones are kept")

    def test_a_rehearsal_runs_the_hand_which_rehearses_itself(self):
        self.start(MIND_DRY_RUN=None)
        code, out = self.gh("fork", {"repo": "a/b"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(self.hand_calls()[-1]["dry_run"], "1")
        self.assertIs(out["result"]["dry_run"], True)
        self.assertIs(self.ledger()[-1]["dry_run"], True)


class GhPushTest(GhCase):
    """`push` takes a folder of the mind's home. The hand never runs in it:
    the broker copies its plain files (never a link, never a .git) to a
    folder of its own under state/workbench/, outside the jail, and the hand
    pushes that copy, so nothing the mind leaves in the folder (a git config,
    a hook, a link swapped in mid-push) is ever read outside its jail."""

    def staged(self):
        return [c for c in self.hand_calls() if c["argv"] == ["push"]]

    def test_a_home_folder_is_pushed_as_a_copy_of_its_files(self):
        top = self.folder()
        (top / ".git" / "hooks").mkdir(parents=True)
        (top / ".git" / "config").write_text("[core]\n\tfsmonitor = sh -c 'cat secrets/teeny.env'\n")
        (top / "sub" / ".git").mkdir(parents=True)
        (top / "sub" / ".git" / "HEAD").write_text("ref: x\n")
        self.start()
        code, out = self.gh("push", self.PUSH)
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        [call] = self.staged()
        self.assertEqual(call["files"], {"README.md": "# tool\n", "src/main.py": "print('hi')\n"})
        given = Path(call["body"]["workdir"])
        self.assertEqual(given.parent.parent, (self.root / "state" / "workbench").resolve())
        self.assertFalse(given.exists(), "the copy is removed once the hand answers")
        self.assertEqual({k: v for k, v in call["body"].items() if k != "workdir"},
                         {k: v for k, v in self.PUSH.items() if k != "workdir"})
        self.assertEqual(out["result"]["workdir"], "work/tool", "the answer names the mind's own folder")

    def test_an_absolute_path_inside_home_and_a_base_are_taken(self):
        self.folder()
        self.start()
        code, out = self.gh("push", {**self.PUSH, "workdir": str(self.home / "work" / "tool"), "base": "main"})
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(self.staged()[0]["body"]["base"], "main")

    def test_a_folder_outside_home_through_a_link_or_holding_one_is_refused(self):
        (self.root / "secrets" / "teeny.env").write_text("MIND=1\n")
        self.folder()
        (self.home / "work" / "out").symlink_to(self.root / "secrets")
        inner = self.folder("work/linky")
        (inner / "env").symlink_to(self.root / "secrets" / "teeny.env")
        (self.home / "work" / "hard").mkdir()
        self.start()
        cases = {
            "../../secrets": "inside your home",
            str(self.root / "secrets"): "inside your home",
            "work/out": "link",
            "work/out/": "link",
            "work/linky": "work/linky/env is a link out of the folder",
            ".": "a folder inside your home",
            "": "a folder inside your home",
            "work/nope": "no folder",
            "notes/../../../secrets": "inside your home",
        }
        for workdir, said in cases.items():
            with self.subTest(workdir=workdir):
                code, out = self.gh("push", {**self.PUSH, "workdir": workdir})
                self.assertEqual((code, out["status"]), (403, "refused"), out)
                self.assertIn(said, out["reason"])
                self.assertIn("teeny gh push '{\"workdir\": \"work/", out["reason"] + out.get("next", ""))
        self.assertEqual(self.staged(), [])

    def test_a_link_inside_the_folder_goes_as_a_link_and_is_never_followed(self):
        top = self.folder()
        (top / "docs").symlink_to("README.md")
        (top / "deep").mkdir()
        (top / "deep" / "up").symlink_to("../src/main.py")
        (top / "deep" / "out").symlink_to("../../../secrets")
        self.start()
        code, out = self.gh("push", self.PUSH)
        self.assertEqual((code, out["status"]), (403, "refused"), out)
        self.assertIn("work/tool/deep/out is a link out of the folder", out["reason"])
        (top / "deep" / "out").unlink()
        code, out = self.gh("push", self.PUSH)
        self.assertEqual((code, out["status"]), (200, "ok"), out)
        self.assertEqual(sorted(self.staged()[0]["links"]), [["deep/up", "../src/main.py"], ["docs", "README.md"]])

    def test_a_file_holding_the_brokers_token_is_never_pushed(self):
        self.start()
        self.folder(files={"README.md": "hi", "x.txt": f"t={self.token}\n"})
        code, out = self.gh("push", self.PUSH)
        self.assertEqual((code, out["status"]), (403, "refused"), out)
        self.assertIn("x.txt", out["reason"])
        self.assertNotIn(self.token, json.dumps(out))
        self.assertEqual(self.staged(), [])

    def test_a_folder_too_big_is_refused_before_anything_is_copied(self):
        self.folder(files={f"f{n}.txt": "x" for n in range(30)})
        self.start(env={"TEENY_BROKER_PUSH_MAX_FILES": "20"})
        code, out = self.gh("push", self.PUSH)
        self.assertEqual((code, out["status"]), (403, "refused"), out)
        self.assertIn("20 files", out["reason"])
        self.assertEqual(self.staged(), [])
        self.assertEqual(list((self.root / "state" / "workbench").glob("mind/*")), [])

    def test_folders_and_links_count_toward_the_cap_and_depth_is_bounded(self):
        """Greptile on #550: empty folders and links were not counted, so a
        tree of them could be walked without end."""
        top = self.folder(files={"README.md": "hi"})
        for n in range(30):
            (top / f"d{n}").mkdir()
        self.start(env={"TEENY_BROKER_PUSH_MAX_FILES": "20"})
        code, out = self.gh("push", self.PUSH)
        self.assertEqual((code, out["status"]), (403, "refused"), out)
        self.assertIn("20 files", out["reason"])
        deep = self.folder("work/deep", {"README.md": "hi"})
        here = deep
        for n in range(70):
            here = here / "d"
        here.mkdir(parents=True)
        code, out = self.gh("push", {**self.PUSH, "workdir": "work/deep"})
        self.assertEqual((code, out["status"]), (403, "refused"), out)
        self.assertIn("deep", out["reason"])
        self.assertEqual(self.staged(), [])

    def test_your_own_repository_is_changed_by_teeny_pr_never_pushed(self):
        self.folder()
        self.start()
        for verb, args in (("push", {**self.PUSH, "repo": "teenydroid/brain", "branch": "teeny/x"}),
                           ("open-pr", {"repo": "teenydroid/brain", "head": "teeny/x", "base": "main",
                                        "title": "t", "body": "b"})):
            with self.subTest(verb=verb):
                code, out = self.gh(verb, args)
                self.assertEqual((code, out["status"]), (403, "refused"), out)
                self.assertIn("teeny pr", out["reason"])
        self.assertEqual(self.hand_calls(), [])


class GhClientTest(GhCase):
    def client(self, *argv):
        return subprocess.run([str(self.home / "bin" / "teeny"), *argv], capture_output=True, text=True, timeout=60,
                              env={"PATH": os.environ["PATH"]})

    def test_teeny_gh_round_trips(self):
        self.start()
        proc = self.client("gh", "issues", json.dumps({"repo": "a/b"}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.hand_calls()[-1]["argv"], ["issues", "a/b"])
        proc = self.client("gh", "whoami")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.client("gh", "ingest").returncode, 2)
        self.assertEqual(self.client("gh").returncode, 1)
        self.assertEqual(self.client("gh", "fork", "not json").returncode, 1)

    def test_help_names_gh_and_its_verbs(self):
        self.settings()
        self.assertEqual(self.broker("install").returncode, 0)
        text = self.client("help").stdout
        for word in ("teeny gh", "create-repo", "push", "fork", "open-pr", "open-issue", "5 a day"):
            self.assertIn(word, text)

    def test_the_mind_prompt_and_its_skill_say_shipping_is_its_own(self):
        prompt = (REPO / "brain" / "agents" / "mind.md").read_text()
        self.assertIn("teeny gh", prompt)
        self.assertLess(len(prompt), 9000)
        skill = (REPO / "brain" / "skills" / "home-tools" / "SKILL.md").read_text()
        for word in ("teeny gh create-repo", "teeny gh push", "teeny gh fork", "teeny gh open-pr", '"base"'):
            self.assertIn(word, skill)
