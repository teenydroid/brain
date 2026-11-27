import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { AgentRunError, type AgentRunRequest, type AgentRunner } from "../../src/gateway/client.js";
import { GuardedRunner } from "../../src/gateway/guarded.js";

const req = (agentId: string): AgentRunRequest => ({ agentId, sessionKey: `agent:${agentId}:t`, message: "hi", timeoutSecs: 5 });
const inner = (): AgentRunner & { ran: string[] } => {
  const ran: string[] = [];
  return { ran, run: async (r) => (ran.push(r.agentId), { text: "ok" }) };
};
const dir = () => mkdtempSync(join(tmpdir(), "guarded-"));

describe("GuardedRunner", () => {
  it("refuses a shell agent's turn while teeny-guardrails has not written its file (fail closed)", async () => {
    const i = inner();
    const g = new GuardedRunner(i, { file: join(dir(), "guardrails.ready"), holdsShell: (id) => id === "worker", waitMs: 50, pollMs: 10 });
    await expect(g.run(req("worker"))).rejects.toMatchObject({ name: "AgentRunError", kind: "guardrails_not_loaded" });
    expect(i.ran).toEqual([]);
  });

  it("runs a shell agent once the file is there, and a tool-less agent without it", async () => {
    const i = inner();
    const file = join(dir(), "guardrails.ready");
    const g = new GuardedRunner(i, { file, holdsShell: (id) => id === "worker", waitMs: 50, pollMs: 10 });
    await expect(g.run(req("simple"))).resolves.toEqual({ text: "ok" });
    writeFileSync(file, "{}");
    await expect(g.run(req("worker"))).resolves.toEqual({ text: "ok" });
    expect(i.ran).toEqual(["simple", "worker"]);
    expect(new AgentRunError("x")).toBeInstanceOf(Error);
  });

  it("waits for the file when a restart has just removed it", async () => {
    const file = join(dir(), "guardrails.ready");
    const g = new GuardedRunner(inner(), { file, holdsShell: () => true, waitMs: 2000, pollMs: 10 });
    setTimeout(() => writeFileSync(file, "{}"), 60);
    await expect(g.run(req("worker"))).resolves.toEqual({ text: "ok" });
  });
});
