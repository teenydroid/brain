#!/usr/bin/env bash
# bin/proofs over fixture ledgers (docs/roadmap.md, A0): the tables are made
# from the hands' own schemas, filled with rows that meet some proofs, part
# of others and none of the rest, and the report must count each one, name
# every proof it can count, and leave the ledgers as it found them. No core,
# no network, no secrets.
#
#   bin/test/proofs_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-proofs.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

# 2026-11-12T12:00:00Z
export TEENY_NOW=1794484800
unset TEENY_STATE OPENHUMAN_WORKSPACE

# An empty root: every proof is counted as nothing, and the run still answers.
empty="$scratch/empty"
mkdir -p "$empty"
TEENY_ROOT="$empty" "$repo/bin/proofs" >"$scratch/empty.json"
check "an empty root reports ok" python3 -c "
import json,sys; o=json.load(open('$scratch/empty.json'))
assert o['ok'] and o['summary']['met'] == 0 and o['summary']['progress'] == 0, o['summary']
assert all(i['status'] == 'none' for i in o['items'])"
check "and creates no ledger" test ! -e "$empty/state/hands.db"

# The fixture root.
root="$scratch/root"
mkdir -p "$root/state/diary" "$root/state/evolve" "$root/brain/narrative/chronicle" "$root/secrets"
printf 'GITHUB_TOKEN=sentinel-never-read\n' >"$root/secrets/teeny.env"
TEENY_ROOT="$root" python3 - "$repo" <<'PY'
import json, sqlite3, sys, datetime as dt
from pathlib import Path
repo = Path(sys.argv[1])
sys.path.insert(0, str(repo / "hands" / "lib"))
from teeny_hands.x import posting_rules
from teeny_hands.tasks import queue
from teeny_hands.build import jobs
from teeny_hands import requests, stimuli
from teeny_hands.stats import collect
from teeny_hands.gh import actions as gh
from teeny_hands.mail import actions as mail
from teeny_hands.wallet import store
from teeny_hands.moltbook import rules as moltbook

T = 1794484800.0
DAY = 86400
conn = sqlite3.connect(Path(__import__("os").environ["TEENY_ROOT"]) / "state" / "hands.db")
for schema in (posting_rules.SCHEMA, queue.SCHEMA, jobs.SCHEMA, requests.SCHEMA, collect.SCHEMA, gh.SCHEMA,
               mail.SCHEMA, store.SCHEMA, stimuli.SCHEMA, moltbook.SCHEMA):
    conn.executescript(schema if isinstance(schema, str) else "".join(schema))

# X: 52 replies (met), and a post a day for 31 days, all different (met).
words = "apple banana cherry delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa".split()
for i in range(52):
    conn.execute("INSERT INTO x_posts (id, kind, text, created_at) VALUES (?, 'reply', ?, ?)", (f"r{i}", f"reply {i}", T - i * 3600))
for d in range(31):
    text = f"day {d}: " + " ".join(words[(d + k) % len(words)] + str(d) for k in range(6))
    conn.execute("INSERT INTO x_posts (id, kind, text, created_at) VALUES (?, 'post', ?, ?)", (f"p{d}", text, T - d * DAY - 60))

# Moltbook: visible writes on the last 3 days (progress), a hidden one today.
for d in range(3):
    conn.execute("INSERT INTO moltbook_comments (comment_id, post_id, at, status) VALUES (?, 'p', ?, 'verified')", (f"c{d}", T - d * DAY - 60))
conn.execute("INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('m9', ?, 's', 't', 'needs_verification')", (T - 5 * DAY,))

# Browser: an ok task each of the last 7 days (met); one from teeny; one
# that waited for a code; 2 needs_human of 25 in the month (8%: met).
for d in range(7):
    conn.execute("INSERT INTO browser_tasks (kind, goal, start_url, done_when, status, source, created_at, finished_at, code_asked_at)"
                 " VALUES ('research', 'g', 'https://x', 'w', 'ok', ?, ?, ?, ?)",
                 ("teeny" if d == 0 else "operator", T - d * DAY - 120, T - d * DAY - 60, T - d * DAY - 90 if d == 1 else None))
