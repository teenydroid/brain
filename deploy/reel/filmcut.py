"""The story film cutter in Teeny's reel front (deploy/reel/serve.py,
`POST /teeny/v1/films`; docs/video.md §12.5 step 7): a narrated film of three
to eight beats, built audio first.

The narration is the clock. media/narration.py has already said where every
beat sits on the film's timeline and where each slice of the narration goes,
so this cutter only does what it is told, and never stretches anything:

- Picture: each beat is its clip, trimmed to the beat's window (a clip is
  bought at least that long), or its still with a slow push-in when the
  clip was rejected. The beats are joined with no sound of their own.
- Voice: each slice of the narration placed where the clock put it,
  untouched: no clip's sound, no resampling of its pitch, no speed change.
- Score: one bed looped to the film's length, at its volume, ducked under
  the voice by a sidechain compressor, so the music dips whenever Teeny
  talks and comes back between lines.
- Effects: each placed at its time.
- Stems: a clip's own sound, only when asked (`stem_volume_pct`), quiet, with
  short fades at both ends so no beat starts or stops with a click.
- Loudness: the mix measured once, then brought to -16 LUFS / -1.5 dBTP in a
  second, linear pass (two-pass loudnorm), so a quiet film and a loud one
  play the same.
- Captions: the narration's words burned in, a few at a time, at the times
  they are spoken; and the label in a corner for the whole length, as every
  cut has it.

Every file a film names is under media/film/. Plain code with no upstream
import (skitcut's helpers only), so hands/tests/reel tests it as it runs;
only `render` starts ffmpeg and ffprobe.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

import skitcut

BEATS = (3, 8)
#: A film's own files, and nothing else.
FILM_FILE = re.compile(r"media/film/[A-Za-z0-9][A-Za-z0-9_-]{0,63}/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
CLIP_SUFFIX = ".mp4"
PLATE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a")
#: A beat's window, and how short of it a clip may be (its last frame held).
WINDOW = (0.5, 15.0)
MAX_PAD = 1.0
MAX_SFX = 24
MAX_CAPTIONS = 400
MAX_CAPTION_CHARS = 40
SCORE_VOLUME_PCT = (1, 80)
SFX_VOLUME_PCT = (1, 150)
STEM_VOLUME_PCT = (0, 40)
#: The ducking: how hard the score dips under the voice, and how fast.
DUCK = "threshold=0.02:ratio=8:attack=20:release=400:makeup=1"
#: A stem's fade at each end of its beat.
STEM_FADE = 0.1
#: A still's push-in: from 1.0 to this zoom over its beat.
PUSH = 1.12
#: Captions: a few words at a time, no longer than this on screen.
CAPTION_WORDS = 3
CAPTION_SECONDS = 1.6
CAPTION_SIZE = 0.05
LOUDNESS = {"I": -16.0, "TP": -1.5, "LRA": 11.0}
SECONDS = 140


class FilmError(skitcut.CutError):
    """A film the front will not take (400), or one ffmpeg could not make."""


def _number(value: Any, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise FilmError(f"{what} is a number from {low:g} to {high:g}")
    return float(value)


def _file(value: Any, suffixes: tuple[str, ...], what: str) -> str:
    if not isinstance(value, str) or not FILM_FILE.fullmatch(value) or not value.lower().endswith(suffixes):
        raise FilmError(f"{what} is a {'/'.join(s.lstrip('.') for s in suffixes)} file under media/film/<id>/")
    return value


def check_film(body: Any) -> dict[str, Any]:
    """The film's job with every field checked; FilmError otherwise. Paths are
    the job's own (`media/film/...`), for policy.resolve_source."""
    if not isinstance(body, dict):
        raise FilmError("the body is a JSON object")
    known = {"beats", "narration", "pieces", "score", "score_volume_pct", "sfx", "stem_volume_pct", "captions",
             "label", "aspect"}
    unknown = sorted(set(body) - known)
    if unknown:
        raise FilmError(f"not taken here: {', '.join(unknown)}")
    label = body.get("label")
    if not isinstance(label, str) or not skitcut.LABEL.fullmatch(label) or not label.strip():
        raise FilmError(f"label is one line of 1 to {skitcut.MAX_LABEL} printable characters")
    aspect = body.get("aspect", "9:16")
    if aspect not in skitcut.SIZES:
        raise FilmError(f"aspect is one of {', '.join(skitcut.SIZES)}")
    beats_raw = body.get("beats")
    if not isinstance(beats_raw, list) or not BEATS[0] <= len(beats_raw) <= BEATS[1]:
        raise FilmError(f"a film is {BEATS[0]} to {BEATS[1]} beats")
    beats = []
    at = 0.0
    for n, beat in enumerate(beats_raw, 1):
        if not isinstance(beat, dict) or set(beat) - {"clip", "plate", "t0", "t1"}:
            raise FilmError(f"beat {n} is {{clip or plate, t0, t1}}")
        if (beat.get("clip") is None) == (beat.get("plate") is None):
            raise FilmError(f"beat {n} is its clip or its still, one of them")
        t0 = _number(beat.get("t0"), 0, SECONDS, f"beat {n}'s t0")
        t1 = _number(beat.get("t1"), 0, SECONDS, f"beat {n}'s t1")
        if abs(t0 - at) > 0.002:
            raise FilmError(f"beat {n} starts at {t0:g} s, and the beat before it ends at {at:g} s")
        _number(t1 - t0, *WINDOW, f"beat {n}'s window")
        kept = {"t0": t0, "t1": t1}
        if beat.get("clip") is not None:
            kept["clip"] = _file(beat["clip"], (CLIP_SUFFIX,), f"beat {n}'s clip")
        else:
            kept["plate"] = _file(beat["plate"], PLATE_SUFFIXES, f"beat {n}'s still")
        beats.append(kept)
        at = t1
    length = at
    narration = _file(body.get("narration"), AUDIO_SUFFIXES, "the narration")
    pieces_raw = body.get("pieces")
    if not isinstance(pieces_raw, list) or not 1 <= len(pieces_raw) <= BEATS[1]:
        raise FilmError("pieces lists where each slice of the narration goes")
    pieces = []
    for n, piece in enumerate(pieces_raw, 1):
        if not isinstance(piece, dict) or set(piece) - {"from", "to", "at", "beat"}:
            raise FilmError(f"piece {n} is {{from, to, at}}")
        start = _number(piece.get("from"), 0, SECONDS, f"piece {n}'s from")
        end = _number(piece.get("to"), 0, SECONDS, f"piece {n}'s to")
        place = _number(piece.get("at"), 0, length, f"piece {n}'s at")
        if end <= start or place + (end - start) > length + 0.002:
            raise FilmError(f"piece {n} does not fit the film")
        pieces.append({"from": start, "to": end, "at": place})
    score = _file(body["score"], AUDIO_SUFFIXES, "the score") if body.get("score") is not None else None
    score_volume = _number(body.get("score_volume_pct", 25), *SCORE_VOLUME_PCT, "score_volume_pct") / 100
    sfx_raw = body.get("sfx") or []
    if not isinstance(sfx_raw, list) or len(sfx_raw) > MAX_SFX:
        raise FilmError(f"sfx lists at most {MAX_SFX} effects")
    sfx = []
    for n, effect in enumerate(sfx_raw, 1):
        if not isinstance(effect, dict) or set(effect) - {"file", "at", "volume_pct"}:
            raise FilmError(f"effect {n} is {{file, at, volume_pct?}}")
        sfx.append({"file": _file(effect.get("file"), AUDIO_SUFFIXES, f"effect {n}"),
                    "at": _number(effect.get("at"), 0, length, f"effect {n}'s at"),
                    "volume": _number(effect.get("volume_pct", 80), *SFX_VOLUME_PCT, f"effect {n}'s volume") / 100})
    stem = _number(body.get("stem_volume_pct", 0), *STEM_VOLUME_PCT, "stem_volume_pct") / 100
    captions_raw = body.get("captions") or []
    if not isinstance(captions_raw, list) or len(captions_raw) > MAX_CAPTIONS:
        raise FilmError(f"captions lists at most {MAX_CAPTIONS} words")
    words = []
    for n, word in enumerate(captions_raw, 1):
        if not isinstance(word, dict) or set(word) - {"text", "start", "end"}:
            raise FilmError(f"caption {n} is {{text, start, end}}")
        text = word.get("text")
        if not isinstance(text, str) or not skitcut.LABEL.fullmatch(text) or len(text) > MAX_CAPTION_CHARS:
            raise FilmError(f"caption {n} is one printable word")
        start = _number(word.get("start"), 0, length, f"caption {n}'s start")
        end = _number(word.get("end"), start, length, f"caption {n}'s end")
        words.append({"text": text, "start": start, "end": end})
    return {"beats": beats, "length": round(length, 3), "narration": narration, "pieces": pieces, "score": score,
            "score_volume": score_volume, "sfx": sfx, "stem": stem, "captions": words, "label": label.strip(),
            "aspect": aspect}


