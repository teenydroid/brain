"""deploy/ingest/shots.py: how a clip's scene cuts become shots (merged,
split, capped, on whole frames, summing to the excerpt), what ffprobe and
ffmpeg are asked and how their answers are read. Pure code with no ffmpeg
needed; the one test that runs ffmpeg skips where it is not installed."""

import importlib.util
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("ingest_shots", REPO / "deploy" / "ingest" / "shots.py")
shots = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shots)


def lengths(plan):
    return [s["seconds"] for s in plan["shots"]]


class PlanTest(unittest.TestCase):
    def plan(self, cuts, seconds, fps=30.0, **kw):
        kw = {"min_shot": 1.0, "max_shot": 10.0, "max_shots": 6, **kw}
        return shots.plan(cuts, fps, seconds, **kw)

    def test_a_clip_with_no_cut_is_one_shot(self):
        got = self.plan([], 8.0)
        self.assertEqual(got["seconds"], 8.0)
        self.assertEqual(got["shots"], [{"n": 1, "start": 0.0, "seconds": 8.0}])

    def test_cuts_make_contiguous_numbered_shots(self):
        got = self.plan([3.0, 6.0], 9.0)
        self.assertEqual(got["shots"], [{"n": 1, "start": 0.0, "seconds": 3.0}, {"n": 2, "start": 3.0, "seconds": 3.0},
                                        {"n": 3, "start": 6.0, "seconds": 3.0}])

    def test_a_cut_shorter_than_the_minimum_merges_into_its_shorter_neighbour(self):
        self.assertEqual(lengths(self.plan([3.0, 3.4], 9.0)), [3.4, 5.6])
        # the neighbour after is the shorter one here
        self.assertEqual(lengths(self.plan([5.0, 5.4], 6.0)), [5.0, 1.0])

    def test_a_short_first_or_last_shot_merges_inward(self):
        self.assertEqual(lengths(self.plan([0.5], 6.0)), [6.0])
        self.assertEqual(lengths(self.plan([5.5], 6.0)), [6.0])

    def test_several_short_cuts_in_a_row_all_merge(self):
        got = self.plan([2.0, 2.2, 2.4, 2.6, 5.0], 8.0)
        self.assertTrue(all(s >= 1.0 for s in lengths(got)), lengths(got))
        self.assertAlmostEqual(sum(lengths(got)), 8.0, places=6)

    def test_a_shot_longer_than_the_maximum_is_split_evenly(self):
        got = self.plan([], 25.0)
        self.assertEqual(len(got["shots"]), 3)
        for s in lengths(got):
            self.assertAlmostEqual(s, 25 / 3, places=2)
        self.assertAlmostEqual(sum(lengths(got)), 25.0, places=6)

    def test_a_split_never_leaves_a_piece_under_the_minimum(self):
        got = self.plan([], 10.5, max_shot=10.0, min_shot=1.0)
        self.assertEqual(len(got["shots"]), 2)
        self.assertTrue(all(s >= 1.0 for s in lengths(got)))

    def test_more_shots_than_the_cap_end_the_excerpt_at_the_last_one_kept(self):
        got = self.plan([5.0, 10.0, 15.0, 20.0, 25.0], 30.0, max_shots=3)
        self.assertEqual(got["seconds"], 15.0)
        self.assertEqual(len(got["shots"]), 3)
        self.assertAlmostEqual(sum(lengths(got)), got["seconds"], places=6)

    def test_the_cap_counts_shots_after_the_splitting(self):
        got = self.plan([], 60.0, max_shots=4)
        self.assertEqual(len(got["shots"]), 4)
        self.assertAlmostEqual(got["seconds"], 40.0, places=6)

    def test_cuts_outside_the_excerpt_or_repeated_or_unsorted_are_ignored(self):
        got = self.plan([6.0, -1.0, 3.0, 3.0, 9.0, 12.0, 0.0], 9.0)
        self.assertEqual(lengths(got), [3.0, 3.0, 3.0])

    def test_an_excerpt_shorter_than_the_minimum_is_still_one_shot(self):
        self.assertEqual(lengths(self.plan([], 0.5)), [0.5])

    def test_nothing_to_plan_is_an_error(self):
        for seconds in (0, -1, 0.001):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    self.plan([], seconds)

    def test_boundaries_are_whole_frames(self):
        for fps in (24.0, 25.0, 30.0, 29.97, 59.94):
            with self.subTest(fps=fps):
                got = self.plan([1.234, 3.3333, 5.01, 7.77], 9.0, fps=fps, min_shot=0.5)
                for s in got["shots"]:
                    self.assertAlmostEqual(s["start"] * fps, round(s["start"] * fps), delta=0.03)
                self.assertAlmostEqual(got["seconds"] * fps, round(got["seconds"] * fps), delta=0.03)

    def test_a_cut_lands_on_the_nearest_frame(self):
        got = self.plan([3.01], 6.0, fps=30.0)
        self.assertEqual(got["shots"][1]["start"], 3.0)
        got = self.plan([3.02], 6.0, fps=30.0)
        self.assertEqual(got["shots"][1]["start"], round(91 / 30, 3))

    def test_shots_always_sum_to_the_excerpt_and_touch(self):
        rng = random.Random(7)
        for fps in (24.0, 25.0, 29.97, 30.0, 50.0, 59.94):
            for _ in range(40):
                seconds = rng.uniform(2.0, 60.0)
                cuts = sorted(rng.uniform(0, seconds) for _ in range(rng.randint(0, 25)))
                max_shots = rng.randint(1, 9)
                got = shots.plan(cuts, fps, seconds, min_shot=1.0, max_shot=10.0, max_shots=max_shots)
                with self.subTest(fps=fps, seconds=seconds, cuts=cuts, max_shots=max_shots):
                    self.assertLessEqual(len(got["shots"]), max_shots)
                    self.assertAlmostEqual(sum(lengths(got)), got["seconds"], places=9)
                    self.assertLessEqual(got["seconds"], round(seconds + 1 / fps, 3))
                    at = 0.0
                    for n, s in enumerate(got["shots"], 1):
                        self.assertEqual(s["n"], n)
                        self.assertAlmostEqual(s["start"], at, places=9)
                        self.assertGreater(s["seconds"], 0)
                        at = s["start"] + s["seconds"]
                    self.assertAlmostEqual(at, got["seconds"], places=9)

    def test_every_shot_but_a_lone_one_is_within_the_limits(self):
        rng = random.Random(11)
        for _ in range(60):
            seconds = rng.uniform(12.0, 60.0)
            cuts = sorted(rng.uniform(0, seconds) for _ in range(rng.randint(0, 25)))
            got = shots.plan(cuts, 30.0, seconds, min_shot=1.0, max_shot=10.0, max_shots=9)
            with self.subTest(seconds=seconds, cuts=cuts):
                for s in lengths(got):
                    self.assertGreaterEqual(s, 1.0 - 1 / 30)
                    self.assertLessEqual(s, 10.0 + 1 / 30)


