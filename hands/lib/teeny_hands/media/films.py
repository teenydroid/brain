"""Story films (`hands/film`; docs/video.md §12): a 60-90 s narrated film of
three to eight beats, built audio first, one paid step at a time, and able to
pick up where it stopped.

A film is a row in media_assets (kind `film`) and a folder,
state/media/film/<id>/, whose manifest.json says what is done. `make` takes a
film the studio wrote and the guard passed (guard/film.py; it runs the code
screen again here, which costs nothing), claims its money and runs it; `run`
carries on a film that stopped (a restart, a deploy) from its manifest, and
never buys a step it already has.

The steps, in order:

1. narration: Teeny reads the whole narration once (media/audio.speak), with
   the time of every word; media/narration.clock turns the words into each
   beat's window and each slice of the voice. A beat too long for any clip is
   refused here, before a picture is bought.
2. score: one music bed for the whole film. A failure is no score, never no
   film.
3. effects: one sound per cue, placed in its beat. A failure is no effect.
4. plates: one still per beat. A Teeny beat is drawn from Teeny's sheet; a
   figure beat from each figure's own photo (media/figures.py) as an
   exaggerated caricature, never photo-real, and without Teeny (a real figure
   never meets Teeny, skit rule d). A figure who is not a public figure with
   a photo refuses the film.
5. clips: one image-to-video job a beat, every one started before any is
   waited on, on the subject's own models (FILM_TEENY_MODELS for Teeny,
   FILM_FIGURE_MODELS for a figure: Seedance refuses a real face), at the
   shortest length on sale that covers the beat. A beat whose clip cannot be
   had plays its still: the film still ships. One that stopped mid-render is
   polled again by its job, never bought twice.
6. cut: the reel service's film cutter (deploy/reel/filmcut.py): the clips
   with no sound of their own, the narration placed untouched, the score
   ducked under it, the effects on cue, captions and the label burned in.

Money: a film reserves FILM_MAX_USD when it is claimed, and every paid step
checks what is left of it first. Films are paid from FILM_MAX_USD_PER_WEEK,
never from MEDIA_MAX_USD_PER_DAY (assets.FILM_OWNED keeps every row a film
owns out of the daily sum), so the daily skit is never starved. Every
purchase is a row with its real cost, its `source_id` the film's.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import time
from pathlib import Path
from typing import Any, Callable

from .. import hand
from ..guard import film as rules
from . import assets, audio, narration
from . import shots as _shots
from .surplus import Surplus, SurplusError

Json = dict[str, Any]

WEEK = 7 * 86400
TEENY_MODELS = "venice-seedance-2-5-image-to-video-basic,seedance-2-0-mini-image-to-video"
FIGURE_MODELS = "venice-wan-3-0-image-to-video,venice-kling-v4-image-to-video"
PLATE_MODELS = "gpt-image-2-edit,nano-banana-pro-edit"
#: What a film's score is told beyond the writer's own line.
SCORE_TAIL = "comic, cinematic, builds to a big finish"
#: Each sound effect's length, and how far apart a beat's effects fall.
SFX_SECONDS = 2.0
SFX_STEP = 1.0
PLATE_MAX_BYTES = 20 * 1024 * 1024
STEPS = ("narration", "score", "effects", "plates", "clips", "cut")


def _usd(name: str, default: str) -> int:
    return assets._usd(name, default)


def _models(name: str, default: str) -> list[str]:
    return [m.strip() for m in (hand.setting(name) or default).split(",") if m.strip()]


def folder(film_id: int) -> Path:
    path = assets._media_dir() / "film" / f"f{film_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _service(path: Path) -> str:
    """A film's file as the reel service names it: media/film/f<id>/<name>."""
    return str(path.relative_to(hand.state_dir()))


def load(film_id: int) -> Json:
    path = folder(film_id) / "manifest.json"
    if not path.is_file():
        raise hand.HandError(f"film {film_id} has no manifest")
    return json.loads(path.read_text())


def save(manifest: Json) -> None:
    path = folder(manifest["id"]) / "manifest.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str))
    tmp.replace(path)


# --- money -------------------------------------------------------------------------------