def caption_chunks(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Words in groups of up to CAPTION_WORDS, each on screen from its first
    word's start to its last word's end (at most CAPTION_SECONDS), and never
    past the next group's start."""
    groups: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda w: w["start"]):
        if groups and len(groups[-1]) < CAPTION_WORDS and word["end"] - groups[-1][0]["start"] <= CAPTION_SECONDS:
            groups[-1].append(word)
        else:
            groups.append([word])
    out = []
    for n, group in enumerate(groups):
        start, end = group[0]["start"], group[-1]["end"]
        if n + 1 < len(groups):
            end = min(end, groups[n + 1][0]["start"])
        out.append({"text": " ".join(w["text"] for w in group), "start": round(start, 3),
                    "end": round(max(end, start + 0.2), 3)})
    return out


def _audio(job: dict[str, Any], files: dict[str, Any], first_input: int) -> tuple[list[str], list[str], str]:
    """(inputs, filters, label): the film's sound as ffmpeg inputs (numbered
    from `first_input`) and a graph ending in its label, before loudness."""
    length = job["length"]
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    # Every stream a mixer sees runs the film's whole length: a stream that
    # ends early (an effect, a slice of the narration) leaves amix's output
    # to the order its inputs happen to end in, which ffmpeg 7's threaded
    # demuxers do not fix, and a film went silent after its first effect.
    whole = f"apad,atrim=end={length:.3f},asetpts=PTS-STARTPTS"
    inputs: list[str] = ["-i", files["narration"]]
    voice = first_input
    n = first_input + 1
    parts: list[str] = []
    split = "".join(f"[nar{i}]" for i in range(len(job["pieces"])))
    parts.append(f"[{voice}:a]{fmt},asplit={len(job['pieces'])}{split}" if len(job["pieces"]) > 1
                 else f"[{voice}:a]{fmt}[nar0]")
    placed = []
    for i, piece in enumerate(job["pieces"]):
        delay = round(piece["at"] * 1000)
        parts.append(f"[nar{i}]atrim=start={piece['from']:.3f}:end={piece['to']:.3f},asetpts=PTS-STARTPTS,"
                     f"adelay={delay}:all=1,{whole}[p{i}]")
        placed.append(f"[p{i}]")
    parts.append(f"{''.join(placed)}amix=inputs={len(placed)}:duration=first:normalize=0,{whole}[voice]"
                 if len(placed) > 1 else f"{placed[0]}anull[voice]")
    mix = ["[voicemix]"]
    if job["score"]:
        inputs += ["-stream_loop", "-1", "-i", files["score"]]
        parts.append("[voice]asplit=2[voicemix][voicekey]")
        parts.append(f"[{n}:a]{fmt},volume={job['score_volume']:.2f},atrim=end={length:.3f},asetpts=PTS-STARTPTS,"
                     f"{whole}[bed]")
        parts.append(f"[bed][voicekey]sidechaincompress={DUCK}[ducked]")
        mix.append("[ducked]")
        n += 1
    else:
        parts.append("[voice]anull[voicemix]")
    for i, effect in enumerate(job["sfx"]):
        inputs += ["-i", files["sfx"][i]]
        parts.append(f"[{n}:a]{fmt},volume={effect['volume']:.2f},adelay={round(effect['at'] * 1000)}:all=1,"
                     f"{whole}[fx{i}]")
        mix.append(f"[fx{i}]")
        n += 1
    for i, stem in enumerate(files.get("stems") or []):
        if stem is None:
            continue
        beat = job["beats"][i]
        window = beat["t1"] - beat["t0"]
        parts.append(f"[{stem}:a]{fmt},atrim=end={window:.3f},asetpts=PTS-STARTPTS,volume={job['stem']:.2f},"
                     f"afade=t=in:d={STEM_FADE},afade=t=out:st={max(0.0, window - STEM_FADE):.3f}:d={STEM_FADE},"
                     f"adelay={round(beat['t0'] * 1000)}:all=1,{whole}[st{i}]")
        mix.append(f"[st{i}]")
    parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:dropout_transition=0:normalize=0,"
                 f"{whole}[mix]" if len(mix) > 1 else f"{mix[0]}anull[mix]")
    return inputs, parts, "[mix]"


