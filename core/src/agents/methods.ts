// inference_agent_chat and inference_agent_chat_simple: one agent turn on the
// Gateway. Contract: core/test/contract/inference_agent_chat*.json.
import { randomUUID } from "node:crypto";
import { AgentRunError } from "../gateway/client.js";
import { RpcError, type CoreContext, type RpcMethods } from "../server/types.js";
import { toModelRef } from "./model.js";

/** The agent a chat without agent_id runs as: Teeny itself. */
export const DEFAULT_AGENT = "teeny";

function str(params: Record<string, unknown>, key: string, required = false): string | undefined {
  const v = params[key];
  if (v === undefined || v === null || v === "") {
    if (required) throw new RpcError(`${key} is required`, -32602);
    return undefined;
  }
  if (typeof v !== "string") throw new RpcError(`${key} must be a string`, -32602);
  return v;
}

function services(ctx: CoreContext) {
  if (!ctx.services) throw new RpcError("core services are not available", -32603);
  return ctx.services;
}

async function turn(ctx: CoreContext, agentId: string, sessionTail: string, p: Record<string, unknown>): Promise<string> {
  const s = services(ctx);
  const message = str(p, "message", true) as string;
  const override = str(p, "model_override");
  const cwd = str(p, "cwd");
  if (!s.hasAgent(agentId)) throw new RpcError(`unknown agent: ${agentId}`, -32602);
  s.prepareAgent(agentId);
  try {
    const result = await s.runner.run({
      agentId,
      sessionKey: `agent:${agentId}:${sessionTail}`,
      message,
      ...(override ? { model: toModelRef(override) } : {}),
      ...(cwd ? { cwd } : {}),
      timeoutSecs: s.store.agentTimeoutSecs,
    });
    s.costLog.record({
      ts: Date.now(),
      agent: agentId,
      ...(result.model ? { model: result.model } : {}),
      ...(result.costUsd !== undefined ? { costUsd: result.costUsd } : {}),
      ...(result.usage?.total !== undefined ? { tokens: result.usage.total } : {}),
    });
    return result.text;
  } catch (e) {
    if (e instanceof AgentRunError) throw new RpcError(`agent turn failed (${e.kind}): ${e.message}`);
    throw e;
  }
}

export const agentMethods: RpcMethods = {
  // message; agent_id and thread_id omitted when empty; model_override; cwd.
  // The thread id is the session key, so a thread resumes where it stopped.
  async inference_agent_chat(p, ctx) {
    const agentId = str(p, "agent_id") ?? (services(ctx).hasAgent(DEFAULT_AGENT) ? DEFAULT_AGENT : "simple");
    return turn(ctx, agentId, str(p, "thread_id") ?? `once-${randomUUID()}`, p);
  },

  // A tool-less one-shot completion: a fresh session, every tool denied.
  async inference_agent_chat_simple(p, ctx) {
    return turn(ctx, "simple", `once-${randomUUID()}`, p);
  },
};
