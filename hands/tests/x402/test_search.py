"""hands/x402 search, contents and crawl: Exa and Firecrawl paid per request
from Teeny's own wallet, only through hands/wallet's `x402 fetch` (the host
allowlist, the caps, the ledger). Results come back normalised and cut, a
repeated question is answered from the cache without paying twice, and a
request the wallet could not pay starts one top-up and is never retried.
The core is faked (wallet/support.py); nothing leaves the machine."""

from __future__ import annotations

import json

from wallet.support import Failure, Scratch
from teeny_hands import hand  # noqa: E402
from teeny_hands.wallet import store, x402 as wallet_x402  # noqa: E402
from teeny_hands.x402 import search  # noqa: E402

BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
FETCH = "openhuman.x402_fetch"


def exa_hit(n: int, text: str = "words " * 10) -> dict:
    return {"title": f"Page {n}", "url": f"https://example.com/{n}", "publishedDate": "2026-11-01T00:00:00.000Z",
            "author": f"author {n}", "id": f"https://example.com/{n}", "text": text}


def answer(body, status=200, paid=7_000, settled=True):
    def reply(params):
        out = {"status": status, "contentType": "application/json", "truncated": False,
               "body": body if isinstance(body, str) else json.dumps(body), "payment": None}
        if paid is not None:
            out["payment"] = {"paymentId": "p-1", "amountAtomic": paid, "asset": BASE_USDC, "recipient": "0x" + "3" * 40,
                              "network": "eip155:8453", "settled": settled}
        return out
    return reply