def _week_spent(conn) -> int:
    """What films took this week, reservations included: every film row and
    every row a film bought that did not fail."""
    row = conn.execute(
        f"SELECT COALESCE(SUM(cost_micro), 0) AS spent FROM media_assets WHERE status != 'failed' AND created_at >= ?"
        f" AND {assets.FILM_OWNED}", (hand.now() - WEEK,)).fetchone()
    return int(row["spent"])


def _spent(conn, film_id: int) -> int:
    """What this film's purchases cost so far, reserved ceilings included."""
    row = conn.execute("SELECT COALESCE(SUM(cost_micro), 0) AS spent FROM media_assets WHERE source_id = ?"
                       " AND status != 'failed'", (film_id,)).fetchone()
    return int(row["spent"])


def _left(conn, film_id: int) -> int:
    return _usd("FILM_MAX_USD", "15.00") - _spent(conn, film_id)


def _claim(film: Json, story: Any) -> int:
    """The caps, then the film's row carrying its whole reservation, under
    the media lock, so the next film's weekly check sees the money."""
    from .. import runway

    reserve = _usd("FILM_MAX_USD", "15.00")
    with hand.lock("media"):
        conn = assets._db()
        try:
            lim = runway.limits()
            if not lim["media"]:
                raise hand.Refused(f"no films on the {lim['tier']} runway tier ({lim['why']})", rule="runway",
                                   tier=lim["tier"])
            week = _usd("FILM_MAX_USD_PER_WEEK", "30.00")
            spent = _week_spent(conn)
            if spent + reserve > week:
                raise hand.Refused(f"a film's ${reserve / 1e6:.2f} would take this week's film spend past "
                                   f"${week / 1e6:.2f} (FILM_MAX_USD_PER_WEEK)", rule="budget",
                                   spent_usd=round(spent / 1e6, 4), budget_usd=week / 1e6)
            detail = {"story": (story or {}).get("id") if isinstance(story, dict) else None,
                      "figures": film["figures"], "logline": film["logline"], "aspect": film["aspect"]}
            with hand.transaction(conn):
                return conn.execute(
                    "INSERT INTO media_assets (kind, status, prompt, purpose, cost_micro, estimated, detail, created_at)"
                    " VALUES ('film', 'pending', ?, 'post', ?, 1, ?, ?)",
                    (film["logline"], reserve, json.dumps(detail, ensure_ascii=False, sort_keys=True), hand.now()),
                ).lastrowid
        finally:
            conn.close()


def _row(conn, film_id: int, kind: str, prompt: str, reserve: int, purpose: str = "post") -> int:
    """A purchase's row, carrying its ceiling until its real cost comes;
    refused when the film has not that much left."""
    with hand.lock("media"):
        left = _left(conn, film_id)
        if reserve > left:
            raise hand.Refused(f"the film has ${left / 1e6:.2f} left, and this could cost ${reserve / 1e6:.2f}",
                               rule="budget")
        with hand.transaction(conn):
            return conn.execute(
                "INSERT INTO media_assets (kind, status, prompt, source_id, purpose, cost_micro, estimated, created_at)"
                " VALUES (?, 'pending', ?, ?, ?, ?, 1, ?)",
                (kind, prompt[:4000], film_id, purpose, reserve, hand.now()),
            ).lastrowid


# --- the steps ----------------------------------------------------------------------------


def _audio_step(conn, manifest: Json, kind: str, prompt: str, reserve: int, buy: Callable[[], Json],
                name: str) -> Json:
    """One bought sound, written into the film's folder: {file, cost} or
    {why} when it could not be had."""
    row = _row(conn, manifest["id"], "audio", f"{kind}: {prompt}", reserve)
    try:
        got = buy()
    except (hand.HandError, hand.Refused, SurplusError) as exc:
        cost = (getattr(exc, "detail", None) or {}).get("cost_micro") or 0
        assets._finish(conn, row, status="unknown" if cost else "failed", cost_micro=cost, error=str(exc)[:1000])
        return {"why": str(getattr(exc, "reason", None) or exc)[:300]}
    path = folder(manifest["id"]) / name
    path.write_bytes(got["audio"])
    cost = got.get("cost_micro")
    assets._finish(conn, row, status="done", path=_service(path), cost_micro=cost if cost is not None else reserve,
                   estimated=0 if cost is not None else 1, model=got.get("model"))
    return {"file": name, "cost": cost, **({"words": got["words"]} if "words" in got else {})}


