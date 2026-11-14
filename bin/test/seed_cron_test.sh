#!/usr/bin/env bash
# Tests for bin/seed-cron against a stand-in for the core's cron RPC that keeps
# its jobs in a JSON file. No OpenHuman needed; bin/test/spine_live_test.sh
# runs the same reconciliation against a real core.
#
#   bin/test/seed_cron_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
seed="$here/../seed-cron"
scratch="$(mktemp -d "${TMPDIR:-/tmp}/teeny-seed-cron.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
check() { local name="$1"; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

# The stand-in: cron_list / cron_add / cron_update / cron_remove / cron_run over
# $FAKE_JOBS, every call appended to $FAKE_CALLS.
fake="$scratch/fake-oh"
cat >"$fake" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
method="$1"; params="${2:-}"; [ -n "$params" ] || params='{}'
printf '%s %s\n' "$method" "$params" >>"$FAKE_CALLS"
jobs="$(cat "$FAKE_JOBS")"
case "$method" in
  cron_list) printf '%s\n' "$jobs" ;;
  cron_add)
    id="job-$(( $(jq length <<<"$jobs") + $(wc -l <"$FAKE_CALLS") ))"
    job="$(jq -c --arg id "$id" '{id: $id, name, job_type, command, prompt, agent_id, session_target, schedule, enabled: true,
      next_run: (now + 3600 | todate)}' <<<"$params")"
    jq -c --argjson j "$job" '. + [$j]' <<<"$jobs" >"$FAKE_JOBS"
    printf '%s\n' "$job" ;;
  cron_update)
    # A patched schedule moves the job's next run to its next slot, as the
    # core's does; here that is simply an hour from now.
    jq -c --argjson p "$params" 'map(if .id == $p.job_id then . + $p.patch +
        (if $p.patch | has("schedule") then {next_run: (now + 3600 | todate)} else {} end) else . end)' \
      <<<"$jobs" >"$FAKE_JOBS"
    echo '{}' ;;
  cron_remove)
    jq -c --argjson p "$params" 'map(select(.id != $p.job_id))' <<<"$jobs" >"$FAKE_JOBS"
    echo '{}' ;;
  cron_run) jq -c '{job_id, status: "queued"}' <<<"$params" ;;
  *) echo "unexpected method $method" >&2; exit 1 ;;
esac
FAKE
chmod +x "$fake"

export TEENY_OH="$fake" FAKE_JOBS="$scratch/jobs.json" FAKE_CALLS="$scratch/calls.log"
unset CALENDAR_TZ
toml="$scratch/cron.toml"
pinned="$scratch/pinned.toml"
: >"$pinned"
mkdir -p "$scratch/playbooks" "$scratch/agents"
printf '+++\ndescription = "x"\noutput = "post"\n+++\ndraft it\n' >"$scratch/playbooks/morning.md"
# Agents a cron job may run: the builder as written for it, and an operator
# that is not (no scheduled_actions), to be refused.
printf 'id = "builder"\nwhen_to_use = "x"\nscheduled_actions = true\n[tools]\nnamed = ["shell"]\n' >"$scratch/agents/builder.toml"
printf 'id = "operator"\nwhen_to_use = "x"\n[tools]\nnamed = ["browser"]\n' >"$scratch/agents/operator.toml"

reset() {
  # The core's own system job is always present and never ours to touch.
  echo '[{"id":"sys-1","name":"memory_background","job_type":"flow","command":"system:memory_background","schedule":{"kind":"every"},"enabled":true}]' >"$FAKE_JOBS"
  : >"$FAKE_CALLS"
}
seed() { "$seed" --file "$toml" --pinned "$pinned" "$@" 2>"$scratch/stderr"; }
jobs() { cat "$FAKE_JOBS"; }
calls() { grep -c "^$1 " "$FAKE_CALLS" || true; }
job_field() { jq -r --arg n "$1" ".[] | select(.name == \$n) | $2" "$FAKE_JOBS"; }

