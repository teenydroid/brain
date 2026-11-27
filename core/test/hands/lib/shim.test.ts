// The shim convention (core/scripts/hand-shim.sh): hands/NAME keeps its path and
// becomes a two-line sh wrapper around core/dist/hands/NAME/cli.js. It has to
// work from any cwd, through a symlink, pass argv, stdin and the exit status
// through untouched, and leave nothing on stderr.

import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CORE } from "./support.js";

const SCRIPT = join(CORE, "scripts", "hand-shim.sh");

function checkout(): { repo: string; run: (path: string, args: string[], input?: string, cwd?: string) => ReturnType<typeof spawnSync> } {
  const repo = mkdtempSync(join(tmpdir(), "shim-"));
  mkdirSync(join(repo, "hands"));
  mkdirSync(join(repo, "core", "scripts"), { recursive: true });
  mkdirSync(join(repo, "core", "src", "hands", "demo"), { recursive: true });
  mkdirSync(join(repo, "core", "dist", "hands", "demo"), { recursive: true });
  writeFileSync(join(repo, "core", "scripts", "hand-shim.sh"), readFileSync(SCRIPT));
  chmodSync(join(repo, "core", "scripts", "hand-shim.sh"), 0o755);
  writeFileSync(join(repo, "core", "src", "hands", "demo", "cli.ts"), "");
  writeFileSync(join(repo, "hands", "demo"), "#!/usr/bin/env -S python3 -I\nprint('the old one')\n");
  chmodSync(join(repo, "hands", "demo"), 0o755);
  writeFileSync(
    join(repo, "core", "dist", "hands", "demo", "cli.js"),
    "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{console.log(JSON.stringify({argv:process.argv.slice(2),cwd:process.cwd(),stdin:s}));process.exit(Number(process.env.EXIT||0));});",
  );
  return {
    repo,
    run: (path, args, input = "", cwd = "/") => spawnSync(path, args, { input, cwd, encoding: "utf8", env: { ...process.env } }),
  };
}

describe("hand-shim.sh", () => {
  it("replaces a Python wrapper with the node one, and the hand answers from any cwd", () => {
    const { repo, run } = checkout();
    const made = spawnSync("sh", [join(repo, "core", "scripts", "hand-shim.sh"), "demo"], { encoding: "utf8" });
    expect(made.status, made.stderr).toBe(0);
    const text = readFileSync(join(repo, "hands", "demo"), "utf8");
    expect(text.split("\n")[0]).toBe("#!/bin/sh");
    expect(text).not.toContain("python");
    const got = run(join(repo, "hands", "demo"), ["top", "--max", "3", "a b", ""], '{"x": 1}', tmpdir());
    expect(got.stderr).toBe("");
    expect(got.status).toBe(0);
    expect(JSON.parse(got.stdout as string)).toEqual({ argv: ["top", "--max", "3", "a b", ""], cwd: tmpdir(), stdin: '{"x": 1}' });
  });

  it("passes the exit status through, and works through a symlink and a PATH lookup", () => {
    const { repo } = checkout();
    spawnSync("sh", [join(repo, "core", "scripts", "hand-shim.sh"), "demo"]);
    const bin = mkdtempSync(join(tmpdir(), "bin-"));
    symlinkSync(join(repo, "hands", "demo"), join(bin, "demo"));
    const viaPath = spawnSync("demo", ["x"], { encoding: "utf8", env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, EXIT: "2" }, input: "" });
    expect(viaPath.status).toBe(2);
    expect(JSON.parse(viaPath.stdout).argv).toEqual(["x"]);
  });

  it("refuses to switch a hand that has no TypeScript source, and keeps the Python one", () => {
    const { repo } = checkout();
    const none = spawnSync("sh", [join(repo, "core", "scripts", "hand-shim.sh"), "missing"], { encoding: "utf8" });
    expect(none.status).toBe(1);
    mkdirSync(join(repo, "hands"), { recursive: true });
    writeFileSync(join(repo, "hands", "bare"), "#!/bin/sh\n");
    const bare = spawnSync("sh", [join(repo, "core", "scripts", "hand-shim.sh"), "bare"], { encoding: "utf8" });
    expect(bare.status).toBe(1);
    expect(bare.stderr).toContain("core/src/hands/bare/cli.ts does not exist");
    expect(readFileSync(join(repo, "hands", "bare"), "utf8")).toBe("#!/bin/sh\n");
  });

  it("prints the wrapper with --check and touches nothing", () => {
    const { repo } = checkout();
    const out = spawnSync("sh", [join(repo, "core", "scripts", "hand-shim.sh"), "demo", "--check"], { encoding: "utf8" });
    expect(out.stdout).toContain('exec node --disable-warning=ExperimentalWarning "$(dirname "$(realpath "$0")")/../core/dist/hands/demo/cli.js" "$@"');
    expect(readFileSync(join(repo, "hands", "demo"), "utf8")).toContain("python3");
  });
});
