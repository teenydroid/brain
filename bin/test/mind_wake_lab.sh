#!/usr/bin/env bash
# One mind wake through a real spine, on a stub model scripted like a real wake, with fake offered mentions
# (some answerable, some not), a fake X, and every hand acting off (DRY_RUN=1). It shows that the wake's inbox
# block reaches the mind's cron run and lists only answerable mentions, that the wake replies to at most 3 of
# them through the real path (bin/teeny do reply_x -> the broker -> hands/x reply), that the 4th is refused by
# teeny-guardrails, that the replies leave the queue marked from the mind, that the unanswerable mentions
# were skipped, that a wake that does nothing is reported by the pulse, and that tick does not also answer them.
# Run it in the image, like spine_live_test.sh:
#
#   docker run --rm --name teeny-lab-mentions-1 --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/srv/teeny \
#     --entrypoint bash teeny-spine:test /srv/teeny/bin/test/mind_wake_lab.sh
#
# It skips (exit 0) when there is no core build to run.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
[ -n "${OPENHUMAN_CORE_BIN:-}" ] || OPENHUMAN_CORE_BIN="$repo/bin/teeny-core"
[ -x "$OPENHUMAN_CORE_BIN" ] || { echo "FAIL no executable at $OPENHUMAN_CORE_BIN" >&2; exit 1; }
if [ ! -f "$("$OPENHUMAN_CORE_BIN" --where 2>/dev/null || echo /nonexistent)" ] ||
  ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)' 2>/dev/null; then
  echo "skip: needs Node 24 and a built core, or run it in the image"
  exit 0
fi
OPENHUMAN_CORE_BIN="$(python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$OPENHUMAN_CORE_BIN")"
export OPENHUMAN_CORE_BIN

scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-mindlab.XXXXXX")"
spine_pid="" model_pid="" x_pid=""
cleanup() {
  # each one best-effort: a child that has already exited must not stop the rest of the cleanup
  if [ -n "$spine_pid" ]; then kill -TERM "$spine_pid" 2>/dev/null || true; wait "$spine_pid" 2>/dev/null || true; fi
  if [ -n "$model_pid" ]; then kill "$model_pid" 2>/dev/null || true; fi
  if [ -n "$x_pid" ]; then kill "$x_pid" 2>/dev/null || true; fi
  if [ "${KEEP_SCRATCH:-0}" = 1 ]; then echo "scratch kept at $scratch"; else rm -rf "$scratch"; fi
}
trap cleanup EXIT
failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@" >/dev/null; then pass "$name"; else fail "$name"; fi; }
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

root="$scratch/root"
mkdir -p "$root/brain" "$root/secrets" "$root/state/openclaw/logs" "$root/deploy" "$root/core"
cp -R "$repo/bin" "$root/bin"
cp -R "$repo/hands" "$root/hands"
cp -R "$repo/evals" "$root/evals"
cp -R "$repo/core/plugins" "$repo/core/src" "$repo/core/package.json" "$root/core/"
cp "$repo/deploy/browser-allow.txt" "$repo/deploy/browser-deny.txt" "$root/deploy/"
cp -R "$repo/brain/playbooks" "$repo/brain/agents" "$repo/brain/narrative" "$repo/brain/skills" "$root/brain/"
cp "$repo/brain/cron.toml" "$repo/brain/SOUL.md" "$repo/brain/CONTEXT.md" "$repo/brain/DRIVE.md" "$repo/brain/CHARTER.md" "$repo/brain/MEMORY.seed.md" "$root/brain/"

# --- a fake X: the account, and the posts the replies are to ---------------------------
x_port="$(free_port)"
cat >"$scratch/x.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
POSTS = json.load(open(sys.argv[2]))

