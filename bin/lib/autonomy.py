"""The autonomy ladder (docs/governance.md; docs/roadmap.md, G2 and G8).

    python3 bin/lib/autonomy.py check  [--root DIR]   the doctor's check: is any switch set above its level?
    python3 bin/lib/autonomy.py report [--root DIR]   each capability's level and what the next one needs
    python3 bin/lib/autonomy.py models [--root DIR]   record the model behind each role; a change drops levels (G8)

`evals/autonomy.toml` grants each capability (post, reply, mail, browse,
build, spend, self-edit, merge, servers) a level from L1 to L5, lists the
settings that switch it on with the level each needs, the evidence that
earns the next level (proof ids `bin/proofs` counts, and evidence a person
reads), and the events that drop it a step: a promotion, a model change, a
guard escape. The file is frozen, so Teeny can read its levels and never
raise one.

The level that counts is the **effective** one: the granted level, one step
lower when a drop event happened after the day the operator last confirmed it
(`confirmed`). A switch that is on while the granted level is under what it
needs is the operator's own misconfiguration and **fails** the doctor: "merge"
at L4 refuses `EVOLVE_AUTO_PROMOTE=1`. A switch that only a drop puts under
its level **warns**, and says what to rerun: a promotion restarts the spine,
and a fallback model (roadmap A18) changes the model at boot, so failing the
boot there would take Teeny down at the moment it is being watched. The
operator confirms again (a new `confirmed` date, in a pull request) once the
fence tests have passed on the new model.

Events are read from:

- `promotion`: `state/evolve/archive.jsonl`, any generation with a
  `promoted_at` after `confirmed`;
- `model-change`: `secrets/autonomy/models.jsonl`, written by `models` (run
  by `bin/doctor` at every boot), any change to a role the capability runs on
  after `confirmed`. It lives under `secrets/`, which no agent's file tools
  reach, so Teeny can't erase a drop;
- `guard-escape`: no ledger counts one yet (the weekly audit, G6); the
  operator lowers the level in a pull request.

Settings are read from the files in `secrets/` only, never the process
environment (hands/tests/test_watchers.py). Nothing here calls a model, a
hand or the network; `models` is the only verb that writes, and only under
`secrets/autonomy/`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, Iterable, Mapping

HERE = Path(__file__).resolve().parent

LEVELS = {1: "operator", 2: "collaborator", 3: "consultant", 4: "approver", 5: "observer"}
CAPABILITIES = ("post", "reply", "mail", "browse", "build", "spend", "self-edit", "merge", "servers")
EVENTS = ("promotion", "model-change", "guard-escape")
WHEN = ("truthy", "zero", "positive")
SECRET_FILES = ("teeny.env", "evolve.env", "spine.env")
#: What a role's model comes from when the role has a setting of its own
#: (file, setting); every other role runs on SPINE_MODEL.
ROLE_MODEL = {"judge": ("evolve.env", "EVOLVE_JUDGE_MODEL"), "guard": ("teeny.env", "SAFETY_JUDGE_MODEL"),
              "reviewer": ("teeny.env", "GUARD_REVIEW_MODEL")}

Json = dict[str, Any]


class LadderError(Exception):
    pass


# --- reading -------------------------------------------------------------------------------


def parse_env(text: str) -> dict[str, str]:
    """KEY=VALUE lines, as the hands read secrets/teeny.env (hand._parse_env_file)."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def read_secrets(secrets: Path) -> dict[str, dict[str, str]]:
    """Each env file under secrets/ this reads; a missing file is empty."""
    out: dict[str, dict[str, str]] = {}
    for name in SECRET_FILES:
        try:
            out[name] = parse_env((secrets / name).read_text(encoding="utf-8"))
        except OSError:
            out[name] = {}
    return out


def _date(text: Any, where: str) -> dt.date:
    if isinstance(text, dt.date):
        return text
    try:
        return dt.date.fromisoformat(str(text))
    except ValueError:
        raise LadderError(f"{where}: confirmed must be a date (YYYY-MM-DD), not {text!r}") from None


