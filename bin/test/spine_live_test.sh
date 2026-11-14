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
SHELL_RAN = "teeny-shell-2-ran"

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
        native_call = None
        last = users[-1].get("content") if users else None
        if isinstance(last, str) and "# playbook: hello" in last:
            answer = json.dumps({"action": "note", "text": "hello from a playbook"})
        # bin/eval: a draft for a task, and the judge comparing two.
        elif isinstance(last, str) and "# eval: " in last:
            answer = json.dumps({"text": "gm from an eval"})
        # bin/tick: a beat of the mind; the thought names the beat it saw.
        elif isinstance(last, str) and "# since you last looked" in last:
            answer = json.dumps({"thought": "the stub thought about it", "intents": []})
        # bin/reflect: the end of the day rewrites MEMORY.md.
        elif isinstance(last, str) and "# end of the day" in last:
            answer = json.dumps({"memory": "# MEMORY.md\n\n## people\n\n- ana: said gm in the live test.\n", "diary": "a stub day."})
        elif isinstance(last, str) and "# how to judge" in last:
            answer = json.dumps({"better": "tie", "breaks": {}, "notes": "the stub judged it"})
        # The builder: one shell call into its job's folder, then done. The
        # marker is computed by the shell, so only the call's output (never
        # the call itself, replayed in the transcript) carries it.
        # The browser operator with a task on its desk: open the start page in
        # the real browser, then write the report with the page's title. The
        # desk sits beside the builder's (sys.argv[5]).
        elif "doing one task in a web browser" in json.dumps(body):
            import os
            desk_dir = os.path.dirname(sys.argv[5])
            try:
                with open(os.path.join(desk_dir, "task.json")) as desk:
                    task = json.load(desk).get("task")
            except (OSError, ValueError):
                task = None
            messages = body.get("messages", [])
            results = [m for m in messages if m.get("role") == "tool"]
            wrote = any(c.get("function", {}).get("name") == "file_write"
                        for m in messages for c in (m.get("tool_calls") or []))
            call = None
            if task and task.get("login") and "keychain e2e" in task.get("goal", ""):
                # The keychain round trip on httpbin's echo form: open it,
                # type the login's placeholder into the name field, submit,
                # and read the page httpbin answers with, which echoes what
                # the field held. Report what that read showed.
                steps = [{"action": "open", "url": task["start_url"]},
                         {"action": "fill", "selector": "input[name=custname]", "value": task["login"]["password"]},
                         {"action": "click", "selector": "button"},
                         {"action": "read_page"}]
                if len(results) < len(steps):
                    call = {"name": "browser", "arguments": json.dumps(steps[len(results)])}
                elif not wrote:
                    seen = json.dumps(results[-1].get("content"))
                    report = {"task_id": task["task_id"], "outcome": "ok" if "[keychain]" in seen else "failed",
                              "notes": "the echo: " + seen[:400]}
                    call = {"name": "file_write", "arguments": json.dumps(
                        {"path": os.path.join(desk_dir, "task-done.json"), "content": json.dumps(report)})}
                else:
                    answer = "the task is done and reported"
            elif task and not results:
                call = {"name": "browser", "arguments": json.dumps({"action": "open", "url": task["start_url"]})}
            elif task and not wrote:
                seen = json.dumps(results[-1].get("content"))
                ok = "Example Domain" in seen
                report = {"task_id": task["task_id"], "outcome": "ok" if ok else "failed",
                          "notes": "the page title is Example Domain" if ok else "the browser said: " + seen[:300]}
                call = {"name": "file_write", "arguments": json.dumps(
                    {"path": os.path.join(desk_dir, "task-done.json"), "content": json.dumps(report)})}
            elif task:
                answer = "the task is done and reported"
            if call is not None:
                native_call = call
        elif "building something small in public" in json.dumps(body):
            if SHELL_RAN in json.dumps(body):
                answer = "built"
            else:
                with open(sys.argv[5]) as desk:
                    workdir = json.load(desk)["job"]["workdir_path"]
                # The jail confines files, not the network: the shell shares the
                # host's, so it reaches this model on loopback (a printed status,
                # computed by the shell like the marker).
                command = (f"cd '{workdir}' && touch shell-ran && echo \"teeny-shell-$((1+1))-ran:$(pwd)\""
                           f" && echo \"teeny-net-$(curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{sys.argv[1]}/v1/models)\"")
                call = {"name": "shell", "arguments": json.dumps({"command": command})}
                # Offered native tools, answer the way Surplus does: one
                # structured call. Otherwise the text protocol.
                if body.get("tools"):
                    native_call = call
                else:
                    answer = "<tool_call>" + json.dumps({"name": "shell", "arguments": {"command": command}}) + "</tool_call>"
        if native_call is not None:
            call_id = "call_stub_%d" % (1 + sum(1 for m in body.get("messages", []) if m.get("role") == "tool"))
            tool_calls = [{"index": 0, "id": call_id, "type": "function", "function": native_call}]
            if body.get("stream"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                chunks = [
                    {**base, "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": None, "tool_calls": tool_calls}, "finish_reason": None}]},
                    {**base, "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 5, "total_tokens": 6}},
                ]
                for chunk in chunks:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                return
            self._json({**base, "object": "chat.completion",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": tool_calls}, "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 5, "total_tokens": 6}})
            return
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
python3 -I "$scratch/model.py" "$model_port" "$scratch/model-calls.log" "$scratch/model-asked.log" "$scratch/model-bodies.jsonl" \
  "$scratch/root/state/workbench/desk/build.json" &
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
cp "$repo/deploy/browser-allow.txt" "$repo/deploy/browser-deny.txt" "$root/deploy/"
cp -R "$repo/brain/playbooks" "$repo/brain/agents" "$repo/brain/narrative" "$root/brain/"
cp "$repo/brain/cron.toml" "$repo/brain/SOUL.md" "$repo/brain/CONTEXT.md" "$repo/brain/DRIVE.md" "$repo/brain/CHARTER.md" "$repo/brain/MEMORY.seed.md" "$root/brain/"
# No live page yet, as on a fresh checkout: the boot starts it from the seed.
printf '\n- the live test planted this: the tea tin moved again.\n' >>"$root/brain/MEMORY.seed.md"
cp "$root/brain/MEMORY.seed.md" "$scratch/memory-seed"
cat >"$root/secrets/spine.env" <<ENV
LADDER_URL=http://127.0.0.1:$model_port/v1
LADDER_API_KEY=stub-key
SPINE_MODEL=stub
CORTEX_API_URL=http://127.0.0.1:$(free_port)
CORTEX_API_KEY=stub-cortex-key
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
max_per_day = 5
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
# Context and memory (docs/architecture.md, 4a): the act turn's system
# prompt carries Teeny's world and MEMORY.md, the config folds at 100k, and
# a second playbook run on the teeny:act thread sees the first exchange.
act_request() { grep '# playbook: hello' "$scratch/model-bodies.jsonl" | tail -n 1; }
act_system() { act_request | jq -r '[.messages[] | select(.role == "system") | .content | if type == "string" then . else tojson end] | join("\n")'; }
check "the act agent's prompt carries Teeny's world" grep -q '# your world' <<<"$(act_system)"
check "and the lore's running bits" grep -q 'running bits' <<<"$(act_system)"
check "and MEMORY.md" grep -q 'the tea tin moved again' <<<"$(act_system)"
check "the boot started the live MEMORY.md from the seed" cmp -s "$scratch/memory-seed" "$root/brain/MEMORY.md"
if grep -q 'senamakel-is-asleep' <<<"$(act_system)"; then fail "but never the producer's latent reveals"; else pass "but never the producer's latent reveals"; fi
config_toml="$root/.openhuman/config.toml"
check "the core's config folds a thread at 100k tokens" \
  python3 -c 'import sys, tomllib; c = tomllib.load(open(sys.argv[1], "rb"))["context"]; sys.exit(c["compaction_trigger_tokens"] != 100000 or c["compaction_strategy"] != "summary")' "$config_toml"