def narrate(conn, manifest: Json, api: Surplus) -> None:
    film = manifest["film"]
    text = narration.joined(film["beats"])
    reserve = _usd("FILM_MAX_USD_NARRATION", "0.50")
    got = _audio_step(conn, manifest, "narration", text, reserve, lambda: audio.speak(text, api=api), "narration.mp3")
    if "why" in got:
        raise hand.HandError(f"the narration was not made: {got['why']}")
    words = got.pop("words")
    if not words:
        heard = audio.transcribe((folder(manifest["id"]) / "narration.mp3").read_bytes(), api=api)
        words = heard["words"]
    manifest["narration"] = {**got, "text": text, "words": words}
    manifest["clock"] = narration.clock(film["beats"], words)


def score(conn, manifest: Json, api: Surplus) -> None:
    film = manifest["film"]
    seconds = max(audio.MUSIC_SECONDS[0], math.ceil(manifest["clock"]["length"]))
    line = f"{film.get('score') or 'a pompous orchestral march'}, {SCORE_TAIL}"
    manifest["score"] = _audio_step(conn, manifest, "score", line, _usd("FILM_MAX_USD_SCORE", "1.00"),
                                    lambda: audio.score(line, seconds, api=api), "score.mp3")


def effects(conn, manifest: Json, api: Surplus) -> None:
    placed = []
    windows = {b["id"]: b for b in manifest["clock"]["beats"]}
    for beat in manifest["film"]["beats"]:
        window = windows[beat["id"]]
        for k, cue in enumerate(beat.get("sfx") or []):
            at = window["t0"] + min((window["t1"] - window["t0"]) / 2 + k * SFX_STEP, window["t1"] - window["t0"])
            got = _audio_step(conn, manifest, "effect", cue, _usd("FILM_MAX_USD_EFFECT", "0.10"),
                              lambda cue=cue: audio.sound(cue, SFX_SECONDS, api=api), f"sfx-{beat['id']}-{k}.mp3")
            placed.append({**got, "beat": beat["id"], "at": round(at, 3)})
    manifest["effects"] = placed


def plate_prompt(film: Json, beat: Json) -> str:
    """What a beat's still is drawn from: the film's look and set, and the
    beat's moment, as the first frame of its shot, drawn so nobody could take
    it for a photograph."""
    looks = " ".join(f"{name}: {line}." for name, line in (film.get("look") or {}).items()) \
        if beat["subject"] == "figure" else ""
    return (f"A {film['aspect']} cinematic film still, the first frame of one shot of a comedy film. {looks} "
            f"Location: {film.get('setting') or 'an impossible, lavish set'}. The moment: {beat['action']} "
            "Obviously impossible and exaggerated, bold editorial caricature, never photo-real. No text, letters, "
            "captions, signs or watermarks anywhere.").replace("  ", " ")


def _plate_body(model: str, film: Json, beat: Json, portraits: list[Json]) -> Json:
    prompt = plate_prompt(film, beat)
    if beat["subject"] == "teeny":
        reference = assets._data_uri(hand.root() / (hand.setting("MEDIA_REFERENCE") or assets.REFERENCE))
        return assets._edit_body(model, prompt, reference, [])
    who = "; ".join(f"photo {i + 1} is {p['name']}" for i, p in enumerate(portraits))
    return {"model": model, "response_format": "b64_json",
            "prompt": f"{who}. Draw each of them from their photo as an exaggerated editorial caricature, "
                      f"unmistakably that person. {prompt}",
            "images": [assets._data_uri(p["path"]) for p in portraits]}


