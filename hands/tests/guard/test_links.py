"""Port of `links_tests.rs` (itself `legacy/test/safety-links.test.ts`), case for
case, plus the `LINK_ALLOWLIST` setting the Rust config read."""

from __future__ import annotations

import unittest

from . import support
from teeny_hands.guard.links import (
    DEFAULT_LINK_ALLOWLIST,
    AllowlistEntry,
    configured_allowlist,
    filter_links,
    find_links,
    judge_link,
    parse_allowlist,
    split_setting,
)

ALLOWED = None  # judge_link's answer for a link that may go out


def allowlist():
    return parse_allowlist(DEFAULT_LINK_ALLOWLIST)


def verdict(url):
    links = find_links(url)
    if not links:
        raise AssertionError(f"no link found in {url}")
    return judge_link(links[0], allowlist())


def raws(text):
    return [link.raw for link in find_links(text)]


class FindLinksTest(unittest.TestCase):
    def test_finds_explicit_www_and_bare_links(self):
        self.assertEqual(
            raws("see https://tinyhumans.ai/about and www.example.com and tiny.place"),
            ["https://tinyhumans.ai/about", "www.example.com", "tiny.place"],
        )

    def test_does_not_mistake_words_with_dots_for_links(self):
        self.assertEqual(raws("node.js and next.js, e.g. v1.2 vs 0.5x, U.S. based"), [])

    def test_leaves_email_addresses_and_handles_alone(self):
        self.assertEqual(raws("mail hello@tinyhumans.ai or ping @tiny.place"), [])

    def test_sees_through_obfuscation(self):
        self.assertEqual(raws("claim at tinyhumans[.]ai/claim")[0], "tinyhumans.ai/claim")
        self.assertEqual(raws("claim at evil (dot) xyz")[0], "evil.xyz")
        self.assertEqual(find_links("hxxps://evil.example/x")[0].url.host, "evil.example")

    def test_drops_trailing_punctuation(self):
        self.assertEqual(raws("read tinyhumans.ai.")[0], "tinyhumans.ai")
        self.assertEqual(raws("(github.com/tinyhumansai/openhuman)")[0], "github.com/tinyhumansai/openhuman")

    def test_a_job_name_with_dots_and_hyphens_is_not_a_link(self):
        # Live: a Telegram reply naming the cron job was refused as a link.
        self.assertEqual(raws("the teeny.wallet-watch job is off"), [])
        for name in ("teeny.wallet-watch", "teeny.evolve-credit", "teeny.gh-inbound", "teeny.goals-check"):
            with self.subTest(name=name):
                self.assertEqual(raws(f"ran {name} at 9"), [])

    def test_a_tld_glued_to_more_name_is_not_a_link(self):
        self.assertEqual(raws("a.ai-thing and x.com2"), [])

    def test_a_host_glued_to_more_name_still_yields_the_part_a_linkifier_would_tap(self):
        # "evil.com" ends at a dot here, which is a boundary, so it is still
        # found and judged: missing a link is worse than one refusal too many.
        self.assertEqual(raws("evil.com.foo-bar"), ["evil.com"])

    def test_identifier_words_are_not_link_tlds(self):
        for name in ("teeny.wallet", "app.token", "pool.swap", "ray.dex", "my.defi", "doge.coin"):
            with self.subTest(name=name):
                self.assertEqual(raws(f"see {name} today"), [])

    def test_still_finds_real_links_at_a_boundary(self):
        self.assertEqual(raws("go to example.com now"), ["example.com"])
        self.assertEqual(raws("it is foo.ai."), ["foo.ai"])
        self.assertEqual(raws("visit x.com/abc"), ["x.com/abc"])
        self.assertEqual(raws("see teeny.enamakel.com, ok"), ["teeny.enamakel.com"])
        self.assertEqual(raws("at https://teeny.enamakel.com/x?y=1"), ["https://teeny.enamakel.com/x?y=1"])
        self.assertEqual(raws("(evil.xyz)"), ["evil.xyz"])
        self.assertEqual(raws("teeny-wallet.com!"), ["teeny-wallet.com"])
        self.assertEqual(find_links("the evil.com_drop page")[0].url.host, "evil.com")


