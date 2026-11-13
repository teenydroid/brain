#!/usr/bin/env bash
# Tell Teeny something from the host, and have it act on it now.
#
#   deploy/tell.sh "tweet that the paywall is live"
#   echo "set up a browser task to find our HN thread" | deploy/tell.sh
#   deploy/tell.sh --no-wait "follow up with ana tomorrow"
#
# The message goes into the inbox as a `host · instruction` stimulus
# (hands/stimuli add, inside the spine container), then one beat is started
# at once (bin/tick --detach) instead of waiting for the 5-minute cron. The
# beat reads it as the operator's instruction, not as data, wakes for it at
# night, and may use open_issue, queue_task and start_mail for that beat even
# while their caps are 0 (bin/lib/tick.py, HOST_LIFTS). The guard still reads
# everything that goes out.
#
# Unless --no-wait, it then follows state/tick/runs.jsonl until the beat that
# held the message is recorded and prints its thought and what each intent
# did. Exit 0 when that beat answered, 1 when it failed, 2 on timeout.
#
# Every message is counted in state/host/said.jsonl (time, ref, length and a
# hash, never the text): DIRECTIVE.md's test says no person starts a turn,
# so audit has to see each one that did.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${TEENY_COMPOSE_FILE:-$ROOT/deploy/compose.yaml}"
STATE="$ROOT/state"
RUNS="$STATE/tick/runs.jsonl"
POLL_SECS="${TELL_POLL_SECS:-5}"

wait_for=1
timeout=1900

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
die() { printf 'tell: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --no-wait) wait_for=0; shift ;;
    --timeout) [ $# -ge 2 ] || die "--timeout needs seconds"; timeout="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    --) shift; break ;;
    -*) die "unknown option $1" ;;
    *) break ;;
  esac
done
case "$timeout" in ''|*[!0-9]*) die "--timeout is a number of seconds" ;; esac

if [ $# -gt 0 ]; then
  text="$*"
else
  [ -t 0 ] && usage 2
  text="$(cat)"
fi
[ -n "${text//[[:space:]]/}" ] || die "nothing to say"

spine() { docker compose -f "$COMPOSE_FILE" exec -T spine "$@"; }

ref="host-$(date +%s)-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
body="$(REF="$ref" TEXT="$text" python3 -c '
import json, os
print(json.dumps({"surface": "host", "kind": "instruction", "ref": os.environ["REF"],
                  "text": os.environ["TEXT"], "who": "operator", "payload": {"source": "host"}}))')"

# Where the beat records start from: only what is written after the send counts.
offset=0
[ -f "$RUNS" ] && offset="$(wc -c <"$RUNS" | tr -d ' ')"

printf '%s\n' "$body" | spine hands/stimuli add >/dev/null || die "the inbox refused it (is the spine up?)"
mkdir -p "$STATE/host"
REF="$ref" TEXT="$text" python3 -c '
import datetime as dt, hashlib, json, os
text = os.environ["TEXT"]
print(json.dumps({"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ref": os.environ["REF"],
                  "chars": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}))' >>"$STATE/host/said.jsonl"
printf 'told teeny (%s)\n' "$ref"

kick() { spine bin/tick --detach >/dev/null 2>&1 || true; }
kick
[ "$wait_for" = 1 ] || exit 0

# Reads the records written since `offset`. Prints the one that held our ref
# and exits 0 (answered) or 1 (failed); exits 3 if none yet, 4 if a beat
# without it finished (another was running: worth starting one more).
check() {
  REF="$ref" OFFSET="$offset" RUNS="$RUNS" python3 -c '
import json, os, sys
ref, path = os.environ["REF"], os.environ["RUNS"]
try:
    with open(path, "rb") as fh:
        fh.seek(int(os.environ["OFFSET"]))
        lines = fh.read().decode("utf-8", "replace").splitlines()
except OSError:
    sys.exit(3)
other = False
for line in lines:
    try:
        rec = json.loads(line)
    except ValueError:
        continue
    if ref not in (rec.get("host") or []):
        other = other or rec.get("kind") not in ("busy",)
        continue
    print(f"beat {rec.get(\"tick\", \"?\")} ({rec.get(\"kind\")}) at {rec.get(\"at\")}")
    if not rec.get("ok"):
        print(f"failed: {rec.get(\"error\")}")
        sys.exit(1)
    if rec.get("thought"):
        print(f"thought: {rec[\"thought\"]}")
    for r in rec.get("results") or []:
        result = json.dumps(r.get("result"), ensure_ascii=False)
        print(f"- {r.get(\"do\")}: {r.get(\"status\")}  {result[:300]}")
    sys.exit(0)
sys.exit(4 if other else 3)'
}

deadline=$(( $(date +%s) + timeout ))
while :; do
  set +e; check; got=$?; set -e
  case "$got" in
    0|1) exit "$got" ;;
    4) offset="$(wc -c <"$RUNS" | tr -d ' ')"; kick ;;  # the beat that was running is done; ours is next
  esac
  if [ "$(date +%s)" -ge "$deadline" ]; then
    printf 'tell: no beat has answered %s after %ss; it is still in the inbox.\n' "$ref" "$timeout" >&2
    printf 'tell: look at state/tick/beat.log and state/tick/runs.jsonl\n' >&2
    exit 2
  fi
  sleep "$POLL_SECS"
done
