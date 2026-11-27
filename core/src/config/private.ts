// What the core does about OpenClaw's "No safe OpenClaw temp root" refusal. OpenClaw checks
// the directory it logs and keeps temp files in, and every ancestor of it, and will not use
// one that group or others can write (unless sticky). A state/ made under umask 002 is such
// a directory, so the core makes everything under state/openclaw itself, private.
import { chmodSync, lstatSync, mkdirSync, readdirSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";

const GROUP_OTHER_WRITE = 0o022;
const STICKY = 0o1000;

/** mkdir -p with mode 0700, then chmod: mkdir leaves an existing directory as it was. */
export function ensurePrivateDir(dir: string): void {
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  chmodSync(dir, 0o700);
}

/**
 * Takes group and other write off `root` and everything under it (symlinks are neither
 * followed nor changed). Read bits stay as they are: OpenClaw may need to read across.
 * Returns how many entries it changed.
 */
export function tightenTree(root: string): number {
  let changed = 0;
  const visit = (path: string): void => {
    const st = lstatSync(path);
    if (st.isSymbolicLink()) return;
    if (st.mode & GROUP_OTHER_WRITE) {
      chmodSync(path, st.mode & 0o7777 & ~GROUP_OTHER_WRITE);
      changed += 1;
    }
    if (!st.isDirectory()) return;
    for (const name of readdirSync(path)) visit(join(path, name));
  };
  visit(root);
  return changed;
}

/** The ancestors of `path` OpenClaw would refuse: group or other writable, not sticky. Nearest first. */
export function unsafeAncestors(path: string): string[] {
  const bad: string[] = [];
  let dir = dirname(realpathSync(path));
  for (;;) {
    const mode = lstatSync(dir).mode;
    if (mode & GROUP_OTHER_WRITE && !(mode & STICKY)) bad.push(dir);
    const parent = dirname(dir);
    if (parent === dir) return bad;
    dir = parent;
  }
}
