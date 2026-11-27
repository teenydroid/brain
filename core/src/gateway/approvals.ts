// OpenClaw's exec approvals tighten a shell agent's exec: the effective security is the stricter of the
// config's and the approvals' (docs/tools/exec-approvals.md), so a leftover allowlist or `ask` policy makes
// exec dead with nothing in the config to show it. The state directory is Teeny's alone and the config
// already says what exec may do (the guardrails plugin is the gate), so a legacy exec-approvals.json found
// there at boot is removed rather than left for `openclaw doctor --fix` to import into the Gateway's policy.
// (This OpenClaw keeps the live policy in its own state database; bin/doctor --live runs a command through
// a shell agent to prove exec works, which is the check that does not depend on where the policy lives.)
import { existsSync, rmSync } from "node:fs";
import { join } from "node:path";

export const LEGACY_APPROVALS_FILES = ["exec-approvals.json"];

/** Removes the legacy approvals files under `dir`; returns the ones it removed. */
export function removeStaleApprovals(dir: string): string[] {
  const removed: string[] = [];
  for (const name of LEGACY_APPROVALS_FILES) {
    const file = join(dir, name);
    if (existsSync(file)) {
      rmSync(file, { force: true });
      removed.push(file);
    }
  }
  return removed;
}
