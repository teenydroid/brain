"""evals/services.toml: the operator's list of services the key proxy
(hands/call) may call, and the loader that holds it to its shape."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "hands" / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.call import services  # noqa: E402

GOOD = '''
[services.fake-svc]
base = "https://api.example.com/v1"
key = "FAKE_API_KEY"
surface = "moltbook"
agent = "teeny-call-fake"
allow = [
  { method = "GET", path = "/[a-z/]{1,50}" },
  { method = "POST", path = "/notes" },
]
'''


class LoadTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = Path(tmp.name) / "services.toml"

    def load(self, text, **kw):
        self.file.write_text(text)
        return services.load(self.file, **kw)

    def refused(self, text, why, **kw):
        with self.assertRaises(hand.HandError) as caught:
            self.load(text, **kw)
        self.assertIn(why, str(caught.exception))

    def test_a_good_file_loads(self):
        found = self.load(GOOD)
        self.assertEqual(list(found), ["fake-svc"])
        svc = found["fake-svc"]
        self.assertEqual((svc.base, svc.key, svc.surface, svc.agent),
                         ("https://api.example.com/v1", "FAKE_API_KEY", "moltbook", "teeny-call-fake"))
        self.assertTrue(svc.allows("GET", "/a/b"))
        self.assertFalse(svc.allows("GET", "/A"))
        self.assertTrue(svc.allows("POST", "/notes"))
        self.assertFalse(svc.allows("POST", "/notes/1"), "the whole path must match")
        self.assertFalse(svc.allows("DELETE", "/notes"))

    def test_the_setting_names_follow_the_service(self):
        svc = self.load(GOOD)["fake-svc"]
        self.assertEqual((svc.reads_setting, svc.writes_setting),
                         ("CALL_FAKE_SVC_READS_PER_DAY", "CALL_FAKE_SVC_WRITES_PER_DAY"))

    def test_a_base_that_is_not_https_is_refused(self):
        for base in ("http://api.example.com/v1", "ftp://api.example.com", "api.example.com/v1", "//api.example.com"):
            self.refused(GOOD.replace("https://api.example.com/v1", base), "https", )

    def test_a_base_with_userinfo_query_or_fragment_is_refused(self):
        for base, why in (("https://user:pw@api.example.com/v1", "user"), ("https://user@api.example.com", "user"),
                          ("https://api.example.com/v1?k=1", "query"), ("https://api.example.com/v1#f", "fragment"),
                          ("https://api.example.com/v1/../x", "path"), ("https://api.example.com/v 1", "path")):
            self.refused(GOOD.replace("https://api.example.com/v1", base), why)

    def test_a_bare_ip_or_localhost_is_refused(self):
        for base in ("https://127.0.0.1/v1", "https://10.0.0.1", "https://[::1]/v1", "https://localhost/v1",
                     "https://api.localhost/v1", "https://intranet/v1"):
            self.refused(GOOD.replace("https://api.example.com/v1", base), "host")

    def test_loopback_only_through_the_tests_own_argument(self):
        text = GOOD.replace("https://api.example.com/v1", "http://127.0.0.1:8080/v1")
        self.refused(text, "https")
        self.assertEqual(self.load(text, loopback=True)["fake-svc"].base, "http://127.0.0.1:8080/v1")
        # Even then, nowhere else over plain http.
        self.refused(GOOD.replace("https://api.example.com/v1", "http://api.example.com/v1"), "https", loopback=True)

    def test_a_bad_key_name_is_refused(self):
        for key in ("fake_api_key", "FAKE_API", "FAKE-API-KEY", "1_KEY", "", "FAKE_PASSWORD"):
            self.refused(GOOD.replace('"FAKE_API_KEY"', f'"{key}"'), "key")

    def test_a_bad_regex_is_refused(self):
        self.refused(GOOD.replace('"/notes"', '"/notes(["'), "regex")

    def test_a_method_off_the_list_is_refused(self):
        self.refused(GOOD.replace('"POST"', '"CONNECT"'), "method")
        self.refused(GOOD.replace('"POST"', '"post"'), "method")

    def test_an_unknown_key_is_refused(self):
        self.refused(GOOD + 'follow_redirects = true\n', "follow_redirects")
        self.refused(GOOD.replace('{ method = "POST", path = "/notes" }',
                                  '{ method = "POST", path = "/notes", guard = false }'), "guard")
        self.refused("[other]\nx = 1\n" + GOOD, "other")

    def test_a_missing_field_is_refused(self):
        self.refused(GOOD.replace('surface = "moltbook"\n', ""), "surface")
        self.refused(GOOD.replace('agent = "teeny-call-fake"\n', ""), "agent")

    def test_an_unknown_surface_is_refused(self):
        self.refused(GOOD.replace('"moltbook"', '"nowhere"'), "surface")

    def test_a_bad_service_name_is_refused(self):
        self.refused(GOOD.replace("[services.fake-svc]", '[services."Fake Svc"]'), "name")

    def test_an_empty_allow_list_and_a_file_that_is_not_toml_are_refused(self):
        self.refused(GOOD.split("allow = [")[0] + "allow = []\n", "allow")
        self.refused("this is not toml [", "services.toml")

    def test_a_missing_file_is_an_error(self):
        with self.assertRaises(hand.HandError):
            services.load(self.file)


class ShippedTest(unittest.TestCase):
    def test_the_shipped_file_validates_and_lists_only_moltbook(self):
        found = services.load(services.SERVICES_FILE)
        self.assertEqual(list(found), ["moltbook"])
        moltbook = found["moltbook"]
        self.assertEqual(moltbook.base, "https://www.moltbook.com/api/v1")
        self.assertEqual(moltbook.key, "MOLTBOOK_API_KEY")
        self.assertEqual(moltbook.surface, "moltbook")
        self.assertEqual(services.SERVICES_FILE, REPO / "evals" / "services.toml")

    def test_the_shipped_moltbook_entry_only_reads(self):
        moltbook = services.load(services.SERVICES_FILE)["moltbook"]
        self.assertEqual({method for method, _ in moltbook.allow}, {"GET"})
        self.assertTrue(moltbook.allows("GET", "/posts/abc/comments"))
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in ("/posts", "/posts/abc/upvote", "/agents/me", "/agents/register"):
                self.assertFalse(moltbook.allows(method, path), (method, path))


if __name__ == "__main__":
    unittest.main()
