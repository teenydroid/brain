#!/usr/bin/env bash
# The Dockerfile's modules stage copies a few of OpenHuman's scripts, not the
# whole tree, so a new relative import in one of them breaks the image build
# while every other check stays green (CI builds the spine with cargo, not
# docker). This checks that every relative import of every copied .mjs is
# copied too, to the path the import resolves to.
#
#   bin/test/dockerfile_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

if [ ! -e "$repo/vendor/openhuman/scripts/release/stage-modules.mjs" ]; then
  echo "skip: vendor/openhuman is not checked out"
  exit 0
fi

python3 -I - "$repo" <<'PY'
import os, re, sys

repo = sys.argv[1]
lines = open(os.path.join(repo, "Dockerfile")).read().splitlines()

# The COPY lines of the stage named `modules`: source paths -> image paths.
copied = {}  # image path -> source path
stage = None
for line in lines:
    m = re.match(r"FROM\s+\S+(?:\s+AS\s+(\S+))?", line, re.I)
    if m:
        stage = m.group(1)
        continue
    if stage != "modules":
        continue
    m = re.match(r"COPY\s+(.+)", line)
    if not m or m.group(1).startswith("--"):
        continue
    *sources, dest = m.group(1).split()
    for src in sources:
        target = os.path.join(dest, os.path.basename(src.rstrip("/"))) if dest.endswith("/") else dest
        copied[os.path.normpath(target)] = src

failures = []
for target, src in copied.items():
    if not src.endswith(".mjs"):
        continue
    text = open(os.path.join(repo, src)).read()
    for spec in re.findall(r"""from\s+["'](\.{1,2}/[^"']+)["']""", text):
        needed = os.path.normpath(os.path.join(os.path.dirname(target), spec))
        if needed not in copied:
            failures.append(f"{src} imports {spec}, but the modules stage never copies {needed}")

if not copied:
    sys.exit("FAIL no modules stage found in the Dockerfile")
for failure in failures:
    print("FAIL " + failure, file=sys.stderr)
if failures:
    sys.exit(1)
print(f"ok   the modules stage copies every relative import ({len(copied)} paths)")
PY
echo "all passed"
