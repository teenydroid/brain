#!/usr/bin/env bash
# Boots a real OpenHuman core through bin/spine over a scratch root and checks
# the whole step-1 surface: first-boot minting, the single storage file, the
# file keyring, cron seeded from brain/cron.toml, a shell job that runs,
# bin/crons reading storage.db, bin/oh, and bin/ask completing a turn against
# a stub OpenAI-compatible model.
#
#   OPENHUMAN_CORE_BIN=/path/to/openhuman-core bin/test/spine_live_test.sh
#
# Skips (exit 0) when no core binary is given; CI runs bin/test/seed_cron_test.sh
# instead. Build one from upstream/main with
# `cargo build --release -p openhuman-cli --bin openhuman-core`.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
if [ -z "${OPENHUMAN_CORE_BIN:-}" ]; then
  echo "skip: set OPENHUMAN_CORE_BIN to an openhuman-core binary to run this test"
  exit 0
fi

# bin/spine runs from a scratch copy, so a relative path would not resolve.
OPENHUMAN_CORE_BIN="$(python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$OPENHUMAN_CORE_BIN")"
export OPENHUMAN_CORE_BIN
[ -x "$OPENHUMAN_CORE_BIN" ] || { echo "FAIL no executable at $OPENHUMAN_CORE_BIN" >&2; exit 1; }

scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-spine.XXXXXX")"
spine_pid=""
model_pid=""
cleanup() {
  [ -n "$spine_pid" ] && kill -TERM "$spine_pid" 2>/dev/null && wait "$spine_pid" 2>/dev/null
  [ -n "$model_pid" ] && kill "$model_pid" 2>/dev/null
  if [ "${KEEP_SCRATCH:-0}" = 1 ]; then echo "scratch kept at $scratch"; else rm -rf "$scratch"; fi
}
trap cleanup EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

# --- a stub model: answers every chat completion with one fixed sentence -------
model_port="$(free_port)"
cat >"$scratch/model.py" <<'PY'
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = "teeny spine says hello"

