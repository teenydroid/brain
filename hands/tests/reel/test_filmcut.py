"""deploy/reel/filmcut.py, the reel front's story film cutter (docs/video.md
§12.5 step 7): the job it takes, the two ffmpeg passes it runs (the sound
measured, then the picture with no sound of its own under the narration,
the score ducked beneath the voice, effects on cue, loudness evened out in
a linear pass, captions and the label burned in), and the captions' timing.
The module imports nothing of upstream's, so it is loaded as it runs; the
test that renders runs only where ffmpeg is installed (the reel image), on
synthetic clips, and checks the film's length, that no seam goes silent,
the loudness, and that the score dips under the voice."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "deploy" / "reel"))
SPEC = importlib.util.spec_from_file_location("reel_filmcut", REPO / "deploy" / "reel" / "filmcut.py")
filmcut = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(filmcut)

FILM = "media/film/f1"
JOB = {
    "label": "AI parody · @teenydroid", "aspect": "9:16",
    "beats": [{"clip": f"{FILM}/b1.mp4", "t0": 0, "t1": 4.4}, {"clip": f"{FILM}/b2.mp4", "t0": 4.4, "t1": 7.4},
              {"plate": f"{FILM}/b3.png", "t0": 7.4, "t1": 11.0}],
    "narration": f"{FILM}/narration.mp3",
    "pieces": [{"beat": "b1", "from": 0, "to": 4.0, "at": 0}, {"beat": "b3", "from": 4.0, "to": 6.1, "at": 7.4}],
    "score": f"{FILM}/score.mp3", "sfx": [{"file": f"{FILM}/sfx-1.mp3", "at": 5.0}],
    "captions": [{"text": "this", "start": 0.1, "end": 0.4}, {"text": "man", "start": 0.45, "end": 0.7},
                 {"text": "needs", "start": 0.75, "end": 1.0}, {"text": "billions", "start": 1.1, "end": 1.6}],
}
MEASURED = {"input_i": "-23.10", "input_tp": "-5.20", "input_lra": "4.10", "input_thresh": "-33.40",
            "target_offset": "0.30"}


def files(job, stems=False):
    return {"clips": [{"path": f"/s/{b['clip'].rsplit('/', 1)[1]}", "audio": stems, "video_seconds": 5.0}
                      if b.get("clip") else None for b in job["beats"]],
            "plates": [f"/s/{b['plate'].rsplit('/', 1)[1]}" if b.get("plate") else None for b in job["beats"]],
            "narration": "/s/narration.mp3", "score": "/s/score.mp3" if job["score"] else None,
            "sfx": ["/s/sfx-1.mp3" for _ in job["sfx"]]}


def graph(argv):
    return argv[argv.index("-filter_complex") + 1]


class CheckTest(unittest.TestCase):
    def test_a_film_takes_its_beats_voice_score_effects_and_words(self):
        job = filmcut.check_film(JOB)
        self.assertEqual(job["length"], 11.0)
        self.assertEqual(job["score_volume"], 0.25)
        self.assertEqual(job["stem"], 0.0)
        self.assertEqual(job["beats"][2], {"plate": f"{FILM}/b3.png", "t0": 7.4, "t1": 11.0})
        self.assertEqual(job["sfx"], [{"file": f"{FILM}/sfx-1.mp3", "at": 5.0, "volume": 0.8}])

    def test_every_file_is_the_films_own(self):
        for path in ("media/remake/s1/b1.mp4", "media/film/../secrets/b1.mp4", "assets/teeny.mp4",
                     "media/film/f1/sub/b1.mp4", "/teeny/media/film/f1/b1.mp4"):
            with self.subTest(path=path):
                bad = {**JOB, "beats": [{**JOB["beats"][0], "clip": path}, *JOB["beats"][1:]]}
                with self.assertRaises(filmcut.FilmError):
                    filmcut.check_film(bad)

    def test_the_beats_tile_the_film_with_no_gap_or_overlap(self):
        gap = {**JOB, "beats": [JOB["beats"][0], {**JOB["beats"][1], "t0": 5.0}, JOB["beats"][2]]}
        with self.assertRaises(filmcut.FilmError):
            filmcut.check_film(gap)

    def test_a_beat_is_its_clip_or_its_still_never_both_or_neither(self):
        for beat in ({"t0": 0, "t1": 4.4}, {"clip": f"{FILM}/b1.mp4", "plate": f"{FILM}/b1.png", "t0": 0, "t1": 4.4}):
            with self.subTest(beat=beat):
                with self.assertRaises(filmcut.FilmError):
                    filmcut.check_film({**JOB, "beats": [beat, *JOB["beats"][1:]]})

    def test_a_piece_of_narration_must_fit_inside_the_film(self):
        late = {**JOB, "pieces": [{"from": 0, "to": 4.0, "at": 9.0}]}
        with self.assertRaises(filmcut.FilmError):
            filmcut.check_film(late)

    def test_unknown_fields_and_bad_volumes_are_refused(self):
        for bad in ({**JOB, "force": True}, {**JOB, "score_volume_pct": 200}, {**JOB, "stem_volume_pct": 90},
                    {**JOB, "captions": [{"text": "two words\n", "start": 0, "end": 1}]},
                    {**JOB, "beats": JOB["beats"][:2]}):
            with self.subTest(bad=bad):
                with self.assertRaises(filmcut.FilmError):
                    filmcut.check_film(bad)


class CaptionTest(unittest.TestCase):
    def test_words_go_up_a_few_at_a_time_and_never_overlap(self):
        chunks = filmcut.caption_chunks(JOB["captions"])
        self.assertEqual([c["text"] for c in chunks], ["this man needs", "billions"])
        self.assertLessEqual(chunks[0]["end"], chunks[1]["start"])

    def test_a_long_pause_starts_a_new_caption(self):
        words = [{"text": "one", "start": 0.0, "end": 0.3}, {"text": "two", "start": 3.0, "end": 3.3}]
        self.assertEqual(len(filmcut.caption_chunks(words)), 2)


class ArgvTest(unittest.TestCase):
    def argv(self, job=None, stems=False):
        job = filmcut.check_film(job or JOB)
        return filmcut.argv("ffmpeg", job, files(job, stems), MEASURED, "/c/label.txt",
                            ["/c/caption-000.txt", "/c/caption-001.txt"], "/f/font.ttf", "/c/cut.mp4")

    def test_the_clips_bring_no_sound_and_the_voice_is_placed_untouched(self):
        g = graph(self.argv())
        self.assertIn("concat=n=3:v=1:a=0[vc]", g)
        self.assertNotRegex(g, r"\[[0-2]:a\]")  # no clip's sound
        self.assertIn("atrim=start=4.000:end=6.100,asetpts=PTS-STARTPTS,adelay=7400:all=1[p1]", g)
        self.assertNotIn("atempo", g)
        self.assertNotIn("rubberband", g)

    def test_the_score_is_ducked_under_the_voice(self):
        g = graph(self.argv())
        self.assertIn("[voice]asplit=2[voicemix][voicekey]", g)
        self.assertIn(f"[bed][voicekey]sidechaincompress={filmcut.DUCK}[ducked]", g)
        self.assertIn("volume=0.25", g)

    def test_loudness_is_applied_linearly_from_the_measurement(self):
        g = graph(self.argv())
        self.assertIn("measured_I=-23.10", g)
        self.assertIn("linear=true", g)

    def test_a_rejected_clips_beat_plays_its_still_with_a_push_in(self):
        g = graph(self.argv())
        self.assertIn("[2:v]scale=2136:3800", g)
        self.assertIn("zoompan=z='min(1+", g)
        self.assertIn(":d=108:", g)  # 3.6 s at 30 fps

    def test_captions_and_the_label_are_burned_in_from_files(self):
        g = graph(self.argv())
        self.assertIn("textfile='/c/label.txt'", g)
        self.assertIn("textfile='/c/caption-001.txt'", g)
        self.assertIn("enable='between(t,1.100,1.600)'", g)

    def test_stems_are_off_unless_asked_and_then_quiet_and_faded(self):
        g = graph(self.argv({**JOB, "stem_volume_pct": 15}, stems=True))
        self.assertIn("[0:a]", g)
        self.assertIn("volume=0.15,afade=t=in:d=0.1", g)
        self.assertIn("adelay=4400:all=1[st1]", g)

    def test_a_clip_far_short_of_its_beat_is_refused(self):
        job = filmcut.check_film(JOB)
        short = files(job)
        short["clips"][0]["video_seconds"] = 2.0
        with self.assertRaises(filmcut.FilmError):
            filmcut.argv("ffmpeg", job, short, MEASURED, "/c/l.txt", [], "/f/f.ttf", "/c/cut.mp4")

    def test_the_measurement_is_read_from_loudnorms_last_json(self):
        stderr = "frame=1\n[Parsed_loudnorm_0 @ 0x1]\n" + json.dumps({**MEASURED, "output_i": "-16"}) + "\n"
        self.assertEqual(filmcut.read_loudness(stderr), MEASURED)
        with self.assertRaises(filmcut.FilmError):
            filmcut.read_loudness("no json here")


FONT = next((p for p in ("/MoneyPrinterTurbo/resource/fonts/BeVietnamPro-Bold.ttf",
                         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") if os.path.exists(p)), None)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe") and FONT, "needs ffmpeg and a font")
class RenderTest(unittest.TestCase):
    """A real film from synthetic parts: a tone for the voice with silence
    between its lines, a quieter tone for the score, a click for an effect."""

    def run_ffmpeg(self, *args):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.dir = self.tmp.name
        for name, seconds in (("b1", 5), ("b2", 5)):
            self.run_ffmpeg("-f", "lavfi", "-i", f"testsrc=size=320x568:rate=24:d={seconds}",
                            "-f", "lavfi", "-i", f"sine=f=200:d={seconds}", "-shortest", f"{d}/{name}.mp4")
        self.run_ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x568:d=1", "-frames:v", "1", f"{d}/b3.png")
        # The voice: two lines of tone, 4 s and 2.1 s, with a pause between.
        self.run_ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=6.1", "-af",
                        "volume='if(between(t,3.8,4.2),0,1)':eval=frame", f"{d}/narration.mp3")
        self.run_ffmpeg("-f", "lavfi", "-i", "sine=f=110:d=3", f"{d}/score.mp3")
        self.run_ffmpeg("-f", "lavfi", "-i", "sine=f=1000:d=0.3", f"{d}/sfx-1.mp3")

    def tearDown(self):
        self.tmp.cleanup()

    def stats(self, path, start, end, chain=""):
        """The mean volume of `path` between two times, in dB."""
        out = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-ss", str(start), "-t", str(end - start),
                              "-i", path, "-af", f"{chain}volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        return float(re.search(r"mean_volume: (-?[\d.]+) dB", out).group(1))

    def test_a_film_is_its_narrations_length_with_no_silent_seam_and_even_loudness(self):
        d = self.dir
        job = filmcut.check_film(JOB)
        paths = {"clips": [f"{d}/b1.mp4", f"{d}/b2.mp4", None], "plates": [None, None, f"{d}/b3.png"],
                 "narration": f"{d}/narration.mp3", "score": f"{d}/score.mp3", "sfx": [f"{d}/sfx-1.mp3"]}
        out_dir = os.path.join(d, "cut")
        os.makedirs(out_dir)
        made = filmcut.render(job, paths, out_dir, FONT)
        cut = os.path.join(out_dir, "cut.mp4")
        self.assertAlmostEqual(made["seconds"], 11.0, delta=0.1)
        self.assertEqual(made["stills"], [2])
        # No seam goes silent: the score runs under every cut.
        silences = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", cut, "-af",
                                   "silencedetect=noise=-50dB:d=0.3", "-f", "null", "-"],
                                  capture_output=True, text=True).stderr
        self.assertNotIn("silence_start", silences)
        # Loudness evened out to about -16 LUFS.
        loud = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", cut, "-af", "ebur128", "-f", "null", "-"],
                              capture_output=True, text=True).stderr
        integrated = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", loud)[-1])
        self.assertAlmostEqual(integrated, -16.0, delta=1.5)
        # The score dips under the voice: its 110 Hz band is quieter while
        # Teeny talks (0-4 s) than in the silent beat between lines (4.4-7.4 s).
        band = "lowpass=f=150,highpass=f=80,"
        self.assertLess(self.stats(cut, 1.0, 3.5, band), self.stats(cut, 5.5, 7.2, band) - 3)
