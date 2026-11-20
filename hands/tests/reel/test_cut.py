"""deploy/reel/skitcut.py, the reel front's skit cutter: the job it takes,
the ffmpeg command it runs (each shot's own sound kept, silence for a shot
with none, the loudness evened out, an optional bed, the label burned in
for the whole length from a file), and when it takes the viewer's frames.
A remake's cut adds `trims` (each clip cut to its source shot, to the
frame) and `sound` (the source's audio, the only sound); without them the
command is byte for byte what it was.
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
REMAKE = {"clips": [f"media/remake-3-shot-{n}.mp4" for n in range(1, 7)], "label": "AI parody · @teenydroid",
          "aspect": "9:16", "trims": [2.4, 4.0, 1.0, 10.0, 3, 1.5], "sound": "media/remake/s1/audio.m4a"}


class CheckTest(unittest.TestCase):
    def test_a_cut_takes_its_clips_label_and_aspect(self):
        self.assertEqual(skitcut.check_cut(JOB), {"clips": JOB["clips"], "label": "AI parody · @teenydroid",
                                                  "aspect": "16:9", "bed": None, "bed_volume": 0.12,
                                                  "trims": None, "sound": None})

    def test_a_remake_takes_up_to_six_clips_their_trims_and_its_sound(self):
        checked = skitcut.check_cut(REMAKE)
        self.assertEqual((checked["trims"], checked["sound"]), ([2.4, 4.0, 1.0, 10.0, 3.0, 1.5], REMAKE["sound"]))
        no_sound = skitcut.check_cut({**REMAKE, "sound": None})
        self.assertEqual((len(no_sound["clips"]), no_sound["sound"]), (6, None))

    def test_a_remakes_trims_and_sound_are_held_to_their_rules(self):
        bad = [
            {**REMAKE, "bed": "media/bed.mp3"},
            {**REMAKE, "sound": "media/bed.m4a"},
            {**REMAKE, "sound": "assets/remake/s1/audio.m4a"},
            {**REMAKE, "sound": "media/remake/../bed.m4a"},
            {**REMAKE, "sound": "media/remake/s1/../../x.m4a"},
            {**REMAKE, "sound": "media/remake/s1/shot-1.jpg"},
            {**REMAKE, "sound": "/etc/passwd"},
            {**REMAKE, "trims": None},
            {**REMAKE, "trims": REMAKE["trims"][:5]},
            {**REMAKE, "trims": [0.9] + REMAKE["trims"][1:]},
            {**REMAKE, "trims": [10.1] + REMAKE["trims"][1:]},
            {**REMAKE, "trims": [True] + REMAKE["trims"][1:]},
            {**REMAKE, "trims": "2.4"},
            {**REMAKE, "clips": REMAKE["clips"] + ["media/remake-3-shot-7.mp4"], "trims": REMAKE["trims"] + [2.0]},
            {**JOB, "clips": JOB["clips"] * 3},
        ]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(skitcut.CutError):
                    skitcut.check_cut(body)

    def test_a_sound_must_really_be_under_the_remake_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            remake = os.path.join(tmp, "media", "remake")
            os.makedirs(os.path.join(remake, "s1"))
            inside = os.path.join(remake, "s1", "audio.m4a")
            outside = os.path.join(tmp, "media", "bed.m4a")
            for path in (inside, outside):
                Path(path).write_bytes(b"x")
            os.symlink(outside, os.path.join(remake, "s1", "link.m4a"))
            self.assertTrue(skitcut.under(inside, remake))
            self.assertFalse(skitcut.under(outside, remake))
            self.assertFalse(skitcut.under(os.path.join(remake, "s1", "link.m4a"), remake))

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
        self.assertEqual(len(skitcut.frame_times([3.0] * 4)), 6)
        self.assertEqual(skitcut.frame_times([]), [])

    def test_six_shots_get_every_middle_within_the_viewers_eight_frames(self):
        times = skitcut.frame_times([2.4, 4.0, 1.0, 10.0, 3.0, 1.5])
        self.assertEqual(skitcut.MAX_FRAMES, 8)
        self.assertEqual(times, [0.4, 1.2, 4.4, 6.9, 12.4, 18.9, 21.15, 21.5])

    # --- the command without trims or sound is what it always was --------------------------

    SKIT_ARGV = ['ffmpeg', '-hide_banner', '-nostdin', '-y', '-loglevel', 'error', '-i', '/s/1.mp4', '-i', '/s/2.mp4',
                 '-f', 'lavfi', '-t', '4.000', '-i', 'anullsrc=r=48000:cl=stereo', '-filter_complex',
                 "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,fps=30,"
                 "format=yuv420p[v0];[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a0];"
                 "[1:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,fps=30,"
                 "format=yuv420p[v1];[2:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a1];"
                 "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vc][ac];[vc]drawtext=fontfile='/f/font.ttf':"
                 "textfile='/c/label.txt':fontsize=37:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=12:"
                 "x=w-tw-32:y=32[vout];[ac]loudnorm=I=-16:TP=-1.5:LRA=11[an]",
                 '-map', '[vout]', '-map', '[an]', '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt',
                 'yuv420p', '-r', '30', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart',
                 '-t', '140', '/c/cut.mp4']
    BED_ARGV = ['ffmpeg', '-hide_banner', '-nostdin', '-y', '-loglevel', 'error', '-i', '/s/1.mp4', '-i', '/s/2.mp4',
                '-f', 'lavfi', '-t', '4.000', '-i', 'anullsrc=r=48000:cl=stereo', '-stream_loop', '-1', '-i',
                '/s/bed.mp3', '-filter_complex',
                "[0:v]scale=1068:1900:force_original_aspect_ratio=increase,crop=1068:1900,setsar=1,fps=30,"
                "format=yuv420p[v0];[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a0];"
                "[1:v]scale=1068:1900:force_original_aspect_ratio=increase,crop=1068:1900,setsar=1,fps=30,"
                "format=yuv420p[v1];[2:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a1];"
                "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vc][ac];[vc]drawtext=fontfile='/f/f.ttf':textfile='/c/l.txt':"
                "fontsize=65:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=21:x=w-tw-57:y=57[vout];"
                "[ac]loudnorm=I=-16:TP=-1.5:LRA=11[an];[3:a]aresample=48000,aformat=sample_fmts=fltp:"
                "channel_layouts=stereo,volume=0.10[bed];[an][bed]amix=inputs=2:duration=first:dropout_transition=0:"
                "normalize=0[amixed]",
                '-map', '[vout]', '-map', '[amixed]', '-c:v', 'libx264', '-preset', 'medium', '-crf', '19',
                '-pix_fmt', 'yuv420p', '-r', '30', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags',
                '+faststart', '-t', '140', '/c/cut.mp4']

    def test_a_skits_command_is_byte_for_byte_what_it_was(self):
        self.assertEqual(skitcut.argv("ffmpeg", self.CLIPS, "/c/label.txt", "/f/font.ttf", "/c/cut.mp4", "16:9"),
                         self.SKIT_ARGV)
        self.assertEqual(skitcut.argv("ffmpeg", self.CLIPS, "/c/l.txt", "/f/f.ttf", "/c/cut.mp4", "9:16",
                                      "/s/bed.mp3", 0.1), self.BED_ARGV)
        self.assertEqual(skitcut.argv("ffmpeg", self.CLIPS, "/c/label.txt", "/f/font.ttf", "/c/cut.mp4", "16:9",
                                      trims=None, sound=None), self.SKIT_ARGV)

    # --- a remake's trims and sound -----------------------------------------------------------

    REMAKE_CLIPS = [{"path": "/s/1.mp4", "audio": True, "seconds": 5.04}, {"path": "/s/2.mp4", "audio": False,
                                                                           "seconds": 3.7}]

    def test_the_source_sound_is_the_only_sound_cut_to_the_pictures_length(self):
        argv = skitcut.argv("ffmpeg", self.REMAKE_CLIPS, "/c/l.txt", "/f/f.ttf", "/c/cut.mp4", "9:16",
                            trims=[2.4, 4.0], sound="/m/audio.m4a")
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-i"], ["/s/1.mp4", "/s/2.mp4", "/m/audio.m4a"])
        self.assertNotIn("anullsrc=r=48000:cl=stereo", argv)
        graph = self.graph(argv)
        self.assertNotIn("[0:a]", graph)  # the clips' own sound is dropped
        self.assertIn("format=yuv420p,trim=end_frame=72,setpts=PTS-STARTPTS[v0]", graph)
        self.assertIn("format=yuv420p,tpad=stop_mode=clone:stop_duration=0.333,trim=end_frame=120,"
                      "setpts=PTS-STARTPTS[v1]", graph)
        self.assertIn("[v0][v1]concat=n=2:v=1:a=0[vc]", graph)
        self.assertIn("[2:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=end=6.400000,"
                      "asetpts=PTS-STARTPTS[as];[as]loudnorm=I=-16:TP=-1.5:LRA=11[an]", graph)
        self.assertIn("drawtext=fontfile='/f/f.ttf':textfile='/c/l.txt'", graph)
        self.assertEqual(argv[argv.index("-map") + 1:argv.index("-map") + 4], ["[vout]", "-map", "[an]"])

    def test_trims_without_a_sound_keep_each_clips_own_trimmed(self):
        argv = skitcut.argv("ffmpeg", self.REMAKE_CLIPS, "/c/l.txt", "/f/f.ttf", "/c/cut.mp4", "16:9",
                            trims=[2.4, 4.0])
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-i"],
                         ["/s/1.mp4", "/s/2.mp4", "anullsrc=r=48000:cl=stereo"])
        self.assertEqual(argv[argv.index("anullsrc=r=48000:cl=stereo") - 2], "4.000")
        graph = self.graph(argv)
        self.assertIn("[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=end=2.400000,"
                      "asetpts=PTS-STARTPTS[a0]", graph)
        self.assertIn("[2:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=end=4.000000,"
                      "asetpts=PTS-STARTPTS[a1]", graph)
        self.assertIn("[v0][a0][v1][a1]concat=n=2:v=1:a=1[vc][ac]", graph)

    def test_trims_are_whole_frames_adding_up_to_their_sum(self):
        self.assertEqual(skitcut.trim_frames([2.4, 4.0]), [72, 120])
        self.assertEqual(skitcut.trim_frames([1.01, 1.01, 1.01]), [30, 31, 30])
        self.assertEqual(sum(skitcut.trim_frames([1.37] * 6)), round(1.37 * 6 * skitcut.FPS))

    def test_a_clip_short_of_its_trim_by_over_half_a_second_is_refused(self):
        short = [{"path": "/s/1.mp4", "audio": True, "seconds": 3.4}]
        with self.assertRaises(skitcut.CutError):
            skitcut.argv("ffmpeg", short, "/c/l.txt", "/f/f.ttf", "/c/cut.mp4", "16:9", trims=[4.0], sound="/m/a.m4a")
        self.assertIn("tpad", self.graph(skitcut.argv("ffmpeg", short, "/c/l.txt", "/f/f.ttf", "/c/cut.mp4", "16:9",
                                                      trims=[3.9], sound="/m/a.m4a")))


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

    def test_a_remake_cut_to_its_trims_with_the_source_sound(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, out = os.path.join(tmp, "a.mp4"), os.path.join(tmp, "b.mp4"), os.path.join(tmp, "cut")
            sound = os.path.join(tmp, "audio.m4a")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24:duration=3",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", a], check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=360x640:rate=30:duration=1.8", b], check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=9",
                            "-c:a", "aac", sound], check=True)
            os.makedirs(out)
            font = next((str(p) for p in Path("/MoneyPrinterTurbo/resource/fonts").glob("*.ttf")), None) or \
                str(REPO / "vendor" / "moneyprinter" / "resource" / "fonts" / skitcut.FONT)
            made = skitcut.render([a, b], "AI parody · @teenydroid", "9:16", out, font, trims=[2.4, 2.0], sound=sound)
            self.assertEqual((made["sound"], made["trimmed"]), (True, [2.4, 2.0]))
            self.assertEqual(made["padded"][0], 0.0)
            self.assertGreater(made["padded"][1], 0.0)
            self.assertTrue(4.3 < made["seconds"] < 4.6, made["seconds"])


if __name__ == "__main__":
    unittest.main()
