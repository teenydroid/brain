"""Sound for a story film (docs/video.md §12): Teeny's narration with the
time of every word, a music bed, sound effects, and word times read back from
any audio.

All of it is ElevenLabs on Surplus's Provider Market
(/v1/resources/elevenlabs/..., paid from the same SURPLUS_API_KEY): five
routes are open there, and these use four. Each answers in one call, with
what it cost in a header, so nothing here polls. Only ElevenLabs' default
voices are allowed on that route (a cloned voice belongs to a seller's
account), so Teeny's voice is one of those, named by FILM_VOICE_ID.

The film's narration is the clock every shot is cut to, so `speak` asks for
the timings in the same call (`with-timestamps`) and turns the characters'
times into words. `transcribe` reads word times from audio that came without
them.

These calls spend money. They keep no ledger themselves: the caller
(media/films.py) reserves the film's budget first and records each cost this
returns. A dry run spends nothing and answers a stub.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from .. import hand
from .surplus import Surplus, SurplusError

Json = dict[str, Any]

ROUTE = "/v1/resources/elevenlabs/v1"
#: George, the default voice in Surplus's own example; FILM_VOICE_ID picks
#: another of ElevenLabs' default voices.
VOICE = "JBFqnCBsd6RMkjVDRZzb"
#: v3 reads audio tags ([laughs], [whispers]) and is $0.08 per 1,000
#: characters on Surplus; it takes at most 2,500 characters a call.
VOICE_MODELS = ("eleven_v3", "eleven_multilingual_v2")
TEXT_MAX = {"eleven_v3": 2500, "eleven_multilingual_v2": 6000}
TEXT_MAX_DEFAULT = 2500
STT_MODEL = "scribe_v2"
#: ElevenLabs' sound effects run 0.5 to 22 seconds; its music 10 s to 5 min
#: on Surplus's priced ceiling.
SOUND_SECONDS = (0.5, 22.0)
MUSIC_SECONDS = (10, 300)
PROMPT_MAX = 450
AUDIO_MAX_BYTES = 32 * 1024 * 1024
#: The answers that mean "this model, not you": try the next voice model.
NEXT_MODEL = {400, 404, 409, 422, 503}
_VOICE_ID = re.compile(r"^[A-Za-z0-9]{8,40}$")


def _api(api: Surplus | None) -> Surplus:
    return api if api is not None else Surplus()


def _text(value: Any, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise hand.Refused(f"{what} is empty", rule="body")
    value = value.strip()
    if len(value) > limit:
        raise hand.Refused(f"{what} is over {limit} characters", rule="body", limit=limit)
    return value


def voice_models() -> list[str]:
    named = [m.strip() for m in (hand.setting("FILM_VOICE_MODELS") or "").split(",") if m.strip()]
    return named or list(VOICE_MODELS)


def voice_id() -> str:
    voice = (hand.setting("FILM_VOICE_ID") or VOICE).strip()
    if not _VOICE_ID.match(voice):
        raise hand.Refused("FILM_VOICE_ID is not an ElevenLabs voice id", rule="config")
    return voice


def words_of(alignment: Any) -> list[Json]:
    """ElevenLabs' character alignment as words: [{text, start, end}] in
    seconds, a word being a run of characters with no whitespace in it. An
    alignment of the wrong shape is no words, never a guess."""
    if not isinstance(alignment, dict):
        return []
    chars = alignment.get("characters")
    starts = alignment.get("character_start_times_seconds")
    ends = alignment.get("character_end_times_seconds")
    if not (isinstance(chars, list) and isinstance(starts, list) and isinstance(ends, list)):
        return []
    if not (len(chars) == len(starts) == len(ends)):
        return []
    words: list[Json] = []
    current: Json | None = None
    for char, start, end in zip(chars, starts, ends):
        if not isinstance(char, str) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return []
        if char.isspace() or not char:
            current = None
            continue
        if current is None:
            current = {"text": "", "start": float(start), "end": float(end)}
            words.append(current)
        current["text"] += char
        current["end"] = float(end)
    return words


def speak(text: Any, api: Surplus | None = None) -> Json:
    """Teeny reads `text` aloud: {audio (mp3 bytes), words, model, voice,
    cost_micro}. Each voice model in FILM_VOICE_MODELS is tried in turn when
    one is refused for itself; a refusal for the text is final."""
    models = voice_models()
    limit = min(TEXT_MAX.get(m, TEXT_MAX_DEFAULT) for m in models)
    text = _text(text, "the narration", limit)
    voice = voice_id()
    if hand.dry_run():
        return {"dry_run": True, "audio": b"", "words": [], "model": models[0], "voice": voice, "cost_micro": 0}
    tried: list[str] = []
    for model in models:
        try:
            raw, cost = _api(api).send(f"{ROUTE}/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128",
                                       {"text": text, "model_id": model}, limit=AUDIO_MAX_BYTES * 2)
        except SurplusError as exc:
            tried.append(f"{model}: {exc}")
            if exc.status in NEXT_MODEL:
                continue
            raise hand.HandError("the narration was not made", tried=tried) from None
        try:
            answer = json.loads(raw)
            audio = base64.b64decode(answer["audio_base64"], validate=True)
        except (ValueError, KeyError, TypeError):
            raise hand.HandError("the narration came back unreadable", model=model, cost_micro=cost) from None
        words = words_of(answer.get("alignment")) or words_of(answer.get("normalized_alignment"))
        return {"audio": audio, "words": words, "model": model, "voice": voice, "cost_micro": cost}
    raise hand.HandError("no voice model made the narration", tried=tried)


def transcribe(audio: bytes, kind: str = "audio/mpeg", api: Surplus | None = None) -> Json:
    """Word times read from `audio` (an mp3 or a wav): {words, cost_micro}."""
    if not isinstance(audio, (bytes, bytearray)) or not audio:
        raise hand.Refused("there is no audio to read", rule="body")
    if hand.dry_run():
        return {"dry_run": True, "words": [], "cost_micro": 0}
    name = "take.wav" if kind == "audio/wav" else "take.mp3"
    raw, cost = _api(api).send(f"{ROUTE}/speech-to-text",
                               fields={"model_id": STT_MODEL, "timestamps_granularity": "word"},
                               file=(name, bytes(audio), kind))
    try:
        answer = json.loads(raw)
    except ValueError:
        raise hand.HandError("the transcript came back unreadable", cost_micro=cost) from None
    found = answer.get("words") if isinstance(answer, dict) else None
    words = []
    for word in found if isinstance(found, list) else []:
        if not isinstance(word, dict) or word.get("type", "word") != "word":
            continue
        text, start, end = word.get("text"), word.get("start"), word.get("end")
        if isinstance(text, str) and text.strip() and isinstance(start, (int, float)) and isinstance(end, (int, float)):
            words.append({"text": text.strip(), "start": float(start), "end": float(end)})
    return {"words": words, "cost_micro": cost}


def _seconds(value: Any, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise hand.Refused(f"{what} runs from {low:g} to {high:g} seconds", rule="body")
    return float(value)


def sound(prompt: Any, seconds: Any, api: Surplus | None = None) -> Json:
    """One sound effect: {audio (mp3 bytes), cost_micro}."""
    prompt = _text(prompt, "the sound effect", PROMPT_MAX)
    seconds = _seconds(seconds, *SOUND_SECONDS, "a sound effect")
    if hand.dry_run():
        return {"dry_run": True, "audio": b"", "cost_micro": 0}
    audio, cost = _api(api).send(f"{ROUTE}/sound-generation", {"text": prompt, "duration_seconds": seconds},
                                 limit=AUDIO_MAX_BYTES)
    if not audio:
        raise hand.HandError("the sound effect came back empty", cost_micro=cost)
    return {"audio": audio, "cost_micro": cost}


def score(prompt: Any, seconds: Any, api: Surplus | None = None) -> Json:
    """One music bed, no singing: {audio (mp3 bytes), cost_micro}."""
    prompt = _text(prompt, "the score", PROMPT_MAX)
    seconds = _seconds(seconds, *MUSIC_SECONDS, "the score")
    if hand.dry_run():
        return {"dry_run": True, "audio": b"", "cost_micro": 0}
    audio, cost = _api(api).send(f"{ROUTE}/music",
                                 {"prompt": f"{prompt}. Instrumental, no vocals.",
                                  "music_length_ms": int(seconds * 1000)}, limit=AUDIO_MAX_BYTES)
    if not audio:
        raise hand.HandError("the score came back empty", cost_micro=cost)
    return {"audio": audio, "cost_micro": cost}