# --- a first seed adds every job, shell-typed and prefixed --------------------
reset
cat >"$toml" <<'TOML'
[jobs.heartbeat]
schedule = "*/5 * * * *"
command = "bin/heartbeat"

[jobs.morning]
schedule = "0 7 * * *"
command = "bin/act morning"
tz = "Asia/Kolkata"
TOML
seed
check "adds both jobs" test "$(calls cron_add)" = 2
check "names them teeny.<name>" test "$(jobs | jq '[.[].name] | sort | join(",")')" = '"memory_background,teeny.heartbeat,teeny.morning"'
check "makes them shell jobs" test "$(job_field teeny.heartbeat .job_type)" = shell
check "stores the cron expression" test "$(job_field teeny.morning .schedule.expr)" = "0 7 * * *"
check "keeps a job's own tz" test "$(job_field teeny.morning .schedule.tz)" = Asia/Kolkata
check "defaults the tz to UTC" test "$(job_field teeny.heartbeat .schedule.tz)" = UTC
check "asks for no delivery" grep -q '"delivery":{"mode":"none"}' "$FAKE_CALLS"
check "leaves the core's system job alone" test "$(job_field memory_background .id)" = sys-1

# --- a second seed over an unchanged file does nothing -----------------------
: >"$FAKE_CALLS"
seed
check "is idempotent: no add" test "$(calls cron_add)" = 0
check "is idempotent: no update" test "$(calls cron_update)" = 0
check "is idempotent: no remove" test "$(calls cron_remove)" = 0
check "says so" grep -q '0 change(s) applied' "$scratch/stderr"

# --- a changed job is updated in place, keeping its id -----------------------
id_before="$(job_field teeny.morning .id)"
sed -i.bak 's/0 7 \* \* \*/30 6 * * */' "$toml"
: >"$FAKE_CALLS"
seed
check "updates a changed schedule" test "$(job_field teeny.morning .schedule.expr)" = "30 6 * * *"
check "updates in place (same id)" test "$(job_field teeny.morning .id)" = "$id_before"
check "touches only the changed job" test "$(calls cron_update)" = 1

# --- a job dropped from the file is removed; the system job stays ------------
cat >"$toml" <<'TOML'
[jobs.heartbeat]
schedule = "*/5 * * * *"
command = "bin/heartbeat"
TOML
seed
check "removes a job no longer in the file" test -z "$(job_field teeny.morning .id)"
check "still leaves the system job" test "$(job_field memory_background .id)" = sys-1

# --- enabled = false: added, then switched off; flipping back re-enables -----
reset
cat >"$toml" <<'TOML'
[jobs.paused]
schedule = "0 * * * *"
command = "bin/heartbeat"
enabled = false
TOML
seed
check "adds a disabled job switched off" test "$(job_field teeny.paused .enabled)" = false
sed -i.bak 's/enabled = false/enabled = true/' "$toml"
seed
check "re-enables it when the file does" test "$(job_field teeny.paused .enabled)" = true

# --- duplicates of one name collapse to one -----------------------------------
reset
jq -c '. + [
  {id:"d1",name:"teeny.heartbeat",job_type:"shell",command:"bin/heartbeat",schedule:{kind:"cron",expr:"*/5 * * * *",tz:"UTC"},enabled:true},
  {id:"d2",name:"teeny.heartbeat",job_type:"shell",command:"bin/heartbeat",schedule:{kind:"cron",expr:"*/5 * * * *",tz:"UTC"},enabled:true}]' \
  "$FAKE_JOBS" >"$scratch/j" && mv "$scratch/j" "$FAKE_JOBS"
printf '[jobs.heartbeat]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"\n' >"$toml"
seed
check "keeps one of two same-named jobs" test "$(jobs | jq '[.[] | select(.name == "teeny.heartbeat")] | length')" = 1

# --- CALENDAR_TZ is the default when the file names none ---------------------
reset
CALENDAR_TZ=Europe/Berlin seed
check "falls back to CALENDAR_TZ" test "$(job_field teeny.heartbeat .schedule.tz)" = Europe/Berlin