def loudnorm(measured: dict[str, Any] | None) -> str:
    """The loudness filter: the first pass measures (print_format=json), the
    second applies the measurement linearly."""
    target = f"I={LOUDNESS['I']}:TP={LOUDNESS['TP']}:LRA={LOUDNESS['LRA']}"
    if measured is None:
        return f"loudnorm={target}:print_format=json"
    return (f"loudnorm={target}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true")


def measure_argv(ffmpeg: str, job: dict[str, Any], files: dict[str, Any]) -> list[str]:
    """The first pass: the sound alone, measured, written nowhere. Stems are
    clips' sound, so their clips come first as inputs."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "info"]
    stems = _stem_inputs(job, files, cmd)
    inputs, parts, label = _audio(job, {**files, "stems": stems}, len([s for s in stems if s is not None]))
    cmd += inputs
    parts.append(f"{label}{loudnorm(None)}[out]")
    return cmd + ["-filter_complex", ";".join(parts), "-map", "[out]", "-f", "null", "-"]


def _stem_inputs(job: dict[str, Any], files: dict[str, Any], cmd: list[str]) -> list[int | None]:
    """Each beat's clip as an input when its sound is kept: its input number,
    or None (no stems asked, a still, or a clip with no sound)."""
    out: list[int | None] = []
    at = 0
    for beat, clip in zip(job["beats"], files["clips"]):
        if job["stem"] > 0 and clip and clip.get("audio"):
            cmd += ["-i", clip["path"]]
            out.append(at)
            at += 1
        else:
            out.append(None)
    return out


def read_loudness(stderr: str) -> dict[str, Any]:
    """The first pass's measurement, from the JSON loudnorm prints last."""
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    try:
        found = json.loads(stderr[start:end + 1])
        return {k: found[k] for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError):
        raise FilmError("the loudness could not be measured") from None


