// Path rules for every file tool and every path a shell command names: the
// workspace_only / forbidden_paths / trusted_roots policy OpenHuman enforced
// (bin/lib/core_sections/automation.py, [autonomy]), with the normalisation a
// jail needs: `..` collapsed, `~` expanded, and every symlink resolved before
// the check, so a link inside an allowed folder cannot lead out of it.
import { lstatSync, readlinkSync, realpathSync } from "node:fs";
import { basename, dirname, join, resolve, sep } from "node:path";

export interface PathRules {
  /** Where a relative path starts (the agent's working folder). */
  workspace: string;
  /** Folders the agent may read and write. */
  rw: string[];
  /** Folders the agent may only read. */
  ro: string[];
  /** Never reachable, even under a root above (secrets, the live brain's own files, .git, state). */
  closed: string[];
  /** OpenHuman's default forbidden_paths: lifted for anything under an allowed root. */
  systemForbidden: string[];
  /** Home directory for `~`. */
  home: string;
}

export type Access = "read" | "write";

/** True when `child` is `parent` or inside it, comparing whole path segments. */
export function isUnder(child: string, parent: string): boolean {
  if (child === parent) return true;
  const prefix = parent.endsWith(sep) ? parent : parent + sep;
  return child.startsWith(prefix);
}

/**
 * The path with every symlink resolved, for a path that may not exist yet:
 * the longest existing prefix is realpath'd, a dangling link is followed by
 * hand (a write through it would create its target), and the rest is appended.
 */
export function realish(path: string, depth = 0): string {
  if (depth > 40) throw new Error("too many symbolic links");
  try {
    return realpathSync(path);
  } catch {
    // fall through: the path or a part of it does not exist
  }
  let target: string | undefined;
  try {
    if (lstatSync(path).isSymbolicLink()) target = resolve(dirname(path), readlinkSync(path));
  } catch {
    // not a link either
  }
  // Outside the try: a loop must refuse, not fall through to "the path does not exist".
  if (target !== undefined) return realish(target, depth + 1);
  const parent = dirname(path);
  if (parent === path) return path;
  return join(realish(parent, depth + 1), basename(path));
}

export function expandHome(raw: string, home: string): string {
  if (raw === "~") return home;
  if (raw.startsWith("~/")) return join(home, raw.slice(2));
  return raw;
}

/** Why `raw` may not be used for `access`, or undefined when it may. */
export function checkPath(rules: PathRules, raw: unknown, access: Access): string | undefined {
  if (typeof raw !== "string" || raw.trim() === "") return "the path is missing or not a string";
  if (raw.includes("\0")) return "the path holds a NUL byte";
  const lexical = resolve(rules.workspace, expandHome(raw, rules.home));
  let real: string;
  try {
    real = realish(lexical);
  } catch {
    return `the path ${raw} cannot be resolved (symbolic link loop)`;
  }
  const roots = (access === "write" ? rules.rw : rules.ro.concat(rules.rw)).flatMap((r) => [resolve(r), safeReal(r)]);
  // Both the path as written and where it really leads must pass every test.
  for (const candidate of new Set([lexical, real])) {
    for (const closed of rules.closed) {
      if (isUnder(candidate, closed) || isUnder(candidate, safeReal(closed))) {
        return `${raw} is in a folder closed to agents`;
      }
    }
    // git runs what is in .git/hooks and .git/config: no tool writes there (git itself does, by its own commands).
    if (access === "write" && candidate.split(sep).includes(".git")) return `${raw} is inside a .git folder, whose hooks and config run programs`;
    const held = roots.filter((r) => isUnder(candidate, r));
    if (held.length === 0) {
      return access === "write"
        ? `${raw} is outside the folders this agent may write`
        : `${raw} is outside the folders this agent may read`;
    }
    for (const forbidden of rules.systemForbidden) {
      // A trusted root lifts the system list beneath it (the checkout may sit under /home), but
      // a forbidden folder inside a root stays forbidden.
      const f = safeReal(expandHome(forbidden, rules.home));
      // Any trusted root inside the forbidden folder lifts it, whatever order the roots are listed in.
      if (isUnder(candidate, f) && !held.some((r) => isUnder(r, f))) return `${raw} is a forbidden path`;
    }
  }
  return undefined;
}

function safeReal(p: string): string {
  try {
    return realish(resolve(p));
  } catch {
    return resolve(p);
  }
}
