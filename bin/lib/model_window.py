"""Make sure the core knows a model's context window before a turn on it.

    python3 bin/lib/model_window.py ensure MODEL...

Prints `{"added": {model: window}, "unknown": [model], "error": "..."}`
(the last only when the core could not be read or written) and exits 0
either way: a turn is never stopped by this, only helped.

Why. The core never asks a local-openai endpoint for a model's window. A
model with no entry in its `model_registry` is budgeted at the local
provider floor of 4,096 tokens (tinyinference-local `profile.rs`,
`CONSERVATIVE_LOCAL_CONTEXT_FLOOR`), 3,584 of it for input. An agent's
system prompt alone is bigger than that, so the history trim
(tinyagents-harness `ImageAwareMessageTrimMiddleware`, which never drops a
system message) drops the user's message, the request goes out with
system messages only, and the provider answers "HTTP 400 (request_rejected):
messages: at least one message is required". bin/spine registers windows at
boot (bin/lib/model_windows.sh), but only for the models configured then: a
`bin/ask --model M` on any other model (the studio preview of 2026-11-19 on
claude-fable-5.2 and claude-opus-5.5, set for that run only) hit the floor
and failed. `bin/ask --model` runs this first, so every model a turn asks
for has its window, whatever was configured at boot.

The window comes from the endpoint's own listing (LADDER_URL/models, the
`context_length` the ladder and Surplus report), LADDER_URL and
LADDER_API_KEY read from the environment, then secrets/spine.env, as
bin/spine reads them. The registry write replaces the whole array (the
core's `inference_update_model_settings`), so it is read first and written
back with the new entries added, under a lock, never with entries lost.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

Json = dict[str, Any]
#: (method, params) -> the method's result, raising on an error.
Call = Callable[[str, Json], Any]

HERE = Path(__file__).resolve().parent
LISTING_TIMEOUT = 10
CORE_TIMEOUT = 30


class WindowError(RuntimeError):
    """The core could not be read or written."""


def registry_of(config: Any) -> list[Json]:
    """The `model_registry` out of a get_config answer (`{config: {...}}`
    or the config itself)."""
    if isinstance(config, dict) and isinstance(config.get("config"), dict):
        config = config["config"]
    entries = config.get("model_registry") if isinstance(config, dict) else None
    if not isinstance(entries, list):
        raise WindowError("the core's config has no model_registry")
    return [e for e in entries if isinstance(e, dict)]


def known(registry: list[Json]) -> set[str]:
    """The model ids the core has a window for."""
    out = set()
    for entry in registry:
        window = entry.get("context_window")
        if isinstance(entry.get("id"), str) and isinstance(window, int) and window > 0:
            out.add(entry["id"].strip())
    return out


def window_of(listing: Any, model: str) -> int | None:
    """MODEL's window in an OpenAI-style listing (`{data: [{id,
    context_length | context_window, aliases?}]}`), or None."""
    data = listing.get("data") if isinstance(listing, dict) else None
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        names = [item.get("id")] + list(item.get("aliases") or [])
        if model not in names:
            continue
        for key in ("context_length", "context_window"):
            value = item.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
    return None


def merged(registry: list[Json], windows: dict[str, int], provider: str = "local-openai") -> list[Json]:
    """REGISTRY with each of WINDOWS set: an entry for the model is updated
    in place (every other field kept), a new one appended. Nothing dropped."""
    out = [dict(e) for e in registry]
    for model, window in windows.items():
        for entry in out:
            if isinstance(entry.get("id"), str) and entry["id"].strip() == model:
                entry["context_window"] = window
                break
        else:
            out.append({"id": model, "provider": provider, "context_window": window})
    return out


def ensure(models: Iterable[str], call: Call, listing: Callable[[], Any]) -> Json:
    """Gives the core a window for each of MODELS it has none for. The
    listing is fetched only when one is missing; the registry is written
    only when a window was found."""
    wanted: list[str] = []
    for model in models:
        model = (model or "").strip()
        if model and model not in wanted:
            wanted.append(model)
    out: Json = {"added": {}, "unknown": []}
    if not wanted:
        return out
    registry = registry_of(call("get_config", {}))
    missing = [m for m in wanted if m not in known(registry)]
    if not missing:
        return out
    try:
        listed = listing()
    except Exception as exc:  # noqa: BLE001 - an unreachable listing is an unknown window, not a crash
        listed, out["listing_error"] = None, f"{type(exc).__name__}: {exc}"[:200]
    found = {m: w for m in missing if (w := window_of(listed, m))}
    out["unknown"] = [m for m in missing if m not in found]
    if found:
        call("inference_update_model_settings", {"model_registry": merged(registry, found)})
        out["added"] = found
    return out


# --- the real core and endpoint -------------------------------------------------------


def _env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        values[key] = value.strip().strip("\"'")
    return values


def _root(environ: dict[str, str]) -> Path:
    return Path(environ.get("TEENY_ROOT") or HERE.parents[1])


def oh_call(environ: dict[str, str]) -> Call:
    """bin/oh (or TEENY_OH, the tests' stand-in) as a Call."""
    oh = environ.get("TEENY_OH") or str(_root(environ) / "bin" / "oh")

    def call(method: str, params: Json) -> Any:
        try:
            done = subprocess.run([oh, method, json.dumps(params)], capture_output=True, text=True,
                                  timeout=CORE_TIMEOUT, env=environ)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WindowError(f"{method}: {type(exc).__name__}") from exc
        if done.returncode != 0:
            raise WindowError(f"{method}: {done.stderr.strip()[-200:] or f'exit {done.returncode}'}")
        try:
            return json.loads(done.stdout or "null")
        except ValueError as exc:
            raise WindowError(f"{method}: not JSON") from exc

    return call


def endpoint_listing(environ: dict[str, str]) -> Callable[[], Any]:
    """The endpoint's model listing, LADDER_URL/models, with LADDER_API_KEY
    as the bearer when there is one (in a header, never argv)."""

    def fetch() -> Any:
        filed = _env_file(Path(environ.get("TEENY_SECRETS") or _root(environ) / "secrets") / "spine.env")
        url = (environ.get("LADDER_URL") or filed.get("LADDER_URL") or "").strip()
        if not url:
            raise WindowError("no LADDER_URL")
        key = (environ.get("LADDER_API_KEY") or filed.get("LADDER_API_KEY") or "").strip()
        request = urllib.request.Request(f"{url.rstrip('/')}/models",
                                         headers={"authorization": f"Bearer {key}"} if key else {})
        try:
            with urllib.request.urlopen(request, timeout=LISTING_TIMEOUT) as response:
                return json.loads(response.read() or b"{}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise WindowError(f"the endpoint's listing could not be read ({type(exc).__name__})") from exc

    return fetch


def ensure_on_core(models: Iterable[str], environ: dict[str, str]) -> Json:
    """`ensure` against the running core, under state/locks/model-window.lock
    so two turns registering at once cannot write over each other."""
    state = Path(environ.get("TEENY_STATE") or _root(environ) / "state")
    try:
        (state / "locks").mkdir(parents=True, exist_ok=True)
        lock = open(state / "locks" / "model-window.lock", "a")
    except OSError:
        lock = None
    try:
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        return ensure(models, oh_call(environ), endpoint_listing(environ))
    except WindowError as exc:
        return {"added": {}, "unknown": [], "error": str(exc)}
    finally:
        if lock is not None:
            lock.close()


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "ensure":
        print("usage: model_window.py ensure MODEL...", file=sys.stderr)
        return 2
    print(json.dumps(ensure_on_core(argv[1:], dict(os.environ))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
