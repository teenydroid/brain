"""Write Teeny's own tables into the core's config.toml before a boot.

    python3 bin/lib/core_config.py CONFIG_TOML ROOT

The core reads these tables only at boot and the spine is configured over
RPC for everything else (bin/spine), so they are rewritten here every boot,
from the checkout. Each section module in bin/lib/core_sections/ owns some
tables and exposes

    def tables(root: Path) -> dict[str, str]   # "[header]" -> the table's body

A table a module owns is replaced whole (its header and every subtable
under it, `[name.*]` and `[[name.*]]`); everything else in the file, which
the core writes itself, is left as it is. A new concern (the wallet's
`[wallet_policy]`, say) is a new module beside the others, so no two
concerns edit the same file. Modules run in name order, and two modules
claiming one table is an error.

A module may also set single keys inside a table the core owns (the
`[memory]` table, which the core writes itself), with

    def keys(root: Path) -> dict[str, dict[str, str]]  # table -> key -> TOML value

Only those keys change; the rest of the table, and its subtables, stay as
the core left them. Two modules setting one key is an error.

bin/lib/inbound_config.py writes the inbound-source tables the same way and
runs just before this.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path

SECTIONS = Path(__file__).resolve().parent / "core_sections"
_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def _name(header: str) -> str:
    match = _HEADER.match(header)
    if not match:
        raise ValueError(f"not a table header: {header!r}")
    return match.group(1)


def drop(text: str, name: str) -> str:
    """Removes table `name` and every table under it (`name.x`, `[[name.x]]`),
    each from its header line up to the next header."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        match = _HEADER.match(line)
        if match:
            table = match.group(1).strip().strip('"')
            skipping = table == name or table.startswith(name + ".")
        if not skipping:
            out.append(line)
    return "".join(out)


def modules(sections: Path = SECTIONS) -> list[Path]:
    return sorted(p for p in sections.glob("*.py") if not p.name.startswith("_"))


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"teeny_core_section_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect(root: Path, sections: Path = SECTIONS) -> dict[str, str]:
    """Every section module's tables, by header."""
    owned: dict[str, str] = {}
    owner: dict[str, str] = {}
    for path in modules(sections):
        module = _load(path)
        for header, body in module.tables(root).items():
            name = _name(header)
            if name in owner:
                raise ValueError(f"[{name}] is claimed by both {owner[name]} and {path.name}")
            owner[name] = path.name
            owned[header] = body
    return owned


def collect_keys(root: Path, sections: Path = SECTIONS) -> dict[tuple[str, str], str]:
    """Every section module's single keys, by (table, key)."""
    keys: dict[tuple[str, str], str] = {}
    owner: dict[tuple[str, str], str] = {}
    for path in modules(sections):
        module = _load(path)
        for table, values in getattr(module, "keys", lambda _root: {})(root).items():
            for key, value in values.items():
                if (table, key) in owner:
                    raise ValueError(f"[{table}] {key} is set by both {owner[table, key]} and {path.name}")
                owner[table, key] = path.name
                keys[table, key] = value
    return keys


def set_key(text: str, table: str, key: str, value: str) -> str:
    """Sets `key = value` in table `table` (its own lines, not a subtable's),
    replacing the key if it is there and adding it after the table's last
    line if not. A missing table is added at the end."""
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        match = _HEADER.match(line)
        if match and match.group(1).strip().strip('"') == table and not line.lstrip().startswith("[["):
            start = i
            break
    if start is None:
        body = text.rstrip("\n")
        return (body + "\n\n" if body else "") + f"[{table}]\n{key} = {value}\n"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _HEADER.match(lines[i]):
            end = i
            break
    assign = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i in range(start + 1, end):
        if assign.match(lines[i]):
            lines[i] = f"{key} = {value}\n"
            return "".join(lines)
    last = start
    for i in range(start + 1, end):
        if lines[i].strip():
            last = i
    if not lines[last].endswith("\n"):
        lines[last] += "\n"
    lines.insert(last + 1, f"{key} = {value}\n")
    return "".join(lines)


def render(text: str, owned: dict[str, str]) -> str:
    for header in owned:
        text = drop(text, _name(header))
    text = text.rstrip("\n")
    blocks = [f"{header}\n{body.rstrip()}\n" for header, body in owned.items()]
    return (text + "\n\n" if text else "") + "\n".join(blocks)


def write(config: Path, root: Path) -> None:
    text = config.read_text() if config.exists() else ""
    # Keys first, so a table they add sits before the owned tables, which
    # render moves to the end: a second run then changes nothing.
    for (table, key), value in collect_keys(root).items():
        text = set_key(text, table, key, value)
    rendered = render(text, collect(root))
    tomllib.loads(rendered)  # never leave the core a file it cannot read
    config.write_text(rendered)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: core_config.py CONFIG_TOML ROOT")
    try:
        write(Path(sys.argv[1]), Path(sys.argv[2]).resolve())
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        sys.exit(f"core_config: {exc}")