for i in range(18):
    status = "needs_human" if i < 2 else "ok"
    conn.execute("INSERT INTO browser_tasks (kind, goal, start_url, done_when, status, source, created_at, finished_at)"
                 " VALUES ('research', 'g', 'https://x', 'w', ?, 'operator', ?, ?)", (status, T - 10 * DAY, T - 10 * DAY))

# Builds: two shipped requests this month (progress), one shipped 40 days ago.
for i, age in enumerate((2, 9, 40)):
    conn.execute("INSERT INTO build_jobs (kind, request_id, slug, repo, text, workdir, status, claimed_at, shipped_at)"
                 " VALUES ('request', ?, ?, 'teenydroid/x', 't', 'w', 'shipped', ?, ?)",
                 (i + 1, f"s{i}", T - age * DAY, T - age * DAY))

# GitHub: an issue opened and closed (met), one left open; an upstream PR
# (progress: merging is read on GitHub); a revert PR.
for verb, ref in (("open-issue", "7"), ("close-issue", "7"), ("open-issue", "8")):
    conn.execute("INSERT INTO gh_actions (at, verb, repo, ref) VALUES (?, ?, 'teenydroid/brain', ?)", (T - DAY, verb, ref))
conn.execute("INSERT INTO gh_actions (at, verb, repo, external, ref, detail) VALUES (?, 'open-pr', 'tinyhumansai/openhuman', 1, '5', '{}')", (T,))
conn.execute("INSERT INTO gh_actions (at, verb, repo, ref, detail) VALUES (?, 'open-pr', 'teenydroid/brain', '9', ?)",
             (T, json.dumps({"head": "teeny/revert-gen-3"})))

# Health: one alarm that opened and closed its issue (met).
conn.execute("INSERT INTO stimuli (id, surface, kind, ref, text, payload, at, status, updated_at)"
             " VALUES ('h', 'health', 'recovered', 'outage:1', 'back', ?, ?, 'done', ?)", (json.dumps({"issue": 41}), T, T))

# Stats: the audience total rises for the last 5 weeks, so 4+ (met).
for w in range(6):
    day = (dt.datetime.fromtimestamp(T, dt.timezone.utc).date() - dt.timedelta(days=7 * w)).isoformat()
    conn.execute("INSERT INTO stats (date, source, metric, value, at) VALUES (?, 'x', 'followers', ?, ?)", (day, 100 - w * 5, T))
    conn.execute("INSERT INTO stats (date, source, metric, value, at) VALUES (?, 'x', 'following', ?, ?)", (day, 900 + w * 50, T))

# The wallet: a paid devnet payment only (none on mainnet).
conn.execute("INSERT INTO wallet_payments (created_at, updated_at, cluster, asset, amount_raw, usd_micro, to_address, purpose, ref, status)"
             " VALUES (?, ?, 'devnet', 'USDC', 1, 1, 'a', 'vendor', 'r', 'paid')", (T, T))

# People (hands/people): one promise kept 8 days after it was made, one kept
# the same day, one still open. Read by a person, so never met.
from teeny_hands.people import records as people
conn.executescript(people.SCHEMA)
# The weekly vote (hands/requests): one honoured vote whose winner shipped.
from teeny_hands import votes
conn.executescript(votes.SCHEMA)
cur = conn.execute("INSERT INTO build_requests (source, source_id, author, text, created_at, status) VALUES ('x:mentions', 'v1', 'ana', 'a timer', ?, 'shipped')", (T,))
conn.execute("INSERT INTO votes (post_id, options, opened_at, closes_at, status, winner_id) VALUES ('1', '[]', ?, ?, 'honoured', ?)", (T - 9 * DAY, T - 2 * DAY, cur.lastrowid))
for made, closed, status in ((T - 9 * DAY, T - DAY, "kept"), (T - DAY, T - DAY + 60, "kept"), (T - DAY, None, "open")):
    conn.execute("INSERT INTO people_promises (surface, who, what, made_at, due_at, status, closed_at) VALUES ('x', 'ana', 'w', ?, ?, ?, ?)",
                 (made, made + DAY, status, closed))
conn.commit()
conn.close()

root = Path(__import__("os").environ["TEENY_ROOT"])
today = dt.datetime.fromtimestamp(T, dt.timezone.utc).date()
for d in range(1, 13):  # yesterday back: 12 days in a row (progress)
    (root / "state" / "diary" / f"{today - dt.timedelta(days=d)}.md").write_text("# a day\n")