def plates(conn, manifest: Json, api: Surplus) -> None:
    film = manifest["film"]
    portraits = assets._portraits(film["figures"]) if any(b["subject"] == "figure" for b in film["beats"]) else []
    made = manifest.setdefault("plates", {})
    reserve = _usd("FILM_MAX_USD_PLATE", "0.30")
    for beat in film["beats"]:
        if beat["id"] in made:
            continue
        row = _row(conn, manifest["id"], "image", plate_prompt(film, beat), reserve, purpose="video_frame")
        tried = []
        for model in _models("FILM_PLATE_MODELS", PLATE_MODELS):
            try:
                answer = api.call("POST", "/v1/images/edits", _plate_body(model, film, beat, portraits), timeout=240)
            except SurplusError as exc:
                tried.append(f"{model}: {exc}")
                if exc.status in assets.FALLBACK_STATUSES or exc.status is None or exc.status in (400, 403, 422):
                    continue
                assets._finish(conn, row, status="failed", cost_micro=0, error="; ".join(tried)[:1000])
                raise hand.HandError(f"{beat['id']}'s still was not made: {exc}") from None
            item = (answer.get("data") or [{}])[0] if isinstance(answer, dict) else {}
            data = base64.b64decode(item["b64_json"]) if item.get("b64_json") else (
                api.download(item["url"], PLATE_MAX_BYTES) if item.get("url") else b"")
            ext = assets._extension(data)
            if ext not in ("png", "jpg", "webp"):
                tried.append(f"{model}: no picture in the answer")
                continue
            path = folder(manifest["id"]) / f"{beat['id']}.{ext}"
            path.write_bytes(data)
            assets._finish(conn, row, status="done", model=model, path=_service(path),
                           cost_micro=assets._image_estimate(api, model) or reserve, estimated=1)
            made[beat["id"]] = {"file": path.name, "model": model}
            save(manifest)
            break
        else:
            assets._finish(conn, row, status="failed", cost_micro=0, error="; ".join(tried)[:1000])
            raise hand.HandError(f"{beat['id']}'s still was not made: " + "; ".join(tried)[:500])


def _plan(windows: dict[str, Json], per_clip: int) -> _shots.Plan:
    return _shots.Plan(prefix="film", key="teeny-film", style=None, params="FILM_PARAMS",
                       body=lambda model, prompt, seconds, aspect, params, shot: _shots.request_body(
                           model, prompt, seconds, aspect, params, image=shot.image,
                           resolution_setting="FILM_RESOLUTION"),
                       length=lambda shot, sold: narration.buy(windows[shot.name], sold), per_shot=per_clip)


def clips(conn, manifest: Json, api: Surplus, sleep=time.sleep, clock=time.monotonic) -> None:
    """Every beat's clip started, then all polled together within
    FILM_WAIT_SECONDS. A beat that cannot be had keeps its still."""
    film = manifest["film"]
    windows = {b["id"]: b for b in manifest["clock"]["beats"]}
    made = manifest.setdefault("clips", {})
    per_clip = min(_usd("FILM_MAX_USD_PER_CLIP", "5.00"), max(0, _left(conn, manifest["id"])))
    plan = _plan(windows, per_clip)
    job_params = _shots.params("FILM_PARAMS")
    sold = _shots.lengths(api)
    wait = hand.number("FILM_WAIT_SECONDS", 1800, 300, 5400)
    deadline = clock() + wait
    going: list[_shots._Shot] = []
    for n, (beat, window) in enumerate(zip(film["beats"], manifest["clock"]["beats"]), 1):
        kept = made.get(beat["id"]) or {}
        if kept.get("file") or kept.get("still"):
            continue
        models = _models("FILM_TEENY_MODELS" if beat["subject"] == "teeny" else "FILM_FIGURE_MODELS",
                         TEENY_MODELS if beat["subject"] == "teeny" else FIGURE_MODELS)
        plate = folder(manifest["id"]) / manifest["plates"][beat["id"]]["file"]
        seconds = narration.buy(window, None) or narration.BEAT_MAX
        prompt = rules.beat_prompt(film, n - 1, seconds)
        if kept.get("job") and kept.get("row"):
            shot = _shots._Shot(n, kept["row"], prompt, seconds, [], name=beat["id"], image=assets._data_uri(plate))
            shot.job, shot.model, shot.cost = kept["job"], kept.get("model"), kept.get("reserved", per_clip)
        else:
            row = _row(conn, manifest["id"], "shot", prompt, per_clip)
            shot = _shots._Shot(n, row, prompt, seconds, models, name=beat["id"], image=assets._data_uri(plate),
                                cap=per_clip)
            if not _shots.submit(api, conn, manifest["id"], shot, film["aspect"], plan, job_params, sold):
                shot.failed = "no model could take it: " + "; ".join(shot.tried)
        made[beat["id"]] = {"row": shot.row, "job": shot.job, "model": shot.model, "reserved": shot.cost}
        save(manifest)
        going.append(shot)
    while any(s.job and not s.path and not s.failed for s in going):
        for shot in [s for s in going if s.job and not s.path and not s.failed]:
            try:
                status = api.call("GET", f"/v1/video/generations/{shot.job}", timeout=30)
            except SurplusError:
                continue
            word = str((status or {}).get("status") or "") if isinstance(status, dict) else ""
            if word == "succeeded":
                _shots.land(api, conn, manifest["id"], shot, status, plan, sleep, clock, deadline)
            elif word in ("failed", "canceled", "expired"):
                shot.tried.append(f"{shot.model}: {word}")
                _shots._set(conn, shot.row, cost_micro=0)
                shot.job, shot.cost = None, 0
                if not _shots.submit(api, conn, manifest["id"], shot, film["aspect"], plan, job_params, sold):
                    shot.failed = "no model could make it: " + "; ".join(shot.tried)
            made[shot.name].update(job=shot.job, model=shot.model)
        save(manifest)
        if clock() >= deadline:
            for shot in going:
                if shot.job and not shot.path:
                    _shots._cancel(api, shot.job)
                    shot.failed = f"not done after {wait} s"
            break
        if any(s.job and not s.path and not s.failed for s in going):
            sleep(_shots.POLL_SECONDS)
    for shot in going:
        if shot.path:
            target = folder(manifest["id"]) / f"{shot.name}.mp4"
            (hand.root() / shot.path).replace(target)
            _shots._set(conn, shot.row, path=_service(target))
            made[shot.name] = {"file": target.name, "model": shot.model, "cost": shot.cost}
        else:
            _shots._end_shots(conn, [shot])
            made[shot.name] = {"still": True, "why": (shot.failed or "not made")[:300]}
    save(manifest)


