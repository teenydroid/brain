"""A story film's clock (docs/video.md §12.5 step 2): Teeny's narration,
read once, decides when every beat starts and ends.

`clock` takes the film's beats in order, each with its narration (or none: a
beat of pure action) and the words `media/audio.speak` timed from the
narration read as one text. It answers where each beat sits on the film's
timeline, and where each piece of the narration goes on it:

- The narration is cut between beats at the middle of the silence between
  one beat's last word and the next one's first, so no word loses its tail.
- A beat of pure action is a silence of its own `hold` seconds, laid between
  the pieces; the narration around it is not stretched or sped.
- Every spoken beat gets a short breath after its piece, and the film's last
  beat a longer hold, for the held still it ends on.

Nothing is stretched anywhere: a clip is bought at the shortest length on
sale that covers its beat (`shots.at_least`) and trimmed, and the voice is
played as it was read. A beat longer than any clip the model sells is
refused here, by name, so the writer splits it before anything is bought.
These are pure functions: no network, no files.
"""

from __future__ import annotations

import re
from typing import Any

from .. import hand
from .shots import at_least

Json = dict[str, Any]

#: The release kept after the narration's very last word; the silence after
#: each spoken beat's piece; and the hold on the film's last beat (the held
#: still it ends on).
RELEASE = 0.2
BREATH = 0.4
TAIL = 1.5
#: A beat of pure action: its default length and the most it may ask.
HOLD = 3.0
HOLD_RANGE = (1.0, 8.0)
#: The most a single beat may run: the longest single take any of the film
#: models sells (Seedance 2.5 and Wan 3 top out at 15 s).
#: How long each sound effect plays. A beat's effects play one after another
#: in the silence after its line, which the clock leaves for them, never over
#: a word (f99: set mid-beat, they landed on the narration).
SFX_SECONDS = 1.2
BEAT_MAX = 15.0
#: How far the first word may be from the start of the audio: TTS answers
#: with a little silence first, and that is the film's opening.
LEAD_MAX = 1.0


#: An ElevenLabs v3 direction in the narration ("[whispers]", "[laughs]"):
#: how a line is read, never a word of it.
TAG = re.compile(r"\[[^\[\]\n]{1,30}\]")


def untagged(text: str) -> str:
    """The narration as it is heard: every [tag] taken out."""
    return " ".join(TAG.sub(" ", text).split())


def tokens(text: Any) -> list[str]:
    """A beat's narration as words, split as `audio.words_of` splits the
    timings: runs of non-whitespace, the [tags] left out."""
    return untagged(text).split() if isinstance(text, str) else []


def joined(beats: list[Json]) -> str:
    """The one text read aloud for the whole film: each spoken beat's
    narration, in order, a paragraph apart."""
    return "\n\n".join(" ".join(tokens(b.get("narration"))) for b in beats if tokens(b.get("narration")))


def _hold(beat: Json) -> float:
    hold = beat.get("hold", HOLD)
    if isinstance(hold, bool) or not isinstance(hold, (int, float)) or not HOLD_RANGE[0] <= hold <= HOLD_RANGE[1]:
        raise hand.Refused(f"beat {beat.get('id')!r}: a beat of action holds {HOLD_RANGE[0]:g} to "
                           f"{HOLD_RANGE[1]:g} seconds", rule="clock")
    return float(hold)


def clock(beats: list[Json], words: list[Json], beat_max: float = BEAT_MAX) -> Json:
    """{beats: [{id, t0, t1}], pieces: [{beat, from, to, at}], length}: every
    beat's window on the film's timeline, and each beat's slice of the
    narration audio (`from`-`to` in the audio) with where it starts in the
    film (`at`). Refused when the words do not match the text or a beat runs
    longer than `beat_max`."""
    if not isinstance(beats, list) or not beats:
        raise hand.Refused("a film has no beats", rule="clock")
    counts = [len(tokens(b.get("narration"))) for b in beats]
    if sum(counts) != len(words):
        raise hand.Refused(f"the narration was read as {len(words)} words, and its text has {sum(counts)}",
                           rule="clock")
    if not any(counts):
        raise hand.Refused("a film has no narration", rule="clock")
    # Each spoken beat's first and last word.
    spans: list[tuple[float, float] | None] = []
    at = 0
    for count in counts:
        if count:
            spans.append((words[at]["start"], words[at + count - 1]["end"]))
            at += count
        else:
            spans.append(None)
    spoken = [i for i, s in enumerate(spans) if s]
    if spans[spoken[0]][0] > LEAD_MAX:
        raise hand.Refused(f"the narration starts {spans[spoken[0]][0]:.1f} s into its audio", rule="clock")
    # Where the audio is cut: halfway through the silence between spoken beats.
    cuts: dict[int, tuple[float, float]] = {}
    for n, i in enumerate(spoken):
        begin = 0.0 if n == 0 else (spans[spoken[n - 1]][1] + spans[i][0]) / 2
        if n < len(spoken) - 1:
            end = (spans[i][1] + spans[spoken[n + 1]][0]) / 2
        else:
            end = spans[i][1] + RELEASE
        cuts[i] = (begin, end)
    out_beats: list[Json] = []
    pieces: list[Json] = []
    t = 0.0
    for i, beat in enumerate(beats):
        name = beat.get("id") or f"b{i + 1}"
        if spans[i] is None:
            length = _hold(beat)
        else:
            begin, end = cuts[i]
            pieces.append({"beat": name, "from": round(begin, 3), "to": round(end, 3), "at": round(t, 3)})
            length = end - begin
        room = len(beat.get("sfx") or []) * SFX_SECONDS if spans[i] else 0.0
        length += max(TAIL, room) if i == len(beats) - 1 else (max(BREATH, room) if spans[i] else 0.0)
        if spans[i] is None:
            length = max(length, len(beat.get("sfx") or []) * SFX_SECONDS)
        if length > beat_max + 1e-9:
            raise hand.Refused(f"beat {name!r} runs {length:.1f} s, over the {beat_max:g} s a clip can be: "
                               "split it or say less", rule="clock", beat=name)
        out_beats.append({"id": name, "t0": round(t, 3), "t1": round(t + length, 3)})
        t += length
    return {"beats": out_beats, "pieces": pieces, "length": round(t, 3)}


def buy(window: Json, sold: list[int] | None) -> int | None:
    """The clip length to buy for a beat's window: the shortest on sale that
    covers it, trimmed afterwards; None when the model sells none so long."""
    return at_least(window["t1"] - window["t0"], sold)