class X(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/2/users/me":
            return self._send(200, {"data": {"id": "1000", "username": "teenydroid"}})
        if path.startswith("/2/tweets/"):
            post = POSTS.get(path.rsplit("/", 1)[1])
            if post:
                return self._send(200, {"data": {"id": post["id"], "text": post["text"], "author_id": "7", "conversation_id": post["id"],
                                                 "created_at": "2026-11-28T10:00:00.000Z"},
                                        "includes": {"users": [{"id": "7", "username": "ana"}]}})
        self._send(404, {"title": "Not Found"})
    def do_POST(self):
        with open(sys.argv[3], "a") as fh:  # a write that reached X: there must be none under DRY_RUN
            fh.write(self.path + "\n")
        self._send(500, {"title": "the lab never posts"})
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), X).serve_forever()
PY
: >"$scratch/x-writes.log"
# Five mentions that tag the account (A1..A5, ids 101..105), two that do not (U1, U2: 201, 202).
python3 - >"$scratch/posts.json" <<'PY'
import json
posts = {str(100 + i): {"id": str(100 + i), "text": f"@teenydroid question {i}: what are you building this week?"} for i in range(1, 6)}
posts.update({"201": {"id": "201", "text": "gm anon, nice bird"}, "202": {"id": "202", "text": "hey @someone look at this, @teenydroid is not tagged by me"}})
posts["202"]["text"] = "hey look at this thread, nobody tagged the bot"
print(json.dumps(posts))
PY
python3 -I "$scratch/x.py" "$x_port" "$scratch/posts.json" "$scratch/x-writes.log" &
x_pid=$!

# --- the stub model: the judge's clean verdict, a tick beat, and a scripted wake ------------
model_port="$(free_port)"
cat >"$scratch/model.py" <<'PY'
import json, re, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = sys.argv[2]  # file: "reply" (work the inbox block) or "ignore" (do the mind's own work only)
BODIES = sys.argv[3]

def flat(content):
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else json.dumps(content)

def wake_steps(prompt, mode):
    if mode == "ignore":
        return ["hands/x mentions --status offered --limit 1"]
    ids = re.findall(r"^- x (\d+) @", prompt, re.M)
    steps = []
    for i in ids[:4]:  # tries one more than the cap of 3: the 4th is for teeny-guardrails to refuse
        body = json.dumps({"do": "reply_x", "to": i, "text": f"thanks, {i}: I am building the inbox I should have answered"})
        steps.append("bin/teeny do '%s'" % body)
    steps.append("hands/x mark %s --status skipped --note 'over the three a wake; next wake'" % (ids[3] if len(ids) > 3 else "0"))
    return steps