# --- --dry-run plans without calling anything that writes ---------------------
reset
seed --dry-run
check "dry run adds nothing" test "$(calls cron_add)" = 0
check "dry run reports the plan" grep -q 'add.*teeny.heartbeat' "$scratch/stderr"

# --- the file is validated before the core is asked anything -----------------
refuses() {
  local name="$1" body="$2"
  reset
  printf '%s\n' "$body" >"$toml"
  if seed; then fail "$name"; elif [ "$(calls cron_add)" = 0 ]; then pass "$name"; else fail "$name (still wrote)"; fi
}
refuses "refuses a command outside bin/" $'[jobs.x]\nschedule = "*/5 * * * *"\ncommand = "curl https://example.com"'
refuses "refuses a bin/ path that climbs out" $'[jobs.x]\nschedule = "*/5 * * * *"\ncommand = "bin/../secrets/run"'
refuses "refuses a four-field schedule" $'[jobs.x]\nschedule = "* * * *"\ncommand = "bin/heartbeat"'
refuses "refuses an unknown job key" $'[jobs.x]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"\nprompt = "post something"'
refuses "refuses an uppercase name" $'[jobs.X]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"'
refuses "refuses a non-boolean enabled" $'[jobs.x]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"\nenabled = "no"'

# --- the rules Teeny cannot edit: what a brain job runs, how often, how many ---
refuses "refuses a job every minute" $'[jobs.x]\nschedule = "* * * * *"\ncommand = "bin/heartbeat"'
refuses "refuses a job every 3 minutes" $'[jobs.x]\nschedule = "*/3 * * * *"\ncommand = "bin/heartbeat"'
refuses "refuses two firings 2 minutes apart" $'[jobs.x]\nschedule = "0,2 9 * * *"\ncommand = "bin/heartbeat"'
refuses "refuses a brain job running any other bin/ script" $'[jobs.x]\nschedule = "*/5 * * * *"\ncommand = "bin/spine"'
refuses "refuses bin/act with a playbook that does not exist" $'[jobs.x]\nschedule = "*/5 * * * *"\ncommand = "bin/act nope"'
refuses "refuses bin/act with a path for a playbook" $'[jobs.x]\nschedule = "*/5 * * * *"\ncommand = "bin/act ../secrets"'
refuses "refuses more bin/act runs a day than the budget" \
  $'[jobs.a]\nschedule = "*/5 * * * *"\ncommand = "bin/act morning"\n[jobs.b]\nschedule = "*/5 * * * *"\ncommand = "bin/act morning"'
many=""
for i in $(seq 1 25); do many+=$'[jobs.j'"$i"$']\nschedule = "0 * * * *"\ncommand = "bin/heartbeat"\n'; done
refuses "refuses more than 24 jobs" "$many"

reset
printf '[jobs.a]\nschedule = "*/5 * * * *"\ncommand = "bin/act morning"\nenabled = false\n[jobs.b]\nschedule = "*/5 * * * *"\ncommand = "bin/act morning"\n' >"$toml"
seed
check "a disabled job does not count against the bin/act budget" test "$(calls cron_add)" = 2

# --- pinned jobs: always merged in, never redefined -----------------------------
printf '[jobs.watch]\nschedule = "0 * * * *"\ncommand = "bin/doctor --live"\n' >"$pinned"
reset
printf '[jobs.heartbeat]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"\n' >"$toml"
seed
check "seeds the pinned jobs beside the brain's" test "$(job_field teeny.watch .command)" = "bin/doctor --live"
printf '' >"$toml"
seed
check "keeps the pinned jobs when the brain drops every job of its own" test -n "$(job_field teeny.watch .id)"
check "and removes the brain's heartbeat when the brain drops it" test -z "$(job_field teeny.heartbeat .id)"
refuses "refuses a brain job that takes a pinned job's name" $'[jobs.watch]\nschedule = "0 * * * *"\ncommand = "bin/heartbeat"'
# The cap is on the brain's own jobs: the operator's pinned ones (the mind's
# tick, the inbound jobs) never crowd Teeny out of its 24.
full=""
for i in $(seq 1 24); do full+=$'[jobs.j'"$i"$']\nschedule = "0 * * * *"\ncommand = "bin/heartbeat"\n'; done
reset
printf '%s' "$full" >"$toml"
if seed >/dev/null; then pass "the pinned jobs do not count against the brain's 24"; else fail "the pinned jobs do not count against the brain's 24: $(cat "$scratch/stderr")"; fi
: >"$pinned"