def argv(ffmpeg: str, job: dict[str, Any], files: dict[str, Any], measured: dict[str, Any], label_file: str,
         caption_files: list[str], font: str, out: str) -> list[str]:
    """The second pass: the picture, the sound and the text, into `out`.
    `files` holds each beat's {path, audio, video_seconds} clip or None, its
    still's path or None, the narration, the score and the effects."""
    width, height = skitcut.SIZES[job["aspect"]]
    windows = [b["t1"] - b["t0"] for b in job["beats"]]
    frames = skitcut.trim_frames(windows)
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    parts: list[str] = []
    joined = ""
    for i, (beat, clip, plate) in enumerate(zip(job["beats"], files["clips"], files["plates"])):
        count = frames[i]
        if clip is not None:
            cmd += ["-i", clip["path"]]
            short = count / skitcut.FPS - clip["video_seconds"]
            if short > MAX_PAD + 1e-6:
                raise FilmError(f"beat {i + 1}'s clip runs {clip['video_seconds']:.2f} s, "
                                f"{short:.2f} s short of its beat")
            pad = f",tpad=stop_mode=clone:stop_duration={max(0.0, short) + 1 / skitcut.FPS:.3f}" if short > 0 else ""
            parts.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                         f"setsar=1,fps={skitcut.FPS},format=yuv420p{pad},trim=end_frame={count},"
                         f"setpts=PTS-STARTPTS[v{i}]")
        else:
            cmd += ["-i", plate]
            step = (PUSH - 1) / max(1, count)
            parts.append(f"[{i}:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                         f"crop={width * 2}:{height * 2},zoompan=z='min(1+{step:.6f}*on,{PUSH})':d={count}:"
                         f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={skitcut.FPS},"
                         f"setsar=1,format=yuv420p,trim=end_frame={count},setpts=PTS-STARTPTS[v{i}]")
        joined += f"[v{i}]"
    parts.append(f"{joined}concat=n={len(job['beats'])}:v=1:a=0[vc]")
    size = max(16, round(height * skitcut.LABEL_SIZE))
    margin = max(8, round(height * skitcut.LABEL_MARGIN))
    text = f"[vc]drawtext=fontfile='{skitcut._quote(font)}':textfile='{skitcut._quote(label_file)}':fontsize={size}:" \
           f"fontcolor=white:box=1:boxcolor=black@0.6:boxborderw={max(6, size // 3)}:x=w-tw-{margin}:y={margin}"
    big = max(20, round(height * CAPTION_SIZE))
    for chunk, path in zip(caption_chunks(job["captions"]), caption_files):
        text += (f",drawtext=fontfile='{skitcut._quote(font)}':textfile='{skitcut._quote(path)}':fontsize={big}:"
                 f"fontcolor=white:borderw={max(2, big // 12)}:bordercolor=black:x=(w-tw)/2:y=h*0.72:"
                 f"enable='between(t,{chunk['start']:.3f},{chunk['end']:.3f})'")
    parts.append(f"{text}[vout]")
    stems = [None] * len(job["beats"])
    if job["stem"] > 0:
        stems = [i if clip is not None and clip.get("audio") else None for i, clip in enumerate(files["clips"])]
    inputs, sound, label = _audio(job, {**files, "stems": stems}, len(job["beats"]))
    cmd += inputs
    parts += sound
    parts.append(f"{label}{loudnorm(measured)},aresample=48000[aout]")
    return cmd + ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
                  "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(skitcut.FPS),
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                  "-t", f"{job['length']:.3f}", out]