class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # The window is reported the way the ladder reports it. The core does not
    # ask a local-openai endpoint for it; bin/spine must.
    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "stub", "object": "model", "context_length": 128000}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        # Like the ladder: no key, no answer. A core that never stored the key
        # (a secret write it could not make) must fail here, not pass.
        if self.headers.get("authorization") != "Bearer stub-key":
            data = json.dumps({"error": {"message": "missing or invalid api key", "type": "ladder_router_error"}}).encode()
            self.send_response(401)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        with open(sys.argv[2], "a") as log:
            log.write(self.path + "\n")
        # What the model was actually asked: the last user message it received.
        users = [m for m in body.get("messages", []) if m.get("role") == "user"]
        # Every request whole, for checks on what an agent was offered.
        with open(sys.argv[4], "a") as log:
            log.write(json.dumps(body) + "\n")
        with open(sys.argv[3], "a") as log:
            log.write(json.dumps(users[-1].get("content") if users else None) + "\n")
        base = {"id": "stub-1", "created": int(time.time()), "model": body.get("model", "stub")}
        # A bin/act or bin/eval turn: answer the JSON it asks for.
        answer = ANSWER
        last = users[-1].get("content") if users else None
        if isinstance(last, str) and "# playbook: hello" in last:
            answer = json.dumps({"action": "note", "text": "hello from a playbook"})
        # bin/eval: a draft for a task, and the judge comparing two.
        elif isinstance(last, str) and "# eval: " in last:
            answer = json.dumps({"text": "gm from an eval"})
        elif isinstance(last, str) and "# how to judge" in last:
            answer = json.dumps({"better": "tie", "breaks": {}, "notes": "the stub judged it"})
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            chunks = [
                {**base, "object": "chat.completion.chunk",
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}, "finish_reason": None}]},
                {**base, "object": "chat.completion.chunk",
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 5, "total_tokens": 6}},
            ]
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json({**base, "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 5, "total_tokens": 6}})

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Model).serve_forever()
PY
python3 -I "$scratch/model.py" "$model_port" "$scratch/model-calls.log" "$scratch/model-asked.log" "$scratch/model-bodies.jsonl" &
model_pid=$!

# --- a scratch copy of the checkout, laid out as on the box ---------------------
# Cron shell jobs get an allowlisted environment and run from the action dir,
# so a job finds state/ and secrets/ only from the root it runs in. Relocating
# those with TEENY_* variables would test something production never does.
root="$scratch/root"
mkdir -p "$root/brain" "$root/secrets"
cp -R "$repo/bin" "$root/bin"
cp -R "$repo/hands" "$root/hands"
cp -R "$repo/evals" "$root/evals"
mkdir -p "$root/deploy"
cp "$repo/deploy/browser-allow.txt" "$root/deploy/"
cp -R "$repo/brain/playbooks" "$repo/brain/agents" "$repo/brain/narrative" "$root/brain/"
cp "$repo/brain/cron.toml" "$repo/brain/SOUL.md" "$repo/brain/CONTEXT.md" "$root/brain/"
cat >"$root/secrets/spine.env" <<ENV
LADDER_URL=http://127.0.0.1:$model_port/v1
LADDER_API_KEY=stub-key
SPINE_MODEL=stub
ENV

core_port="$(free_port)"
export OPENHUMAN_CORE_PORT="$core_port" SPINE_BOOT_SECS=90
unset OPENHUMAN_CORE_TOKEN OPENHUMAN_CORE_URL OPENHUMAN_WORKSPACE CALENDAR_TZ \
  TEENY_ROOT TEENY_BRAIN TEENY_STORAGE TEENY_SECRETS TEENY_STATE
"$root/bin/spine" >"$scratch/spine.log" 2>&1 &
spine_pid=$!
for _ in $(seq 1 120); do
  grep -q '\[spine\] ready' "$scratch/spine.log" && break
  kill -0 "$spine_pid" 2>/dev/null || break
  sleep 1
done
if ! grep -q '\[spine\] ready' "$scratch/spine.log"; then
  cat "$scratch/spine.log" >&2
  echo "FAIL the spine never became ready" >&2
  exit 1
fi
pass "the spine boots and reports ready"

# --- first boot -----------------------------------------------------------------
check "mints the core token" test -s "$root/secrets/core.token"
check "mints the keyring master key" test -s "$root/secrets/openhuman.key"
mode_of() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }
check "keeps the core's config (with the model key) private" test "$(mode_of "$root/.openhuman/config.toml")" = 600
check "keeps the token private" test "$(stat -c %a "$root/secrets/core.token" 2>/dev/null || stat -f %Lp "$root/secrets/core.token")" = 600
check "writes one storage file in the brain" test -s "$root/brain/storage.db"
check "leaves Teeny's SOUL.md exactly as it was" cmp -s "$repo/brain/SOUL.md" "$root/brain/SOUL.md"
# OpenHuman only treats a folder named `workspace` as a workspace beside its
# config; anything else nests one inside it unless the config already exists.
check "the core uses brain/ itself, not a folder nested in it" test ! -e "$root/brain/workspace"
check "keeps config out of the brain" test ! -e "$root/brain/config.toml"
check "puts config beside the brain" test -s "$root/.openhuman/config.toml"
check "never exports the model key to the core" \
  bash -c "! grep -q stub-key /proc/$spine_pid/environ 2>/dev/null"

# --- cron, seeded and visible as a query ----------------------------------------
crons="$("$root/bin/crons" 2>&1)"
check "bin/crons lists the seeded heartbeat" grep -q 'teeny.heartbeat' <<<"$crons"
check "bin/crons shows it as a shell job" grep -Eq 'teeny.heartbeat +shell' <<<"$crons"
check "bin/crons also shows the core's own jobs" grep -q 'memory_background' <<<"$crons"
check "bin/crons shows the job's timezone" grep -q 'teeny.heartbeat.*UTC' <<<"$crons"
check "bin/crons refuses a name that is not a job name" bash -c "! '$root/bin/crons' runs \"x'; drop table docs; --\" 2>/dev/null"