# --- agent jobs: an agent written to act on a schedule, with a prompt ------------
reset
printf '[jobs.builder]\nschedule = "0 */2 * * *"\nagent = "builder"\nprompt = "build the next job"\n' >"$toml"
seed
check "adds an agent job" test "$(job_field teeny.builder .job_type)" = agent
check "names its agent" test "$(job_field teeny.builder .agent_id)" = builder
check "stores its prompt" test "$(job_field teeny.builder .prompt)" = "build the next job"
check "runs it in its own session" test "$(job_field teeny.builder .session_target)" = isolated
check "gives an agent job no command" test "$(job_field teeny.builder .command)" = null
: >"$FAKE_CALLS"
seed
check "an unchanged agent job is kept" test "$(calls cron_add)$(calls cron_update)$(calls cron_remove)" = 000
id_before="$(job_field teeny.builder .id)"
printf '[jobs.builder]\nschedule = "0 */2 * * *"\nagent = "builder"\nprompt = "build something else"\n' >"$toml"
seed
check "updates a changed prompt in place" test "$(job_field teeny.builder .prompt)" = "build something else"
check "keeps the agent job's id" test "$(job_field teeny.builder .id)" = "$id_before"
printf '[jobs.builder]\nschedule = "0 */2 * * *"\ncommand = "bin/heartbeat"\n' >"$toml"
: >"$FAKE_CALLS"
seed
check "a job that changes kind is replaced (removed)" test "$(calls cron_remove)" = 1
check "a job that changes kind is replaced (added)" test "$(job_field teeny.builder .job_type)" = shell
check "and is now a shell job with its command" test "$(job_field teeny.builder .command)" = bin/heartbeat

refuses "refuses an agent no cron job may run" $'[jobs.x]\nschedule = "0 * * * *"\nagent = "act"\nprompt = "p"'
mv "$scratch/agents/operator.toml" "$scratch/operator.toml.away"
refuses "refuses an allowed agent the brain does not define" $'[jobs.x]\nschedule = "0 * * * *"\nagent = "operator"\nprompt = "p"'
mv "$scratch/operator.toml.away" "$scratch/agents/operator.toml"
refuses "refuses an agent without scheduled_actions" $'[jobs.x]\nschedule = "0 * * * *"\nagent = "operator"\nprompt = "p"'
refuses "refuses an agent job without a prompt" $'[jobs.x]\nschedule = "0 * * * *"\nagent = "builder"'
refuses "refuses an agent job with an empty prompt" $'[jobs.x]\nschedule = "0 * * * *"\nagent = "builder"\nprompt = "  "'
refuses "refuses a job with both a command and an agent" $'[jobs.x]\nschedule = "0 * * * *"\nagent = "builder"\nprompt = "p"\ncommand = "bin/heartbeat"'
refuses "refuses more agent runs a day than the budget" \
  $'[jobs.a]\nschedule = "*/20 * * * *"\nagent = "builder"\nprompt = "p"\n[jobs.b]\nschedule = "*/20 * * * *"\nagent = "builder"\nprompt = "p"'
