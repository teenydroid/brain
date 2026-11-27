import { chmodSync, mkdirSync, mkdtempSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ensurePrivateDir, tightenTree, unsafeAncestors } from "../../src/config/private.js";

const mode = (p: string): number => statSync(p).mode & 0o7777;
const scratch = (): string => mkdtempSync(join(tmpdir(), "private-"));

describe("private directories under state/openclaw", () => {
  it("ensurePrivateDir makes missing directories 0700 even under umask 002, and tightens an existing one", () => {
    const root = scratch();
    const old = process.umask(0o002);
    try {
      const deep = join(root, "a", "b");
      ensurePrivateDir(deep);
      expect(mode(deep)).toBe(0o700);
      chmodSync(deep, 0o775);
      ensurePrivateDir(deep);
      expect(mode(deep)).toBe(0o700);
    } finally {
      process.umask(old);
    }
  });

  it("tightenTree takes group and other write off every directory and file, and leaves read bits and symlinks alone", () => {
    const root = scratch();
    const sub = join(root, "workspaces", "simple");
    mkdirSync(sub, { recursive: true });
    const file = join(sub, "AGENTS.md");
    writeFileSync(file, "x");
    chmodSync(file, 0o666);
    chmodSync(sub, 0o777);
    chmodSync(join(root, "workspaces"), 0o775);
    const outside = scratch();
    chmodSync(outside, 0o777);
    symlinkSync(outside, join(root, "link"));
    expect(tightenTree(root)).toBeGreaterThanOrEqual(3);
    expect(mode(sub)).toBe(0o755);
    expect(mode(join(root, "workspaces"))).toBe(0o755);
    expect(mode(file)).toBe(0o644);
    expect(mode(outside)).toBe(0o777); // what a link points at is not ours to change
    expect(tightenTree(root)).toBe(0);
  });

  it("unsafeAncestors names a group-writable ancestor and ignores a sticky one", () => {
    const root = scratch();
    const state = join(root, "state");
    const dir = join(state, "openclaw");
    mkdirSync(dir, { recursive: true });
    chmodSync(state, 0o775);
    expect(unsafeAncestors(dir)).toContain(state);
    chmodSync(state, 0o755);
    expect(unsafeAncestors(dir)).not.toContain(state);
    chmodSync(state, 0o1777);
    expect(unsafeAncestors(dir)).not.toContain(state);
  });
});