check "records the core URL for cron jobs" grep -q ":$core_port" "$root/state/core.url"
id="$("$root/bin/oh" cron_list | jq -r '.[] | select(.name == "teeny.heartbeat") | .id')"
check "bin/oh reads the job back over RPC" test -n "$id"
"$root/bin/oh" cron_run "$(jq -cn --arg id "$id" '{job_id: $id}')" >/dev/null
for _ in $(seq 1 30); do [ -s "$root/state/heartbeat" ] && break; sleep 1; done
check "the heartbeat job runs and writes state/heartbeat" test -s "$root/state/heartbeat"
sleep 1
check "bin/crons runs shows the run ok" grep -q ' ok ' <<<"$("$root/bin/crons" runs heartbeat 2>&1)"

TEENY_OH="$root/bin/oh" "$root/bin/seed-cron" 2>"$scratch/reseed.log"
check "re-seeding an unchanged file changes nothing" grep -q '0 change(s) applied' "$scratch/reseed.log"
sed -i.bak 's#\*/5 \* \* \* \*#*/10 * * * *#' "$root/brain/cron.toml"
"$root/bin/seed-cron" 2>"$scratch/reseed.log"
check "a changed schedule is updated on the real core" \
  test "$("$root/bin/oh" cron_list | jq -r '.[] | select(.name == "teeny.heartbeat") | .schedule.expr')" = "*/10 * * * *"

# --- bin/oh errors, bin/ask a whole turn ----------------------------------------
if "$root/bin/oh" cron_remove '{"job_id":"no-such-job"}' >/dev/null 2>"$scratch/err.log" ||
   ! grep -q 'openhuman.cron_remove' "$scratch/err.log"; then
  # A core that treats removing an unknown id as a no-op is fine too; what must
  # not happen is an error swallowed into a zero exit.
  if grep -q 'openhuman.cron_remove' "$scratch/err.log"; then fail "bin/oh exits non-zero on an RPC error"; else pass "bin/oh: removing an unknown id is a no-op on this core"; fi
else
  pass "bin/oh exits non-zero on an RPC error and names the method"
fi
if OPENHUMAN_CORE_TOKEN=wrong "$root/bin/oh" cron_list >/dev/null 2>&1; then
  fail "bin/oh is refused with the wrong token"
else
  pass "bin/oh is refused with the wrong token"
fi

answer="$("$root/bin/ask" "please say hello" 2>"$scratch/ask.log" || true)"
check "bin/ask completes a turn through the configured model" grep -q 'teeny spine says hello' <<<"$answer"
check "the turn went to the configured endpoint" grep -q 'chat/completions' "$scratch/model-calls.log"
# The core budgets a local-openai model it knows nothing about at a few
# thousand tokens; its own system prompt is bigger, so the trim drops the
# user's message and the model answers a turn with nothing in it.
check "the model is asked what bin/ask asked" grep -q 'please say hello' "$scratch/model-asked.log"
# The first turn writes the core's persona defaults beside SOUL.md.
check "the core writes its persona defaults into brain/ on the first turn" test -s "$root/brain/IDENTITY.md"
# --- the doctor and bin/act ---------------------------------------------------
check "the spine ran the doctor before serving, and it passed" \
  test "$(jq -r .ok "$root/state/doctor.json" 2>/dev/null)" = true
if "$root/bin/doctor" --live >/dev/null 2>"$scratch/doctor.log"; then
  pass "bin/doctor --live passes on the running spine"
else
  fail "bin/doctor --live passes on the running spine"
  grep '^FAIL' "$scratch/doctor.log" >&2 || true
fi
cat >"$root/brain/playbooks/hello.md" <<'MD'
+++
description = "say hello, for the live test"
output = "none"
+++
Say hello.
MD
acted="$("$root/bin/act" hello 2>"$scratch/act.log" || true)"
check "bin/act runs a playbook through the act agent" \
  test "$(jq -r '.actions[0].result.noted' <<<"$acted" 2>/dev/null)" = "hello from a playbook"
