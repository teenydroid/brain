"""Port of `house_style_tests.rs` (itself `legacy/test/house-style.test.ts`), case
for case, plus the two question helpers the market tool and the pipelines use."""

from __future__ import annotations

import unittest

from . import support  # noqa: F401  (puts hands/lib on the path)
from teeny_hands.guard.style import (
    MAX_POST_LENGTH,
    apply_house_style,
    asks_the_reader,
    count_emoji,
    post_length,
    truncate_at_boundary,
    without_the_ask,
)


def style(draft):
    return apply_house_style(draft)


def replying(draft, to):
    return apply_house_style(draft, replying_to=to)


class CountEmojiTest(unittest.TestCase):
    def test_counts_a_sequence_once(self):
        self.assertEqual(count_emoji("gm 🛠️"), 1)
        self.assertEqual(count_emoji("family 👨‍👩‍👧 here"), 1)
        self.assertEqual(count_emoji("wave 👋🏽 once"), 1)
        self.assertEqual(count_emoji("gm 🛠️ 🚀"), 2)
        self.assertEqual(count_emoji("plain text"), 0)

    def test_counts_pictographs_newer_than_the_interpreter(self):
        # Unicode 16's Extended_Pictographic, as the Rust regex crate had it,
        # whatever Unicode version this Python ships.
        self.assertEqual(count_emoji("🫩 tired"), 1)
        self.assertEqual(count_emoji("© and ™"), 2)


class ApplyHouseStyleTest(unittest.TestCase):
    def test_leaves_a_clean_draft_untouched(self):
        result = style("gm tiny humans. what are we building today? 🛠️")
        self.assertEqual(result.fixes, [])
        self.assertEqual(result.text, "gm tiny humans. what are we building today? 🛠️")

    def test_cuts_no_emoji_unless_a_cap_is_given(self):
        # 2026-11-22: the guard keeps no emoji cap.
        self.assertEqual(style("gm 👀 frens 💛 building 🛠️").text, "gm 👀 frens 💛 building 🛠️")
        result = style("gm 👀 frens 💛 building 🛠️", max_emoji=1)
        self.assertEqual(result.text, "gm 👀 frens building")
        self.assertEqual(count_emoji(result.text), 1)
        self.assertIn("cut 2 emoji over the cap of 1", result.fixes)

    def test_replaces_em_and_en_dashes(self):
        result = style("ready to serve something big — stay tuned")
        self.assertEqual(result.text, "ready to serve something big, stay tuned")
        self.assertIn("replaced em/en dashes", result.fixes)

    def test_straightens_curly_quotes(self):
        self.assertEqual(style("what’re we “cooking” today").text, "what're we \"cooking\" today")

    def test_trims_an_overlong_draft(self):
        result = style("a" * 300)
        self.assertEqual(len(result.text), MAX_POST_LENGTH)
        self.assertIn("trimmed to 280 characters", result.fixes)

    def test_honours_a_cap_of_zero(self):
        self.assertEqual(apply_house_style("gm 👀", max_emoji=0).text, "gm")


class DisclaimersTest(unittest.TestCase):
    def test_drops_a_trailing_nfa_with_the_comma_that_hung_it_on(self):
        result = style("$696K mcap today, $1 is a stop on the way not the end, nfa 🚀")
        self.assertEqual(result.text, "$696K mcap today, $1 is a stop on the way not the end 🚀")
        self.assertIn("dropped a disclaimer", result.fixes)

    def test_drops_nfa_mid_sentence_in_brackets_and_in_caps(self):
        self.assertEqual(
            style("we are at $700K, GOAT at $400M, that is the flip (NFA) and i am not selling").text,
            "we are at $700K, GOAT at $400M, that is the flip and i am not selling",
        )
        self.assertEqual(
            style("that is a 500x from here. NFA. still buying.").text,
            "that is a 500x from here. still buying.",
        )

    def test_drops_the_long_forms_too(self):
        self.assertEqual(style("$2B is the plan, not financial advice, dyor").text, "$2B is the plan")
        self.assertEqual(style("$2B is the plan - do your own research").text, "$2B is the plan")

    def test_leaves_words_that_merely_contain_the_letters_alone(self):
        self.assertEqual(style("the unfair advantage of a plush toy is patience").fixes, [])


