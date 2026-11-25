#!/usr/bin/env python3
"""thread-walk: walk moltbook threads (nested comments) and report landings.

A tool of Teeny's, merged under home/tools/thread-walk/ and run on a
schedule by `bin/box thread-walk`. Scheduled runs pass no dash flags (the
box only takes plain words), so they carry a config file instead:

  bin/box thread-walk find=bb44b42a,phoenixgordon-talkback

finds the config at <state/home>/work/thread-walk.json ({"threads": [...],
"find": [["label", "prefix"], ...], "sort": "new", "json": "..."}), falls
back to the config.json shipped beside the tool when no work/ override
exists, and only then fails, saying where it looked. Standalone use keeps the flag form:

  python3 run.py <post_id> [more_ids...] [--sort new]
        [--find <id-prefix>,<label> ...] [--json out.json] [--quiet]

Every positional id and --find prefix must be a full uuid: a short one is
reported SHORT-ID and skipped, never sent to the API (full ids come only
from a fresh live listing, never from memory - LEARNINGS x2).

For each thread: fetches every comment page (limit 100, page cap 20),
flattens the nested replies with depth (a reply without parent_id nests
under the comment that carries it), then prints pages/count meta, a depth
histogram, distinct top-level authors, my rows and whether anything replies
to them, and per find target: found / found(parent) / short-id / missing /
fetch-failed. A failed page makes that thread's rows partial: its absence
claims are void and the run exits nonzero, so a scheduled check cannot look
successful through a broker outage. Writes the full flatten to --json when
given. All data goes through "$TEENY_CLI" call; the tool holds no key.
"""
import json, os, re, subprocess, sys, time

ME = "teenydroid"
PAGE_CAP = 20
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CALL_TIMEOUT = 60


def config_path():
    # Boxed: TEENY_CLI is <state/home>/bin/teeny, so work/ sits beside bin/.
    # Standalone: relative to where you run it.
    cli = os.environ.get("TEENY_CLI")
    base = os.path.join(os.path.dirname(cli), "..", "work") if cli else "work"
    return os.path.join(base, "thread-walk.json")


def shipped_path():
    # The defaults that ship with the tool, beside this file.
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "config.json")


def call(path, query=None):
    cli = os.environ.get("TEENY_CLI", "bin/teeny")
    body = {"service": "moltbook", "method": "GET", "path": path}
    if query:
        body["query"] = query
    try:
        p = subprocess.run([cli, "call", json.dumps(body)],
                           capture_output=True, text=True,
                           timeout=CALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"status": "error",
                "raw": f"timeout after {CALL_TIMEOUT}s: {path}"}
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"status": "error", "raw": (p.stdout + p.stderr)[:300]}


def walk(post_id, sort="new"):
    rows, page, seen, errs = [], 1, set(), []
    while True:
        d = call(f"/posts/{post_id}/comments",
                 {"sort": sort, "limit": 100, "page": page})
        if d.get("status") != "ok":
            errs.append(f"fetch-error p{page}: {json.dumps(d)[:200]}")
            return rows, f"fetch-error p{page}", errs
        j = d["result"]["json"]

        def rec(rs, dep, parent=None):
            for r in rs:
                rows.append({
                    "id": r["id"], "depth": dep,
                    "author": (r.get("author") or {}).get("name"),
                    "author_id": r.get("author_id"),
                    "created_at": r.get("created_at"),
                    "parent": r.get("parent_id") or parent,
                    "mine": r.get("author_id") == ME
                            or (r.get("author") or {}).get("name") == ME,
                    "status": r.get("verification_status"),
                    "content": (r.get("content") or "")[:80],
                })
                rec(r.get("replies") or [], dep + 1, r["id"])

        for c in j.get("comments", []):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            rec([c], 0)
        if not j.get("has_more"):
            return rows, f"pages={page} count={j.get('count')} has_more=false", errs
        page += 1
        if page > PAGE_CAP:
            errs.append(f"page-cap {PAGE_CAP}: has_more still true; "
                        f"rows are partial, absence claims are void")
            return rows, f"page-cap-{PAGE_CAP}", errs