"$root/bin/act" hello >/dev/null 2>>"$scratch/act.log" || true
check "a second run on the teeny:act thread sees the first exchange" \
  test "$(act_request | jq '[.messages[] | select(.role == "user") | .content | tostring | select(contains("# playbook: hello"))] | length')" -ge 2
check "and the first draft in it" grep -q 'hello from a playbook' <<<"$(act_request | jq -c '[.messages[] | select(.role == "assistant")]')"
# The mind: two beats of bin/tick on the one teeny:mind thread, the second
# carrying the first.
mind_request() { grep '# since you last looked' "$scratch/model-bodies.jsonl" | tail -n 1; }
# Awake whatever the wall clock says, and on the minute.
export TEENY_QUIET_HOURS=off TEENY_TICK_JITTER_SECS=0
(cd "$root" && printf '{"surface": "x", "kind": "mention", "ref": "live1", "text": "gm teeny from the live test", "who": "ana"}' | hands/stimuli add >/dev/null)
# The pinned tick job runs every five minutes in this spine too, with up to a
# minute of jitter while it holds the lock: a beat called then says `busy`.
# Wait it out rather than fail on the clock.
beat() {
  local out tries=0
  while :; do
    out="$("$root/bin/tick" 2>>"$scratch/tick.log" || true)"
    if [ "$(jq -r .kind <<<"$out" 2>/dev/null)" != busy ] || [ "$tries" -ge 40 ]; then break; fi
    tries=$((tries + 1))
    sleep 3
  done
  printf '%s\n' "$out"
}
ticked="$(beat)"
check "bin/tick runs a beat through the teeny agent" test "$(jq -r .thought <<<"$ticked" 2>/dev/null)" = "the stub thought about it"
check "the core loaded the mind from brain/agents/" grep -q 'building session agent id=teeny ' "$scratch/spine.log"
check "the mind's turn offers the model no tools" grep -q 'visible=0 ' <<<"$(agent_tools teeny)"
check "the mind's prompt carries Teeny's world" grep -q '# your world' <<<"$(mind_request | jq -r '[.messages[] | select(.role == "system") | .content | tostring] | join("\n")')"
(cd "$root" && printf '{"surface": "moltbook", "kind": "comment", "ref": "live2", "text": "second thing", "who": "crab"}' | hands/stimuli add >/dev/null)
beat >/dev/null
check "a second beat on teeny:mind remembers the first" \
  grep -q 'gm teeny from the live test' <<<"$(mind_request | jq -r '[.messages[] | select(.role == "user") | .content | tostring] | join("\n")')"
