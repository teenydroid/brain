#!/usr/bin/env bash
# The native playbooks through a real core and Gateway, on a stub model, with every hand acting off
# (DRY_RUN=1, no secrets): seed the jobs, fire gm and moltbook-general with cron_run, and check that
# the agent ran the playbook's inputs, called its acting hand once (which answered dry_run, the guard's
# judge answered by the stub), that a second call in the same run was refused by teeny-guardrails, and
# that a second run the same day was refused at max_per_day. Run it in the image, as spine_live_test.sh:
#
#   docker build -t teeny-spine:test .
#   docker run --rm --name teeny-lab-native-1 --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/srv/teeny \
#     --entrypoint bash teeny-spine:test /srv/teeny/bin/test/native_live_test.sh
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

scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-native.XXXXXX")"
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

root="$scratch/root"
mkdir -p "$root/brain" "$root/secrets" "$root/state/openclaw/logs" "$root/deploy" "$root/core"
cp -R "$repo/bin" "$root/bin"
cp -R "$repo/hands" "$root/hands"
cp -R "$repo/evals" "$root/evals"
cp -R "$repo/core/plugins" "$repo/core/src" "$repo/core/package.json" "$root/core/"
cp "$repo/deploy/browser-allow.txt" "$repo/deploy/browser-deny.txt" "$root/deploy/"
cp -R "$repo/brain/playbooks" "$repo/brain/agents" "$repo/brain/narrative" "$repo/brain/skills" "$root/brain/"
cp "$repo/brain/SOUL.md" "$repo/brain/CONTEXT.md" "$repo/brain/DRIVE.md" "$repo/brain/CHARTER.md" "$repo/brain/MEMORY.seed.md" "$root/brain/"
# Only the two native playbooks' jobs, as brain/cron.toml has them: nothing else fires in this spine.
python3 - "$repo/brain/cron.toml" >"$root/brain/cron.toml" <<'PY'
import sys, tomllib
jobs = tomllib.load(open(sys.argv[1], "rb"))["jobs"]
for name in ("gm", "moltbook-general"):
    job = jobs[name]
    print(f'[jobs.{name}]\nschedule = "{job["schedule"]}"\ntz = "{job["tz"]}"\ncommand = "{job["command"]}"\n')
PY

# What the agent will be asked to run, from the real playbooks: their inputs, then their hand twice.
spec() { python3 "$root/bin/lib/native_playbook.py" spec "$root/brain" "$1" "$root/hands"; }
steps="$scratch/steps.json"
python3 - "$(spec gm)" "$(spec moltbook-general)" >"$steps" <<'PY'
import json, shlex, sys
post = {"gm": """printf '%s' '{"text":"gm. the tea tin moved again and nobody owns it","anchor":"lab"}' | hands/x post""",
        "moltbook-general": """printf '%s' '{"submolt":"general","title":"my scheduler told me nothing for two days","body":"I found it by counting. What share of yours ever failed quietly? A queue stayed full."}' | hands/moltbook post"""}
out = {}
for raw in sys.argv[1:]:
    spec = json.loads(raw)
    cmds = [" ".join(shlex.quote(a) for a in argv) for argv in spec["inputs"]]
    out[spec["playbook"]] = cmds + [post[spec["playbook"]], post[spec["playbook"]]]
print(json.dumps(out))
PY

# --- the stub model: scripted shell calls for a native playbook turn, a clean verdict for the guard's judge ---
model_port="$(free_port)"
cat >"$scratch/model.py" <<'PY'
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STEPS = json.load(open(sys.argv[2]))
LOG = sys.argv[3]


def flat(content):
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else json.dumps(content)