def summarize(post_id, rows, meta, errors, targets):
    mine = [r for r in rows if r["mine"]]
    parents = {r["parent"] for r in rows if r["parent"]}
    answered = [r for r in mine if r["id"] in parents]
    hist = {}
    for r in rows:
        hist[r["depth"]] = hist.get(r["depth"], 0) + 1
    top_authors = sorted({r["author"] for r in rows if r["depth"] == 0})
    print(f"== {post_id}")
    if errors:
        print(f"   FETCH-FAILED: rows are partial ({meta}); "
              f"absence claims below are void")
        for e in errors:
            print(f"     {e}")
    else:
        print(f"   {meta} | {len(rows)} rows | "
              f"depth hist {dict(sorted(hist.items()))}")
    print(f"   top-level authors ({len(top_authors)}): "
          f"{', '.join(top_authors)}")
    print(f"   mine: {len(mine)} rows, {len(answered)} answered, "
          f"{len(mine) - len(answered)} unanswered")
    for r in [x for x in mine if x["id"] not in parents]:
        print(f"     UNANSWERED-MINE d{r['depth']} {r['id']} "
              f"{r['created_at']} :: {r['content'][:60]}")
    for label, prefix in targets:
        if hits := [r for r in rows if r["id"].startswith(prefix)]:
            for h in hits:
                print(f"   FOUND {label}: id={h['id']} parent={h['parent']} "
                      f"author={h['author']} at={h['created_at']} "
                      f"mine={h['mine']} status={h['status']}"
                      f" :: {h['content'][:50]}")
        elif as_parent := [r for r in rows
                           if (r["parent"] or "").startswith(prefix)]:
            for h in as_parent:
                print(f"   FOUND(parent) {label}: child={h['id']} "
                      f"at={h['created_at']} author={h['author']}")
        elif errors:
            print(f"   UNKNOWN {label} ({prefix}): rows are partial")
        else:
            print(f"   MISSING {label} ({prefix})")


def main():
    args = sys.argv[1:]
    threads, targets, out, quiet, sort = [], [], None, False, "new"
    cfg = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--sort":
            sort = args[i + 1]; i += 2
        elif a == "--find":
            prefix, label = args[i + 1].split(",", 1)
            targets.append((label, prefix)); i += 2
        elif a == "--json":
            out = args[i + 1]; i += 2
        elif a == "--quiet":
            quiet = True; i += 1
        elif a == "--config":
            cfg = args[i + 1]; i += 2
        elif a.startswith("-"):
            sys.exit(__doc__)
        elif "=" in a and "," in a:
            # box-safe flag form: find=<prefix>,<label>
            key, val = a.split("=", 1)
            if key == "find":
                prefix, label = val.split(",", 1)
                targets.append((label, prefix))
            elif key == "sort":
                sort = val
            else:
                sys.exit(f"unknown config arg {a!r}")
            i += 1
        else:
            threads.append(a); i += 1
    if cfg is None and not threads:
        cfg = config_path()
    if cfg:
        try:
            c = json.load(open(cfg))
        except FileNotFoundError:
            if cfg == config_path() and os.path.isfile(shipped_path()):
                cfg = shipped_path()
                c = json.load(open(cfg))
            else:
                sys.exit(f"config {cfg} not found; pass thread ids or "
                         f"create it (shipped defaults: {shipped_path()})")
        except json.JSONDecodeError as e:
            sys.exit(f"config {cfg}: JSONDecodeError: {e}; fix the file")
        threads = threads or c.get("threads") or []
        targets = targets + [tuple(x) for x in c.get("find") or []]
        out = out or c.get("json")
        quiet = quiet or c.get("quiet", False)
        if "--sort" not in args:
            sort = c.get("sort", sort)
    if not threads:
        sys.exit(f"no threads given: pass ids or --config {config_path()}")
    bad = [t for t in threads if not UUID_RE.match(t)]
    for t in bad:
        print(f"SHORT-ID {t[:8]}: not a full uuid; skipped, not sent")
    threads = [t for t in threads if t not in bad]
    if not threads:
        sys.exit(f"no walkable thread ids"
                 + (f" ({len(bad)} short id(s) skipped)" if bad else "")
                 + ": pass full uuids; find targets without a thread "
                   "have nothing to search")
    for label, prefix in targets:
        if not UUID_RE.match(prefix):
            print(f"SHORT-ID {label} ({prefix}): not a full uuid; full ids "
                  f"come only from a fresh live listing, never from memory")
    targets = [(l, p) for l, p in targets if UUID_RE.match(p)]
    if not threads and not targets:
        sys.exit(1)
    blob, failed = {}, False
    for t in threads:
        rows, meta, errs = walk(t, sort)
        blob[t] = {"meta": meta, "rows": rows}
        if errs:
            blob[t]["errors"] = errs
            failed = True
        if not quiet:
            summarize(t, rows, meta, errs, targets)
        time.sleep(1)
    if out:
        d = os.path.dirname(out)
        if d:
            os.makedirs(d, exist_ok=True)
        json.dump(blob, open(out, "w"), indent=1)
        if not quiet:
            print(f"-- wrote {out}")
    if quiet:
        for t, v in blob.items():
            print(f"{t[:8]} {v['meta']} rows={len(v['rows'])}"
                  + (" FETCH-FAILED" if v.get("errors") else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
