#!/usr/bin/env bash
# The image builds the TypeScript core in a node:24 stage and runs it on a node:24
# runtime. A build breaks quietly when the Dockerfile copies a path that is not
# there, when .dockerignore hides a file a COPY needs, or when the runtime loses
# Node 24 or a tool bin/ needs, while every other check stays green. This checks
# the shape without building (the CI job builds it for real).
#
#   bin/test/dockerfile_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

python3 -I - "$repo" <<'PY'
import fnmatch, json, os, re, sys

repo = sys.argv[1]
failures = []


def fail(msg):
    failures.append(msg)


text = open(os.path.join(repo, "Dockerfile")).read()
# Join continuation lines, drop comments.
logical, cur = [], ""
for raw in text.splitlines():
    if raw.lstrip().startswith("#"):
        continue
    if raw.rstrip().endswith("\\"):
        cur += raw.rstrip()[:-1] + " "
        continue
    logical.append((cur + raw).strip())
    cur = ""
lines = [l for l in logical if l]

# Stages: name -> {from, lines}
stages, cur_stage = [], None
for line in lines:
    m = re.match(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
    if m:
        cur_stage = {"image": m.group(1), "name": m.group(2), "lines": []}
        stages.append(cur_stage)
    elif cur_stage is not None:
        cur_stage["lines"].append(line)
by_name = {s["name"]: s for s in stages if s["name"]}
runtime = stages[-1]

# --- OpenHuman is gone from the build -------------------------------------------------
for s in stages:
    if s["image"].startswith("rust:"):
        fail("a rust build stage is still in the Dockerfile")
    if s["name"] in ("builder", "modules"):
        fail(f"the {s['name']} stage (OpenHuman's build) is still in the Dockerfile")
if re.search(r"vendor/openhuman|Cargo\.|teeny-spine\b(?! image)|spine/ spine/|OPENHUMAN_BUNDLED_MODULES", "\n".join(lines)):
    fail("the Dockerfile still builds or points at OpenHuman or the Rust spine")

# --- the core stage -------------------------------------------------------------------
core = by_name.get("core")
if core is None:
    fail("no `core` stage builds the TypeScript core")
else:
    if not re.match(r"node:24(\.|-|$)", core["image"]):
        fail(f"the core stage is {core['image']}, not node:24")
    body = "\n".join(core["lines"])
    if "npm ci" not in body or "npm run build" not in body:
        fail("the core stage does not run `npm ci` and `npm run build`")
    if "package-lock.json" not in body:
        fail("the core stage does not copy package-lock.json (npm ci needs it)")
    if "--omit=dev" not in body:
        fail("the core stage does not prune to production dependencies")

pkg = json.load(open(os.path.join(repo, "core", "package.json")))
if "build" not in pkg.get("scripts", {}):
    fail("core/package.json has no build script")
if not os.path.exists(os.path.join(repo, "core", "tsconfig.build.json")):
    fail("core/tsconfig.build.json is missing")
if not str(pkg.get("engines", {}).get("node", "")).startswith(">=24"):
    fail("core/package.json does not require node >=24")

# --- the runtime ----------------------------------------------------------------------
if not re.match(r"node:24(\.|-|$)", runtime["image"]):
    fail(f"the runtime is {runtime['image']}; it needs Node 24 (OpenClaw requires it)")
rbody = "\n".join(runtime["lines"])
for tool in ("chromium", "bubblewrap", "python3", "jq", "sqlite3", "curl"):
    if not re.search(rf"\b{tool}\b", rbody):
        fail(f"the runtime does not install {tool}")
for needle, why in (
    ("COPY --from=core /build/core/dist /opt/teeny-core/dist", "the built core"),
    ("COPY --from=core /build/core/node_modules /opt/teeny-core/node_modules", "the production node_modules"),
    ("COPY bin/teeny-core bin/openclaw /usr/local/bin/", "the teeny-core and openclaw wrappers"),
    ("OPENHUMAN_CORE_BIN=/usr/local/bin/teeny-core", "OPENHUMAN_CORE_BIN naming the wrapper"),
    ("OPENHUMAN_CORE_PORT=7788", "port 7788"),
    ("TEENY_CHROME_PATH=/usr/bin/chromium", "TEENY_CHROME_PATH"),
):
    if needle not in rbody:
        fail(f"the runtime stage is missing {why}: {needle}")
if not re.search(r"HEALTHCHECK .*http://127\.0\.0\.1:7788/health", rbody):
    fail("the HEALTHCHECK no longer polls http://127.0.0.1:7788/health")
if 'ENTRYPOINT ["/srv/teeny/bin/spine"]' not in rbody:
    fail("the ENTRYPOINT is not /srv/teeny/bin/spine")

# --- every COPY from the build context exists and .dockerignore lets it through ---------
ignore = []
for l in open(os.path.join(repo, ".dockerignore")).read().splitlines():
    l = l.strip()
    if l and not l.startswith("#"):
        ignore.append(l)


def ignored(path):
    verdict = False
    for pat in ignore:
        neg = pat.startswith("!")
        pat = pat.lstrip("!").rstrip("/")
        parts = path.split("/")
        hit = any(
            fnmatch.fnmatch("/".join(parts[: i + 1]), pat) or fnmatch.fnmatch(parts[i], pat) and "/" not in pat
            for i in range(len(parts))
        )
        if hit:
            verdict = not neg
    return verdict


n = 0
for s in stages:
    for line in s["lines"]:
        m = re.match(r"COPY\s+(.+)", line)
        if not m:
            continue
        toks = m.group(1).split()
        if toks and toks[0].startswith("--from"):
            continue
        toks = [t for t in toks if not t.startswith("--")]
        for src in toks[:-1]:
            n += 1
            path = src.rstrip("/")
            if not os.path.exists(os.path.join(repo, path)):
                fail(f"COPY {src}: not in the repository")
            elif ignored(path):
                fail(f"COPY {src}: .dockerignore hides it from the build")

for failure in failures:
    print("FAIL " + failure, file=sys.stderr)
if failures:
    sys.exit(1)
print(f"ok   the core stage builds on node:24, the runtime has node 24 and its tools, {n} COPY sources are in the context")
PY
echo "all passed"