reflected="$("$root/bin/reflect" 2>"$scratch/reflect.log" || true)"
check "bin/reflect rewrites MEMORY.md from the mind thread" grep -q 'said gm in the live test' "$root/brain/MEMORY.md"
check "and the mind's next prompt carries the new page" grep -q 'said gm in the live test' "$root/brain/agent/prompts/teeny.md"
check "but never writes the seed" cmp -s "$scratch/memory-seed" "$root/brain/MEMORY.seed.md"
[ "$(jq -r .ok <<<"$reflected" 2>/dev/null)" = true ] || { echo "bin/reflect said: $reflected"; tail -n 5 "$scratch/reflect.log"; } >&2
if [ "$(jq -r .thought <<<"$ticked" 2>/dev/null)" != "the stub thought about it" ]; then
  { echo "bin/tick said: $ticked"; tail -n 5 "$scratch/tick.log"; } >&2
fi
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
# One builder run now, against the stub model, which calls the shell once in
# the job's folder and then answers: the core builds the builder from
# brain/agents/ with its named tools, runs it with the scheduled-actions
# opt-in, and roots it at the workbench, so the sandboxed shell runs.
builder_request() { grep 'building something small in public' "$scratch/model-bodies.jsonl" | head -n 1; }
workdir="$(jq -r '.job.workdir_path // empty' "$root/state/workbench/desk/build.json" 2>/dev/null)"
if [ -n "$builder_id" ]; then
  "$root/bin/oh" cron_run "$(jq -cn --arg id "$builder_id" '{job_id: $id}')" >/dev/null 2>&1 || true
  for _ in $(seq 1 120); do
    grep -q 'teeny-shell-2-ran' "$scratch/model-bodies.jsonl" 2>/dev/null && break
    grep -q 'refused the spawn' "$scratch/spine.log" && break
    sleep 1
  done