# The stub cannot see this: a local model without native tool calls gets its
# tools written into the prompt, not in the request's `tools`. The core logs
# what it offered each agent; the act agent's turn must offer nothing.
agent_tools() { # ID: what the core offered that agent's first turn
  awk -v id="building session agent id=$1 " 'index($0, id) {found = 1} found && /tool spec filter:/ {print; exit}' "$scratch/spine.log"
}
check "the core loaded the act agent from brain/agents/" grep -q 'building session agent id=act ' "$scratch/spine.log"
check "the act agent's turn offers the model no tools" grep -q 'visible=0 ' <<<"$(agent_tools act)"
check "bin/act records the run" test -s "$root/state/act/hello/runs.jsonl"
if [ "$(jq -r '.actions[0].result.noted' <<<"$acted" 2>/dev/null)" != "hello from a playbook" ]; then
  { echo "bin/act said: $acted"; tail -n 5 "$scratch/act.log"; tail -c 1500 "$root/state/act/hello/ask.err" 2>/dev/null; } >&2
fi

# bin/eval: one task drafted as the act agent, then judged as the judge agent.
evals="$scratch/evals"
"$root/bin/eval" run --samples 1 --only morning-post "$evals/base" >/dev/null 2>"$scratch/eval.log" || true
check "bin/eval drafts a task through the act agent" \
  test "$(jq -r .draft.text "$evals/base/drafts.jsonl" 2>/dev/null)" = "gm from an eval"
cp -R "$evals/base" "$evals/cand"
code=0
"$root/bin/eval" compare --min-pairs 1 "$evals/base" "$evals/cand" "$evals/out" >/dev/null 2>>"$scratch/eval.log" || code=$?
check "bin/eval judges the pair through the judge agent" \
  test "$(jq -r '.pairs[0].notes[0]' "$evals/out/report.json" 2>/dev/null)" = "the stub judged it"
check "a tie promotes nothing" test "$code" = 2
check "the core loaded the judge agent from brain/agents/" grep -q 'building session agent id=judge ' "$scratch/spine.log"
check "the judge agent's turn offers the model no tools" grep -q 'visible=0 ' <<<"$(agent_tools judge)"
if [ "$(jq -r '.pairs[0].notes[0]' "$evals/out/report.json" 2>/dev/null)" != "the stub judged it" ]; then
  { tail -n 5 "$scratch/eval.log"; cat "$evals/base/drafts.jsonl" "$evals/out/judged.jsonl" 2>/dev/null; } >&2
fi

# --- the scheduled agents: seeded as agent jobs, run as CronActions ------------
jobs_json="$("$root/bin/oh" cron_list 2>/dev/null || echo '[]')"
builder_id="$(jq -r '[.[] | select(.name == "teeny.builder")][0].id // empty' <<<"$jobs_json")"
check "seeds the builder as an agent job" \
  test "$(jq -r '[.[] | select(.name == "teeny.builder")][0] | "\(.job_type) \(.agent_id)"' <<<"$jobs_json")" = "agent builder"
check "seeds the operator as an agent job" \
  test "$(jq -r '[.[] | select(.name == "teeny.operator")][0] | "\(.job_type) \(.agent_id)"' <<<"$jobs_json")" = "agent operator"
check "seeds build-ship as a shell job" \
  test "$(jq -r '[.[] | select(.name == "teeny.build-ship")][0].job_type' <<<"$jobs_json")" = shell
check "the core kept the cron shell timeout" python3 -c 'import sys, tomllib
cfg = tomllib.load(open(sys.argv[1], "rb"))
assert cfg["cron"]["shell_timeout_secs"] == 1800, cfg.get("cron")
assert cfg["autonomy"]["enabled"] and cfg["autonomy"]["workspace_only"]
assert cfg["browser"]["unattended_actions"]' "$root/.openhuman/config.toml"
# The builder's desk, laid out the way bin/build-ship does it every 30 minutes
# (DRY_RUN is on: nothing ships).
"$root/bin/build-ship" >"$scratch/build-ship.log" 2>&1 || true
check "bin/build-ship lays the builder's desk out" \
  test "$(jq -r .job.kind "$root/state/workbench/desk/build.json" 2>/dev/null)" = daily
