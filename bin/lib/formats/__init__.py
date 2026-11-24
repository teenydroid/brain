"""The studio's video formats, one module each, behind one interface
(docs/architecture.md, "The studio", "Formats").

A `Format` is everything bin/lib/studio.py needs to know about a kind of
video: what the write turn of a video slot is told about it (its mechanics),
the structural check its piece faces, the producer that makes it after the
editor ranked it, what the record keeps of it and how much of the run's time
it needs. The studio holds no `if fmt == ...` of its own: it walks `REGISTRY`,
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
    """One video format. Its words are said in the write turn of a video
    slot, after the cards; the hooks are called with the run's `Room` (from
    bin/lib/studio_room.py) where they need one."""

    #: The format's name: a post's `format`, and the key its piece is kept
    #: under (`post[name]`).
    name: str
    #: The write turn's paragraph on it: what the piece is and how it is
    #: written (the mechanics; the content rules are writer.md's list).
    ask: str
    #: The JSON of its piece, under its name in one post of the answer.
    draft_shape: str
    #: Its mechanics a piece must meet to be made, said after `ask`.
    rules: str
    #: A post's piece as kept: (raw, the card it joins, study) -> (the fields
    #: it adds to the post, {name: piece, ...}, and why it cannot be made, ""
    #: when it can). Structure only: shots, lengths, required fields.
    shape: Callable[[Any, Json, Json | None], tuple[Json, str]]
    #: What the editor is shown of the piece (`post[name]`).
    shown: Callable[[Json], Json]
    #: The producer, after the editor ranked it: (room, post, note) -> the
    #: media ids to post with ([] when only planned), or None when it could
    #: not be made (`note.failed` says why). `note` is the attempt's record.
    make: Callable[[Any, Json, Json], list[str] | None]
    #: What the record keeps of one that went out: (post, note, post_id).
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
    #: Read before the write turn (a remake's study of one of its sources):
    #: (room, source id) -> (the study or None, why it cannot be made or "",
    #: what the record keeps of it). At most `prepare_max` tries a run.
    prepare: Callable[[Any, str], tuple[Json | None, str, Json]] | None = None
    prepare_max: int = 0
    #: The write turn's section on the study, when there is one.
    study_part: Callable[[Json], str] | None = None
    #: Whether the format is offered this run at all (room -> bool), for a
    #: format with a cadence of its own (a film every other day); None is
    #: always.
    offered: Callable[[Any], bool] | None = None


def named(**given: Any) -> dict[str, Any]:
    """What was passed by a format's `sources_name` (`charts=[...]`), by the
    format's name: {"remake": [...]}. Nothing for a None."""
    out: dict[str, Any] = {}
    for fmt in REGISTRY.values():
        if fmt.sources_name and given.get(fmt.sources_name) is not None:
            out[fmt.name] = given[fmt.sources_name]
    return out


from formats import film, remake, skit  # noqa: E402  (each builds its FORMAT from Format above)

#: The video formats, in order.
REGISTRY: dict[str, Format] = {
    "skit": skit.FORMAT,
    "remake": remake.FORMAT,
    "film": film.FORMAT,
}