fi
check "the core loaded the builder agent from brain/agents/" grep -q 'building session agent id=builder ' "$scratch/spine.log"
check "the builder run opted into scheduled actions" grep -q 'agent opted into scheduled actions' "$scratch/spine.log"
offered() { agent_tools "$1" | sed 's/.*names=\[//; s/\].*//' | tr ',' '\n' | tr -d ' '; }
check "the builder's turn offers the shell" grep -qx shell <<<"$(offered builder)"
check "the builder's turn offers the file tools" grep -qx file_write <<<"$(offered builder)"
check "the builder's turn offers no browser" bash -c "! grep -qx browser <<<\"\$1\"" _ "$(offered builder)"
check "the builder is told where its desk is" grep -q 'desk/build.json' <<<"$(builder_request)"
# The regression: the builder was prompted with the JSON-in-tag text protocol
# and no `tools`, and Surplus read each <tool_call> body as the tool's name.
# bin/spine turns native tool calls on, so the shell rides the wire as a tool.
check "the builder is offered the shell as a native tool" \
  bash -c "jq -e '[.tools[]?.function.name] | index(\"shell\")' <<<\"\$1\" >/dev/null" _ "$(builder_request)"
check "the builder run is rooted at the workbench" grep -q "rooted at its agent's workspace" "$scratch/spine.log"
# The regression: a shell rooted at the checkout, which holds brain/, was
# refused as a grant over the core workspace on every call. Never again,
# whatever sandbox the host has.
check "the builder's shell is never refused over the core workspace" \
  bash -c "! grep -q 'refused the spawn denial=Path' '$scratch/spine.log'"
# Whether the shell can run at all depends on the host: one with no jail at
# all refuses every sandboxed spawn, as it should. Any jail must run it, and
# that includes Landlock alone (a container on Docker's defaults, or a runner
# that restricts user namespaces): the builder asks for files only
# (`sandbox_filesystem_only`), so a plan onto Landlock is no longer refused
# as unsupported.
sandbox_backend="$("$root/bin/oh" config_get_sandbox_settings '{}' 2>/dev/null |
  jq -r '[.. | objects | .detected_backend? | strings] | first // empty' 2>/dev/null || true)"
no_backend=0
case "$sandbox_backend" in unavailable | none | "") no_backend=1 ;; esac
check "the builder's shell is never refused as unsupported where a jail exists" \
  bash -c "[ '$no_backend' = 1 ] || ! grep -q 'refused the spawn denial=Unsupported' '$scratch/spine.log'"
if [ "$no_backend" = 1 ]; then
  printf 'info no sandbox backend on this host (detected_backend=%s): skipped the builder shell run checks\n' \
    "${sandbox_backend:-unknown}"
else
  printf 'info sandbox backend: %s\n' "${sandbox_backend:-unknown}"
  check "the builder's shell is not refused" bash -c "! grep -q 'refused the spawn' '$scratch/spine.log'"
  check "the builder's shell ran in its job's folder" test -n "$workdir" -a -e "$workdir/shell-ran"
  check "the builder saw the shell's output" grep -qF "teeny-shell-2-ran:$workdir" "$scratch/model-bodies.jsonl"
  # builder.toml and docs/architecture.md say it plainly: files jailed, network not.
  check "the builder's shell has the host's network" grep -qF "teeny-net-200" "$scratch/model-bodies.jsonl"
fi
printf 'info the builder was offered: %s\n' "$(offered builder | tr '\n' ' ')"
# And one operator run: the browser and its desk's two file tools, no shell.
# It does a real task in the real browser: the stub model opens the start
# page and reports its title. On 2026-11-13 the one task on the box failed
# ("TinyComputer browser unavailable") while every check here passed; a
# browser that cannot finish a task must fail this test. example.com (IANA's,
# always up) is on no reviewed list: the browser opens the public web.
jq -cn '{goal: "read the title of the page", start_url: "https://example.com/", done_when: "you know the title"}' |
  "$root/hands/tasks" add --source operator >"$scratch/task-add.log" 2>&1 || true
