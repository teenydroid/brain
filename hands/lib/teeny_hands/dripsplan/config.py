"""Read, validate and plan a Drips funding config. Offline by design.

A config is a mapping:

    recipient: name            # optional, echoed only
    entries:                   # 1..200, in priority order
      - teenydroid/refusal-ledger    # a GitHub repo slug ...
      - 0x0000...fe18              # ... or an ETH address
    weights: [70, 30]          # percent points, same length as entries,
                               # sum 100; 0 is legal (200 entries x min 1
                               # could never sum to 100)
    note: optional text        # echoed only, never sent anywhere

Facts this file does not guess: the Drips npm package name and the
split-weight unit are unverified (no network here), so `emit` outputs
percent points as written and names no SDK call. The sequence is the
shape a signer would run: create the Drip List, set its splits, fund it.
"""
from __future__ import annotations

import json
import re
import sys

MAX_ENTRIES = 200
TOTAL_WEIGHT = 100
_SLUG = re.compile(r"^[\w.-]+/[\w.-]+$")
_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_INT = re.compile(r"^[+-]?\d+$")


class ConfigError(Exception):
    """The config is wrong. `issues` holds one line per problem."""

    def __init__(self, issues):
        self.issues = issues
        super().__init__("; ".join(issues))


def _scalar(raw):
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _value(text):
    """A scalar, or an inline list like [70, 30]. Ints stay ints."""
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_value(part) for part in inner.split(",")]
    scalar = _scalar(text)
    if _INT.match(scalar):
        return int(scalar)
    return scalar


def _strip_comment(text):
    out, quote = [], None
    for ch in text:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _read_yaml(text):
    """The subset of YAML this tool accepts: mappings, lists, scalars.

    Enough for a Drips config, nothing more. Anything else is a clear
    parse error with a line number, not a silent wrong answer.
    """
    doc, current = {}, None
    list_key = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if body.startswith("- "):
            if current is None:
                raise ConfigError([f"line {lineno}: list item before any key"])
            if indent == 0:
                raise ConfigError([f"line {lineno}: list item at top level"])
            current.append(_scalar(body[2:]))
            continue
        if ":" not in body:
            raise ConfigError([f"line {lineno}: expected 'key: value', got: {body!r}"])
        key, _, rest = body.partition(":")
        key, rest = key.strip(), rest.strip()
        if indent == 0:
            if rest:
                doc[key] = _value(rest)
            else:
                doc[key] = []
                current, list_key = doc[key], key
        elif list_key is not None and current is not None:
            raise ConfigError(
                [f"line {lineno}: unexpected nested key under '{list_key}'"])
        else:
            raise ConfigError([f"line {lineno}: key '{key}' must be at column 0"])
    return doc