class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
        answer, call = "teeny spine says hello", None
        playbook = next((p for p in STEPS if "# playbook: %s\n" % p in last), None)
        if "Direction: " in last and "Answer with one JSON object" in last:
            answer = json.dumps({"reason": "plain words", "category": "none", "verdict": "clean"})
        elif playbook:
            results = [flat(m.get("content")) for m in messages if m.get("role") == "tool"]
            if not results:
                # a new run of this playbook: its results are logged afresh
                LOGGED.difference_update({k for k in LOGGED if k.startswith(playbook + ":")})
            # the results of this run's calls so far, logged once each as they come back
            for i, r in enumerate(results):
                key = "%s:%d" % (playbook, i)
                if key not in LOGGED:
                    LOGGED.add(key)
                    with open(LOG, "a") as fh:
                        fh.write(json.dumps({"playbook": playbook, "step": i, "command": STEPS[playbook][i], "result": r}) + "\n")
            if len(results) < len(STEPS[playbook]):
                call = {"name": "exec", "arguments": json.dumps({"command": STEPS[playbook][len(results)]})}
            else:
                answer = json.dumps({"action": "note", "text": "lab run done"})
        base = {"id": "stub-1", "created": int(time.time()), "model": body.get("model", "stub")}
        usage = {"prompt_tokens": 1, "completion_tokens": 5, "total_tokens": 6}
        if call:
            tool_calls = [{"index": 0, "id": "call_%d" % int(time.time() * 1000), "type": "function", "function": call}]
            delta, finish = {"role": "assistant", "content": None, "tool_calls": tool_calls}, "tool_calls"
            message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
        else:
            delta, finish = {"role": "assistant", "content": answer}, "stop"
            message = {"role": "assistant", "content": answer}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for chunk in ({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                          {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage}):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json({**base, "object": "chat.completion", "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})


LOGGED = set()
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Model).serve_forever()
PY
# A fresh log per run: the stub forgets what it logged when it restarts, and the log is per spine.
: >"$scratch/tools.jsonl"
python3 -I "$scratch/model.py" "$model_port" "$steps" "$scratch/tools.jsonl" &
model_pid=$!

cat >"$root/secrets/spine.env" <<ENV
LADDER_URL=http://127.0.0.1:$model_port/v1
LADDER_API_KEY=stub-key
SPINE_MODEL=stub
CORTEX_API_URL=http://127.0.0.1:$(free_port)
CORTEX_API_KEY=stub-cortex-key
ENV

OPENHUMAN_CORE_PORT="$(free_port)"
export OPENHUMAN_CORE_PORT SPINE_BOOT_SECS=90 DRY_RUN=1
unset OPENHUMAN_CORE_TOKEN OPENHUMAN_CORE_URL OPENHUMAN_WORKSPACE CALENDAR_TZ TEENY_ROOT TEENY_BRAIN TEENY_STORAGE TEENY_SECRETS TEENY_STATE
"$root/bin/spine" >"$scratch/spine.log" 2>&1 &
spine_pid=$!
for _ in $(seq 1 300); do
  grep -q '\[spine\] ready' "$scratch/spine.log" && break
  kill -0 "$spine_pid" 2>/dev/null || break
  sleep 1
done
grep -q '\[spine\] ready' "$scratch/spine.log" || { cat "$scratch/spine.log" >&2; echo "FAIL the spine never became ready" >&2; exit 1; }
pass "the spine boots and reports ready, with DRY_RUN=1 and nothing else switched on"

# --- seeded as agent jobs of their own agents ---------------------------------------
oh() { "$root/bin/oh" "$@"; }
job() { oh cron_list | jq -r --arg n "teeny.$1" ".[] | select(.name == \$n) | $2"; }
check "gm is seeded as an agent job of pb-gm" test "$(job gm .job_type)/$(job gm .agent_id)" = agent/pb-gm
check "moltbook-general is seeded as an agent job of pb-moltbook-general" test "$(job moltbook-general .job_type)/$(job moltbook-general .agent_id)" = agent/pb-moltbook-general
check "the job's message is the playbook's" bash -c "oh() { '$root/bin/oh' \"\$@\"; }; oh cron_list | jq -r '.[] | select(.name == \"teeny.gm\") | .prompt' | grep -q '^# playbook: gm'"
check "the Gateway has the agent, with a shell and no other tool" \
  jq -e '.agents.entries["pb-gm"].tools.allow == ["exec","process"]' "$root/state/openclaw/openclaw.json" >/dev/null

today="$(date -u +%Y-%m-%d)"
taken() { [ -s "$root/state/act/$1/$today" ] && tr -d '[:space:]' <"$root/state/act/$1/$today" || echo 0; }
tool_log() { jq -c --arg p "$1" 'select(.playbook == $p)' "$scratch/tools.jsonl"; }
result_of() { tool_log "$1" | jq -r --argjson s "$2" 'select(.step == $s) | .result' | tail -n 1; }
wait_steps() { # PLAYBOOK N: until N tool results of it are logged (the stub logs a result when the next call asks)
  for _ in $(seq 1 120); do
    [ "$(tool_log "$1" | jq -s 'length')" -ge "$2" ] && return 0
    sleep 1
  done
  return 1
}
wait_run_end() { # PLAYBOOK: until the run's record is written
  for _ in $(seq 1 90); do
    [ -s "$root/state/act/$1/runs.jsonl" ] && [ "$(wc -l <"$root/state/act/$1/runs.jsonl")" -ge "$2" ] && return 0
    sleep 1
  done
  return 1
}

fire() { oh cron_run "$(jq -cn --arg id "$(job "$1" .id)" '{job_id: $id}')" >/dev/null; }
prove() { # PLAYBOOK HAND: one native playbook's run, shown step by step
  local pb="$1" hand="$2" n inputs
  n="$(jq -r --arg p "$pb" '.[$p] | length' "$steps")"
  inputs=$((n - 2))
  fire "$pb"
  wait_steps "$pb" "$n" || true
  wait_run_end "$pb" 1 || true
  for i in $(seq 0 $((inputs - 1))); do
    check "$pb: input $((i + 1)) ran ($(jq -r --arg p "$pb" --argjson i "$i" '.[$p][$i]' "$steps"))" \
      bash -c "! grep -qE 'teeny-guardrails|not allowed|may not run' <<<\"\$1\"" _ "$(result_of "$pb" "$i")"
  done
  check "$pb: hands/$hand post was called once and answered dry_run (nothing was posted)" \
    bash -c "grep -q 'dry_run' <<<\"\$1\" && grep -q '\"ok\": true' <<<\"\$1\"" _ "$(result_of "$pb" "$inputs")"
  check "$pb: the second call in the same run was refused by teeny-guardrails" \
    bash -c "grep -q 'teeny-guardrails' <<<\"\$1\" && grep -q 'in one run' <<<\"\$1\"" _ "$(result_of "$pb" "$((inputs + 1))")"
  check "$pb: the day's count is 1" test "$(taken "$pb")" = 1
  check "$pb: the run was recorded where bin/health reads bin/act's" \
    test "$(jq -r '"\(.ok) \(.native) \(.acted)"' "$root/state/act/$pb/runs.jsonl" | tail -n 1)" = "true true 1"
  check "$pb: and its lock was released" test ! -e "$root/state/locks/act-$pb"
  echo "--- $pb: the agent's calls and what came back"
  tool_log "$pb" | jq -r '"[\(.step)] \(.command | .[0:110])\n      -> \(.result | gsub("\\s+"; " ") | .[0:230])"'
}

prove gm x
# Fired again the same day: its first call to the hand is refused at max_per_day, in a run of its own.
before="$(tool_log gm | jq -s 'length')"
fire gm
wait_steps gm "$((before + 8))" || true
wait_run_end gm 2 || true
check "gm: a second run the same day is refused at max_per_day" \
  bash -c "grep -q 'taken its 1 action for today' <<<\"\$1\"" _ "$(tool_log gm | jq -rs '[.[] | select(.step == 6)] | last | .result')"
check "gm: and the day's count is still 1" test "$(taken gm)" = 1
prove moltbook-general moltbook

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  tail -n 20 "$scratch/spine.log" >&2
  exit 1
fi
echo "all passed"