def captions(manifest: Json) -> list[Json]:
    """The narration's words on the film's timeline: each word moved by its
    beat's slice, from where it was read to where it is played."""
    out = []
    for word in manifest["narration"]["words"]:
        piece = next((p for p in manifest["clock"]["pieces"] if p["from"] <= word["start"] < p["to"]), None)
        if piece is None:
            continue
        shift = piece["at"] - piece["from"]
        text = "".join(ch for ch in word["text"] if ch.isprintable())[:40]
        if text:
            out.append({"text": text, "start": round(word["start"] + shift, 3), "end": round(word["end"] + shift, 3)})
    return out


def cut_job(manifest: Json, the_label: str) -> Json:
    base = f"media/film/f{manifest['id']}"
    beats = []
    for window in manifest["clock"]["beats"]:
        kept = manifest["clips"].get(window["id"]) or {}
        if kept.get("file"):
            beats.append({"clip": f"{base}/{kept['file']}", "t0": window["t0"], "t1": window["t1"]})
        else:
            beats.append({"plate": f"{base}/{manifest['plates'][window['id']]['file']}",
                          "t0": window["t0"], "t1": window["t1"]})
    job: Json = {"beats": beats, "narration": f"{base}/{manifest['narration']['file']}",
                 "pieces": [{k: p[k] for k in ("from", "to", "at")} for p in manifest["clock"]["pieces"]],
                 "sfx": [{"file": f"{base}/{e['file']}", "at": e["at"]} for e in manifest.get("effects") or []
                         if e.get("file")],
                 "captions": captions(manifest), "label": the_label, "aspect": manifest["film"]["aspect"],
                 "score_volume_pct": hand.number("FILM_SCORE_VOLUME_PCT", 25, 1, 80)}
    if (manifest.get("score") or {}).get("file"):
        job["score"] = f"{base}/{manifest['score']['file']}"
    return job


def cut(conn, manifest: Json, reel: Any) -> None:
    from . import skits

    the_label = skits.label()
    cuts: list[str] = []
    try:
        made, info, path, frames = _shots.keep_cut(reel, cut_job(manifest, the_label), f"film-{manifest['id']}",
                                                   the_label, hand.number("FILM_CUT_SECONDS", 600, 120, 1200), cuts,
                                                   make=reel.film)
    finally:
        _shots.drop_cuts(reel, cuts)
    manifest["cut"] = {"path": str(path.relative_to(hand.root())), "frames": frames, "label": the_label,
                       "seconds": info.get("seconds") or made.get("seconds"), "stills": made.get("stills"),
                       "loudness": made.get("loudness")}


# --- the job -------------------------------------------------------------------------------


