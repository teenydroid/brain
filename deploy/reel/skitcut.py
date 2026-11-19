"""The skit cutter in Teeny's reel front (deploy/reel/serve.py,
`POST /teeny/v1/cuts`): one to four text-to-video shots joined into one mp4
with each shot's own sound, the loudness evened out, an optional music bed
under it, and the label burned into a corner for the whole length. Then a
few frames are taken from the cut for the viewer check.

Why not a MoneyPrinterTurbo task: upstream opens every material with
`audio=False` (app/services/video.py, `_open_video_clip_quietly`) and lays
one voice or one track over the whole video, so a shot's own dialogue and
sound effects would be thrown away. The submodule is never edited, so the
join is done here with the image's own ffmpeg, beside upstream, and
upstream's font (resource/fonts) draws the label.

Plain code with no upstream import, so hands/tests/reel tests it as it runs;
only `render` (and `probe`) start ffmpeg and ffprobe.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

MAX_CLIPS = 4
MAX_LABEL = 80
#: Where the label sits and how big it is, as a share of the picture's height.
LABEL_SIZE = 0.034
LABEL_MARGIN = 0.03
#: X takes 1920x1200 landscape and 1200x1900 portrait at most.
SIZES = {"16:9": (1920, 1080), "9:16": (1068, 1900), "1:1": (1080, 1080)}
FPS = 30
#: The loudness every cut is evened out to (EBU R128, as most platforms play).
LOUDNESS = "I=-16:TP=-1.5:LRA=11"
MAX_FRAMES = 6
MAX_SECONDS = 140
FONT = "BeVietnamPro-Bold.ttf"
CUT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: The files a cut's folder serves.
FILE = re.compile(r"cut\.mp4|frame-[0-9]\.jpg")
#: A label: printable, one line, no filter syntax to escape (it is read from
#: a file by drawtext, never written into the filter).
LABEL = re.compile(r"[^\x00-\x1f\x7f]{1,80}")
BED_VOLUME_PCT = (1, 60)
RENDER_SECONDS = 600


class CutError(ValueError):
    """A cut the front will not take (400), or one ffmpeg could not make."""


def check_cut(body: Any) -> dict[str, Any]:
    """{clips, label, aspect, bed, bed_volume}: the cut's job with every field
    checked; CutError otherwise. Paths are the job's own (`media/...`), for
    policy.resolve_source."""
    if not isinstance(body, dict):
        raise CutError("the body is a JSON object")
    unknown = sorted(set(body) - {"clips", "label", "aspect", "bed", "bed_volume_pct"})
    if unknown:
        raise CutError(f"not taken here: {', '.join(unknown)}")
    clips = body.get("clips")
    if not isinstance(clips, list) or not 1 <= len(clips) <= MAX_CLIPS:
        raise CutError(f"clips lists 1 to {MAX_CLIPS} shots")
    for clip in clips:
        if not isinstance(clip, str) or not clip.startswith("media/") or not clip.lower().endswith(".mp4"):
            raise CutError("a clip is an mp4 under media/")
    label = body.get("label")
    if not isinstance(label, str) or not LABEL.fullmatch(label) or not label.strip():
        raise CutError(f"label is one line of 1 to {MAX_LABEL} printable characters")
    aspect = body.get("aspect", "16:9")
    if aspect not in SIZES:
        raise CutError(f"aspect is one of {', '.join(SIZES)}")
    bed = body.get("bed")
    if bed is not None and (not isinstance(bed, str) or not bed.startswith(("media/", "assets/"))):
        raise CutError("a bed is an audio file under media/ or assets/")
    volume = body.get("bed_volume_pct", 12)
    if isinstance(volume, bool) or not isinstance(volume, int) or not BED_VOLUME_PCT[0] <= volume <= BED_VOLUME_PCT[1]:
        raise CutError(f"bed_volume_pct is {BED_VOLUME_PCT[0]} to {BED_VOLUME_PCT[1]}")
    return {"clips": list(clips), "label": label.strip(), "aspect": aspect, "bed": bed, "bed_volume": volume / 100}


def frame_times(durations: list[float]) -> list[float]:
    """When to take the viewer's frames: just after the start, the middle of
    every shot, and just before the end, at most MAX_FRAMES, in order."""
    total = sum(durations)
    if total <= 0:
        return []
    times = [min(0.4, total / 4)]
    at = 0.0
    for seconds in durations:
        times.append(at + seconds / 2)
        at += seconds
    times.append(max(0.0, total - 0.4))
    out: list[float] = []
    for t in sorted(times):
        if not out or t - out[-1] >= 0.2:
            out.append(round(t, 2))
    if len(out) > MAX_FRAMES:
        step = (len(out) - 1) / (MAX_FRAMES - 1)
        out = [out[round(i * step)] for i in range(MAX_FRAMES)]
    return out


def argv(ffmpeg: str, clips: list[dict[str, Any]], label_file: str, font: str, out: str, aspect: str,
         bed: str | None = None, bed_volume: float = 0.12) -> list[str]:
    """The ffmpeg command for one cut. `clips` are {path, audio, seconds}: a
    clip with no sound track gets silence of its own length, so every shot
    keeps its place and the concat lines up."""
    width, height = SIZES[aspect]
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    inputs = 0
    parts: list[str] = []
    joined = ""
    for n, clip in enumerate(clips):
        cmd += ["-i", clip["path"]]
        video = inputs
        inputs += 1
        if clip["audio"]:
            audio = f"[{video}:a]"
        else:
            cmd += ["-f", "lavfi", "-t", f"{clip['seconds']:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            audio = f"[{inputs}:a]"
            inputs += 1
        parts.append(f"[{video}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                     f"setsar=1,fps={FPS},format=yuv420p[v{n}]")
        parts.append(f"{audio}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{n}]")
        joined += f"[v{n}][a{n}]"
    size = max(16, round(height * LABEL_SIZE))
    margin = max(8, round(height * LABEL_MARGIN))
    parts.append(f"{joined}concat=n={len(clips)}:v=1:a=1[vc][ac]")
    # The label: every frame, top right, white on a dark box, from a file so
    # nothing in it is read as filter syntax.
    parts.append(f"[vc]drawtext=fontfile='{_quote(font)}':textfile='{_quote(label_file)}':fontsize={size}:"
                 f"fontcolor=white:box=1:boxcolor=black@0.6:boxborderw={max(6, size // 3)}:"
                 f"x=w-tw-{margin}:y={margin}[vout]")
    parts.append(f"[ac]loudnorm={LOUDNESS}[an]")
    final_audio = "[an]"
    if bed:
        cmd += ["-stream_loop", "-1", "-i", bed]
        parts.append(f"[{inputs}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"volume={bed_volume:.2f}[bed]")
        parts.append("[an][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amixed]")
        final_audio = "[amixed]"
        inputs += 1
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", final_audio,
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-t", str(MAX_SECONDS), out]
    return cmd


def _quote(path: str) -> str:
    """A path inside a filter's single quotes."""
    return path.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def probe(ffprobe: str, path: str) -> dict[str, Any]:
    """{audio, video, seconds} of one clip, from ffprobe."""
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                              "-of", "json", path], capture_output=True, text=True, timeout=60, check=True).stdout
        info = json.loads(out)
        kinds = {s.get("codec_type") for s in info.get("streams") or []}
        seconds = float((info.get("format") or {}).get("duration") or 0)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise CutError(f"a clip could not be read ({type(exc).__name__})") from None
    if "video" not in kinds or seconds <= 0:
        raise CutError("a clip has no picture")
    return {"audio": "audio" in kinds, "video": True, "seconds": seconds}


