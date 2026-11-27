// The native allowlist is computed twice, in Python (bin/lib/native_playbook.py, which seed-cron needs) and in
// TypeScript (what the core builds the agent's shell from). They must agree, and the message must fit the screen.
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { loadPlaybook, nativeSpec, NATIVE_HANDS, STUDIO_HELD } from "../../src/playbooks/index.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const brain = join(root, "brain");
const tool = join(root, "bin", "lib", "native_playbook.py");
const py = (verb: string, name: string): { ok: boolean; out: string } => {
  const r = spawnSync("python3", ["-I", tool, verb, brain, name, join(root, "hands")], { encoding: "utf8" });
  return { ok: r.status === 0, out: r.stdout };
};

describe("nativeSpec", () => {
  it.each(["gm", "moltbook-general"])("%s: TypeScript and Python build the same allowlist", (name) => {
    const book = loadPlaybook(brain, name, { handsDir: join(root, "hands"), repoRoot: root });
    expect(book.run).toBe("native");
    const python = py("spec", name);
    expect(python.ok).toBe(true);
    expect(nativeSpec(book)).toEqual(JSON.parse(python.out));
  });

  it("the native hands and the studio-held playbooks match Python's tables", () => {
    const text = readFileSync(join(root, "bin", "lib", "playbook.py"), "utf8");
    const held = /STUDIO_HELD = \(([^)]*)\)/.exec(text)![1]!.match(/"([^"]+)"/g)!.map((s) => s.slice(1, -1));
    expect(STUDIO_HELD).toEqual(held);
    expect(NATIVE_HANDS).toEqual({ x: ["post", "reply", "like"], moltbook: ["post", "comment"] });
    expect(/NATIVE_HANDS = (\{[^}]*\})/.exec(text)![1]).toBe('{"x": ("post", "reply", "like"), "moltbook": ("post", "comment")}');
  });

  it("only the playbooks that mean to run natively do: gm and moltbook-general, nothing else in the brain", () => {
    const natives = readdirSync(join(brain, "playbooks"))
      .filter((f) => f.endsWith(".md"))
      .map((f) => f.slice(0, -3))
      .filter((n) => loadPlaybook(brain, n, { handsDir: join(root, "hands"), repoRoot: root }).run === "native");
    expect(natives).toEqual(["gm", "moltbook-general"]);
  });
});

describe("the native message's size", () => {
  it.each(["gm", "moltbook-general"])("%s: stays under the 64 KiB prompt screen with its margin, and has no cut inputs", (name) => {
    const { ok, out } = py("message", name);
    expect(ok).toBe(true);
    const bytes = Buffer.byteLength(out);
    expect(bytes).toBeLessThan(61_440);
    // the hands' own output is read at run time, never part of the message: the message is the playbook and its frame
    expect(bytes).toBeGreaterThan(2_000);
    expect(out).not.toContain("DRY_RUN=1 hands/");
  });
});
