"""The checker kinds: the only ways a goal's success is ever decided.

A goal names one kind and its parameters. Teeny never supplies code, a
query or a shell command: each kind below is code in this file (frozen,
evals/frozen.txt), and a goal can only fill in its parameters, which
`validate` checks against the kind's fixed shape.

  stats_delta    {source, metric, at_least}     a `stats` row rose by at_least
                                                since the goal was proposed
  pr_merged      {repo, author?}                a pull request by author (default
                                                GITHUB_USER) merged into repo
                                                between proposal and deadline
  build_shipped  {}                             a build job shipped between
                                                proposal and deadline
  wallet_inflow  {at_least_usdc}                the USDC balance rose by at least
                                                that much, payments out added back
  world_delta    {component, at_least}          W's component rose by at_least
                                                (hands/world report; open while
                                                that hand is absent)
  stimuli_count  {kind, surface?, at_least}     at least that many stimuli of that
                                                kind arrived between proposal and
                                                deadline

A kind with a baseline (stats_delta, wallet_inflow, world_delta) records
it once, at proposal when it can be read then, else at the first check,
and measures the rise from there. Data that cannot be read now raises
`Unavailable`: the goal stays open, and fails at its deadline like any goal
that was not shown solved.

Each data read is a module-level function, so the tests stand in for it.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
import subprocess
from typing import Any, Callable, Mapping

from .. import hand

Json = dict[str, Any]

COMPONENTS = ("attention", "tiny", "money", "capability", "shipped", "world")
STATS_SOURCES = ("x", "moltbook", "github")
_METRIC = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_KIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
USDC_MICRO = 1_000_000
MAX_AMOUNT = 1_000_000_000


class Unavailable(Exception):
    """The data a check needs cannot be read now."""


class BadParams(Exception):
    """A check's parameters do not fit its kind."""


# --- data reads (stubbed by the tests) -------------------------------------------------------


def stats_value(source: str, metric: str, at: float | None) -> float | None:
    """The newest `stats` value recorded at or before `at` (None: the newest of
    all); when `at` is given and nothing is that old, the oldest after it."""
    from ..stats import collect

    conn = collect.db()
    try:
        if at is None:
            row = conn.execute("SELECT value FROM stats WHERE source = ? AND metric = ? ORDER BY date DESC LIMIT 1",
                               (source, metric)).fetchone()
        else:
            row = conn.execute("SELECT value FROM stats WHERE source = ? AND metric = ? AND at <= ?"
                               " ORDER BY at DESC LIMIT 1", (source, metric, at)).fetchone()
            if row is None:
                row = conn.execute("SELECT value FROM stats WHERE source = ? AND metric = ? ORDER BY at LIMIT 1",
                                   (source, metric)).fetchone()
    finally:
        conn.close()
    return None if row is None else float(row["value"])


def merged_prs(repo: str, author: str, since: float) -> list[Json]:
    """Pull requests by `author` merged into `repo` since `since`, from
    GitHub's search (hands/gh's client, read only)."""
    from ..gh import reads
    from ..gh.client import GitHubApi

    day = _dt.datetime.fromtimestamp(since, _dt.timezone.utc).strftime("%Y-%m-%d")
    got = GitHubApi.from_settings().search_issues(f"repo:{repo} author:{author} type:pr is:merged merged:>={day}", 50)
    return [reads._pr_item(i) for i in got.get("items") or [] if isinstance(i, dict)]


def github_user() -> str:
    from ..gh.client import user

    return user()


def shipped_builds(since: float, until: float) -> list[Json]:
    from ..build import jobs

    conn = jobs.db()
    try:
        rows = conn.execute("SELECT id, slug, repo_url, pr_url, shipped_at FROM build_jobs WHERE status = 'shipped'"
                            " AND shipped_at >= ? AND shipped_at <= ? ORDER BY shipped_at", (since, until)).fetchall()
    finally:
        conn.close()
    return [{k: r[k] for k in r.keys() if r[k] is not None} for r in rows]


def usdc_balance() -> int:
    """Teeny's USDC balance, raw (6 decimals): one Solana RPC read."""
    from ..wallet import reads

    return int(reads.balance()["usdc"]["raw"])


def usdc_paid_out(since: float) -> int:
    from ..wallet import store

    conn = store.db()
    try:
        row = conn.execute("SELECT COALESCE(SUM(amount_raw), 0) FROM wallet_payments WHERE asset = 'USDC'"
                           " AND status IN ('paid', 'in_doubt') AND created_at >= ?", (since,)).fetchone()
    finally:
        conn.close()
    return int(row[0])


def world_report() -> Json:
    """`hands/world report --days 7` (built beside this hand), or Unavailable
    while that hand is absent or its answer is not a JSON object."""
    exe = hand.root() / "hands" / "world"
    if not exe.is_file():
        raise Unavailable("hands/world is not installed")
    try:
        proc = subprocess.run([str(exe), "report", "--days", "7"], capture_output=True, text=True, timeout=120,
                              cwd=hand.root(), stdin=subprocess.DEVNULL)
        out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, IndexError) as exc:
        raise Unavailable(f"hands/world report: {exc}") from None
    if not isinstance(out, dict) or out.get("ok") is False:
        raise Unavailable("hands/world report did not answer")
    return out