printf 'id = "builder"\nwhen_to_use = "x"\nscheduled_actions = true\n[tools]\nnamed = ["*"]\n' >"$scratch/agents/builder.toml"
refuses "refuses an agent whose tools are the wildcard" $'[jobs.x]\nschedule = "0 * * * *"\nagent = "builder"\nprompt = "p"'
printf 'id = "builder"\nwhen_to_use = "x"\nscheduled_actions = true\n[tools]\nnamed = ["shell"]\n' >"$scratch/agents/builder.toml"
printf '[jobs.pinned-builder]\nschedule = "0 */2 * * *"\nagent = "builder"\nprompt = "p"\n' >"$pinned"
reset
printf '' >"$toml"
seed
check "a pinned agent job reads its agent from the brain" test "$(job_field teeny.pinned-builder .agent_id)" = builder
: >"$pinned"

# --- a job whose next run passed while the box was down is re-armed --------------
# 2026-11-11: the box rebooted mid-run and three jobs kept a next_run in the
# past for over an hour (the dead run's claim held their slot). Seeding at
# boot patches the same schedule back, which moves next_run to the next slot.
ago() { jq -rn --argjson s "$1" 'now - $s | floor | todate'; }
stale_jobs() { # one job per NAME:NEXT_RUN_SECONDS_AGO:ENABLED, all on */5
  local spec name secs enabled
  reset
  for spec in "$@"; do
    IFS=: read -r name secs enabled <<<"$spec"
    jq -c --arg n "$name" --arg t "$(ago "$secs")" --argjson e "$enabled" '. + [{id: ("id-" + $n), name: $n,
      job_type: "shell", command: "bin/heartbeat", schedule: {kind: "cron", expr: "*/5 * * * *", tz: "UTC"},
      enabled: $e, next_run: $t}]' "$FAKE_JOBS" >"$scratch/j" && mv "$scratch/j" "$FAKE_JOBS"
  done
  : >"$FAKE_CALLS"
}
printf '[jobs.heartbeat]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"\n[jobs.late]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"\n[jobs.soon]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"\n[jobs.off]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"\nenabled = false\n' >"$toml"
stale_jobs teeny.heartbeat:-120:true teeny.late:4500:true teeny.soon:60:true teeny.off:4500:false
jq -c '. + [{id: "sys-2", name: "memory_digest", job_type: "flow", command: "system:digest",
  schedule: {kind: "cron", expr: "0 * * * *"}, enabled: true, next_run: "2026-01-01T00:00:00Z"}]' \
  "$FAKE_JOBS" >"$scratch/j" && mv "$scratch/j" "$FAKE_JOBS"
late_was="$(job_field teeny.late .next_run)"
seed
check "re-arms an enabled job whose next run is long past" \
  grep -qF "[seed-cron] rearm teeny.late (next_run was $late_was)" "$scratch/stderr"
check "by patching the same schedule back" \
  grep -qF 'cron_update {"job_id":"id-teeny.late","patch":{"schedule":{"kind":"cron","expr":"*/5 * * * *","tz":"UTC"}}}' "$FAKE_CALLS"
check "which moves its next run to the future" \
  test "$(jq -r '.[] | select(.name == "teeny.late") | .next_run | fromdateiso8601 > now' "$FAKE_JOBS")" = true
check "re-arms only that job" test "$(calls cron_update)" = 1
check "leaves a job a minute late alone (the grace)" bash -c "! grep -q 'rearm teeny.soon' '$scratch/stderr'"
check "leaves a disabled job alone" bash -c "! grep -q 'rearm teeny.off' '$scratch/stderr'"
check "leaves the core's own jobs alone" bash -c "! grep -q 'rearm memory_digest' '$scratch/stderr'"
check "does not count a re-arm as a change to the schedule" grep -q '0 change(s) applied' "$scratch/stderr"
: >"$FAKE_CALLS"
seed
check "a re-armed job is not re-armed again" test "$(calls cron_update)" = 0

stale_jobs teeny.heartbeat:4500:true teeny.late:0:true teeny.soon:0:true teeny.off:0:false
seed --dry-run
check "--dry-run reports a re-arm" grep -q 'rearm teeny.heartbeat (next_run was' "$scratch/stderr"
check "--dry-run does not re-arm" test "$(calls cron_update)" = 0

