import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { countMarks } from "../../plugins/teeny-guardrails/exec.js";
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
    for (const tool of ["tool_search", "process"]) expect(await f.call("mind", tool, { query: "x" }), tool).toBeUndefined();
  });

  it("does not offer memory-core's tools: the Gateway runs no memory plugin, and the mind's list names none", async () => {
    const f = makeFixture();
    for (const tool of ["memory_search", "memory_get"]) expect(blocked(await f.call("mind", tool, { query: "x" })), tool).toBeTruthy();
  });
});

describe("OpenClaw's tool-search controls", () => {
  // OpenClaw offers tool_describe and dispatch_action to every agent, tools.deny or not, and has no
  // per-agent switch for them (tools.toolSearch is global only). The guard is what keeps them harmless.
  it("refuses dispatch_action and tool_describe to an agent without tool_search", async () => {
    const f = makeFixture();
    for (const agent of ["evolver", "babysitter", "simple"]) {
      expect(blocked(await f.call(agent, "dispatch_action", { id: "read", args: { path: "x" } })), agent).toMatch(/no dispatch_action tool|holds no tools/);
      expect(blocked(await f.call(agent, "tool_describe", { id: "read" })), agent).toMatch(/no tool_describe tool|holds no tools/);
      expect(blocked(await f.call(agent, "tool_search", { query: "x" })), agent).toMatch(/no tool_search tool|holds no tools/);
    }
  });

  it("judges dispatch_action as the tool it names, with the arguments it passes", async () => {
    const f = makeFixture();
    // mind holds tool_search and exec, so exec is reachable through it, but under exec's own rules
    expect(blocked(await f.call("mind", "dispatch_action", { id: "exec", args: { command: "curl x" } }))).toMatch(/curl/);
    expect(blocked(await f.call("mind", "dispatch_action", { id: "read", args: { path: "../../secrets/teeny.env" } }))).toMatch(/closed to agents/);
    const ok = await f.call("mind", "dispatch_action", { id: "read", args: { path: "work/a.txt" } });
    expect((ok as { params: { id: string; args: { path: string } } }).params.id).toBe("read");
    expect((ok as { params: { args: { path: string } } }).params.args.path).toBe(join(f.home, "work/a.txt"));
  });

  it("refuses a dispatch of a tool the agent does not hold, of a control, or with no target", async () => {
    const f = makeFixture();
    expect(blocked(await f.call("operator", "dispatch_action", { id: "edit", args: { path: "state/workbench/desk/task.json" } }))).toMatch(/no edit tool/);
    expect(blocked(await f.call("mind", "dispatch_action", { id: "gateway", args: {} }))).toMatch(/no gateway tool/);
    expect(blocked(await f.call("mind", "dispatch_action", { id: "dispatch_action", args: { id: "exec" } }))).toMatch(/not a tool it may run/);
    expect(blocked(await f.call("mind", "dispatch_action", { args: {} }))).toMatch(/no id/);
    expect(blocked(await f.call("mind", "dispatch_action", { id: "read", args: ["x"] }))).toMatch(/args is not an object/);
  });

  it("counts a dispatch once against the run's budget", async () => {
    const f = makeFixture();
    for (let i = 1; i <= 30; i++) {
      const d = await f.call("operator", "dispatch_action", { id: "read", args: { path: "state/workbench/desk/task.json" } }, { runId: "r" });
      expect(blocked(d), `dispatch ${i}`).toBeUndefined();
    }
    expect(blocked(await f.call("operator", "tool_describe", { id: "read" }, { runId: "r" }))).toMatch(/30 tool calls/);
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
  it("lets the mind make 250 calls a wake and refuses the 251st", async () => {
    const f = makeFixture();
    expect(f.agent("mind").maxToolCalls).toBe(250);
    for (let i = 1; i <= 249; i++) {
      const d = await f.call("mind", "tool_search", { query: "x" }, { runId: "wake-1" });
      if (d) throw new Error(`call ${i} was refused: ${blocked(d)}`);
    }
    // a call refused by an ordinary policy check is call 250 and counts
    expect(blocked(await f.call("mind", "read", { path: "../../secrets/teeny.env" }, { runId: "wake-1" }))).toMatch(/closed/);
    // the next call would be allowed on its merits: it is the budget that refuses it
    expect(blocked(await f.call("mind", "tool_search", { query: "x" }, { runId: "wake-1" }))).toMatch(/250 tool calls/);
    expect(blocked(await f.call("mind", "tool_search", { query: "x" }, { runId: "wake-1" }))).toMatch(/250 tool calls/);
    // a refused call still counts: the run cannot probe its way past the cap
    expect(blocked(await f.call("mind", "read", { path: "work/a.txt" }, { runId: "wake-1" }))).toMatch(/250 tool calls/);
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
    // the lower cap goes first: were the key shared, the mind's next call would be the operator's 31st
    for (let i = 0; i < 30; i++) await f.call("mind", "tool_search", {}, { runId: "same" });
    expect(await f.call("operator", "tool_search", {}, { runId: "same" })).toBeUndefined();
  });

  it("forgets old runs without growing without bound", async () => {
    const f = makeFixture();
    // an old run is used up, then pushed out by newer ones: it starts fresh, while a recent used-up run stays used up
    for (let i = 0; i < 30; i++) await f.call("operator", "tool_search", {}, { runId: "old" });
    expect(blocked(await f.call("operator", "tool_search", {}, { runId: "old" }))).toMatch(/30 tool calls/);
    for (let i = 0; i < 4200; i++) await f.call("mind", "tool_search", {}, { runId: `r${i}` });
    for (let i = 0; i < 30; i++) await f.call("operator", "tool_search", {}, { runId: "recent" });
    expect(await f.call("operator", "tool_search", {}, { runId: "old" })).toBeUndefined();
    expect(blocked(await f.call("operator", "tool_search", {}, { runId: "recent" }))).toMatch(/30 tool calls/);
  });
});

describe("the per-run reply caps (the mind's inbox pass)", () => {
  const x = (f: ReturnType<typeof makeFixture>, runId: string, agent = "mind") => f.call(agent, "exec", { command: "hands/x reply --to 1" }, { runId });
  const mail = (f: ReturnType<typeof makeFixture>, runId: string) => f.call("mind", "exec", { command: "hands/mail reply" }, { runId });

  it("the mind and its worker hold 3 X replies and 2 mail replies a run, and no one else holds a cap", () => {
    const f = makeFixture();
    expect(f.agent("mind").replyCaps).toEqual({ x: 3, mail: 2 });
    expect(f.agent("worker").replyCaps).toEqual({ x: 3, mail: 2 });
    expect(f.agent("operator").replyCaps).toBeUndefined();
  });

  it("refuses the 4th hands/x reply in one mind run; another run and another agent are unaffected", async () => {
    const f = makeFixture();
    for (let i = 0; i < 3; i++) expect(blocked(await x(f, "beat-1")), `reply ${i + 1}`).toBeUndefined();
    expect(blocked(await x(f, "beat-1"))).toMatch(/already sent its 3 X replies/);
    expect(blocked(await x(f, "beat-1"))).toMatch(/already sent its 3 X replies/);
    // a read of the same hand is not a reply
    expect(blocked(await f.call("mind", "exec", { command: "hands/x mentions --status offered" }, { runId: "beat-1" }))).toBeUndefined();
    // the next beat starts fresh
    expect(blocked(await x(f, "beat-2"))).toBeUndefined();
    // the worker's own run has its own count
    expect(blocked(await x(f, "beat-1", "worker"))).toBeUndefined();
  });

  describe("the inbox-pass record", () => {
    const lines = (f: ReturnType<typeof makeFixture>) => {
      const file = join(f.paths.stateDir, "mind", "inbox-pass.jsonl");
      return existsSync(file) ? readFileSync(file, "utf8").trim().split("\n").map((l) => JSON.parse(l) as Record<string, unknown>) : [];
    };
    const end = (f: ReturnType<typeof makeFixture>, runId: string, agent = "mind") => f.guard.runEnded({ runId, success: true }, { agentId: agent, sessionKey: `agent:${agent}:t`, runId });

    it("flags a wake that was given the block and neither replied nor marked", async () => {
      const f = makeFixture();
      f.guard.blockGiven({ agentId: "mind", sessionKey: "agent:mind:t", runId: "w1" });
      await f.call("mind", "exec", { command: "hands/x mentions --status offered" }, { runId: "w1" });
      end(f, "w1");
      expect(lines(f)).toMatchObject([{ agent: "mind", run: "w1", block: true, replies: { x: 0, mail: 0 }, marks: 0, owed: true }]);
    });

    it("a reply, or a mark of a mention or mail as not worth answering, pays the pass", async () => {
      const f = makeFixture();
      for (const [run, command] of [["a", "bin/teeny do '{\"do\": \"reply_x\", \"to\": \"1\", \"text\": \"hi\"}'"], ["b", "hands/x mark 5 --status skipped --note 'a bare link'"], ["c", "hands/stimuli close"], ["d", "hands/x reply --to 1"]] as const) {
        f.guard.blockGiven({ agentId: "mind", sessionKey: "agent:mind:t", runId: run });
        await f.call("mind", "exec", { command }, { runId: run });
        end(f, run);
      }
      expect(lines(f).map((l) => [l.run, l.owed])).toEqual([["a", false], ["b", false], ["c", false], ["d", false]]);
      expect(lines(f)[0]).toMatchObject({ replies: { x: 1, mail: 0 }, marks: 0 });
      expect(lines(f)[1]).toMatchObject({ replies: { x: 0, mail: 0 }, marks: 1 });
    });

    it("a run that was given no block owes nothing", async () => {
      const f = makeFixture();
      end(f, "quiet");
      expect(lines(f)).toMatchObject([{ run: "quiet", block: false, owed: false }]);
    });

    it("counts only the marking hands: hands/x mentions and a mark of something else are not marks", () => {
      expect(countMarks("hands/x mentions --status offered")).toBe(0);
      expect(countMarks("hands/x mark 5 --status skipped && hands/stimuli close")).toBe(2);
      expect(countMarks("echo hands/x mark")).toBe(0);
    });
  });

  it("refuses the 3rd mail reply, counts the broker's reply_x and the timeout wrapper, and the channels are separate", async () => {
    const f = makeFixture();
    expect(blocked(await mail(f, "b"))).toBeUndefined();
    expect(blocked(await mail(f, "b"))).toBeUndefined();
    expect(blocked(await mail(f, "b"))).toMatch(/already sent its 2 mail replies/);
    expect(blocked(await f.call("mind", "exec", { command: "timeout 30 hands/x reply --to 1" }, { runId: "b" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "exec", { command: "bin/teeny do '{\"do\": \"reply_x\", \"to\": \"1\", \"text\": \"hi\"}'" }, { runId: "b" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "exec", { command: "hands/x reply --to 2 && hands/x reply --to 3" }, { runId: "b" }))).toMatch(/3 X replies/);
  });

  it("counts a hand named through a dotted path and a broker verb written with a JSON escape", async () => {
    const f = makeFixture();
    expect(blocked(await f.call("mind", "exec", { command: "hands/./x reply --to 1" }, { runId: "d" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "exec", { command: "hands//x reply --to 1" }, { runId: "d" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "exec", { command: "bin/teeny do '{\"do\":\"reply\\u005fx\",\"to\":\"1\",\"text\":\"hi\"}'" }, { runId: "d" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "exec", { command: "hands/./x reply --to 1" }, { runId: "d" }))).toMatch(/already sent its 3 X replies/);
  });

  it("counts a body built from a file fragment as a reply, and reads only the verb of a JSON body", async () => {
    const f = makeFixture();
    const call = (command: string, runId: string) => f.call("mind", "exec", { command }, { runId });
    // `"{\"do\":$(cat work/verb.json)...}"` is one text with a file spliced in: the guard cannot read it whole.
    const mixed = 'bin/teeny do "{\\"do\\":$(cat work/verb.json),\\"to\\":\\"1\\"}"';
    expect(blocked(await call(mixed, "e"))).toBeUndefined();
    expect(blocked(await call(mixed, "e"))).toBeUndefined();
    expect(blocked(await call(mixed, "e"))).toMatch(/already sent its 2 mail replies|already sent its 3 X replies/);
    // a mail reply that mentions reply_x is one mail reply, not an X reply
    const g = makeFixture();
    const text = (n: number) => g.call("mind", "exec", { command: `bin/teeny do '{"do":"reply_mail","text":"not a reply_x ${n}"}'` }, { runId: "g" });
    expect(blocked(await text(1))).toBeUndefined();
    expect(blocked(await text(2))).toBeUndefined();
    expect(blocked(await g.call("mind", "exec", { command: "hands/x reply --to 1" }, { runId: "g" }))).toBeUndefined();
    // a JSON body whose do is something else counts nothing, whatever its text says
    for (let i = 0; i < 4; i++) {
      expect(blocked(await g.call("mind", "exec", { command: "bin/teeny do '{\"do\":\"status\",\"text\":\"reply_x reply_mail\"}'" }, { runId: "g" })), `status ${i}`).toBeUndefined();
    }
  });

  it("counts a verb that comes from a file, and a body that an earlier part of the command could have replaced", async () => {
    const f = makeFixture();
    const call = (command: string, runId: string) => f.call("mind", "exec", { command }, { runId });
    // hands/mail "$(cat verb)": the verb is unknown, so it is a mail reply
    expect(blocked(await call('hands/mail "$(cat work/verb)" < work/mail.json', "h"))).toBeUndefined();
    expect(blocked(await call('hands/mail "$(cat work/verb)" < work/mail.json', "h"))).toBeUndefined();
    expect(blocked(await call('hands/mail "$(cat work/verb)" < work/mail.json', "h"))).toMatch(/2 mail replies/);
    // a body read after another command of the same call (which may have rewritten the file) counts on every channel
    const g = makeFixture();
    const chained = 'cp work/reply.json work/intent.json && bin/teeny do "$(cat work/intent.json)"';
    expect(blocked(await g.call("mind", "exec", { command: chained }, { runId: "i" }))).toBeUndefined();
    expect(blocked(await g.call("mind", "exec", { command: chained }, { runId: "i" }))).toBeUndefined();
    expect(blocked(await g.call("mind", "exec", { command: chained }, { runId: "i" }))).toMatch(/2 mail replies/);
  });

  it("a refused call still spends the reply, and a chain of two over the cap is refused whole", async () => {
    const f = makeFixture();
    expect(blocked(await f.call("mind", "exec", { command: "hands/x reply --to 1 && hands/x reply --to 2 && hands/x reply --to 3 && hands/x reply --to 4" }, { runId: "c" }))).toMatch(/3 X replies/);
    expect(blocked(await x(f, "c"))).toBeUndefined();
  });
});

describe("the memory tools of teeny-cortexdb", () => {
  const all = ["memory_recall", "memory_list", "memory_store", "memory_forget"];

  it("belong to no agent while the memory plugin is not in the config", async () => {
    const f = makeFixture();
    for (const tool of all) expect(blocked(await f.call("mind", tool, { query: "x", content: "x", reason: "a good reason" })), tool).toMatch(/mind has no .* tool/);
    expect(f.agent("mind").tools).not.toContain("memory_recall");
  });

  it("are the mind's, and no other shell agent's", async () => {
    const f = makeFixture({ memoryTools: true });
    expect(f.agent("mind").tools).toEqual(expect.arrayContaining(all));
    expect(blocked(await f.call("mind", "memory_recall", { query: "ships" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "memory_list", { limit: 5 }))).toBeUndefined();
    expect(blocked(await f.call("mind", "memory_store", { content: "the harbour opens at dawn" }))).toBeUndefined();
    expect(blocked(await f.call("mind", "memory_forget", { query: "harbour", reason: "it was retracted" }))).toBeUndefined();
    for (const id of ["worker", "operator", "evolver", "babysitter"]) {
      for (const tool of all) expect(blocked(await f.call(id, tool, { query: "x", content: "x", reason: "a good reason" })), `${id} ${tool}`).toMatch(/has no .* tool/);
    }
  });

  it("leave a tool-less agent tool-less, even with the plugin in", async () => {
    const f = makeFixture({ memoryTools: true });
    for (const id of ["teeny", "simple", "judge", "act", "writer", "editor", "teeny-mail"]) {
      expect(f.policy.agents[id], id).toBeUndefined();
      expect(blocked(await f.call(id, "memory_recall", { query: "x" })), id).toMatch(/holds no tools/);
    }
  });

  it("refuse a forget without a reason or a query, and any argument that asks for more than the tool offers", async () => {
    const f = makeFixture({ memoryTools: true });
    expect(blocked(await f.call("mind", "memory_forget", { query: "harbour" }))).toMatch(/reason/);
    expect(blocked(await f.call("mind", "memory_forget", { query: "harbour", reason: "short" }))).toMatch(/reason/);
    expect(blocked(await f.call("mind", "memory_forget", { reason: "it was retracted" }))).toMatch(/query/);
    expect(blocked(await f.call("mind", "memory_forget", { query: "ha", reason: "it was retracted" }))).toMatch(/query/);
    for (const extra of [{ confirmAll: true }, { all: true }, { scope: "app:x" }, { cascade: "redact_events" }, { ids: ["a"] }]) {
      expect(blocked(await f.call("mind", "memory_forget", { query: "harbour", reason: "it was retracted", ...extra })), JSON.stringify(extra)).toMatch(/takes query, reason and nothing else/);
    }
    expect(blocked(await f.call("mind", "memory_recall", { query: "" }))).toMatch(/query/);
    expect(blocked(await f.call("mind", "memory_recall", { query: "x", scope: "other" }))).toMatch(/nothing else/);
    expect(blocked(await f.call("mind", "memory_store", { content: "x".repeat(4001) }))).toMatch(/longer than/);
    expect(blocked(await f.call("mind", "memory_store", {}))).toMatch(/content/);
    expect(blocked(await f.call("mind", "memory_list", { query: "x" }))).toMatch(/nothing else/);
  });

  it("count against the run's tool-call budget like any other tool", async () => {
    const f = makeFixture({ memoryTools: true });
    const max = f.agent("mind").maxToolCalls;
    let last: string | undefined;
    for (let i = 0; i <= max; i++) last = blocked(await f.call("mind", "memory_list", {}, { runId: "budget" }));
    expect(last).toMatch(/tool calls/);
  });
});