class Model(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "stub", "object": "model", "context_length": 128000}]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if self.headers.get("authorization") != "Bearer stub-key":
            return self._json({"error": {"message": "missing or invalid api key"}}, 401)
        messages = body.get("messages", [])
        users = [flat(m.get("content")) for m in messages if m.get("role") == "user"]
        users = [u for u in users if not u.startswith("OpenClaw runtime context:")]
        last = users[-1] if users else ""
        answer, call = "stub says hello", None
        if "Direction: " in last and "Answer with one JSON object" in last:
            answer = json.dumps({"reason": "plain words", "category": "none", "verdict": "clean"})
        elif "# since you last looked" in last:
            answer = json.dumps({"thought": "the stub thought about it", "intents": []})
            i = last.find("what are you building this week")
            open(BODIES, "a").write(json.dumps({"kind": "tick", "mentions_in_prompt": "what are you building this week" in last, "around": last[max(0, i - 200):i + 100] if i >= 0 else ""}) + "\n")
        elif "You are awake" in last:
            mode = open(MODE).read().strip()
            results = [flat(m.get("content")) for m in messages if m.get("role") == "tool"]
            steps = wake_steps(last, mode)
            if not results:
                open(BODIES, "a").write(json.dumps({"kind": "wake", "block_in_prompt": "X mentions waiting" in last, "mode": mode,
                                                    "first_user_chars": last[:3000]}) + "\n")
            if results:  # each request carries one more result than the last: log the newest, once
                n = len(results) - 1
                open(BODIES, "a").write(json.dumps({"kind": "step", "command": steps[n] if n < len(steps) else "", "result": results[-1]}) + "\n")
            if len(results) < len(steps):
                call = {"name": "exec", "arguments": json.dumps({"command": steps[len(results)]})}
            else:
                answer = "nothing to do: inbox pass done"
        base = {"id": "stub-1", "created": int(time.time()), "model": body.get("model", "stub")}
        usage = {"prompt_tokens": 1, "completion_tokens": 5, "total_tokens": 6}
        if call:
            tc = [{"index": 0, "id": "call_%d" % int(time.time() * 1000), "type": "function", "function": call}]
            delta, finish, message = {"role": "assistant", "content": None, "tool_calls": tc}, "tool_calls", {"role": "assistant", "content": None, "tool_calls": tc}
        else:
            delta, finish, message = {"role": "assistant", "content": answer}, "stop", {"role": "assistant", "content": answer}
        if body.get("stream"):
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            for chunk in ({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                          {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage}):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json({**base, "object": "chat.completion", "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Model).serve_forever()
PY
echo reply >"$scratch/mode"
: >"$scratch/bodies.jsonl"
python3 -I "$scratch/model.py" "$model_port" "$scratch/mode" "$scratch/bodies.jsonl" &
model_pid=$!

cat >"$root/secrets/spine.env" <<ENV
LADDER_URL=http://127.0.0.1:$model_port/v1
LADDER_API_KEY=stub-key
SPINE_MODEL=stub
CORTEX_API_URL=http://127.0.0.1:$(free_port)
CORTEX_API_KEY=stub-cortex-key
ENV
cat >"$root/secrets/teeny.env" <<ENV
MIND=1
DRY_RUN=1
X_API_BASE=http://127.0.0.1:$x_port
X_API_KEY=ck-FAKE
X_API_SECRET=cs-FAKE
X_ACCESS_TOKEN=at-FAKE
X_ACCESS_TOKEN_SECRET=ats-FAKE
X_BEARER_TOKEN=bearer-FAKE
ENV

OPENHUMAN_CORE_PORT="$(free_port)"
export OPENHUMAN_CORE_PORT SPINE_BOOT_SECS=90 DRY_RUN=1 TEENY_QUIET_HOURS=off TEENY_TICK_JITTER_SECS=0
unset OPENHUMAN_CORE_TOKEN OPENHUMAN_CORE_URL OPENHUMAN_WORKSPACE CALENDAR_TZ TEENY_ROOT TEENY_BRAIN TEENY_STORAGE TEENY_SECRETS TEENY_STATE

# --- the backlog: offered mentions, as the webhook and bin/mentions leave them -------------
seed() { # ID TEXT
  (cd "$root" && TEENY_ROOT="$root" python3 -I - "$1" "$2" <<'PY'
import sys
sys.path.insert(0, "hands/lib")
from teeny_hands import stimuli
from teeny_hands.x import mentions
i, text = sys.argv[1], sys.argv[2]
conn = mentions._db()
conn.execute("INSERT OR IGNORE INTO x_mentions (id, author_id, author_username, conversation_id, text, post, status, seen_at, updated_at)"
             " VALUES (?, '7', 'ana', ?, ?, '{}', 'offered', 1, 1)", (i, i, text))
stimuli.add("x", "mention", i, text, who="ana")
PY
  )
}
"$root/bin/spine" >"$scratch/spine.log" 2>&1 &
spine_pid=$!
for _ in $(seq 1 300); do
  grep -q '\[spine\] ready' "$scratch/spine.log" && break
  kill -0 "$spine_pid" 2>/dev/null || break
  sleep 1
done
grep -q '\[spine\] ready' "$scratch/spine.log" || { cat "$scratch/spine.log" >&2; echo "FAIL the spine never became ready" >&2; exit 1; }
pass "the spine boots with MIND=1 and DRY_RUN=1 (a fake X, a stub model)"

for i in 101 102 103 104 105; do seed "$i" "@teenydroid question ${i#10}: what are you building this week?"; done
seed 201 "gm anon, nice bird"
(cd "$root" && ./hands/x skip-unreplyable </dev/null >"$scratch/skip.json")
seed 202 "hey look at this thread, nobody tagged the bot"
db() { sqlite3 -readonly "$root/state/hands.db" "$1"; }
check "skip-unreplyable skipped the untagged backlog row, with its note, and kept every tagged one" \
  test "$(db "select group_concat(id||':'||status||':'||coalesce(note,''), ' ') from x_mentions where id in ('101','201') order by id")" = "101:offered: 201:skipped:not replyable: no tag in text"

# --- the wake -------------------------------------------------------------------------------
oh() { "$root/bin/oh" "$@"; }
mind_id="$(oh cron_list | jq -r '.[] | select(.name == "teeny.mind") | .id')"
check "the pinned mind job is seeded for MIND=1" test -n "$mind_id"
fire() { oh cron_run "$(jq -cn --arg id "$mind_id" '{job_id: $id}')" >/dev/null; }
pass_lines() { [ -s "$root/state/mind/inbox-pass.jsonl" ] && wc -l <"$root/state/mind/inbox-pass.jsonl" || echo 0; }
wait_pass() { for _ in $(seq 1 150); do [ "$(pass_lines)" -ge "$1" ] && return 0; sleep 1; done; return 1; }

fire
wait_pass 1 || { tail -n 30 "$scratch/spine.log" >&2; echo "FAIL the wake never ended" >&2; exit 1; }
echo "--- wake 1: what the stub model saw and the mind did"
jq -c 'select(.kind == "wake" or .kind == "step") | del(.first_user_chars)' "$scratch/bodies.jsonl" | cut -c1-420
check "the inbox block reached the mind's cron run" jq -e -s '[.[] | select(.kind == "wake")][0].block_in_prompt == true' "$scratch/bodies.jsonl"
check "the block lists the answerable mentions and none of the untagged ones" \
  bash -c "jq -rs '[.[] | select(.kind == \"wake\")][0].first_user_chars' '$scratch/bodies.jsonl' | grep -q '^- x 10' && ! jq -rs '[.[] | select(.kind == \"wake\")][0].first_user_chars' '$scratch/bodies.jsonl' | grep -Eq '^- x 20'"
replied="$(db "select count(*) from x_mentions where note = 'answered from the mind' and status = 'drafted'")"
check "at most 3 X replies went through the real path (DRY_RUN: marked drafted, note 'answered from the mind'): $replied" test "$replied" = 3
check "the 4th reply was refused by teeny-guardrails" bash -c "jq -rs '[.[] | select(.kind == \"step\")][3].result' '$scratch/bodies.jsonl' | grep -q 'already sent its 3 X replies'"
check "the one it would not answer was marked skipped" test "$(db "select count(*) from x_mentions where status = 'skipped' and note like 'over the three%'")" = 1
check "nothing reached X (every hand acted off)" test ! -s "$scratch/x-writes.log"
check "the pass was recorded: block given, 3 X replies, 1 mark, not owed" \
  jq -e -s 'last | .block == true and .replies.x == 3 and .marks == 1 and .owed == false' "$root/state/mind/inbox-pass.jsonl"
check "the answered and skipped mentions' stimuli are closed; the two still offered wait" \
  test "$(db "select group_concat(ref, ',') from (select ref from stimuli where surface = 'x' and status = 'pending' order by ref)")" = "101,202"

# --- tick does not answer them too ---------------------------------------------------------
before="$(db "select count(*) from x_mentions where status = 'offered'")/$(db "select count(*) from x_replies")"
ticked="$(for _ in 1 2 3 4 5 6 7 8 9 10; do o="$("$root/bin/tick" 2>>"$scratch/tick.log" || true)"; [ "$(jq -r .kind <<<"$o" 2>/dev/null)" = busy ] || break; sleep 3; done; printf '%s' "$o")"
check "tick completed a beat that found nothing for it (kind quiet, not busy or failed)" test "$(jq -r .kind <<<"$ticked" 2>/dev/null)" = quiet
check "and left the mentions to the mind: the offered and reply counts are unchanged" \
  test "$(db "select count(*) from x_mentions where status = 'offered'")/$(db "select count(*) from x_replies")" = "$before"
check "and the beat's prompt carried none of the mentions" bash -c "! jq -e -s 'any(.[]; .kind == \"tick\" and .mentions_in_prompt == true)' '$scratch/bodies.jsonl' >/dev/null"

# --- a wake that ignores the block is reported ---------------------------------------------------
echo ignore >"$scratch/mode"
fire
wait_pass 2 || { echo "FAIL the second wake never ended" >&2; exit 1; }
check "a wake given the block that did neither is recorded as owed" jq -e -s 'last | .block == true and .owed == true' "$root/state/mind/inbox-pass.jsonl"
pulse="$(cd "$root" && TEENY_ROOT="$root" python3 -I -c 'import sys, json; sys.path.insert(0, "bin/lib"); import pulse; print(json.dumps(pulse.pulse(1).get("inbox_pass")))')"
echo "pulse inbox_pass: $pulse"
check "bin/teeny pulse shows the skipped wake" test "$(jq -r .wakes_that_skipped_it <<<"$pulse")" = 1

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  tail -n 20 "$scratch/spine.log" >&2
  exit 1
fi
echo "all passed"