# A job seeding changes anyway gets its schedule (and a fresh next run) from the update.
stale_jobs teeny.heartbeat:4500:true teeny.late:0:true teeny.soon:0:true teeny.off:0:false
jq -c 'map(if .name == "teeny.heartbeat" then .command = "bin/old" else . end)' "$FAKE_JOBS" >"$scratch/j" && mv "$scratch/j" "$FAKE_JOBS"
seed
check "an updated job is not re-armed on top" test "$(calls cron_update)" = 1
check "and is updated" grep -q '^\[seed-cron\] update  teeny.heartbeat' "$scratch/stderr"

# --- a daily slot missed across a restart is caught up, once -----------------------
# 2026-11-13 and 11-14: a deploy restarted the spine while teeny.self-improve
# was running; this boot's re-arm moved it to tomorrow and the day's run was
# lost. A job with `catch_up = true` is run once now instead (bin/lib/catch_up.py).
export TEENY_STATE="$scratch/state" TEENY_STORAGE="$scratch/storage.db" TEENY_QUIET_HOURS=off
mkdir -p "$TEENY_STATE"
# The core's storage as it would be: the jobs the fake knows, by id, no runs.
storage_from_fake() {
  rm -f "$TEENY_STORAGE"
  python3 - "$FAKE_JOBS" "$TEENY_STORAGE" <<'PY'
import datetime as dt, json, sqlite3, sys
jobs = json.load(open(sys.argv[1]))
db = sqlite3.connect(sys.argv[2])
db.execute("create table docs (scope text default '', coll text, id text, created_ms integer default 0,"
           " updated_ms integer default 0, deleted integer default 0, body text)")
for job in jobs:
    if not job.get("next_run"):
        continue
    next_ms = int(dt.datetime.fromisoformat(job["next_run"].replace("Z", "+00:00")).timestamp() * 1000)
    body = {"name": job["name"], "enabled": job["enabled"], "next_run_ms": next_ms, "schedule": job["schedule"]}
    db.execute("insert into docs (coll, id, body) values ('cron.jobs', ?, ?)", (job["id"], json.dumps(body)))
db.commit()
PY
}
# A daily job at the minute eight minutes ago (UTC), next run still on it.
slot="$(date -u -d '-8 minutes' '+%M %H')"
printf '[jobs.daily]\nschedule = "%s * * *"\ncommand = "bin/heartbeat"\ncatch_up = true\n[jobs.plain]\nschedule = "%s * * *"\ncommand = "bin/heartbeat"\n' "$slot" "$slot" >"$toml"
reset
for n in daily plain; do
  jq -c --arg n "teeny.$n" --arg t "$(ago 480)" --arg e "$slot * * *" '. + [{id: ("id-" + $n), name: $n,
    job_type: "shell", command: "bin/heartbeat", schedule: {kind: "cron", expr: $e, tz: "UTC"},
    enabled: true, next_run: $t}]' "$FAKE_JOBS" >"$scratch/j" && mv "$scratch/j" "$FAKE_JOBS"
done
: >"$FAKE_CALLS"
storage_from_fake
seed --dry-run
check "--dry-run reports the catch-up" grep -q 'catch up teeny.daily' "$scratch/stderr"
check "--dry-run runs nothing" test "$(calls cron_run)" = 0
seed
check "re-arms both missed jobs" test "$(calls cron_update)" = 2
check "catches up the one with catch_up, at boot" grep -qF 'cron_run {"job_id":"id-teeny.daily"}' "$FAKE_CALLS"
check "with one run" test "$(calls cron_run)" = 1
check "and says so" grep -q 'caught up teeny.daily (its .* slot)' "$scratch/stderr" || cat "$scratch/stderr" >&2
check "leaves the other to its next slot" bash -c "! grep -q 'id-teeny.plain\"}' <(grep '^cron_run' '$FAKE_CALLS')"
: >"$FAKE_CALLS"
seed
check "never runs the same slot twice across boots" test "$(calls cron_run)" = 0
# The re-arm moved both to their next run (the fake: an hour on); storage follows.
storage_from_fake
seed
check "nor once the re-arm has moved it on" test "$(calls cron_run)" = 0
# A job seeding adds now has no slot of its own to catch up.
printf '[jobs.fresh]\nschedule = "%s * * *"\ncommand = "bin/heartbeat"\ncatch_up = true\n' "$slot" >"$toml"
reset
storage_from_fake
seed
storage_from_fake
seed
check "a job just added is not caught up" test "$(calls cron_run)" = 0
unset TEENY_STATE TEENY_STORAGE TEENY_QUIET_HOURS

