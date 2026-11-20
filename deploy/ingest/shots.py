"""How a clip becomes shots, for the ingest service (deploy/ingest/serve.py):
scene cuts in, contiguous shots out, on whole frames, and the ffmpeg and
ffprobe command lines and answers around that. Plain code with nothing
outside the standard library, so hands/tests/ingest tests it as it runs;
only serve.py starts ffmpeg.

Times here are seconds into the excerpt (the part of the video taken), which
starts at 0. A shot's start and length are whole frames at the clip's frame
rate, written to three decimals as the difference of two rounded edges, so
the lengths add up to the excerpt's exactly however long the clip runs.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

DEFAULT_FPS = 30.0
FPS_RANGE = (5.0, 120.0)
#: How far into a shot its frame is taken.
FRAME_OFFSET = 0.1
#: The longest side of a shot's frame, in pixels.
FRAME_SIDE = 1024
SCENE_THRESHOLD = 0.3
MAX_SHOT_FILES = 9

_PTS_TIME = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
_ASPECTS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}


# --- the plan -------------------------------------------------------------------------------


def plan(cuts: list[float], fps: float, seconds: float, min_shot: float, max_shot: float, max_shots: int) -> dict[str, Any]:
    """{seconds, shots: [{n, start, seconds}]} for an excerpt of `seconds`
    with scene cuts at `cuts` (seconds into it).

    Cuts are moved to the nearest whole frame; those outside the excerpt or
    repeated are dropped. A shot shorter than `min_shot` merges into its
    shorter neighbour (the one before on a tie), shortest first; one longer
    than `max_shot` is split into the fewest equal pieces (to the frame)
    that fit. With more than `max_shots` shots the excerpt ends where shot
    `max_shots` does. ValueError when the excerpt has no whole frame."""
    total = round(seconds * fps)
    if total < 1:
        raise ValueError("the excerpt is shorter than one frame")
    edges = sorted({round(c * fps) for c in cuts if 0 < round(c * fps) < total})
    marks = [0, *edges, total]
    lengths = [b - a for a, b in zip(marks, marks[1:])]
    least = max(1, round(min_shot * fps))
    most = max(least, round(max_shot * fps))
    while len(lengths) > 1:
        shortest = min(range(len(lengths)), key=lambda i: (lengths[i], i))
        if lengths[shortest] >= least:
            break
        if shortest == 0:
            other = 1
        elif shortest == len(lengths) - 1:
            other = shortest - 1
        else:
            other = shortest - 1 if lengths[shortest - 1] <= lengths[shortest + 1] else shortest + 1
        low, high = min(shortest, other), max(shortest, other)
        lengths[low] += lengths[high]
        del lengths[high]
    frames: list[int] = []
    for length in lengths:
        pieces = max(1, math.ceil(length / most))
        base, extra = divmod(length, pieces)
        frames += [base + 1 if n < extra else base for n in range(pieces)]
    frames = frames[:max_shots]
    shots = []
    at = 0
    for n, length in enumerate(frames, 1):
        start, end = round(at / fps, 3), round((at + length) / fps, 3)
        shots.append({"n": n, "start": start, "seconds": round(end - start, 3)})
        at += length
    return {"seconds": round(at / fps, 3), "shots": shots}


def frame_name(n: int) -> str:
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_SHOT_FILES:
        raise ValueError(f"a shot is numbered 1 to {MAX_SHOT_FILES}")
    return f"shot-{n}.jpg"


def frame_time(shot: dict[str, Any]) -> float:
    """When, in the excerpt, a shot's frame is taken."""
    return round(shot["start"] + min(FRAME_OFFSET, shot["seconds"] / 2), 3)


# --- what ffprobe and ffmpeg say ------------------------------------------------------------


def parse_fps(rate: Any) -> float:
    """A frame rate as ffprobe writes it ("30000/1001", "25"), or DEFAULT_FPS
    for anything unreadable or outside FPS_RANGE."""
    try:
        text = str(rate).strip()
        top, _, bottom = text.partition("/")
        value = float(top) / float(bottom) if bottom else float(top)
    except (ValueError, ZeroDivisionError):
        return DEFAULT_FPS
    if not math.isfinite(value) or not FPS_RANGE[0] <= value <= FPS_RANGE[1]:
        return DEFAULT_FPS
    return value