class LeadingHandlesTest(unittest.TestCase):
    def test_drops_the_handle_x_adds_to_a_reply_itself(self):
        result = replying("@rafaelitewrld peekaboo 👀", "rafaelitewrld")
        self.assertEqual(result.text, "peekaboo 👀")
        self.assertIn("dropped leading @handles X adds itself", result.fixes)

    def test_drops_a_leading_handle_that_is_not_the_one_being_replied_to(self):
        # The observed failure: replying to HappyTinyHuman with a draft opening
        # "@senamakel", which X published as "@HappyTinyHuman @senamakel @senamakel".
        self.assertEqual(replying("@senamakel me? trade? nah", "HappyTinyHuman").text, "me? trade? nah")

    def test_drops_a_whole_run_of_leading_handles(self):
        self.assertEqual(style("@a @b @c gm frens").text, "gm frens")

    def test_keeps_a_handle_that_is_part_of_the_sentence(self):
        self.assertEqual(replying("gm @someone else entirely", "rafaelitewrld").text, "gm @someone else entirely")

    def test_keeps_the_text_when_it_is_nothing_but_handles(self):
        self.assertEqual(style("@a @b").text, "@a @b")


class RepeatedHandlesTest(unittest.TestCase):
    def test_keeps_the_name_but_drops_the_at_when_x_already_tags_them(self):
        result = replying("heyyy @rafaelitewrld shy peek returns", "rafaelitewrld")
        self.assertEqual(result.text, "heyyy rafaelitewrld shy peek returns")
        self.assertIn("unlinked a repeated @handle X already tags", result.fixes)

    def test_leaves_a_third_party_tagged_which_may_be_deliberate(self):
        self.assertEqual(
            replying("say hi to @someoneelse for me", "rafaelitewrld").text, "say hi to @someoneelse for me"
        )

    def test_takes_the_handle_with_or_without_its_at(self):
        self.assertEqual(replying("yo @Alice gm", "@alice").text, "yo Alice gm")


class TruncateAtBoundaryTest(unittest.TestCase):
    def test_leaves_text_that_already_fits_alone(self):
        self.assertEqual(truncate_at_boundary("short enough", 100), "short enough")

    # The published failure: a reply ended "that's OpenHum".
    def test_cuts_at_a_word_rather_than_through_one(self):
        self.assertEqual(truncate_at_boundary("we are building OpenHumans together", 20), "we are building")

    # Half a URL is not a shorter receipt, it is a broken one.
    def test_drops_a_receipt_link_whole_rather_than_publishing_half_of_it(self):
        text = "sent! receipt: https://rh-scan.com/tx/0x" + "ab" * 32
        cut = truncate_at_boundary(text, 40)
        self.assertEqual(cut, "sent! receipt:")
        self.assertNotIn("rh-scan", cut)

    def test_still_cuts_a_single_unbroken_token_since_half_of_it_is_meaningless_anyway(self):
        self.assertEqual(truncate_at_boundary("a" * 50, 10), "a" * 10)

    def test_counts_length_as_x_and_the_legacy_service_did_in_utf16_units_and_never_splits_a_char(self):
        self.assertEqual(post_length("gm 🚀"), 5)
        self.assertEqual(truncate_at_boundary("🚀🚀🚀", 3), "🚀")


class TheAskTest(unittest.TestCase):
    def test_sees_a_question_on_the_last_line_only(self):
        self.assertTrue(asks_the_reader("the chart held.\nwhat are you watching?  \n\n"))
        self.assertFalse(asks_the_reader("what are you watching?\nthe chart held."))
        self.assertFalse(asks_the_reader(""))

    def test_cuts_the_last_line_or_turns_a_lone_question_deadpan(self):
        self.assertEqual(without_the_ask("the chart held.\nwhat are you watching?"), "the chart held.")
        self.assertEqual(without_the_ask("what are you watching?"), "what are you watching.")
