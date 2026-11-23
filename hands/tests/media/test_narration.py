"""media/narration.py: the narration read once is the film's clock; each beat
gets its window and its slice of the voice, nothing is stretched, and a beat
too long for any clip is refused before money is spent."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.media import narration  # noqa: E402


def timed(*spans):
    """Words with the given (start, end) times, named w0, w1, ..."""
    return [{"text": f"w{i}", "start": s, "end": e} for i, (s, e) in enumerate(spans)]


class ClockTest(unittest.TestCase):
    def test_the_text_read_aloud_is_every_spoken_beat_in_order(self):
        beats = [{"narration": "  i  have news "}, {"narration": ""}, {"narration": "it is a toilet"}]
        self.assertEqual(narration.joined(beats), "i have news\n\nit is a toilet")

    def test_beats_are_cut_halfway_through_the_silence_between_them(self):
        beats = [{"id": "b1", "narration": "one two"}, {"id": "b2", "narration": "three"}]
        got = narration.clock(beats, timed((0.1, 0.5), (0.6, 1.0), (2.0, 2.6)))
        # Cut at (1.0 + 2.0) / 2 = 1.5: neither word loses its tail.
        self.assertEqual(got["pieces"], [{"beat": "b1", "from": 0.0, "to": 1.5, "at": 0.0},
                                         {"beat": "b2", "from": 1.5, "to": 2.8, "at": 1.9}])
        # b1: 1.5 s of voice plus a breath; b2 (last): 1.3 s plus the held still.
        self.assertEqual(got["beats"], [{"id": "b1", "t0": 0.0, "t1": 1.9}, {"id": "b2", "t0": 1.9, "t1": 4.7}])
        self.assertEqual(got["length"], 4.7)

    def test_an_action_beat_is_its_own_silence_and_the_voice_waits_for_it(self):
        beats = [{"id": "b1", "narration": "watch"}, {"id": "b2", "hold": 4}, {"id": "b3", "narration": "oh no"}]
        got = narration.clock(beats, timed((0.0, 0.5), (1.0, 1.4), (1.5, 1.9)))
        self.assertEqual([b["id"] for b in got["beats"]], ["b1", "b2", "b3"])
        b1, b2, b3 = got["beats"]
        self.assertEqual((b2["t0"], b2["t1"]), (b1["t1"], b1["t1"] + 4))
        self.assertEqual([p["beat"] for p in got["pieces"]], ["b1", "b3"])
        self.assertEqual(got["pieces"][1]["at"], b3["t0"])

    def test_the_film_ends_on_a_held_still_once_even_after_an_action_beat(self):
        beats = [{"id": "b1", "narration": "bye"}, {"id": "b2", "hold": 2}]
        got = narration.clock(beats, timed((0.0, 0.4)))
        b1, b2 = got["beats"]
        self.assertAlmostEqual(b1["t1"] - b1["t0"], 0.4 + narration.RELEASE + narration.BREATH)
        self.assertAlmostEqual(b2["t1"] - b2["t0"], 2 + narration.TAIL)

    def test_nothing_is_stretched_each_piece_plays_as_long_as_it_was_read(self):
        beats = [{"id": f"b{i}", "narration": "a b c"} for i in range(4)]
        words = timed(*[(i * 0.3, i * 0.3 + 0.25) for i in range(12)])
        got = narration.clock(beats, words)
        played = sum(p["to"] - p["from"] for p in got["pieces"])
        self.assertAlmostEqual(played, words[-1]["end"] + narration.RELEASE, places=3)

    def test_words_that_do_not_match_the_text_are_refused(self):
        with self.assertRaises(hand.Refused):
            narration.clock([{"narration": "one two three"}], timed((0, 1), (1, 2)))

    def test_a_beat_longer_than_any_clip_is_refused_by_name(self):
        beats = [{"id": "rant", "narration": "on and on"}]
        with self.assertRaises(hand.Refused) as caught:
            narration.clock(beats, timed((0.0, 5.0), (5.0, 10.0), (10.0, 16.0)))
        self.assertEqual(caught.exception.detail["beat"], "rant")

    def test_an_action_beat_outside_its_range_or_a_silent_film_is_refused(self):
        with self.assertRaises(hand.Refused):
            narration.clock([{"narration": "hi"}, {"hold": 20}], timed((0, 1)))
        with self.assertRaises(hand.Refused):
            narration.clock([{"hold": 3}], [])
        with self.assertRaises(hand.Refused):
            narration.clock([], [])

    def test_a_narration_that_starts_late_in_its_audio_is_refused(self):
        with self.assertRaises(hand.Refused):
            narration.clock([{"narration": "late"}], timed((3.0, 3.5)))

    def test_a_clip_is_bought_at_the_shortest_length_that_covers_its_beat(self):
        self.assertEqual(narration.buy({"t0": 2.0, "t1": 9.2}, [5, 10, 15]), 10)
        self.assertEqual(narration.buy({"t0": 0.0, "t1": 5.0}, [5, 10, 15]), 5)
        self.assertIsNone(narration.buy({"t0": 0.0, "t1": 16.0}, [5, 10, 15]))


if __name__ == "__main__":
    unittest.main()
