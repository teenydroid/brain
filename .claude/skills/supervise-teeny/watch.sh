#!/usr/bin/env bash
# Teeny supervision snapshot. Read-only. Usage: watch.sh [since, default 20m]
SINCE=${1:-20m}
T=/home/ubuntu/teeny
cd "$T/deploy" || exit 1
echo "== $(date -u +%FT%TZ)  containers"
docker ps --filter name=teeny-spine --format '{{.Names}} {{.Status}}'
echo "== doctor: $(python3 -c "import json;d=json.load(open('$T/state/doctor.json'));print(d['ok'],d['at'],d['commit'][:7],'failed',d['failed'])")  heartbeat: $(cat $T/state/heartbeat)  DRY_RUN: $(grep -c '^DRY_RUN=0' $T/secrets/teeny.env | sed 's/1/live/;s/0/dry/')"
L=$(docker logs --since "$SINCE" teeny-spine-spine-1 2>&1 | grep -vE 'storage_host|GET /health')
echo "== logs since $SINCE: agent_fail=$(grep -c 'agent run failed' <<<"$L") 401=$(grep -c 'invalid_api_key' <<<"$L") ERR=$(grep -c ':ERR:' <<<"$L") cron_fail=$(grep -c 'did not succeed' <<<"$L")"
grep -E ':ERR:' <<<"$L" | sed -E 's/^[0-9:]+:ERR:[a-z_]+ //' | cut -c1-180 | sort | uniq -c | sort -rn | head -5
echo "== cron errors (last status):"
docker compose exec -T spine bin/crons 2>/dev/null | grep -E ' error ' | cut -c1-140
echo "== health: $(cat $T/state/health.json 2>/dev/null | head -c 300)"
echo "== tick last:"; docker compose exec -T spine bin/crons runs teeny.tick 2>/dev/null | sed -n 3p | cut -c1-300
python3 - "$T" <<'EOF'
import sqlite3,sys,time,datetime
c=sqlite3.connect(f'file:{sys.argv[1]}/state/hands.db?mode=ro',uri=True)
def q(s):
    try: return list(c.execute(s))
    except Exception as e: return [f"ERR {e}"]
cut=time.time()-3*3600
print("== x_posts last 3h:")
for r in q(f"select id,kind,created_at,replace(substr(text,1,160),char(10),' ') from x_posts where created_at>{cut} order by created_at desc limit 10"):
    print("  ",r[0],r[1],datetime.datetime.utcfromtimestamp(r[2]).strftime('%H:%M'),r[3])
print("== mentions:",q("select status,count(*) from x_mentions group by status"))
print("== holds total:",q("select count(*) from guard_holds")," by reason:",q("select substr(reason,1,45),count(*) from guard_holds group by 1 order by 2 desc limit 4"))
print("== moltbook posts:",q("select datetime(at,'unixepoch'),submolt,substr(title,1,60),status from moltbook_posts order by at desc limit 3"))
print("== moltbook comments:",q("select status,count(*) from moltbook_comments group by 1"), " actions:",q("select verb,count(*) from moltbook_actions group by verb"))
print("== mail:",q("select status,count(*) from mail_messages group by status"))
print("== stimuli:",q("select status,count(*) from stimuli group by status"))
print("== build_jobs:",q("select id,kind,slug,status from build_jobs order by id desc limit 3"))
EOF