def world_components() -> dict[str, float]:
    """W per component, from the world report. No numbers is Unavailable."""
    found = parse_world(world_report())
    if not found:
        raise Unavailable("hands/world report gave no component values")
    return found


def parse_world(out: Any) -> dict[str, float]:
    """W's components from a world report, whichever of the plain shapes it
    uses: {"W": {c: n}}, {"W": {c: {"value": n}}}, {"components": {...}} or
    {"components": [{"name"|"component": c, "value": n}]}."""
    if not isinstance(out, Mapping):
        return {}
    raw = out.get("W", out.get("w", out.get("components")))
    pairs: list[tuple[Any, Any]] = []
    if isinstance(raw, Mapping):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        pairs = [(e.get("name", e.get("component")), e) for e in raw if isinstance(e, Mapping)]
    found: dict[str, float] = {}
    for name, value in pairs:
        if isinstance(value, Mapping):
            value = value.get("value", value.get("W", value.get("now")))
        if name in COMPONENTS and _finite(value):
            found[name] = float(value)
    return found


def stimuli_seen(kind: str, surface: str | None, since: float, until: float) -> int:
    from .. import stimuli

    conn = stimuli.db()
    try:
        query = "SELECT COUNT(*) FROM stimuli WHERE kind = ? AND at >= ? AND at <= ?"
        params: tuple[Any, ...] = (kind, since, until)
        if surface:
            query += " AND surface = ?"
            params += (surface,)
        return int(conn.execute(query, params).fetchone()[0])
    finally:
        conn.close()


# --- parameter shapes ---------------------------------------------------------------------------


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _amount(params: Mapping[str, Any], key: str, whole: bool = False) -> float:
    value = params.get(key)
    if not _finite(value) or value <= 0 or value > MAX_AMOUNT or (whole and value != int(value)):
        kind = "a whole number" if whole else "a number"
        raise BadParams(f'"{key}" must be {kind} above 0')
    return int(value) if whole else float(value)