refuses "refuses a catch-up window as long as the gap between slots" \
  $'[jobs.x]\nschedule = "0 9,15 * * *"\ncommand = "bin/heartbeat"\ncatch_up = true'
refuses "refuses a catch-up window on a job every hour" \
  $'[jobs.x]\nschedule = "0 * * * *"\ncommand = "bin/heartbeat"\ncatch_up = true\ncatch_up_hours = 1'
refuses "refuses catch_up_hours without catch_up" \
  $'[jobs.x]\nschedule = "0 9 * * *"\ncommand = "bin/heartbeat"\ncatch_up_hours = 3'
refuses "refuses a catch-up window over 12 hours" \
  $'[jobs.x]\nschedule = "0 9 * * *"\ncommand = "bin/heartbeat"\ncatch_up = true\ncatch_up_hours = 13'
refuses "refuses a non-boolean catch_up" \
  $'[jobs.x]\nschedule = "0 9 * * *"\ncommand = "bin/heartbeat"\ncatch_up = "yes"'

# --- --check validates without asking the core anything --------------------------
reset
printf '[jobs.heartbeat]\nschedule = "*/5 * * * *"\ncommand = "bin/heartbeat"\n' >"$toml"
seed --check
check "--check reads nothing from the core" test ! -s "$FAKE_CALLS"
check "--check says the file is valid" grep -q 'is valid' "$scratch/stderr"

# --- the repository's own schedule file is valid ------------------------------
reset
"$seed" --dry-run --file "$here/../../brain/cron.toml" --pinned "$here/../../evals/cron.pinned.toml" 2>"$scratch/stderr"
check "brain/cron.toml parses and plans" grep -q 'teeny.heartbeat' "$scratch/stderr"
check "and the mind's tick is pinned beside it" grep -q 'teeny.tick' "$scratch/stderr"
check "and its nightly dream" grep -qE 'teeny\.dream +0 2 \* \* \* .*bin/dream --detach' "$scratch/stderr"
check "and its midday one" grep -qE 'teeny\.dream-midday +0 14 \* \* \* .*bin/dream --light --detach' "$scratch/stderr"
check "and the receipts after each" grep -qE 'teeny\.receipts +30 2,14 \* \* \*' "$scratch/stderr"
check "and no reflection of its own any more" bash -c "! grep -q 'teeny.reflect' '$scratch/stderr'"
check "and the health check, every 15 minutes" grep -qE 'teeny\.health +\*/15 \* \* \* \* .* bin/health' "$scratch/stderr"
check "and Teeny listens to the world twice a day, before the take and before the evening" \
  grep -qE 'teeny\.world-scan +5 13,22 \* \* \* Asia/Kolkata bin/act world-scan' "$scratch/stderr"
# shipped shared 19:30 with moltbook-evening and never ran once on the box
# (2026-11-11 to 11-13) while moltbook-evening ran every day.
check "and no two of its enabled jobs fire on the same schedule" bash -c "
  python3 '$here/../lib/cron_policy.py' '$here/../../brain/cron.toml' /dev/null |
    jq -e '[.[] | select(.enabled) | [.expr, .tz]] | (length == (unique | length))' >/dev/null"

if [ "$failures" -gt 0 ]; then
  printf '%d failure(s)\n' "$failures" >&2
  exit 1
fi
echo "all passed"