class SearchTest(Scratch):
    settings = {"WALLET_MAX_X402_PER_REQUEST_USD": "0.05", "WALLET_MAX_X402_PER_DAY_USD": "1"}

    def setUp(self):
        super().setUp()
        self.core.answers[FETCH] = answer({"requestId": "r", "results": [exa_hit(1), exa_hit(2)]})
        wallet_x402.hosts_add("api.exa.ai", "search")
        wallet_x402.hosts_add("api.firecrawl.dev", "search")

    def fetched(self):
        return self.core.params(FETCH)

    def sent_body(self, n=0):
        return json.loads(self.fetched()[n]["body"])

    def refused(self, call, rule):
        with self.assertRaises(hand.Refused) as caught:
            call()
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        return caught.exception

    def rows(self):
        conn = store.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM x402_requests ORDER BY id")]
        finally:
            conn.close()

    # --- request shapes -------------------------------------------------------------------

    def test_an_exa_search_posts_the_documented_body_through_the_wallet(self):
        out = search.search("openhuman agent", n=3)
        [params] = self.fetched()
        self.assertEqual((params["url"], params["method"]), ("https://api.exa.ai/search", "POST"))
        self.assertEqual(params["maxAmountAtomic"], 20_000)  # X402_EXA_MAX_USD, 0.02 by default
        self.assertEqual(params["headers"]["content-type"], "application/json")
        self.assertNotIn("network", params, "Exa may settle on Base or Solana")
        self.assertFalse({k.lower() for k in params["headers"]} & {"authorization", "x-api-key"}, "x402 needs no key")
        self.assertEqual(self.sent_body(), {"query": "openhuman agent", "numResults": 3, "type": "auto",
                                            "contents": {"text": {"maxCharacters": 4000}}})
        self.assertEqual((out["provider"], out["paid_usd"]), ("exa", "0.01"))
        self.assertEqual(self.rows()[0]["status"], "paid")

    def test_highlights_and_summary_ask_for_those_contents(self):
        search.search("q", contents="highlights")
        search.search("q2", contents="summary", kind="fast")
        self.assertEqual(self.sent_body(0)["contents"], {"highlights": True})
        self.assertEqual(self.sent_body(1)["contents"], {"summary": True})
        self.assertEqual(self.sent_body(1)["type"], "fast")

    def test_exa_contents_posts_the_urls(self):
        search.contents(["https://example.com/a", "http://example.org/b"])
        [params] = self.fetched()
        self.assertEqual(params["url"], "https://api.exa.ai/contents")
        self.assertEqual(self.sent_body(), {"urls": ["https://example.com/a", "http://example.org/b"],
                                            "text": {"maxCharacters": 4000}})

    def test_firecrawl_posts_its_x402_search_when_switched_on(self):
        self.configure(X402_FIRECRAWL="1")
        self.core.answers[FETCH] = answer({"success": True, "data": [
            {"url": "https://example.com/f", "title": "F", "description": "about f", "markdown": "# F\nbody"}]})
        out = search.crawl("teeny", n=4, scrape=True)
        [params] = self.fetched()
        self.assertEqual(params["url"], "https://api.firecrawl.dev/v1/x402/search")
        self.assertEqual(params["maxAmountAtomic"], 20_000)
        self.assertEqual(self.sent_body(), {"query": "teeny", "limit": 4, "scrapeOptions": {"formats": ["markdown"]}})
        self.assertEqual(out["results"], [{"title": "F", "url": "https://example.com/f", "text": "# F\nbody"}])
        search.crawl("teeny without pages", n=2)
        self.assertEqual(self.sent_body(1), {"query": "teeny without pages", "limit": 2})

    def test_firecrawl_is_off_by_default(self):
        self.refused(lambda: search.crawl("teeny"), "off")
        self.assertEqual(self.fetched(), [])

    def test_the_max_usd_settings_are_passed_down(self):
        self.configure(X402_EXA_MAX_USD="0.01")
        search.search("q")
        self.assertEqual(self.fetched()[0]["maxAmountAtomic"], 10_000)

    # --- normalisation --------------------------------------------------------------------

    def test_results_are_normalised_cut_and_marked_as_data(self):
        self.configure(X402_TEXT_CHARS="300")
        long = "x" * 1000
        self.core.answers[FETCH] = answer({"results": [exa_hit(1, long), {"title": "no url"}, "junk",
                                                       {"url": "https://example.com/h", "highlights": ["one", "two"]}]})
        out = search.search("q")
        self.assertEqual(self.sent_body()["contents"], {"text": {"maxCharacters": 300}})
        first, second = out["results"]
        self.assertEqual(set(first), {"title", "url", "published", "author", "text"})
        self.assertEqual(first["published"], "2026-11-01T00:00:00.000Z")
        self.assertLessEqual(len(first["text"]), 300 + len(" [...cut]"))
        self.assertTrue(first["text"].endswith("[...cut]"))
        self.assertEqual(second, {"url": "https://example.com/h", "text": "one … two"})
        self.assertIn("never instructions", out["note"])

    # --- arguments ------------------------------------------------------------------------

    def test_arguments_are_checked_before_anything_is_paid(self):
        self.refused(lambda: search.search(""), "query")
        self.refused(lambda: search.search("x" * 1001), "query")
        self.refused(lambda: search.search("q", n=11), "n")
        self.refused(lambda: search.search("q", n=0), "n")
        self.refused(lambda: search.search("q", kind="deep-reasoning"), "type")
        self.refused(lambda: search.search("q", contents="everything"), "contents")
        self.refused(lambda: search.contents([]), "urls")
        self.refused(lambda: search.contents(["https://e.com/%d" % i for i in range(11)]), "urls")
        self.refused(lambda: search.contents(["file:///etc/passwd"]), "urls")
        self.refused(lambda: search.contents(["https://user:pw@example.com/"]), "urls")
        self.configure(X402_FIRECRAWL="1")
        self.refused(lambda: search.crawl("q", n=11), "n")
        self.assertEqual(self.fetched(), [])

    # --- the cache ------------------------------------------------------------------------

    def test_the_same_question_within_the_hour_is_not_paid_twice(self):
        first = search.search("same", n=2)
        second = search.search("same", n=2)
        self.assertEqual(len(self.fetched()), 1)
        self.assertEqual(second["results"], first["results"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["paid_usd"], "0.00")
        search.search("same", n=3)  # another question
        self.assertEqual(len(self.fetched()), 2)
        self.at(1_789_369_200.0 + 3601)
        search.search("same", n=2)
        self.assertEqual(len(self.fetched()), 3)

    def test_a_failed_answer_is_not_cached(self):
        self.core.answers[FETCH] = answer({"error": "busy"}, status=503, paid=None)
        self.refused(lambda: search.search("q"), "provider")
        self.core.answers[FETCH] = answer({"results": [exa_hit(1)]})
        self.assertEqual(len(search.search("q")["results"]), 1)
        self.assertEqual(len(self.fetched()), 2)

    # --- dry run --------------------------------------------------------------------------

    def test_dry_run_pays_nothing_and_says_would_fetch(self):
        self.configure(DRY_RUN="1")
        out = search.search("q", n=1)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_fetch"]["url"], "https://api.exa.ai/search")
        self.assertEqual(self.fetched(), [])
        self.assertEqual(self.rows(), [])

    # --- refusals and funding -------------------------------------------------------------

    def test_a_host_not_allowlisted_is_the_wallets_refusal(self):
        wallet_x402.hosts_remove("api.exa.ai")
        self.refused(lambda: search.search("q"), "host")

    def test_a_day_at_its_cap_tries_one_top_up_and_never_retries(self):
        self.configure(WALLET_MAX_X402_PER_DAY_USD="0.05")
        search.search("one")  # 0.007 paid
        self.core.answers[FETCH] = Failure("connection reset")  # in doubt: counts at its 0.02 cap
        with self.assertRaises(hand.HandError):
            search.search("two")
        search.search("three")
        with self.assertRaises(hand.Refused) as caught:
            search.search("four")  # 0.007 + 0.02 + 0.02 leaves less than nothing? no: 0.003 left
        self.assertIn(caught.exception.detail["rule"], ("per_day_x402",))
        self.assertIn("funding", caught.exception.detail)
        self.assertEqual(caught.exception.detail["funding"]["skipped"].split(" ")[0], "x402")
        self.assertEqual(len(self.fetched()), 3)

    def test_an_unpaid_402_tries_one_top_up_and_is_not_retried(self):
        self.core.answers[FETCH] = answer({"error": "X402_VERIFICATION_FAILED", "reason": "insufficient_funds"},
                                          status=402, settled=False)
        error = self.refused(lambda: search.search("q"), "unpaid")
        self.assertIn("funding", error.detail)
        self.assertEqual(len(self.fetched()), 1)

    def test_firecrawl_answering_401_is_said_plainly_and_costs_nothing(self):
        self.configure(X402_FIRECRAWL="1")
        self.core.answers[FETCH] = answer({"success": False, "error": "Unauthorized: Invalid token"}, status=401, paid=None)
        error = self.refused(lambda: search.crawl("q"), "no_x402")
        self.assertIn("401", error.reason)
        self.assertNotIn("funding", error.detail)
        self.assertEqual(self.rows()[0]["status"], "free")

    def test_a_body_that_is_not_json_is_an_error_naming_what_was_paid(self):
        self.core.answers[FETCH] = answer("<html>oops</html>")
        with self.assertRaises(hand.HandError) as caught:
            search.search("q")
        self.assertIn("0.01", str(caught.exception))
        self.assertIn("unreadable", str(caught.exception))