def render(job: dict[str, Any], paths: dict[str, Any], folder: str, font: str, ffmpeg: str = "ffmpeg",
           ffprobe: str = "ffprobe") -> dict[str, Any]:
    """The film into `folder` (cut.mp4, frame-N.jpg): {seconds, beats, frames,
    label, labelled, loudness, captions}. `paths` maps the job's files to
    real paths: {clips: [path|None], plates: [path|None], narration, score,
    sfx: [path]}. FilmError when ffmpeg fails or a clip is too short."""
    if job["length"] > SECONDS:
        raise FilmError(f"a film runs at most {SECONDS} s")
    clips = [{"path": p, **skitcut.probe(ffprobe, p)} if p else None for p in paths["clips"]]
    files = {**paths, "clips": clips}
    try:
        measured = subprocess.run(measure_argv(ffmpeg, job, files), capture_output=True, text=True,
                                  timeout=skitcut.RENDER_SECONDS, check=True)
    except subprocess.CalledProcessError as exc:
        raise FilmError(f"ffmpeg could not measure the sound: {(exc.stderr or '').strip()[-300:]}") from None
    except (subprocess.SubprocessError, OSError) as exc:
        raise FilmError(f"ffmpeg could not measure the sound ({type(exc).__name__})") from None
    loudness = read_loudness(measured.stderr)
    label_file = os.path.join(folder, "label.txt")
    with open(label_file, "w", encoding="utf-8") as fh:
        fh.write(job["label"])
    caption_files = []
    for n, chunk in enumerate(caption_chunks(job["captions"])):
        path = os.path.join(folder, f"caption-{n:03d}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(chunk["text"])
        caption_files.append(path)
    out = os.path.join(folder, "cut.mp4")
    try:
        subprocess.run(argv(ffmpeg, job, files, loudness, label_file, caption_files, font, out),
                       capture_output=True, text=True, timeout=skitcut.RENDER_SECONDS, check=True)
    except subprocess.CalledProcessError as exc:
        raise FilmError(f"ffmpeg could not cut the film: {(exc.stderr or '').strip()[-300:]}") from None
    except (subprocess.SubprocessError, OSError) as exc:
        raise FilmError(f"ffmpeg could not cut the film ({type(exc).__name__})") from None
    seconds = skitcut.probe(ffprobe, out)["seconds"]
    windows = [b["t1"] - b["t0"] for b in job["beats"]]
    names = []
    for n, at in enumerate(skitcut.frame_times(windows, skitcut.MAX_REMAKE_FRAMES)):
        name = f"frame-{n}.jpg"
        try:
            subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{at:.2f}",
                            "-i", out, "-frames:v", "1", "-q:v", "3", os.path.join(folder, name)],
                           capture_output=True, timeout=60, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            raise FilmError(f"a frame could not be taken ({type(exc).__name__})") from None
        names.append(name)
    return {"seconds": round(seconds, 3), "beats": [round(w, 3) for w in windows], "frames": names,
            "label": job["label"], "labelled": True, "loudness": loudness,
            "captions": len(caption_files), "stills": [i for i, c in enumerate(clips) if c is None]}