def load(evals: Path, known_proofs: Iterable[str] | None = None) -> dict[str, Json]:
    """evals/autonomy.toml, checked: every capability, levels 1 to 5, each
    switch well formed, `next` one level up, and every proof id known."""
    path = evals / "autonomy.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise LadderError(f"no {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise LadderError(f"{path}: {exc}") from None
    caps = raw.get("capability")
    if not isinstance(caps, dict):
        raise LadderError("autonomy.toml has no [capability.*] tables")
    missing = [c for c in CAPABILITIES if c not in caps]
    if missing:
        raise LadderError(f"autonomy.toml lacks {', '.join(missing)}")
    proofs = set(known_proofs) if known_proofs is not None else None
    out: dict[str, Json] = {}
    for name, cap in caps.items():
        where = f"capability.{name}"
        if name not in CAPABILITIES:
            raise LadderError(f"{where}: not a capability ({', '.join(CAPABILITIES)})")
        level = cap.get("level")
        if level not in LEVELS:
            raise LadderError(f"{where}: level must be 1 to 5, not {level!r}")
        drops = cap.get("drops_on", [])
        bad = [d for d in drops if d not in EVENTS]
        if bad:
            raise LadderError(f"{where}: unknown drop event {bad[0]!r}")
        switches = []
        for s in cap.get("switch", []):
            if s.get("file") not in SECRET_FILES or not s.get("name") or s.get("when") not in WHEN \
                    or s.get("needs") not in LEVELS:
                raise LadderError(f"{where}: a switch needs file ({'/'.join(SECRET_FILES)}), name, when "
                                  f"({'/'.join(WHEN)}) and needs (1 to 5): {s!r}")
            switches.append(dict(s))
        nxt = dict(cap.get("next") or {})
        if level < 5:
            if nxt.get("level") != level + 1:
                raise LadderError(f"{where}: next.level must be {level + 1}")
            for p in nxt.get("proofs", []):
                if not p.get("id"):
                    raise LadderError(f"{where}: a proof needs an id")
                if proofs is not None and p["id"] not in proofs:
                    raise LadderError(f"{where}: bin/proofs counts no {p['id']}")
            if not nxt.get("proofs") and not nxt.get("also"):
                raise LadderError(f"{where}: next names no evidence")
        out[name] = {"name": name, "what": cap.get("what", ""), "level": level,
                     "confirmed": _date(cap.get("confirmed"), where), "roles": list(cap.get("roles", [])),
                     "drops_on": list(drops), "switch": switches, "next": nxt}
    return out


def is_on(value: str | None, when: str) -> bool:
    if value is None or value.strip() == "":
        return False
    v = value.strip().lower()
    if when == "truthy":
        return v in ("1", "true", "yes", "on")
    if when == "zero":
        return v == "0"
    try:
        return float(v) > 0
    except ValueError:
        # A number that doesn't parse is refused where it is read; here it
        # counts as on, so the ladder never under-counts a switch.
        return True


# --- drop events ---------------------------------------------------------------------------


def _day_of(at: Any) -> dt.date | None:
    if at is None:
        return None
    if isinstance(at, (int, float)):
        return dt.datetime.fromtimestamp(float(at), dt.timezone.utc).date()
    try:
        return dt.datetime.fromisoformat(str(at).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _jsonl(path: Path) -> list[Json]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def promotions(state: Path) -> list[Json]:
    """Promoted generations from bin/evolve's archive (each id's latest line)."""
    merged: dict[str, Json] = {}
    for e in _jsonl(state / "evolve" / "archive.jsonl"):
        if e.get("id") is not None:
            merged[str(e["id"])] = {**merged.get(str(e["id"]), {}), **e}
    return [{"event": "promotion", "at": e["promoted_at"], "detail": f"gen {e['id']} promoted"}
            for e in merged.values() if e.get("promoted_at")]


def model_changes(secrets: Path) -> list[Json]:
    return [{"event": "model-change", "at": e.get("at"), "role": e.get("role"),
             "detail": f"{e.get('role')}: {e.get('from')} -> {e.get('to')}"}
            for e in _jsonl(secrets / "autonomy" / "models.jsonl") if e.get("kind") == "change"]


def drops(cap: Json, state: Path, secrets: Path) -> list[Json]:
    """The drop events since the capability was last confirmed."""
    events: list[Json] = []
    if "promotion" in cap["drops_on"]:
        events += promotions(state)
    if "model-change" in cap["drops_on"]:
        events += [e for e in model_changes(secrets) if e.get("role") in cap["roles"]]
    return [e for e in events if (_day_of(e.get("at")) or dt.date.min) > cap["confirmed"]]


def effective(cap: Json, events: list[Json]) -> int:
    return max(1, cap["level"] - (1 if events else 0))


# --- the doctor's check --------------------------------------------------------------------


def check(root: Path, secrets: Path, state: Path) -> Json:
    """Every switch that is on, against its capability's level. `status` is
    fail (a switch above the granted level), warn (above the effective level
    only, after a drop), or ok."""
    caps = load(root / "evals")
    env = read_secrets(secrets)
    fails: list[str] = []
    warns: list[str] = []
    on = 0
    for cap in caps.values():
        events = drops(cap, state, secrets)
        eff = effective(cap, events)
        for s in cap["switch"]:
            value = env[s["file"]].get(s["name"])
            if not is_on(value, s["when"]):
                continue
            on += 1
            setting = f"{s['name']}={value} (secrets/{s['file']})"
            if cap["level"] < s["needs"]:
                fails.append(f"{setting} needs {cap['name']} at L{s['needs']}; it is at L{cap['level']}")
            elif eff < s["needs"]:
                why = "; ".join(e["detail"] for e in events)
                warns.append(f"{setting} needs {cap['name']} at L{s['needs']}; it dropped to L{eff} ({why}): "
                             f"rerun the fence tests, then confirm it again in evals/autonomy.toml")
    if fails:
        return {"status": "fail", "detail": "; ".join(fails), "fails": fails, "warns": warns}
    if warns:
        return {"status": "warn", "detail": "; ".join(warns), "fails": [], "warns": warns}
    return {"status": "ok", "detail": f"{on} switch(es) on, each within its level", "fails": [], "warns": []}


# --- the report ----------------------------------------------------------------------------


def _proofs_module():
    sys.path.insert(0, str(HERE))
    import proofs  # noqa: PLC0415

    return proofs


def report(root: Path, secrets: Path, state: Path) -> Json:
    """Each capability: its level, any drop, and what the next level still
    needs, counted by bin/proofs where a ledger can count it."""
    proofs = _proofs_module()
    caps = load(root / "evals", [p[0] for p in proofs.PROOFS])
    db = proofs.Ledgers(state / "hands.db")
    items = []
    try:
        for cap in caps.values():
            events = drops(cap, state, secrets)
            nxt = cap["next"]
            needs: list[Json] = []
            for p in nxt.get("proofs", []):
                got = proofs.count(p["id"], db)
                need = max(int(p.get("need", 0)), int(got["need"]))
                needs.append({"proof": p["id"], "has": got["has"], "need": need,
                              "met": got["has"] >= need and got["id"] not in proofs.OUTSIDE,
                              "what": got["proof"]})
            still = [f"{n['proof']} {n['has']}/{n['need']} ({n['what']})" for n in needs if not n["met"]]
            still += list(nxt.get("also", []))
            items.append({
                "capability": cap["name"], "what": cap["what"], "level": cap["level"],
                "role": LEVELS[cap["level"]], "effective": effective(cap, events),
                "confirmed": cap["confirmed"].isoformat(), "drops": events,
                "next": None if cap["level"] == 5 else nxt.get("level"),
                "evidence": needs, "still_needs": still if cap["level"] < 5 else [],
            })
    finally:
        db.close()
    return {"ok": True, "capabilities": items}


def text(out: Mapping[str, Any]) -> str:
    lines = ["autonomy ladder (evals/autonomy.toml):", ""]
    for c in out["capabilities"]:
        level = f"L{c['level']}" + (f" (dropped to L{c['effective']})" if c["effective"] != c["level"] else "")
        lines.append(f"{c['capability']:<10} {level:<22} {c['role']}")
        for d in c["drops"]:
            lines.append(f"{'':<11}dropped: {d['detail']}")
        if c["next"]:
            needs = c["still_needs"] or ["nothing: the evidence is in; the operator reads it and raises the level"]
            lines.append(f"{'':<11}for L{c['next']}: " + "; ".join(needs))
    return "\n".join(lines)


# --- G8: the model behind each role ----------------------------------------------------------


def roles(root: Path) -> list[str]:
    names = sorted(p.stem for p in (root / "brain" / "agents").glob("*.toml"))
    return sorted(set(names) | set(ROLE_MODEL))


def current_models(root: Path, secrets: Path) -> dict[str, str]:
    """The model each role runs on: its own setting, else SPINE_MODEL. Empty
    when no model is configured (a candidate checkout, a test root)."""
    env = read_secrets(secrets)
    spine = env["spine.env"].get("SPINE_MODEL", "")
    if not spine:
        return {}
    out = {}
    for role in roles(root):
        own = ROLE_MODEL.get(role)
        out[role] = (env[own[0]].get(own[1]) if own else None) or spine
    return out


def record_models(root: Path, secrets: Path, at: float | None = None) -> Json:
    """Compares each role's model with the last one recorded and appends any
    change to secrets/autonomy/models.jsonl. The first record is a baseline,
    never a change."""
    models = current_models(root, secrets)
    if not models:
        return {"recorded": False, "changes": [], "detail": "no SPINE_MODEL: nothing to record"}
    folder = secrets / "autonomy"
    log = folder / "models.jsonl"
    last: dict[str, str] = {}
    for e in _jsonl(log):
        if e.get("role"):
            last[e["role"]] = e.get("to") or e.get("model") or ""
    t = dt.datetime.fromtimestamp(at if at is not None else dt.datetime.now(dt.timezone.utc).timestamp(),
                                  dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: list[Json] = []
    changes: list[Json] = []
    for role, model in models.items():
        if role not in last:
            rows.append({"at": t, "kind": "baseline", "role": role, "to": model})
        elif last[role] != model:
            row = {"at": t, "kind": "change", "role": role, "from": last[role], "to": model}
            rows.append(row)
            changes.append(row)
    if rows:
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        with log.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    detail = (", ".join(f"{c['role']}: {c['from']} -> {c['to']}" for c in changes) if changes
              else f"{len(models)} role(s), no change")
    return {"recorded": bool(rows), "changes": changes, "detail": detail}


# --- the command line ------------------------------------------------------------------------


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    root = Path(args.root or os.environ.get("TEENY_ROOT") or HERE.parents[1]).resolve()
    secrets = Path(args.secrets) if args.secrets else root / "secrets"
    state = Path(args.state) if args.state else root / "state"
    return root, secrets, state


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="autonomy.py", description="The autonomy ladder (evals/autonomy.toml).")
    parser.add_argument("verb", choices=("check", "report", "models"))
    parser.add_argument("--root")
    parser.add_argument("--secrets")
    parser.add_argument("--state")
    parser.add_argument("--text", action="store_true")
    args = parser.parse_args(argv)
    root, secrets, state = _paths(args)
    try:
        if args.verb == "check":
            out = check(root, secrets, state)
            print(json.dumps(out))
            return 1 if out["status"] == "fail" else 0
        if args.verb == "models":
            print(json.dumps(record_models(root, secrets)))
            return 0
        out = report(root, secrets, state)
        print(text(out) if args.text else json.dumps(out, ensure_ascii=False))
        return 0
    except LadderError as exc:
        print(json.dumps({"ok": False, "status": "fail", "detail": str(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