class FrameTest(unittest.TestCase):
    def test_a_frame_is_taken_a_tenth_of_a_second_into_the_shot(self):
        self.assertEqual(shots.frame_time({"n": 2, "start": 3.0, "seconds": 3.0}), 3.1)

    def test_a_very_short_shot_is_framed_at_its_middle(self):
        self.assertEqual(shots.frame_time({"n": 1, "start": 1.0, "seconds": 0.1}), 1.05)

    def test_frame_names_follow_the_shot_numbers(self):
        self.assertEqual(shots.frame_name(3), "shot-3.jpg")
        with self.assertRaises(ValueError):
            shots.frame_name(10)


class ReadTest(unittest.TestCase):
    def test_a_frame_rate_as_ffprobe_writes_it(self):
        self.assertEqual(shots.parse_fps("30/1"), 30.0)
        self.assertAlmostEqual(shots.parse_fps("30000/1001"), 29.97, places=2)
        self.assertEqual(shots.parse_fps("25"), 25.0)
        for bad in ("0/0", "", None, "abc", "1/0", "-5/1", "1000/1", "1/10"):
            with self.subTest(bad=bad):
                self.assertEqual(shots.parse_fps(bad), shots.DEFAULT_FPS)

    def test_the_scene_cuts_are_read_from_showinfo_lines(self):
        text = (
            "ffmpeg version x\n"
            "[Parsed_showinfo_1 @ 0x55] config in time_base: 1/30000, frame_rate: 30000/1001\n"
            "[Parsed_showinfo_1 @ 0x55] n:   0 pts:  90090 pts_time:3.003   pos: 1234 fmt:yuv420p\n"
            "[Parsed_showinfo_1 @ 0x55] n:   1 pts: 210210 pts_time:7.007   pos: 2234 fmt:yuv420p\n"
            "[Parsed_showinfo_1 @ 0x55] n:   2 pts: 7 pts_time:abc\n"
            "frame=  1 fps=0.0\n"
        )
        self.assertEqual(shots.parse_cuts(text), [3.003, 7.007])
        self.assertEqual(shots.parse_cuts(""), [])

    def test_ffprobe_json_becomes_what_the_fetch_needs(self):
        text = ('{"streams":[{"codec_type":"video","width":1080,"height":1920,"avg_frame_rate":"30000/1001",'
                '"r_frame_rate":"30000/1001"},{"codec_type":"audio"}],"format":{"duration":"12.480000"}}')
        got = shots.parse_probe(text)
        self.assertEqual((got["width"], got["height"], got["audio"], got["seconds"]), (1080, 1920, True, 12.48))
        self.assertAlmostEqual(got["fps"], 29.97, places=2)

    def test_a_clip_with_no_picture_or_length_is_not_a_clip(self):
        for text in ('{"streams":[{"codec_type":"audio"}],"format":{"duration":"3"}}',
                     '{"streams":[{"codec_type":"video","width":10,"height":10}],"format":{}}',
                     '{"streams":[{"codec_type":"video","width":0,"height":10}],"format":{"duration":"3"}}',
                     "not json", "[]"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    shots.parse_probe(text)

    def test_the_aspect_is_the_nearest_of_three(self):
        for size, want in {(1920, 1080): "16:9", (1280, 720): "16:9", (1080, 1920): "9:16", (720, 1280): "9:16",
                           (1080, 1080): "1:1", (1000, 1100): "1:1", (1100, 1000): "1:1", (1080, 1350): "1:1",
                           (1500, 1080): "16:9", (1080, 1500): "9:16"}.items():
            with self.subTest(size=size):
                self.assertEqual(shots.aspect(*size), want)
        with self.assertRaises(ValueError):
            shots.aspect(0, 10)


class ArgvTest(unittest.TestCase):
    def test_scene_detection_reads_a_file_to_nothing(self):
        argv = shots.scene_argv("ffmpeg", "/d/x/ex.mp4")
        self.assertEqual(argv[0], "ffmpeg")
        self.assertIn("select='gt(scene,0.3)',showinfo", argv)
        self.assertEqual(argv[-3:], ["-f", "null", "-"])
        self.assertEqual(argv[argv.index("-i") + 1], "/d/x/ex.mp4")
        self.assertIn("-nostdin", argv)

    def test_the_excerpt_is_cut_at_the_frame_rate_and_height_asked(self):
        argv = shots.excerpt_argv("ffmpeg", "/d/dl.mp4", "/d/ex.mp4", 12.5, 20.0, 29.97, 720)
        self.assertLess(argv.index("-ss"), argv.index("-i"))
        self.assertEqual(argv[argv.index("-ss") + 1], "12.500")
        self.assertEqual(argv[argv.index("-t") + 1], "20.000")
        self.assertEqual(argv[argv.index("-r") + 1], "29.97")
        self.assertIn("min(720,ih)", " ".join(argv))
        self.assertEqual(argv[-1], "/d/ex.mp4")
        self.assertIn("-an", shots.excerpt_argv("ffmpeg", "/d/dl.mp4", "/d/ex.mp4", 0, 5, 30, 720, audio=False))
        self.assertNotIn("-an", argv)

    def test_the_audio_is_the_excerpt_and_nothing_else(self):
        argv = shots.audio_argv("ffmpeg", "/d/source.mp4", "/d/audio.m4a", 17.4)
        self.assertIn("-vn", argv)
        self.assertEqual(argv[argv.index("-c:a") + 1], "aac")
        self.assertEqual(argv[argv.index("-t") + 1], "17.400")
        self.assertEqual(argv[-1], "/d/audio.m4a")

    def test_a_frame_is_one_jpeg_at_a_second_bounded_side(self):
        argv = shots.frame_argv("ffmpeg", "/d/source.mp4", "/d/shot-2.jpg", 3.1)
        self.assertEqual(argv[argv.index("-ss") + 1], "3.100")
        self.assertEqual(argv[argv.index("-frames:v") + 1], "1")
        self.assertIn(f"{shots.FRAME_SIDE}", " ".join(argv))
        self.assertEqual(argv[-1], "/d/shot-2.jpg")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg and ffprobe (the ingest image has them)")
class RealFfmpegTest(unittest.TestCase):
    def test_two_colours_make_two_shots_with_a_frame_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            clip = folder / "clip.mp4"
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=red:s=320x240:r=30:d=3", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=30:d=3",
                            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", str(clip)],
                           check=True)
            run = subprocess.run(shots.scene_argv("ffmpeg", str(clip)), capture_output=True, text=True)
            cuts = shots.parse_cuts(run.stderr)
            self.assertEqual(len(cuts), 1)
            self.assertAlmostEqual(cuts[0], 3.0, delta=0.1)
            got = shots.plan(cuts, 30.0, 6.0, min_shot=1.0, max_shot=10.0, max_shots=6)
            self.assertEqual(len(got["shots"]), 2)
            out = folder / "shot-1.jpg"
            subprocess.run(shots.frame_argv("ffmpeg", str(clip), str(out), shots.frame_time(got["shots"][0])), check=True)
            self.assertEqual(out.read_bytes()[:3], b"\xff\xd8\xff")


if __name__ == "__main__":
    unittest.main()
