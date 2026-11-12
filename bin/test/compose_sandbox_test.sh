#!/usr/bin/env bash
# The spine container must let TinyBox's bubblewrap jail start, and must not
# be opened any further than that.
#
# The builder's shell runs sandboxed. In a container under Docker's defaults
# the only jail TinyBox finds is Landlock, which confines files but declares no
# network enforcement and no process isolation, so TinySecurity refuses every
# plan (denial=Unsupported). bubblewrap needs an unprivileged user namespace,
# which Docker's default seccomp profile, its docker-default AppArmor profile
# (`deny mount`) and its masked /proc paths each block. deploy/compose.yaml
# relaxes exactly those three, for the spine only.
#
# 1. Statically, from `docker compose config`: the spine carries the seccomp
#    profile, apparmor=unconfined and systempaths=unconfined; no service is
#    privileged, adds a capability or drops seccomp; no other service gets the
#    relaxations; and the profile allows, of the syscalls Docker reserves for
#    CAP_SYS_ADMIN, only the five bubblewrap needs.
# 2. Live, when Docker and the teeny-spine:current image are here: the spine
#    service as compose runs it starts TinyBox's jail (the same flags as its
#    availability probe) in a new PID namespace, and inside the jail a nested
#    user namespace is still refused.
#
#   bin/test/compose_sandbox_test.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
compose="$repo/deploy/compose.yaml"
image="${TEENY_SPINE_IMAGE:-teeny-spine:current}"

if ! docker compose version >/dev/null 2>&1; then
  echo "skip: docker compose is not available"
  exit 0
fi

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }

config="$(docker compose -f "$compose" --profile web config --format json)"
if python3 -I - "$repo" "$config" <<'PY'; then pass "the compose security policy"; else fail "the compose security policy"; fi
import json, os, sys

repo, config = sys.argv[1], json.loads(sys.argv[2])
services = config["services"]
problems = []

WANT = {"apparmor=unconfined", "systempaths=unconfined"}
for name, svc in services.items():
    opts = svc.get("security_opt") or []
    if svc.get("privileged"):
        problems.append(f"{name} is privileged")
    if svc.get("cap_add"):
        problems.append(f"{name} adds capabilities {svc['cap_add']}")
    if any(o.replace(":", "=", 1) == "seccomp=unconfined" for o in opts):
        problems.append(f"{name} runs without seccomp")
    if name == "spine":
        normal = {o.replace(":", "=", 1) for o in opts}
        missing = WANT - normal
        if missing:
            problems.append(f"spine lacks {sorted(missing)}")
        seccomp = [o.split("=", 1)[1] for o in normal if o.startswith("seccomp=")]
        if seccomp != ["seccomp-spine.json"] and [os.path.basename(s) for s in seccomp] != ["seccomp-spine.json"]:
            problems.append(f"spine seccomp is {seccomp}, not deploy/seccomp-spine.json")
    elif opts:
        problems.append(f"{name} has security options {opts}; only the spine runs the jail")

# Docker's default profile allows these only to a container holding
# CAP_SYS_ADMIN, which the spine never does. bubblewrap needs the first five,
# inside its own user namespace; every other one stays refused.
NEEDED = {"clone", "unshare", "mount", "umount2", "pivot_root"}
RESERVED = NEEDED | {
    "bpf", "clone3", "fanotify_init", "fsconfig", "fsmount", "fsopen", "fspick",
    "lookup_dcookie", "lsm_get_self_attr", "lsm_list_modules", "lsm_set_self_attr",
    "mount_setattr", "move_mount", "open_tree", "perf_event_open", "quotactl",
    "quotactl_fd", "setdomainname", "sethostname", "setns", "syslog", "umount",
    "keyctl", "add_key", "request_key", "ptrace", "kexec_load", "init_module",
    "finit_module", "delete_module", "chroot", "acct", "swapon", "swapoff",
    "reboot", "settimeofday", "clock_settime", "userfaultfd",
}
profile = json.load(open(os.path.join(repo, "deploy", "seccomp-spine.json")))
if profile.get("defaultAction") != "SCMP_ACT_ERRNO":
    problems.append(f"profile default is {profile.get('defaultAction')}, not SCMP_ACT_ERRNO")
unconditional = set()
for rule in profile["syscalls"]:
    if rule.get("action") == "SCMP_ACT_ALLOW" and not rule.get("args") and not rule.get("includes") and not rule.get("excludes"):
        unconditional |= set(rule["names"])
opened = unconditional & RESERVED
if opened != NEEDED:
    problems.append(f"profile allows {sorted(opened)} unconditionally; want exactly {sorted(NEEDED)}")
# The CAP_SYS_ADMIN rule itself is Docker's and stays as it is.
if not any(r.get("includes", {}).get("caps") == ["CAP_SYS_ADMIN"] and "setns" in r["names"] for r in profile["syscalls"]):
    problems.append("profile lost Docker's CAP_SYS_ADMIN rule")

for p in problems:
    print("  " + p, file=sys.stderr)
sys.exit(1 if problems else 0)
PY

# --- live: the spine as compose runs it can start TinyBox's jail ---------------
if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "skip: no $image image here; the live jail check needs it"
else
  # The flags TinyBox's bubblewrap probe uses (tinybox-jail bwrap/args.rs, a
  # jail that denies the network, with --disable-userns), then its check: the
  # jail has its own PID namespace. Inside, a nested user namespace must fail.
  probe='parent="$(readlink /proc/self/ns/pid)"
bwrap --unshare-user --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try \
  --unshare-net --disable-userns --die-with-parent --new-session \
  --proc /proc --dev /dev --tmpfs /tmp --ro-bind /usr /usr \
  --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
  --ro-bind-try /etc /etc --chdir / \
  /bin/sh -c "test \"\$(readlink /proc/self/ns/pid)\" != \"\$1\" && echo isolated;
    if unshare -U true 2>/dev/null; then echo nested-userns; else echo no-nested-userns; fi" probe "$parent"'
  # The spine shares cortexdb'"'"'s network, which this one-off run does not start.
  override="$(mktemp "${TMPDIR:-/tmp}/teeny-compose.XXXXXX")"
  trap 'rm -f "$override"' EXIT
  printf 'services:\n  spine:\n    image: %s\n    network_mode: none\n    depends_on: !reset {}\n' "$image" >"$override"
  out="$(docker compose -p "teeny-sandbox-test-$$" -f "$compose" -f "$override" run --rm --no-deps -T \
    --pull never --entrypoint /bin/sh spine -c "$probe" 2>&1)" || true
  if grep -qx isolated <<<"$out"; then
    pass "the spine container starts TinyBox's bubblewrap jail"
  else
    fail "the spine container starts TinyBox's bubblewrap jail"
    printf '%s\n' "$out" | tail -n 5 >&2
  fi
  if grep -qx no-nested-userns <<<"$out"; then
    pass "inside the jail a nested user namespace is refused"
  else
    fail "inside the jail a nested user namespace is refused"
  fi
fi

if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed" >&2
  exit 1
fi
echo "all compose sandbox checks passed"
