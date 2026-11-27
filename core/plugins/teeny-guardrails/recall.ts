// Memory recall: before each turn, the learnings CortexDB recalls for the
// message ride in the prompt, up to a token budget ([memory.recall]
// budget_tokens = 3000 in bin/lib/core_sections/context.py). A turn waits for
// the pack at most `timeoutMs` (recall_each_turn_ms = 5000) and then goes
// without: recall is context, so unlike the tool fences it fails open.
import { Sealer } from "../../src/config/secrets.js";
import { clientFor, loadEngine } from "../../src/memory/engine.js";
import type { RecallPolicy } from "./policy.js";
import { dirname } from "node:path";

/** About four characters to a token, the estimate the budget is held to. */
export const CHARS_PER_TOKEN = 4;

export function clip(texts: string[], budgetTokens: number): string {
  const limit = budgetTokens * CHARS_PER_TOKEN;
  const lines: string[] = [];
  let used = 0;
  for (const t of texts) {
    const line = `- ${t.replace(/\s+/g, " ").trim()}`;
    if (used + line.length + 1 > limit) {
      // Keep what fits of the first line that does not, so a long learning still shows its start.
      const room = limit - used - 1;
      if (lines.length === 0 && room > 20) lines.push(`${line.slice(0, room - 1)}…`);
      break;
    }
    lines.push(line);
    used += line.length + 1;
  }
  return lines.join("\n");
}

export interface RecallDeps {
  fetch?: typeof fetch;
}

export async function recallContext(policy: RecallPolicy, agentId: string | undefined, message: string, deps: RecallDeps = {}): Promise<string | undefined> {
  const agent = agentId ? policy.agents[agentId] : undefined;
  if (!agent || !message.trim()) return undefined;
  const stored = loadEngine(dirname(policy.engineFile));
  if (!stored) return undefined;
  let sealer: Sealer | undefined;
  try {
    sealer = stored.apiKeySealed ? Sealer.fromFile(policy.masterKeyFile) : undefined;
  } catch {
    return undefined;
  }
  let client;
  try {
    client = clientFor(stored, sealer, undefined, deps.fetch);
  } catch {
    return undefined; // a stored endpoint that is not https or loopback: no recall, no key sent
  }
  if (!client) return undefined;
  try {
    const texts = await Promise.race([
      client.recall(message.slice(0, 2000), agent.budgetTokens),
      new Promise<string[]>((_, reject) => setTimeout(() => reject(new Error("recall timed out")), policy.timeoutMs).unref()),
    ]);
    const body = clip(texts, agent.budgetTokens);
    return body ? `[memory.recall] What you remember that may bear on this:\n${body}` : undefined;
  } catch {
    return undefined;
  }
}

