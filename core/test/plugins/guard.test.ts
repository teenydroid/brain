import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { blocked, link, makeFixture, rewritten } from "./fixture.js";

describe("file tools", () => {
  it("read, write and edit judge every path they carry, and run on the absolute path that was judged", async () => {
    const f = makeFixture();
    const d = await f.call("mind", "read", { path: "work/a.txt" });
    expect(rewritten(d)?.path).toBe(join(f.home, "work/a.txt"));
    expect(blocked(await f.call("mind", "write", { path: "work/new.txt", content: "x" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "edit", { path: "work/a.txt", edits: [] }))).toBeUndefined();
    expect(blocked(await f.call("mind", "read", { path: "../../secrets/teeny.env" }))).toMatch(/closed to agents/);
    expect(blocked(await f.call("mind", "read", { file_path: "../../secrets/teeny.env" }))).toMatch(/closed to agents/);
    expect(blocked(await f.call("mind", "read", { filePath: "/etc/shadow" }))).toMatch(/may read/);
    expect(blocked(await f.call("mind", "read", { paths: ["work/a.txt", "../../secrets/teeny.env"] }))).toMatch(/closed/);
    expect(blocked(await f.call("mind", "write", { path: "../../hands/x", content: "#!/bin/sh" }))).toMatch(/may write/);
    expect(blocked(await f.call("mind", "edit", { path: "../../brain/MEMORY.md" }))).toMatch(/closed/);
    expect(blocked(await f.call("mind", "write", { content: "no path" }))).toMatch(/names no path/);
    expect(blocked(await f.call("mind", "read", { path: 5 }))).toMatch(/missing or not a string/);
  });

  it("a symlink in an allowed folder does not lead out of it", async () => {
    const f = makeFixture();
    link(join(f.root, "secrets"), join(f.home, "work", "out"));
    expect(blocked(await f.call("mind", "read", { path: "work/out/teeny.env" }))).toMatch(/closed/);
    expect(blocked(await f.call("mind", "write", { path: "work/out/x" }))).toMatch(/closed/);
  });

  it("the operator reads the task and writes its report, and nothing else", async () => {
    const f = makeFixture();
    expect(rewritten(await f.call("operator", "read", { path: "state/workbench/desk/task.json" }))?.path).toBe(join(f.root, "state/workbench/desk/task.json"));
    expect(blocked(await f.call("operator", "write", { path: "state/workbench/desk/task-done.json", content: "{}" }))).toBeUndefined();
    expect(blocked(await f.call("operator", "write", { path: "state/workbench/other.txt", content: "{}" }))).toMatch(/may write/);
    expect(blocked(await f.call("operator", "read", { path: "brain/SOUL.md" }))).toMatch(/closed|may read/);
    expect(blocked(await f.call("operator", "edit", { path: "state/workbench/desk/task.json" }))).toMatch(/no edit tool/);
  });

  it("the babysitter reads files and has no tool that writes them", async () => {
    const f = makeFixture();
    expect(blocked(await f.call("babysitter", "read", { path: "pr.json" }))).toBeUndefined();
    expect(blocked(await f.call("babysitter", "write", { path: "pr.json", content: "x" }))).toMatch(/no write tool/);
    expect(blocked(await f.call("babysitter", "edit", { path: "pr.json" }))).toMatch(/no edit tool/);
  });
});

describe("the tool list is the agent's own", () => {
  it("refuses a tool the definition does not give, and a tool this guard cannot judge", async () => {
    const f = makeFixture();
    expect(blocked(await f.call("evolver", "exec", { command: "ls" }))).toMatch(/no exec tool/);
    expect(blocked(await f.call("evolver", "browser", { action: "open" }))).toMatch(/no browser tool/);
    expect(blocked(await f.call("operator", "sessions_spawn", { agentId: "worker", task: "x" }))).toMatch(/no sessions_spawn tool/);
    for (const tool of ["apply_patch", "gateway", "cron", "message", "nodes", "canvas", "plugins", "wallet_pay", "x402_fetch"]) {
      expect(blocked(await f.call("mind", tool, {})), tool).toMatch(/has no .* tool/);
    }
    // an agent with no policy holds nothing, whatever it asks for
    expect(blocked(await f.call("teeny", "read", { path: "x" }))).toMatch(/holds no tools/);
    expect(blocked(await f.call("simple", "exec", { command: "ls" }))).toMatch(/holds no tools/);
    expect(blocked(await f.guard.beforeToolCall({ toolName: "read", params: {} }, {}))).toMatch(/holds no tools/);
  });

  it("is case-insensitive about tool names", async () => {
    const f = makeFixture();
    expect(blocked(await f.call("mind", "EXEC", { command: "curl x" }))).toMatch(/curl/);
  });

  it("passes the passive tools through", async () => {
    const f = makeFixture();
    for (const tool of ["tool_search", "memory_search", "memory_get", "process"]) expect(await f.call("mind", tool, { query: "x" }), tool).toBeUndefined();
  });
});

describe("helpers: sessions_spawn", () => {
  it("lets the mind spawn its worker and nobody else, with nothing widened", async () => {
    const f = makeFixture();
    expect(blocked(await f.call("mind", "sessions_spawn", { agentId: "worker", task: "read a paper" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "sessions_spawn", { agentId: "operator", task: "x" }))).toMatch(/may spawn only worker/);
    expect(blocked(await f.call("mind", "sessions_spawn", { agentId: "mind", task: "x" }))).toMatch(/may spawn only worker/);
    expect(blocked(await f.call("mind", "sessions_spawn", { task: "x" }))).toMatch(/may spawn only worker/);
    expect(blocked(await f.call("mind", "sessions_spawn", { agentId: "worker", task: "x", runtime: "acp" }))).toMatch(/subagent runs/);
    for (const k of ["model", "cwd", "projectGitUrl", "resumeSessionId"]) {
      expect(blocked(await f.call("mind", "sessions_spawn", { agentId: "worker", task: "x", [k]: "v" })), k).toMatch(/may not be set/);
    }
    expect(blocked(await f.call("mind", "subagents", { action: "list" }))).toBeUndefined();
    // the worker has no helpers of its own
    expect(blocked(await f.call("worker", "sessions_spawn", { agentId: "worker", task: "x" }))).toMatch(/no sessions_spawn tool/);
  });
});

describe("raw http tools", () => {
  it("only the allow list, when an agent ever holds one", async () => {
    const f = makeFixture();
    f.agent("mind").tools.push("web_fetch");
    expect(blocked(await f.call("mind", "web_fetch", { url: "https://api.example.org/x" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "web_fetch", { url: "https://example.org/x" }))).toMatch(/not on deploy/);
  });
});

describe("the per-run tool-call budget", () => {
  it("lets the mind make 300 calls a wake and refuses the 301st", async () => {
    const f = makeFixture();
    expect(f.agent("mind").maxToolCalls).toBe(300);
    for (let i = 1; i <= 300; i++) {
      const d = await f.call("mind", "tool_search", { query: "x" }, { runId: "wake-1" });
      if (d) throw new Error(`call ${i} was refused: ${blocked(d)}`);
    }
    expect(blocked(await f.call("mind", "tool_search", { query: "x" }, { runId: "wake-1" }))).toMatch(/300 tool calls/);
    // a refused call still counts: the run cannot probe its way past the cap
    expect(blocked(await f.call("mind", "read", { path: "work/a.txt" }, { runId: "wake-1" }))).toMatch(/300 tool calls/);
    // another wake starts fresh
    expect(await f.call("mind", "tool_search", { query: "x" }, { runId: "wake-2" })).toBeUndefined();
  });

  it("takes each agent's cap from its definition", async () => {
    const f = makeFixture();
    expect([f.agent("operator").maxToolCalls, f.agent("babysitter").maxToolCalls, f.agent("evolver").maxToolCalls, f.agent("worker").maxToolCalls]).toEqual([30, 200, 30, 40]);
    for (let i = 0; i < 30; i++) expect(await f.call("evolver", "list", { path: "x" }, { runId: "r" })).toBeDefined();
    for (let i = 0; i < 30; i++) await f.call("operator", "tool_search", {}, { runId: "r" });
    expect(blocked(await f.call("operator", "tool_search", {}, { runId: "r" }))).toMatch(/30 tool calls/);
  });

  it("counts two agents' runs apart even when a run id repeats", async () => {
    const f = makeFixture();
    for (let i = 0; i < 30; i++) await f.call("operator", "tool_search", {}, { runId: "same" });
    expect(await f.call("mind", "tool_search", {}, { runId: "same" })).toBeUndefined();
  });

  it("forgets old runs without growing without bound", async () => {
    const f = makeFixture();
    for (let i = 0; i < 4200; i++) await f.call("mind", "tool_search", {}, { runId: `r${i}` });
    expect(await f.call("mind", "tool_search", {}, { runId: "r4199" })).toBeUndefined();
  });
});