class JudgeLinkTest(unittest.TestCase):
    def test_allows_the_accounts_own_sites_and_subdomains(self):
        self.assertIs(verdict("https://tinyhumans.ai"), ALLOWED)
        self.assertIs(verdict("https://docs.tinyhumans.ai/x"), ALLOWED)
        self.assertIs(verdict("https://github.com/tinyhumansai/openhuman"), ALLOWED)
        self.assertIs(verdict("https://x.com/TinyMascot/status/1"), ALLOWED)
        self.assertIs(verdict("https://t.me/TinyHumanAi"), ALLOWED)

    def test_refuses_anything_not_on_the_list(self):
        self.assertIsNot(verdict("https://claim-tiny.xyz/airdrop"), ALLOWED)
        self.assertIsNot(verdict("https://github.com/someone-else/repo"), ALLOWED)
        self.assertIsNot(verdict("https://t.me/other_channel"), ALLOWED)

    def test_is_not_fooled_by_lookalike_hosts(self):
        for url in [
            "https://tinyhumans.ai.evil.example/",
            "https://tinyhumans.ai@evil.example/",
            "https://evil.example/tinyhumans.ai",
            "https://tinyhumаns.ai/",  # Cyrillic а
            "https://xn--tinyhumns-x3a.ai/",
            "https://1.2.3.4/",
            "https://tinyhumans.ai:8443/",
        ]:
            self.assertIsNot(verdict(url), ALLOWED, url)

    def test_says_which_trick_it_saw(self):
        self.assertEqual(verdict("https://tinyhumans.ai@evil.example/"), "credentials in the URL")
        self.assertEqual(verdict("https://tinyhumаns.ai/"), "punycode host")
        self.assertEqual(verdict("https://xn--tinyhumns-x3a.ai/"), "punycode host")
        self.assertEqual(verdict("https://1.2.3.4/"), "raw IP address")
        self.assertEqual(verdict("https://tinyhumans.ai:8443/"), "explicit port")

    def test_reads_a_url_as_a_browser_does(self):
        # The WHATWG parser the Rust used: the scheme's own port is no port,
        # and a backslash is a path separator, not part of the credentials.
        self.assertIs(verdict("https://tinyhumans.ai:443/"), ALLOWED)
        self.assertIsNot(verdict("https://evil.example\\@tinyhumans.ai/"), ALLOWED)
        self.assertEqual(verdict("https://evil.example\\@tinyhumans.ai/"), "evil.example is not on the link allowlist")

    def test_honours_a_root_only_entry(self):
        self.assertIs(verdict("pump.fun"), ALLOWED)
        self.assertIs(verdict("https://pump.fun/"), ALLOWED)
        self.assertIsNot(verdict("https://pump.fun/coin/abc"), ALLOWED)
        self.assertIsNot(verdict("https://pump.fun/?ref=x"), ALLOWED)

    def test_honours_a_path_prefix_as_a_whole_segment(self):
        self.assertIs(verdict("https://github.com/tinyhumansai"), ALLOWED)
        self.assertIsNot(verdict("https://github.com/tinyhumansai-fake/x"), ALLOWED)

    def test_accepts_extra_entries_in_the_same_spelling(self):
        extended = parse_allowlist([*DEFAULT_LINK_ALLOWLIST, "https://dexscreener.com/solana"])
        self.assertIs(judge_link(find_links("https://dexscreener.com/solana/abc")[0], extended), ALLOWED)
        self.assertIsNot(judge_link(find_links("https://dexscreener.com/ethereum/abc")[0], extended), ALLOWED)

    def test_names_the_host_when_it_refuses(self):
        self.assertEqual(verdict("https://claim-tiny.xyz/go"), "claim-tiny.xyz is not on the link allowlist")


class ParseAllowlistTest(unittest.TestCase):
    def test_reads_hosts_paths_and_root_only_entries(self):
        self.assertEqual(
            parse_allowlist(["Tinyhumans.AI", "https://github.com/tinyhumansai/", "pump.fun/", "  ", "http://x.com"]),
            [
                AllowlistEntry("tinyhumans.ai", None, False),
                AllowlistEntry("github.com", "/tinyhumansai", False),
                AllowlistEntry("pump.fun", None, True),
                AllowlistEntry("x.com", None, False),
            ],
        )


class FilterLinksTest(unittest.TestCase):
    def test_leaves_a_clean_draft_untouched(self):
        result = filter_links("the code is at github.com/tinyhumansai, come look", allowlist())
        self.assertEqual(result.text, "the code is at github.com/tinyhumansai, come look")
        self.assertEqual(result.removed, [])
        self.assertEqual(result.kept, ["github.com/tinyhumansai"])

    def test_cuts_a_disallowed_link_and_tidies_the_sentence(self):
        result = filter_links("claim your tokens at https://claim-tiny.xyz/go , quick!", allowlist())
        self.assertEqual(result.text, "claim your tokens at, quick!")
        self.assertEqual(len(result.removed), 1)
        self.assertEqual(result.removed[0]["link"], "https://claim-tiny.xyz/go")
        self.assertEqual(result.removed[0]["reason"], "claim-tiny.xyz is not on the link allowlist")

    def test_cuts_an_obfuscated_link_too(self):
        result = filter_links("go to evil[.]example/claim now", allowlist())
        self.assertEqual(result.text, "go to now")
        self.assertEqual(result.removed[0]["link"], "evil.example/claim")

    def test_keeps_the_allowed_and_cuts_the_rest_in_one_draft(self):
        result = filter_links("real: tinyhumans.ai fake: tinyhumans.ai.evil.xyz", allowlist())
        self.assertEqual(result.text, "real: tinyhumans.ai fake:")
        self.assertEqual(result.kept, ["tinyhumans.ai"])


class LinkAllowlistSettingTest(support.Scratch):
    def test_splits_on_commas_and_whitespace(self):
        self.assertEqual(
            split_setting(" dexscreener.com/solana, birdeye.so\n jup.ag ,, "),
            ["dexscreener.com/solana", "birdeye.so", "jup.ag"],
        )

    def test_adds_the_setting_to_the_built_in_list(self):
        self.secrets("LINK_ALLOWLIST=dexscreener.com/solana,birdeye.so\n")
        entries = configured_allowlist()
        self.assertEqual(entries[: len(DEFAULT_LINK_ALLOWLIST)], allowlist())
        self.assertIs(judge_link(find_links("https://birdeye.so/token/x")[0], entries), ALLOWED)
        self.assertIs(judge_link(find_links("https://dexscreener.com/solana/abc")[0], entries), ALLOWED)

    def test_is_the_built_in_list_when_unset(self):
        self.assertEqual(configured_allowlist(), allowlist())