(root / "state" / "diary" / "notes.md").write_text("not a day\n")
with open(root / "state" / "evolve" / "archive.jsonl", "w") as fh:
    fh.write(json.dumps({"id": "seed", "verdict": "seed"}) + "\n")
    fh.write(json.dumps({"id": "g1c0", "verdict": "won"}) + "\n")
    fh.write(json.dumps({"id": "g1c0", "pr_url": "https://github.com/teenydroid/brain/pull/1"}) + "\n")
    fh.write("not json\n")
(root / "brain" / "narrative" / "chronicle" / "gen-1.md").write_text("# gen 1\n")
PY

before="$(sha256sum "$root/state/hands.db" | cut -d' ' -f1)"
TEENY_ROOT="$root" "$repo/bin/proofs" >"$scratch/out.json"
check "the report runs over the fixture" python3 -c "import json; assert json.load(open('$scratch/out.json'))['ok']"
check "the ledger is left as it was" test "$(sha256sum "$root/state/hands.db" | cut -d' ' -f1)" = "$before"
check "nothing from secrets/ is in the report" sh -c "! grep -q sentinel '$scratch/out.json'"

expect() {  # id status has
  local id="$1" status="$2" has="$3"
  check "$id is $status at $has" python3 -c "
import json
items = {i['id']: i for i in json.load(open('$scratch/out.json'))['items']}
i = items['$id']
assert i['status'] == '$status' and str(i['has']) == '$has', i"
}
expect L0.health_alarm met 1
expect L1.gh_inbox none 0
expect L1.no_repeats met 30
expect L2.x_replies met 52
expect L2.mail_started none 0
expect L2.remembers progress 1
expect L2.voted_ship met 1
expect L2.moltbook_week progress 3
expect L2.audience met 5
expect L3.browser_week met 7
expect L3.teeny_tasks met 1
expect L3.code_login met 1
expect L3.needs_human met 1
expect L3.shipped progress 2
expect L3.own_issues progress 1
expect L3.upstream progress 1
expect L3.outside none 0
expect L3.mainnet none 0
expect L3.vendor none 0
expect L3.bounty none 0
expect L4.diary progress 12
expect L5.generation_pr met 1
expect L5.promoted none 0
expect L5.rollback met 1
expect L5.chronicle met 1
expect L5.ten_promoted none 0

check "every proof the code counts is in the report, once" python3 - "$repo" "$scratch/out.json" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1] + "/bin/lib")
import proofs
ids = [i["id"] for i in json.load(open(sys.argv[2]))["items"]]
assert ids == [p[0] for p in proofs.PROOFS], ids
assert len(set(ids)) == len(ids)
PY
check "and what it cannot count is named" python3 -c "
import json; o = json.load(open('$scratch/out.json')); assert len(o['not_counted']) >= 10 and all(n['why'] for n in o['not_counted'])"

# A near-duplicate post resets the run of days.
TEENY_ROOT="$root" python3 - <<'PY'
import os, sqlite3
conn = sqlite3.connect(os.environ["TEENY_ROOT"] + "/state/hands.db")
text = conn.execute("SELECT text FROM x_posts WHERE id = 'p3'").fetchone()[0]
conn.execute("INSERT INTO x_posts (id, kind, text, created_at) VALUES ('dup', 'post', ?, 1794484000)", (text + " again",))
conn.commit()
PY
TEENY_ROOT="$root" "$repo/bin/proofs" --only L1.no_repeats >"$scratch/dup.json"
check "a near-duplicate post is caught" python3 -c "
import json; i = json.load(open('$scratch/dup.json'))['items'][0]
assert i['id'] == 'L1.no_repeats' and i['status'] == 'none' and 'near-duplicate' in i['note'], i"
check "--only reports just that proof" python3 -c "
import json; o = json.load(open('$scratch/dup.json')); assert len(o['items']) == 1 and o['not_counted'] == []"

code=0
TEENY_ROOT="$root" "$repo/bin/proofs" --only L9.nothing >"$scratch/bad.json" || code=$?
check "an unknown proof id is refused with exit 2" test "$code" = 2
TEENY_ROOT="$root" "$repo/bin/proofs" --text >"$scratch/out.txt"
check "--text prints a table" grep -q '^met .*L2.x_replies' "$scratch/out.txt"

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
printf 'all proofs tests passed\n'