def make(body: Any, api: Surplus | None = None, reel: Any = None, sleep=time.sleep, clock=time.monotonic) -> Json:
    """A film the studio wrote and the guard passed, claimed and run:
    stdin {"film", "story"}."""
    if not isinstance(body, dict) or set(body) - {"film", "story"}:
        raise hand.Refused("a film job is {film, story}", rule="body")
    film, bad = rules.shape(body.get("film"))
    if bad:
        raise hand.Refused(bad, rule="film")
    reasons = rules.screen(body.get("film"), body.get("story"))
    if reasons:
        raise hand.Refused("; ".join(reasons), rule="screen")
    if hand.dry_run():
        return {"dry_run": True, "would_make": "film", "beats": len(film["beats"]),
                "narration": narration.joined(film["beats"]),
                "budget_usd": _usd("FILM_MAX_USD", "15.00") / 1e6}
    film_id = _claim(film, body.get("story"))
    save({"id": film_id, "film": film, "story": body.get("story"), "done": [], "status": "claimed",
          "started_at": hand.now()})
    return run(film_id, api, reel, sleep, clock)


def run(film_id: int, api: Surplus | None = None, reel: Any = None, sleep=time.sleep, clock=time.monotonic) -> Json:
    """The film's next steps, from its manifest, to its cut. A step it has
    is never bought again."""
    from ..reel.service import ReelService

    api = api if api is not None else Surplus()
    reel = reel if reel is not None else ReelService()
    manifest = load(film_id)
    if manifest.get("status") in ("done", "failed"):
        return status(film_id)
    conn = assets._db()
    steps = {"narration": lambda: narrate(conn, manifest, api), "score": lambda: score(conn, manifest, api),
             "effects": lambda: effects(conn, manifest, api), "plates": lambda: plates(conn, manifest, api),
             "clips": lambda: clips(conn, manifest, api, sleep, clock), "cut": lambda: cut(conn, manifest, reel)}
    try:
        for step in STEPS:
            if step in manifest["done"]:
                continue
            manifest["status"] = step
            save(manifest)
            steps[step]()
            manifest["done"].append(step)
            save(manifest)
        manifest["status"] = "done"
        spent = _spent(conn, film_id)
        assets._finish(conn, film_id, status="done", path=manifest["cut"]["path"], cost_micro=0, estimated=0,
                       detail=json.dumps({"spent_usd": round(spent / 1e6, 6), "frames": manifest["cut"]["frames"],
                                          "stills": manifest["cut"].get("stills"),
                                          "seconds": manifest["cut"].get("seconds")}, sort_keys=True))
        manifest["spent_usd"] = round(spent / 1e6, 6)
        save(manifest)
        return status(film_id)
    except hand.Refused as exc:
        _fail(conn, manifest, str(exc.reason))
        raise
    except Exception as exc:  # noqa: BLE001 - recorded, then raised for the caller
        manifest["error"] = f"{type(exc).__name__}: {exc}"[:500]
        save(manifest)
        raise
    finally:
        conn.close()


def _fail(conn, manifest: Json, why: str) -> None:
    """A film that cannot go on: its reservation let go, what it bought kept
    at its cost."""
    manifest["status"], manifest["error"] = "failed", why[:500]
    save(manifest)
    assets._finish(conn, manifest["id"], status="failed", cost_micro=0, error=why[:1000])


def status(film_id: int) -> Json:
    manifest = load(film_id)
    out = {k: manifest.get(k) for k in ("id", "status", "done", "error", "spent_usd")}
    if manifest.get("cut"):
        out["cut"] = manifest["cut"]
    if manifest.get("clips"):
        out["stills"] = [k for k, v in manifest["clips"].items() if v.get("still")]
    return out


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hands/film", description="Teeny's story films (docs/video.md §12).")
    verbs = p.add_subparsers(dest="verb")
    verbs.add_parser("make", help="claim and make a film: stdin {film, story}").set_defaults(
        handler=lambda a, body: make(body), takes_body=True)
    for name, handler, what in (("run", lambda a, _: run(a.id), "carry on a film from its manifest"),
                                ("status", lambda a, _: status(a.id), "where a film is")):
        verb = verbs.add_parser(name, help=what)
        verb.add_argument("id", type=int)
        verb.set_defaults(handler=handler, takes_body=False)
    return p


def main(argv: list[str]) -> int:
    return hand.run("film", parser(), argv)
