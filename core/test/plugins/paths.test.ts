import { mkdirSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { checkPath, expandHome, isUnder, realish, type PathRules } from "../../plugins/teeny-guardrails/paths.js";
import { link, makeFixture, write } from "./fixture.js";

function rules(f: ReturnType<typeof makeFixture>, id: string): PathRules {
  const a = f.agent(id);
  return { workspace: a.workspace, rw: a.rw, ro: a.ro, closed: a.closed, systemForbidden: f.policy.systemForbidden, home: f.policy.home };
}

describe("path rules (workspace_only, forbidden_paths, trusted_roots)", () => {
  it("compares whole segments, not string prefixes", () => {
    expect(isUnder("/a/workbench", "/a/workbench")).toBe(true);
    expect(isUnder("/a/workbench/x", "/a/workbench")).toBe(true);
    expect(isUnder("/a/workbench2", "/a/workbench")).toBe(false);
  });

  it("lets the mind write its home and the workbench, read the code folders, and nothing else", () => {
    const f = makeFixture();
    const r = rules(f, "mind");
    expect(checkPath(r, "work/a.txt", "write")).toBeUndefined();
    expect(checkPath(r, join(f.root, "state/workbench/other.txt"), "write")).toBeUndefined();
    expect(checkPath(r, join(f.root, "hands/x"), "read")).toBeUndefined();
    expect(checkPath(r, join(f.root, "docs/hands.md"), "read")).toBeUndefined();
    // read-only roots are not writable
    expect(checkPath(r, join(f.root, "hands/x"), "write")).toMatch(/outside the folders this agent may write/);
    expect(checkPath(r, join(f.root, "docs/hands.md"), "write")).toMatch(/may write/);
    // state beyond home and workbench is not a root at all
    expect(checkPath(r, join(f.root, "state/ledger/wallet.json"), "read")).toMatch(/may read/);
    expect(checkPath(r, join(f.root, "state/openclaw/openclaw.json"), "read")).toMatch(/may read/);
  });

  it("closes secrets, .git, .openhuman and Teeny's own files in the brain, even for reads", () => {
    const f = makeFixture();
    const r = rules(f, "mind");
    for (const p of ["secrets/teeny.env", ".git/config", ".openhuman/config.toml", "brain/MEMORY.md", "brain/playbooks/p.md", "brain/agents/mind.md", "brain/SOUL.md"]) {
      expect(checkPath(r, join(f.root, p), "read"), p).toMatch(/closed to agents/);
      expect(checkPath(r, join(f.root, p), "write"), p).toMatch(/closed to agents/);
    }
    // the rest of the brain is the file tools' workspace
    expect(checkPath(r, join(f.root, "brain/library/note.md"), "write")).toBeUndefined();
  });

  it("refuses ../ escapes however they are written", () => {
    const f = makeFixture();
    const r = rules(f, "mind");
    expect(checkPath(r, "../../secrets/teeny.env", "read")).toMatch(/closed/);
    expect(checkPath(r, "work/../../../secrets/teeny.env", "read")).toMatch(/closed/);
    expect(checkPath(r, "work/./../../ledger/wallet.json", "read")).toMatch(/may read/);
    expect(checkPath(r, `${f.home}/../../secrets/x`, "read")).toMatch(/closed/);
    expect(checkPath(r, "/etc/passwd", "read")).toMatch(/may read/);
  });

  it("follows symlinks before judging: a link inside home to secrets is secrets", () => {
    const f = makeFixture();
    const r = rules(f, "mind");
    link(join(f.root, "secrets"), join(f.home, "work", "innocent"));
    expect(checkPath(r, "work/innocent/teeny.env", "read")).toMatch(/closed/);
    expect(checkPath(r, "work/innocent/new.txt", "write")).toMatch(/closed/);
    // a link to the ledger (not closed, not a root)
    link(join(f.root, "state/ledger"), join(f.home, "work", "led"));
    expect(checkPath(r, "work/led/wallet.json", "read")).toMatch(/may read/);
  });

  it("follows a dangling link: writing through it would create its target", () => {
    const f = makeFixture();
    const r = rules(f, "mind");
    link(join(f.root, "secrets", "planted.txt"), join(f.home, "work", "dangling"));
    expect(checkPath(r, "work/dangling", "write")).toMatch(/closed/);
    link(join(f.root, "state/ledger/new.json"), join(f.home, "work", "dangling2"));
    expect(checkPath(r, "work/dangling2", "write")).toMatch(/may write/);
  });

  it("refuses a symlink loop and a NUL byte, and a missing path", () => {
    const f = makeFixture();
    const r = rules(f, "mind");
    symlinkSync(join(f.home, "work", "b"), join(f.home, "work", "a"));
    symlinkSync(join(f.home, "work", "a"), join(f.home, "work", "b"));
    expect(checkPath(r, "work/a", "read")).toMatch(/symbolic link loop/);
    expect(checkPath(r, "work/a\0.txt", "read")).toMatch(/NUL/);
    expect(checkPath(r, "", "read")).toMatch(/missing/);
    expect(checkPath(r, undefined, "read")).toMatch(/missing/);
    expect(() => realish(join(f.home, "work", "a"))).toThrow();
  });

  it("expands ~ to the configured home, never the process's", () => {
    expect(expandHome("~/x", "/h")).toBe("/h/x");
    expect(expandHome("~", "/h")).toBe("/h");
    expect(expandHome("~root/x", "/h")).toBe("~root/x");
    const f = makeFixture();
    expect(checkPath(rules(f, "mind"), "~/.ssh/id_rsa", "read")).toMatch(/may read/);
  });

  it("keeps the operator to the desk and the evolver read-only outside a lab", () => {
    const f = makeFixture();
    const op = rules(f, "operator");
    expect(checkPath(op, "state/workbench/desk/task.json", "read")).toBeUndefined();
    expect(checkPath(op, "state/workbench/desk/task-done.json", "write")).toBeUndefined();
    expect(checkPath(op, "state/workbench/other.txt", "write")).toMatch(/may write/);
    expect(checkPath(op, "hands/x", "read")).toMatch(/may read/);
    const ev = rules(f, "evolver");
    expect(checkPath(ev, "hands/x", "read")).toBeUndefined();
    expect(checkPath(ev, "hands/x", "write")).toMatch(/may write/);
  });

  it("on a lab spine the evolver writes its worktree, never .git, secrets, state or the core's files", () => {
    const f = makeFixture({ env: { SPINE_LAB: "1", SPINE_LAB_EDIT: "1" } });
    const r = rules(f, "evolver");
    expect(checkPath(r, "hands/x", "write")).toBeUndefined();
    expect(checkPath(r, "brain/library/note.md", "write")).toBeUndefined();
    for (const p of [".git/config", "secrets/teeny.env", "state/ledger/wallet.json", "brain/storage.db", "brain/agent/prompts/x.md", "HYPOTHESIS.md", ".openhuman/config.toml"]) {
      expect(checkPath(r, p, "write"), p).toMatch(/closed/);
    }
    // a link out of the worktree is judged by where it leads
    link("/etc", join(f.root, "etc-link"));
    expect(checkPath(r, "etc-link/passwd", "write")).toMatch(/may write/);
  });

  it("honors an explicit trusted root inside a forbidden folder whatever order the roots come in", () => {
    const f = makeFixture();
    const proj = join(f.root, "srv-home", "project");
    write(join(proj, "a.txt"));
    const base = { workspace: f.root, home: f.policy.home, closed: [], ro: [], systemForbidden: [join(f.root, "srv-home")] };
    for (const rw of [[f.root, proj], [proj, f.root]]) {
      expect(checkPath({ ...base, rw }, join(proj, "a.txt"), "write"), rw.join(",")).toBeUndefined();
      // the forbidden folder itself stays forbidden for a file outside the explicit root
      write(join(f.root, "srv-home", "other.txt"));
      expect(checkPath({ ...base, rw }, join(f.root, "srv-home", "other.txt"), "read"), rw.join(",")).toMatch(/forbidden path/);
    }
  });

  it("does not forbid a checkout that sits under /home: a trusted root lifts the system list", () => {
    const f = makeFixture();
    const r = rules(f, "mind");
    mkdirSync(join(f.home, "work"), { recursive: true });
    write(join(f.home, "work", "ok.txt"));
    const under = { ...r, systemForbidden: [f.root.split("/").slice(0, 2).join("/") || "/tmp"] };
    expect(checkPath(under, "work/ok.txt", "read")).toBeUndefined();
    // but a forbidden folder inside a root stays forbidden
    const inside = { ...r, systemForbidden: [join(f.home, "work")] };
    expect(checkPath(inside, "work/ok.txt", "read")).toMatch(/forbidden path/);
  });
});