def _choice(params: Mapping[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    value = params.get(key)
    if value not in allowed:
        raise BadParams(f'"{key}" is one of {", ".join(allowed)}')
    return value


def _pattern(params: Mapping[str, Any], key: str, pattern: re.Pattern[str], what: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not pattern.match(value):
        raise BadParams(f'"{key}" must be {what}')
    return value


def _stats_params(p: Mapping[str, Any]) -> Json:
    return {"source": _choice(p, "source", STATS_SOURCES),
            "metric": _pattern(p, "metric", _METRIC, "a stats metric name, like followers"),
            "at_least": _amount(p, "at_least")}


def _pr_params(p: Mapping[str, Any]) -> Json:
    out = {"repo": _pattern(p, "repo", _REPO, "OWNER/REPO")}
    if p.get("author") is not None:
        out["author"] = _pattern(p, "author", _LOGIN, "a GitHub login").lstrip("@")
    return out


def _stimuli_params(p: Mapping[str, Any]) -> Json:
    from .. import stimuli

    out: Json = {"kind": _pattern(p, "kind", _KIND, "a stimulus kind, like mention"),
                 "at_least": _amount(p, "at_least", whole=True)}
    if p.get("surface") is not None:
        out["surface"] = _choice(p, "surface", stimuli.SURFACES)
    return out


# --- evaluation ---------------------------------------------------------------------------------

Result = Json  # {"passed": bool, "evidence": {...}}


def _rise(now: float, base: float, at_least: float, **more: Any) -> Result:
    rise = now - base
    return {"passed": rise >= at_least, "evidence": {"now": now, "baseline": base, "rise": rise,
                                                      "at_least": at_least, **more}}


def _stats_baseline(goal: Mapping[str, Any]) -> float:
    p = goal["params"]
    value = stats_value(p["source"], p["metric"], goal["proposed_at"])
    if value is None:
        raise Unavailable(f"no stats row for {p['source']}.{p['metric']} yet")
    return value


def _stats_eval(goal: Mapping[str, Any], base: float) -> Result:
    p = goal["params"]
    value = stats_value(p["source"], p["metric"], None)
    if value is None:
        raise Unavailable(f"no stats row for {p['source']}.{p['metric']}")
    return _rise(value, base, p["at_least"], metric=f"{p['source']}.{p['metric']}")


def _ts(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _pr_eval(goal: Mapping[str, Any], _base: Any) -> Result:
    p = goal["params"]
    author = p.get("author") or github_user()
    try:
        prs = merged_prs(p["repo"], author, goal["proposed_at"])
    except hand.HandError as exc:
        raise Unavailable(f"GitHub search: {exc}") from None
    inside = [pr for pr in prs
              if (at := _ts(pr.get("merged_at"))) is not None and goal["proposed_at"] <= at <= goal["deadline_at"]
              and str(pr.get("repo") or p["repo"]).lower() == p["repo"].lower()]
    return {"passed": bool(inside), "evidence": {"repo": p["repo"], "author": author,
                                                  "merged": [pr.get("html_url") or pr.get("number") for pr in inside][:5]}}


def _build_eval(goal: Mapping[str, Any], _base: Any) -> Result:
    shipped = shipped_builds(goal["proposed_at"], goal["deadline_at"])
    return {"passed": bool(shipped), "evidence": {"shipped": [b.get("pr_url") or b.get("repo_url") or b.get("slug")
                                                               for b in shipped][:5]}}


def _wallet_baseline(_goal: Mapping[str, Any]) -> float:
    try:
        return float(usdc_balance())
    except (hand.HandError, KeyError, TypeError, ValueError) as exc:
        raise Unavailable(f"wallet balance: {exc}") from None


def _wallet_eval(goal: Mapping[str, Any], base: float) -> Result:
    now = _wallet_baseline(goal)
    out = usdc_paid_out(goal.get("baseline_at") or goal["proposed_at"])
    inflow = now - base + out
    want = goal["params"]["at_least_usdc"] * USDC_MICRO
    return {"passed": inflow >= want, "evidence": {"usdc_raw_now": now, "usdc_raw_baseline": base,
                                                    "usdc_raw_paid_out": out, "inflow_usdc": inflow / USDC_MICRO,
                                                    "at_least_usdc": goal["params"]["at_least_usdc"]}}


def _world_baseline(goal: Mapping[str, Any]) -> float:
    component = goal["params"]["component"]
    values = world_components()
    if component not in values:
        raise Unavailable(f"hands/world report has no {component}")
    return values[component]


def _world_eval(goal: Mapping[str, Any], base: float) -> Result:
    return _rise(_world_baseline(goal), base, goal["params"]["at_least"], component=goal["params"]["component"])


def _stimuli_eval(goal: Mapping[str, Any], _base: Any) -> Result:
    p = goal["params"]
    seen = stimuli_seen(p["kind"], p.get("surface"), goal["proposed_at"], goal["deadline_at"])
    return {"passed": seen >= p["at_least"], "evidence": {"seen": seen, "at_least": p["at_least"], "kind": p["kind"],
                                                          **({"surface": p["surface"]} if p.get("surface") else {})}}


class Kind:
    def __init__(self, params: Callable[[Mapping[str, Any]], Json], keys: tuple[str, ...],
                 evaluate: Callable[[Mapping[str, Any], Any], Result],
                 baseline: Callable[[Mapping[str, Any]], float] | None = None, threshold: str | None = None) -> None:
        self.params, self.keys, self.evaluate, self.baseline, self.threshold = params, keys, evaluate, baseline, threshold


KINDS: dict[str, Kind] = {
    "stats_delta": Kind(_stats_params, ("source", "metric", "at_least"), _stats_eval, _stats_baseline, "at_least"),
    "pr_merged": Kind(_pr_params, ("repo", "author"), _pr_eval),
    "build_shipped": Kind(lambda p: {}, (), _build_eval),
    "wallet_inflow": Kind(lambda p: {"at_least_usdc": _amount(p, "at_least_usdc")}, ("at_least_usdc",), _wallet_eval,
                          _wallet_baseline, "at_least_usdc"),
    "world_delta": Kind(lambda p: {"component": _choice(p, "component", COMPONENTS), "at_least": _amount(p, "at_least")},
                        ("component", "at_least"), _world_eval, _world_baseline, "at_least"),
    "stimuli_count": Kind(_stimuli_params, ("kind", "surface", "at_least"), _stimuli_eval, None, "at_least"),
}


def validate(check: Any) -> tuple[str, Json]:
    """A goal's check as (kind, params), or BadParams. An unknown kind or an
    unknown parameter is refused; nothing in a check is ever run."""
    if not isinstance(check, Mapping):
        raise BadParams('"check" is {"kind": ..., "params": {...}}')
    extra = set(check) - {"kind", "params"}
    if extra:
        raise BadParams(f"unknown field(s) in check: {', '.join(sorted(extra))}")
    kind = check.get("kind")
    if kind not in KINDS:
        raise BadParams(f"unknown check kind {kind!r}; the kinds are {', '.join(KINDS)}")
    params = check.get("params", {})
    if not isinstance(params, Mapping):
        raise BadParams('"params" must be an object')
    spec = KINDS[kind]
    unknown = set(params) - set(spec.keys)
    if unknown:
        raise BadParams(f"unknown param(s) for {kind}: {', '.join(sorted(unknown))}")
    return kind, spec.params(params)


def evaluate(goal: Mapping[str, Any], baseline: Any) -> Result:
    return KINDS[goal["check_kind"]].evaluate(goal, baseline)


def baseline(goal: Mapping[str, Any]) -> float | None:
    """The kind's baseline now, or None when the kind has none."""
    spec = KINDS[goal["check_kind"]]
    return None if spec.baseline is None else spec.baseline(goal)