def read(path):
    """Read a config file. JSON first, then the YAML subset."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as err:
        raise ConfigError([f"cannot read {path}: {err.strerror or err}"]) from err
    return read_text(text, path)


def read_text(text, source="<config>"):
    """Parse JSON config text, or fall back to the YAML subset."""
    try:
        doc = json.loads(text)
    except ValueError:
        try:
            return _read_yaml(text)
        except ConfigError:
            raise
    if not isinstance(doc, dict):
        raise ConfigError([f"{source}: top level must be a mapping, "
                           f"got {type(doc).__name__}"])
    return doc


def _check_entries(entries):
    if not isinstance(entries, list) or not entries:
        return ["entries: must be a non-empty list"]
    issues = []
    if len(entries) > MAX_ENTRIES:
        issues.append(f"entries: {len(entries)} listed, max is {MAX_ENTRIES}")
    seen = set()
    for i, item in enumerate(entries, start=1):
        label = _scalar(item) if isinstance(item, str) else ""
        if not label:
            issues.append(f"entries[{i}]: must be a slug or address, got {item!r}")
        elif label in seen:
            issues.append(f"entries[{i}]: duplicate entry '{label}'")
        elif not (_SLUG.match(label) or _ADDR.match(label)):
            issues.append(
                f"entries[{i}]: '{label}' is not a repo slug (owner/name) "
                f"or a 0x address")
        seen.add(label)
    return issues


def _check_weights(weights, count):
    if not isinstance(weights, list):
        return ["weights: must be a list of percent-point integers"]
    issues = []
    if len(weights) != count:
        issues.append(f"weights: {len(weights)} given, entries has {count}")
        return issues
    cleaned = []
    for i, item in enumerate(weights, start=1):
        if isinstance(item, bool) or not isinstance(item, int):
            issues.append(f"weights[{i}]: must be a whole number of percent "
                          f"points, got {item!r}")
            return issues
        if item < 0:
            issues.append(f"weights[{i}]: must be 0 or more percent points, "
                          f"got {item}")
            return issues
        cleaned.append(item)
    total = sum(cleaned)
    if total != TOTAL_WEIGHT:
        issues.append(f"weights: sum is {total}, must be {TOTAL_WEIGHT} "
                      f"percent points")
    return issues


def validate(doc, source="<config>"):
    """Validate a config document. Returns it normalised, or raises.

    Normalised: recipient (str, optional), entries (list of str),
    weights (list of int), note (str, optional).
    """
    if not isinstance(doc, dict):
        raise ConfigError([f"{source}: top level must be a mapping"])
    issues = []
    entries = doc.get("entries")
    issues.extend(_check_entries(entries))
    count = len(entries) if isinstance(entries, list) else 0
    issues.extend(_check_weights(doc.get("weights"), count))
    for key in doc:
        if key not in ("recipient", "entries", "weights", "note"):
            issues.append(f"{key}: unknown key (allowed: recipient, entries, "
                          f"weights, note)")
    if issues:
        raise ConfigError([f"{source}: {issue}" for issue in issues])
    return {
        "recipient": str(doc.get("recipient", "")),
        "entries": [_scalar(item) for item in entries],
        "weights": list(doc.get("weights")),
        "note": str(doc.get("note", "")),
    }


def emit(config, source="<config>"):
    """The ordered call plan a signer would run, as plain dicts.

    Byte-deterministic: keys sorted, no timestamps, no randomness, no
    ids beyond config content. Steps are the protocol shape only; no
    SDK call names, addresses beyond the config, or amounts are guessed.
    """
    entries = [{"index": i, "to": slug_or_addr, "weight": weight}
               for i, (slug_or_addr, weight)
               in enumerate(zip(config["entries"], config["weights"]), start=1)]
    return {
        "config": source,
        "dry_run": True,
        "note": config["note"],
        "recipient": config["recipient"],
        "steps": [
            {"action": "create_drip_list", "order": 1},
            {"action": "set_splits",
             "order": 2,
             "recipients": entries,
             "total_weight_points": TOTAL_WEIGHT},
            {"action": "fund", "order": 3, "currency": "from signer wallet"},
        ],
    }


def plan(path, source=None):
    """Read and validate a config file, then emit its call plan."""
    doc = read(path)
    return emit(validate(doc, source or path), source or path)


def write_json(value):
    """Deterministic JSON: sorted keys, fixed separators, newline at end."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def main(argv):
    """The CLI: `dripsplan validate <config>` | `dripsplan emit <config>`."""
    if len(argv) != 2 or argv[0] not in ("validate", "emit"):
        print("usage: dripsplan validate <config.yaml|json> | "
              "dripsplan emit <config.yaml|json>", file=sys.stderr)
        return 2
    verb, path = argv
    source = path
    try:
        validated = validate(read(path), source)
    except ConfigError as err:
        for issue in err.issues:
            print(f"error: {issue}", file=sys.stderr)
        return 1
    if verb == "validate":
        print(f"ok: {source}: {len(validated['entries'])} entries, "
              f"weights sum to {sum(validated['weights'])}")
        return 0
    print(f"call plan for {source} (dry run, nothing signed, nothing sent):")
    print(write_json(plan(path, source)), end="")
    return 0