def render(sources: list[str], label: str, aspect: str, folder: str, font: str, bed: str | None = None,
           bed_volume: float = 0.12, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> dict[str, Any]:
    """The cut into `folder` (cut.mp4, frame-N.jpg): {seconds, shots,
    frames, label, labelled, audio}. CutError when ffmpeg fails."""
    clips = [{"path": path, **probe(ffprobe, path)} for path in sources]
    if sum(c["seconds"] for c in clips) > MAX_SECONDS:
        raise CutError(f"the shots run over {MAX_SECONDS} s together")
    label_file = os.path.join(folder, "label.txt")
    with open(label_file, "w", encoding="utf-8") as fh:
        fh.write(label)
    out = os.path.join(folder, "cut.mp4")
    try:
        subprocess.run(argv(ffmpeg, clips, label_file, font, out, aspect, bed, bed_volume), capture_output=True,
                       text=True, timeout=RENDER_SECONDS, check=True)
    except subprocess.CalledProcessError as exc:
        raise CutError(f"ffmpeg could not cut it: {(exc.stderr or '').strip()[-300:]}") from None
    except (subprocess.SubprocessError, OSError) as exc:
        raise CutError(f"ffmpeg could not cut it ({type(exc).__name__})") from None
    seconds = probe(ffprobe, out)["seconds"]
    frames = []
    for n, at in enumerate(frame_times([c["seconds"] for c in clips])):
        name = f"frame-{n}.jpg"
        try:
            subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{at:.2f}",
                            "-i", out, "-frames:v", "1", "-q:v", "3", os.path.join(folder, name)],
                           capture_output=True, timeout=60, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            raise CutError(f"a frame could not be taken ({type(exc).__name__})") from None
        frames.append(name)
    return {"seconds": round(seconds, 3), "shots": [round(c["seconds"], 3) for c in clips], "frames": frames,
            "label": label, "labelled": True, "audio": [c["audio"] for c in clips]}
