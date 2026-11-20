"""The studio's video formats, one module each, behind one interface
(docs/architecture.md, "The studio", "Formats").

A `Format` is everything bin/lib/studio.py needs to know about a kind of
video: what the writer and the editor are told about it (its prompt blocks),
the checks its pitches and drafts face, the producer that makes it after its
words passed, what the record keeps of it and how much of the run's time it
needs. The studio holds no `if fmt == ...` of its own: it walks `REGISTRY`,
in order, wherever a video format is concerned, and `VIDEO_FORMATS` is
`tuple(REGISTRY)`.

A new format is one module here that builds its `FORMAT`, plus one line in
`REGISTRY`. The order of `REGISTRY` is the order its blocks appear in a
prompt and the order a video slot weighs the formats in."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

Json = dict[str, Any]


@dataclass(frozen=True)
class Format:
    """One video format. The prompt blocks are said word for word where the
    studio says them; the hooks are called with the run's `Room` (from
    bin/lib/studio_room.py) where they need one."""

    #: The format's name: what a pitch's `format` says, and the draft's key
    #: its piece is kept under (`draft[name]`).
    name: str
    #: The pitch turn's paragraph on it, when it is offered, and the line
    #: added in a video slot (STUDIO_SKIT_SLOTS).
    pitch: str
    slot_line: str
    #: The draft turn: one draft's JSON (`{"drafts": [<draft_shape>, ...]}`),
    #: what one draft is, and its own rules after JOIN_RULES on a draft that
    #: joins a story.
    draft_shape: str
    body: str
    rules: str
    #: The compare turn's clause on what its key in a shown draft is.
    judge_line: str
    #: What the editor is told wherever it meets one (the comparisons and the
    #: slot question), what the slot question adds, and the slot question's
    #: opening when a video slot asks it first.
    editor: str
    final: str
    opening: str
    #: Why a kept pitch in this format is dropped ("" when it is not):
    #: (pitch, sources) with pitch {item, hook, unhooked} as validate_pitches
    #: read them ("" for none) and sources the ids of what this format read
    #: (`sources`; an empty set for a format that reads none).
    check_pitch: Callable[[Json, set[str]], str]
    #: A draft's piece as kept: (raw, pitch, study) -> (the fields it adds to
    #: the draft, {name: piece, ...}, and why it is unusable, "" when it is not).
    shape: Callable[[Any, Json, Json | None], tuple[Json, str]]
    #: The piece's hard lines in code before the tournament: (draft, its
    #: words, the story it joins or None) -> what it crosses.
    screen: Callable[[Json, str, Json | None], list[str]]
    #: What the editor is shown of the piece (`draft[name]`).
    shown: Callable[[Json], Json]
    #: The producer, after the words passed: (room, draft, note) -> the media
    #: ids to post with ([] when only planned), or None when it could not be
    #: made (`note.failed` says why). `note` is the attempt's record.
    make: Callable[[Any, Json, Json], list[str] | None]
    #: What the record keeps of one that went out: (draft, note, post_id).
    outcome: Callable[[Json, Json, str | None], Json]
    #: The seconds it may take from now (room -> seconds), and the least that
    #: must be: under it, no video of this format is started (`video_time`).
    time: Callable[[Any], int]
    min_secs: int
    #: The note's keys that say money was spent on a failed one: then no
    #: other video format is tried in the run (one video a day).
    spent: tuple[str, ...] = ("asset",)
    #: A format made from things it reads itself (a remake's chart videos):
    #: (room, slot id or None) -> (items, the note the record keeps, a
    #: warning when there are none, else ""), or None when it sits this run
    #: out. A format with none read is not offered this run. `sources_name`
    #: is what the record calls them (`<name>_<sources_name>`) and the
    #: keyword they are passed by (`charts=`).
    sources: Callable[[Any, str | None], tuple[list[Json], Json, str] | None] | None = None
    sources_name: str = ""
    #: The pitch turn's section listing them, and why another format's pitch
    #: may not name one as its item.
    sources_part: Callable[[list[Json]], str] | None = None
    foreign_item: Callable[[str], str] | None = None
    #: Whether a pitch's item is one of its sources, told to the writer as the
    #: `prepare` study rather than as Teeny's own item.
    item_is_source: bool = False
    #: Read before the pitch's writer turn (a remake's study of its source):
    #: (room, pitch) -> (the study or None, why it cannot be made or "", what
    #: the record keeps of it). At most `prepare_max` a run, past which a
    #: pitch is dropped with `prepare_full`; one refused hands its place to
    #: the next pitch of this format in the writer's ranking.
    prepare: Callable[[Any, Json], tuple[Json | None, str, Json]] | None = None
    prepare_max: int = 0
    prepare_full: str = ""
    #: The draft turn's section on the study, when there is one.
    study_part: Callable[[Json], str] | None = None


def named(**given: Any) -> dict[str, Any]:
    """What was passed by a format's `sources_name` (`charts=[...]`), by the
    format's name: {"remake": [...]}. Nothing for a None."""
    out: dict[str, Any] = {}
    for fmt in REGISTRY.values():
        if fmt.sources_name and given.get(fmt.sources_name) is not None:
            out[fmt.name] = given[fmt.sources_name]
    return out


from formats import remake, skit  # noqa: E402  (each builds its FORMAT from Format above)

#: The video formats, in order.
REGISTRY: dict[str, Format] = {
    "skit": skit.FORMAT,
    "remake": remake.FORMAT,
}