# One builder run now, against the stub model (which answers without calling
# a tool): the core builds the builder from brain/agents/ with its named tools
# and runs it with the scheduled-actions opt-in.
builder_request() { grep 'building something small in public' "$scratch/model-bodies.jsonl" | tail -n 1; }
if [ -n "$builder_id" ]; then
  "$root/bin/oh" cron_run "$(jq -cn --arg id "$builder_id" '{job_id: $id}')" >/dev/null 2>&1 || true
  for _ in $(seq 1 60); do
    grep -q 'tool spec filter:' <<<"$(agent_tools builder)" && [ -n "$(builder_request)" ] && break
    sleep 1
  done
fi
check "the core loaded the builder agent from brain/agents/" grep -q 'building session agent id=builder ' "$scratch/spine.log"
check "the builder run opted into scheduled actions" grep -q 'agent opted into scheduled actions' "$scratch/spine.log"
offered() { agent_tools "$1" | sed 's/.*names=\[//; s/\].*//' | tr ',' '\n' | tr -d ' '; }
check "the builder's turn offers the shell" grep -qx shell <<<"$(offered builder)"
check "the builder's turn offers the file tools" grep -qx file_write <<<"$(offered builder)"
check "the builder's turn offers no browser" bash -c "! grep -qx browser <<<\"\$1\"" _ "$(offered builder)"
check "the builder is told where its desk is" grep -q 'state/workbench/desk/build.json' <<<"$(builder_request)"
printf 'info the builder was offered: %s\n' "$(offered builder | tr '\n' ' ')"
# And one operator run: the browser and its desk's two file tools, no shell.
operator_id="$(jq -r '[.[] | select(.name == "teeny.operator")][0].id // empty' <<<"$jobs_json")"
if [ -n "$operator_id" ]; then
  "$root/bin/oh" cron_run "$(jq -cn --arg id "$operator_id" '{job_id: $id}')" >/dev/null 2>&1 || true
  for _ in $(seq 1 60); do
    grep -q 'tool spec filter:' <<<"$(agent_tools operator)" && break
    sleep 1
  done
fi
check "the core loaded the operator agent from brain/agents/" grep -q 'building session agent id=operator ' "$scratch/spine.log"
check "the operator's turn offers its desk's file tools" grep -qx file_write <<<"$(offered operator)"
check "the operator's turn offers no shell" bash -c "! grep -qx shell <<<\"\$1\"" _ "$(offered operator)"
printf 'info the operator was offered: %s\n' "$(offered operator | tr '\n' ' ')"

registered_window() {
  python3 -c 'import sys, tomllib
cfg = tomllib.load(open(sys.argv[1], "rb"))
print(next((e.get("context_window") for e in cfg.get("model_registry", []) if e.get("id") == "stub"), None))' "$root/.openhuman/config.toml"
}
check "the model's window comes from the endpoint" test "$(registered_window)" = 128000

# --- the keyring survives a restart ---------------------------------------------
kill -TERM "$spine_pid"
wait "$spine_pid" 2>/dev/null || true
spine_pid=""
check "the spine stops on SIGTERM" bash -c "! curl -fsS -o /dev/null http://127.0.0.1:$core_port/health"
: >"$scratch/model-calls.log"
# A second boot with the key gone from spine.env must still reach the model.
sed -i.bak '/^LADDER_API_KEY=/d' "$root/secrets/spine.env"
"$root/bin/spine" >"$scratch/spine2.log" 2>&1 &
spine_pid=$!
for _ in $(seq 1 120); do grep -q '\[spine\] ready' "$scratch/spine2.log" && break; sleep 1; done
answer="$("$root/bin/ask" "say hello" 2>/dev/null || true)"
check "the model key survives a restart in the core's config" grep -q 'teeny spine says hello' <<<"$answer"

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s); spine log:\n' "$failures" >&2
  tail -40 "$scratch/spine.log" >&2
  exit 1
fi
echo "all passed"
