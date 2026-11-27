import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AgentRunError } from "../../src/gateway/client.js";
import { assertMatchesContract, loadContract } from "../contract.js";
import { startTestCore, type TestCore } from "../helpers.js";

let t: TestCore;
beforeEach(async () => {
  t = await startTestCore();
});
afterEach(async () => {
  await t.core.stop();
});

describe("inference_agent_chat", () => {
  it("answers the turn's text and matches the contract", async () => {
    t.runner.reply = { text: "hello back" };
    const answer = await t.rpc("inference_agent_chat", loadContract("inference_agent_chat").params.example);
    expect(answer.result).toBe("hello back");
    assertMatchesContract("inference_agent_chat", answer.result);
  });

  it("uses the thread id as the session key, so a thread resumes", async () => {
    await t.rpc("inference_agent_chat", { message: "one", agent_id: "teeny", thread_id: "teeny:mind" });
    await t.rpc("inference_agent_chat", { message: "two", agent_id: "teeny", thread_id: "teeny:mind" });
    expect(t.runner.calls.map((c) => c.sessionKey)).toEqual(["agent:teeny:teeny:mind", "agent:teeny:teeny:mind"]);
    expect(t.runner.calls.map((c) => c.message)).toEqual(["one", "two"]);
  });

  it("starts a fresh session when there is no thread id", async () => {
    await t.rpc("inference_agent_chat", { message: "a", agent_id: "teeny" });
    await t.rpc("inference_agent_chat", { message: "b", agent_id: "teeny" });
    const [a, b] = t.runner.calls.map((c) => c.sessionKey);
    expect(a).not.toBe(b);
    expect(a).toMatch(/^agent:teeny:once-/);
  });

  it("honours model_override (as provider/model) and cwd", async () => {
    await t.rpc("inference_agent_chat", { message: "m", agent_id: "mind", model_override: "glm-5.3-flash", cwd: "/work" });
    expect(t.runner.calls[0]).toMatchObject({ agentId: "mind", model: "local-openai/glm-5.3-flash", cwd: "/work" });
    await t.rpc("inference_agent_chat", { message: "m", agent_id: "mind", model_override: "local-openai:other" });
    expect(t.runner.calls[1]?.model).toBe("local-openai/other");
    await t.rpc("inference_agent_chat", { message: "m", agent_id: "mind" });
    expect(t.runner.calls[2]).not.toHaveProperty("model");
    expect(t.runner.calls[2]).not.toHaveProperty("cwd");
  });

  it("runs as teeny when no agent is named, and gives the turn the stored budget", async () => {
    await t.rpc("config_update_agent_settings", { agent_timeout_secs: 1800 });
    await t.rpc("inference_agent_chat", { message: "hi" });
    expect(t.runner.calls[0]).toMatchObject({ agentId: "teeny", timeoutSecs: 1800 });
  });

  it("refuses an unknown agent and a missing message", async () => {
    expect((await t.rpc("inference_agent_chat", { message: "x", agent_id: "ghost" })).error?.message).toMatch(/unknown agent: ghost/);
    expect((await t.rpc("inference_agent_chat", {})).error?.message).toMatch(/message is required/);
    expect(t.runner.calls).toHaveLength(0);
  });

  it("answers a failed turn as an error that names why", async () => {
    t.runner.reply = () => {
      throw new AgentRunError("model timed out", "timeout");
    };
    const answer = await t.rpc("inference_agent_chat", { message: "x", agent_id: "teeny" });
    expect(answer.error?.message).toMatch(/timeout.*model timed out/);
  });
});

describe("inference_agent_chat_simple", () => {
  it("runs the tool-less simple agent in a fresh session each time", async () => {
    t.runner.reply = { text: '{"verdict": "release", "reason": "fine"}' };
    const params = loadContract("inference_agent_chat_simple").params.example;
    const answer = await t.rpc("inference_agent_chat_simple", params);
    assertMatchesContract("inference_agent_chat_simple", answer.result);
    expect(answer.result).toBe('{"verdict": "release", "reason": "fine"}');
    await t.rpc("inference_agent_chat_simple", params);
    expect(t.runner.calls.every((c) => c.agentId === "simple")).toBe(true);
    expect(t.runner.calls[0]?.sessionKey).not.toBe(t.runner.calls[1]?.sessionKey);
    expect(t.runner.calls[0]?.model).toBe("local-openai/glm-5.3-flash");
  });

  it("uses the core's default model when none is asked for", async () => {
    await t.rpc("inference_agent_chat_simple", { message: "x" });
    expect(t.runner.calls[0]).not.toHaveProperty("model");
  });
});