def fps_text(fps: float) -> str:
    """A frame rate as ffmpeg is given it: three decimals at most."""
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def parse_cuts(stderr: str) -> list[float]:
    """The scene cuts (seconds) in ffmpeg's showinfo lines."""
    out = []
    for line in stderr.splitlines():
        if "showinfo" not in line:
            continue
        found = _PTS_TIME.search(line)
        if found:
            out.append(float(found.group(1)))
    return out


def parse_probe(text: str) -> dict[str, Any]:
    """{width, height, fps, audio, seconds} from `ffprobe -of json`;
    ValueError when it is not a clip with a picture and a length."""
    try:
        info = json.loads(text)
    except ValueError:
        raise ValueError("ffprobe's answer is not JSON") from None
    if not isinstance(info, dict):
        raise ValueError("ffprobe's answer is not an object")
    streams = [s for s in info.get("streams") or [] if isinstance(s, dict)]
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("the clip has no picture")
    width, height = video.get("width"), video.get("height")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height)):
        raise ValueError("the clip has no size")
    try:
        seconds = float((info.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        raise ValueError("the clip has no length") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("the clip has no length")
    fps = parse_fps(video.get("avg_frame_rate"))
    if fps == DEFAULT_FPS:
        fps = parse_fps(video.get("r_frame_rate"))
    return {"width": width, "height": height, "fps": fps, "seconds": seconds,
            "audio": any(s.get("codec_type") == "audio" for s in streams)}


def aspect(width: int, height: int) -> str:
    """The nearest of 16:9, 9:16 and 1:1 to a picture of this size."""
    if not width > 0 or not height > 0:
        raise ValueError("a picture has a size")
    ratio = width / height
    return min(_ASPECTS, key=lambda name: abs(math.log(ratio / _ASPECTS[name])))


# --- command lines --------------------------------------------------------------------------

_QUIET = ["-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
#: ffmpeg and ffprobe read files and pipes only: never a network protocol a
#: playlist or a container could name.
_FILES_ONLY = ["-protocol_whitelist", "file,pipe"]


def ffprobe_argv(ffprobe: str, path: str) -> list[str]:
    return [ffprobe, "-v", "error", *_FILES_ONLY, "-show_entries",
            "stream=codec_type,width,height,avg_frame_rate,r_frame_rate:format=duration", "-of", "json", path]


def scene_argv(ffmpeg: str, src: str) -> list[str]:
    """ffmpeg reading `src` to nothing, logging the frames whose scene score
    is over the threshold (showinfo, on stderr)."""
    return [ffmpeg, "-hide_banner", "-nostdin", "-nostats", *_FILES_ONLY, "-i", src, "-an", "-vf",
            f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"]


def excerpt_argv(ffmpeg: str, src: str, out: str, start: float, seconds: float, fps: float, max_height: int,
                 audio: bool = True) -> list[str]:
    """The part of `src` from `start` for `seconds`, re-encoded at `fps` and
    at most `max_height` high (even sides), with its sound when `audio`."""
    sound = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100"] if audio else ["-an"]
    return [ffmpeg, *_QUIET, *_FILES_ONLY, "-ss", f"{start:.3f}", "-i", src, "-t", f"{seconds:.3f}", "-vf",
            f"scale=-2:'trunc(min({max_height},ih)/2)*2'", "-r", fps_text(fps), "-c:v", "libx264", "-preset",
            "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", *sound, "-movflags", "+faststart", out]


def audio_argv(ffmpeg: str, src: str, out: str, seconds: float) -> list[str]:
    """The sound of `src`, as AAC, for exactly `seconds`."""
    return [ffmpeg, *_QUIET, *_FILES_ONLY, "-i", src, "-t", f"{seconds:.3f}", "-vn", "-c:a", "aac", "-b:a", "128k", out]


def frame_argv(ffmpeg: str, src: str, out: str, at: float) -> list[str]:
    """One JPEG of `src` at `at` seconds, its longer side at most FRAME_SIDE."""
    side = FRAME_SIDE
    scale = f"scale=w='if(gt(iw,ih),min({side},iw),-2)':h='if(gt(iw,ih),-2,min({side},ih))'"
    return [ffmpeg, *_QUIET, *_FILES_ONLY, "-ss", f"{at:.3f}", "-i", src, "-frames:v", "1", "-vf", scale, "-q:v", "3", out]