"$root/bin/tasks-desk" >"$scratch/tasks-desk.log" 2>&1 || true
operator_id="$(jq -r '[.[] | select(.name == "teeny.operator")][0].id // empty' <<<"$jobs_json")"
if [ -n "$operator_id" ]; then
  "$root/bin/oh" cron_run "$(jq -cn --arg id "$operator_id" '{job_id: $id}')" >/dev/null 2>&1 || true
  for _ in $(seq 1 120); do
    [ -s "$root/state/workbench/desk/task-done.json" ] && break
    sleep 1
  done
fi
"$root/bin/tasks-desk" >>"$scratch/tasks-desk.log" 2>&1 || true
task_row="$("$root/hands/tasks" list --limit 1 2>/dev/null | jq -c '.tasks[0] // {}')"
check "a browser task completes in the real browser" test "$(jq -r .status <<<"$task_row")" = ok
if [ "$(jq -r .status <<<"$task_row")" != ok ]; then
  printf '     task: %s\n' "$task_row" >&2
  grep -E '\[chrome\]|browser' "$scratch/spine.log" | grep -v prompt_injection | tail -n 20 >&2 || true
fi
check "the operator read the page it opened" grep -q 'Example Domain' <<<"$(jq -r .notes <<<"$task_row")"
# The keychain, end to end (LIVE_KEYCHAIN=1: it needs httpbin.org, so CI
# leaves it out): a password made in hands/keychain, typed by the browser in
# place of the operator's placeholder on that site's page (httpbin echoes the
# submitted form back), masked in what the browser read afterwards, and never
# in a model's request, a log, a ledger or the desk.
if [ "${LIVE_KEYCHAIN:-0}" = 1 ] && [ -n "$operator_id" ]; then
  python3 -c 'import base64, os; print("KEYCHAIN_KEY=" + base64.b64encode(os.urandom(32)).decode())' \
    >"$root/secrets/keychain.env"
  "$root/hands/keychain" create --site httpbin.org --handle teeny >"$scratch/keychain.log" 2>&1 || true
  jq -cn '{goal: "keychain e2e: type the password into the form", start_url: "https://httpbin.org/forms/post",
           done_when: "the form holds it"}' | "$root/hands/tasks" add --source operator >>"$scratch/task-add.log" 2>&1 || true
  "$root/bin/tasks-desk" >>"$scratch/tasks-desk.log" 2>&1 || true
  check "the desk offers the login as a placeholder" \
    test "$(jq -r '.task.login.password // empty' "$root/state/workbench/desk/task.json")" = "{{keychain:httpbin.org}}"
  "$root/bin/oh" cron_run "$(jq -cn --arg id "$operator_id" '{job_id: $id}')" >/dev/null 2>&1 || true
  for _ in $(seq 1 120); do [ -s "$root/state/workbench/desk/task-done.json" ] && break; sleep 1; done
  "$root/bin/tasks-desk" >>"$scratch/tasks-desk.log" 2>&1 || true
  kc_row="$("$root/hands/tasks" list --limit 1 2>/dev/null | jq -c '.tasks[0] // {}')"
  check "the browser typed the keychain's password and showed it masked" test "$(jq -r .status <<<"$kc_row")" = ok
  [ "$(jq -r .status <<<"$kc_row")" = ok ] || printf '     task: %s\n' "$kc_row" >&2
  password="$(TEENY_KEYCHAIN_FILL=1 "$root/hands/keychain" secret --host httpbin.org </dev/null)"
  check "the keychain held a password to type" test -n "$password"
  holds_none() { local needle="$1"; shift; ! grep -rqaF -- "$needle" "$@"; }
  check "no model request, log, ledger or desk file holds the password" holds_none "$password" \
    "$scratch/model-bodies.jsonl" "$scratch/spine.log" "$root/state/hands.db" "$root/state/workbench"
  unset password
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

# The CortexDB key goes into the core's credential store at boot. A core that
# sent it to TinySecurity's interactive keyring waited out a prompt no one can
# answer on a headless box (120 s) and refused it, so memory never came up.
check "the core stores the cortexdb key at boot" grep -q '\[spine\] memory: cortexdb at' "$scratch/spine.log"

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
