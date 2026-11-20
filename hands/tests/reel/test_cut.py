"""deploy/reel/skitcut.py, the reel front's skit cutter: the job it takes,
the ffmpeg command it runs (each shot's own sound kept, silence for a shot
with none, the loudness evened out, an optional bed, the label burned in
for the whole length from a file), and when it takes the viewer's frames.
The module imports nothing of upstream's, so it is loaded as it runs; the
one test that renders runs only where ffmpeg is installed (the reel image)."""

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("reel_skitcut", REPO / "deploy" / "reel" / "skitcut.py")
skitcut = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(skitcut)

JOB = {"clips": ["media/skit-1-shot-1.mp4", "media/skit-1-shot-2.mp4"], "label": "AI parody · @teenydroid"}


class CheckTest(unittest.TestCase):
    def test_a_cut_takes_its_clips_label_and_aspect(self):
        self.assertEqual(skitcut.check_cut(JOB), {"clips": JOB["clips"], "label": "AI parody · @teenydroid",
                                                  "aspect": "16:9", "bed": None, "bed_volume": 0.12})

    def test_anything_else_is_refused(self):
        bad = [
            "not an object",
            {**JOB, "clips": []},
            {**JOB, "clips": JOB["clips"] * 3},
            {**JOB, "clips": ["assets/teeny.mp4"]},
            {**JOB, "clips": ["media/x.png"]},
            {**JOB, "label": ""},
            {**JOB, "label": "two\nlines"},
            {**JOB, "label": "x" * 81},
            {**JOB, "aspect": "4:3"},
            {**JOB, "bed": "/etc/passwd"},
            {**JOB, "bed_volume_pct": 90},
            {**JOB, "font": "x.ttf"},
        ]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(skitcut.CutError):
                    skitcut.check_cut(body)

    def test_only_the_cut_and_its_frames_are_served(self):
        for name in ("cut.mp4", "frame-0.jpg", "frame-5.jpg"):
            self.assertTrue(skitcut.FILE.fullmatch(name), name)
        for name in ("label.txt", "../cut.mp4", "frame-10.jpg", "cut.mp4.part"):
            self.assertFalse(skitcut.FILE.fullmatch(name), name)

    def test_every_size_is_one_x_takes(self):
        for aspect, (width, height) in skitcut.SIZES.items():
            with self.subTest(aspect=aspect):
                self.assertTrue(width <= 1920 and height <= 1900 and width % 2 == 0 and height % 2 == 0)


class CommandTest(unittest.TestCase):
    CLIPS = [{"path": "/s/1.mp4", "audio": True, "seconds": 5.0}, {"path": "/s/2.mp4", "audio": False, "seconds": 4.0}]

    def graph(self, argv):
        return argv[argv.index("-filter_complex") + 1]

    def test_each_shot_keeps_its_own_sound_and_a_silent_one_gets_silence(self):
        argv = skitcut.argv("ffmpeg", self.CLIPS, "/c/label.txt", "/f/font.ttf", "/c/cut.mp4", "16:9")
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-i"],
                         ["/s/1.mp4", "/s/2.mp4", "anullsrc=r=48000:cl=stereo"])
        self.assertIn("-t", argv)
        graph = self.graph(argv)
        self.assertIn("[0:a]aresample", graph)
        self.assertIn("[2:a]aresample", graph)
        self.assertIn("concat=n=2:v=1:a=1", graph)
        self.assertIn("loudnorm=", graph)
        self.assertEqual(argv[argv.index("-map") + 1], "[vout]")

    def test_the_label_is_drawn_from_a_file_on_every_frame(self):
        graph = self.graph(skitcut.argv("ffmpeg", self.CLIPS, "/c/label.txt", "/f/font.ttf", "/c/cut.mp4", "9:16"))
        self.assertIn("drawtext=fontfile='/f/font.ttf':textfile='/c/label.txt'", graph)
        self.assertNotIn("enable=", graph)  # no time window: the whole length
        self.assertIn("scale=1068:1900", graph)

    def test_a_bed_goes_under_at_its_volume(self):
        argv = skitcut.argv("ffmpeg", self.CLIPS, "/c/l.txt", "/f/f.ttf", "/c/cut.mp4", "16:9", "/s/bed.mp3", 0.1)
        self.assertIn("/s/bed.mp3", argv)
        self.assertEqual(argv[argv.index("/s/bed.mp3") - 3:argv.index("/s/bed.mp3")], ["-stream_loop", "-1", "-i"])
        self.assertIn("volume=0.10[bed]", self.graph(argv))
        self.assertIn("amix=inputs=2:duration=first", self.graph(argv))

    def test_a_path_the_filter_cannot_quote_is_refused(self):
        with self.assertRaises(skitcut.CutError):
            skitcut.argv("ffmpeg", self.CLIPS, "/c/it's.txt", "/f/f.ttf", "/c/cut.mp4", "16:9")

    def test_frames_at_the_start_each_shots_middle_and_the_end(self):
        self.assertEqual(skitcut.frame_times([5.0, 4.0]), [0.4, 2.5, 7.0, 8.6])
        self.assertEqual(len(skitcut.frame_times([3.0] * 4)), skitcut.MAX_FRAMES)
        self.assertEqual(skitcut.frame_times([]), [])

    def test_one_clip_is_sampled_across_its_whole_length(self):
        # A one-take skit is one clip with every shot in it: frames spread
        # evenly from start to end, so the viewer sees every shot (with the
        # shots' middles alone it would get three frames).
        times = skitcut.frame_times([14.0])
        self.assertEqual(len(times), skitcut.MAX_FRAMES)
        self.assertEqual((times[0], times[-1]), (0.4, 13.6))
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertTrue(max(gaps) - min(gaps) < 0.05, times)
        for start, end in ((0, 4), (4, 9), (9, 14)):  # the take's three shots
            self.assertTrue(any(start <= t < end for t in times), (start, end, times))
        self.assertEqual(skitcut.frame_times([0.5]), [0.12, 0.38][:len(skitcut.frame_times([0.5]))])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg (the reel image has it)")
class RenderTest(unittest.TestCase):
    def test_two_shots_one_silent_cut_with_their_sound_and_the_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, out = os.path.join(tmp, "a.mp4"), os.path.join(tmp, "b.mp4"), os.path.join(tmp, "cut")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24:duration=2",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", a], check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=360x640:rate=30:duration=1", b], check=True)
            os.makedirs(out)
            font = next((str(p) for p in Path("/MoneyPrinterTurbo/resource/fonts").glob("*.ttf")), None) or \
                str(REPO / "vendor" / "moneyprinter" / "resource" / "fonts" / skitcut.FONT)
            made = skitcut.render([a, b], "AI parody · @teenydroid", "16:9", out, font)
            self.assertEqual((made["labelled"], made["audio"], len(made["shots"])), (True, [True, False], 2))
            self.assertTrue(2.5 < made["seconds"] < 3.5, made["seconds"])
            for name in made["frames"]:
                self.assertTrue(Path(out, name).read_bytes().startswith(b"\xff\xd8\xff"))
            kinds = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                                    os.path.join(out, "cut.mp4")], capture_output=True, text=True).stdout.split()
            self.assertEqual(sorted(kinds), ["audio", "video"])


if __name__ == "__main__":
    unittest.main()
