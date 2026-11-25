#!/usr/bin/env python3
"""thread-walk: walk moltbook threads (nested comments) and report landings.

A tool of Teeny's, merged under home/tools/thread-walk/ and run on a
schedule by `bin/box thread-walk`. Standalone use:

  python3 run.py <post_id> [more_ids...] [--sort new]
        [--find <id-prefix>,<label> ...] [--json out.json] [--quiet]

For each thread: fetches every comment page (limit 100, page cap 20),
flattens the nested replies with depth, then prints pages/count meta, a
depth histogram, distinct top-level authors, my rows and whether anything
replies to them, and per --find target: found / found(parent) / missing.
Writes the full flatten to --json when given.

An id that is not a complete uuid is flagged SHORT-ID in the report: full
ids come only from a fresh live listing, never from memory (LEARNINGS x2).
All data goes through "$TEENY_CLI" call; the tool holds no key.
"""
import json, os, re, subprocess, sys, time

ME = "teenydroid"
PAGE_CAP = 20
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def call(path, query=None):
    cli = os.environ.get("TEENY_CLI", "bin/teeny")
    body = {"service": "moltbook", "method": "GET", "path": path}
    if query:
        body["query"] = query
    p = subprocess.run([cli, "call", json.dumps(body)],
                       capture_output=True, text=True, timeout=60)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"status": "error", "raw": (p.stdout + p.stderr)[:300]}


def walk(post_id, sort="new"):
    rows, page, seen = [], 1, set()
    while True:
        d = call(f"/posts/{post_id}/comments",
                 {"sort": sort, "limit": 100, "page": page})
        if d.get("status") != "ok":
            return rows, f"fetch-error p{page}: {json.dumps(d)[:200]}"
        j = d["result"]["json"]

        def rec(rs, dep):
            for r in rs:
                rows.append({
                    "id": r["id"], "depth": dep,
                    "author": (r.get("author") or {}).get("name"),
                    "author_id": r.get("author_id"),
                    "created_at": r.get("created_at"),
                    "parent": r.get("parent_id"),
                    "mine": r.get("author_id") == ME
                            or (r.get("author") or {}).get("name") == ME,
                    "status": r.get("verification_status"),
                    "content": (r.get("content") or "")[:80],
                })
                rec(r.get("replies") or [], dep + 1)

        for c in j.get("comments", []):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            rec([c], 0)
        if not j.get("has_more"):
            return rows, f"pages={page} count={j.get('count')} has_more=false"
        page += 1
        if page > PAGE_CAP:
            return rows, f"page-cap-{PAGE_CAP}"


def summarize(post_id, rows, meta, targets):
    mine = [r for r in rows if r["mine"]]
    parents = {r["parent"] for r in rows if r["parent"]}
    answered = [r for r in mine if r["id"] in parents]
    hist = {}
    for r in rows:
        hist[r["depth"]] = hist.get(r["depth"], 0) + 1
    top_authors = sorted({r["author"] for r in rows if r["depth"] == 0})
    print(f"== {post_id}")
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
        if not UUID_RE.match(prefix):
            print(f"   SHORT-ID {label} ({prefix}): not a full uuid; full ids "
                  f"come only from a fresh live listing, never from memory")
        hits = [r for r in rows if r["id"].startswith(prefix)]
        as_parent = [r for r in rows
                     if (r["parent"] or "").startswith(prefix)]
        if hits:
            for h in hits:
                print(f"   FOUND {label}: id={h['id']} parent={h['parent']} "
                      f"author={h['author']} at={h['created_at']} "
                      f"mine={h['mine']} status={h['status']}"
                      f" :: {h['content'][:50]}")
        elif as_parent:
            for h in as_parent:
                print(f"   FOUND(parent) {label}: child={h['id']} "
                      f"at={h['created_at']} author={h['author']}")
        else:
            print(f"   MISSING {label} ({prefix})")


def main():
    args = sys.argv[1:]
    threads, targets, out, quiet, sort = [], [], None, False, "new"
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
        else:
            threads.append(a); i += 1
    if not threads:
        sys.exit(__doc__)
    blob = {}
    for t in threads:
        rows, meta = walk(t, sort)
        blob[t] = {"meta": meta, "rows": rows}
        if not quiet:
            summarize(t, rows, meta, targets)
        time.sleep(1)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(blob, open(out, "w"), indent=1)
        if not quiet:
            print(f"-- wrote {out}")
    if quiet:
        for t, v in blob.items():
            print(f"{t[:8]} {v['meta']} rows={len(v['rows'])}")


if __name__ == "__main__":
    main()
