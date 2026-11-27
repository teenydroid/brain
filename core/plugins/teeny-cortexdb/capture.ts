// Auto-capture: after a turn (agent_end) the exchange is written to CortexDB as one
// learning, for the agents on the allowlist only, and never on the turn's path: the
// hook hands the work to a detached promise and returns. At most `maxInFlight` writes
// run at once (a slow CortexDB drops captures, it does not queue turns behind them),
// and the stored text is cut to `maxChars`.
import type { ClientSource } from "./client.js";
import type { CortexPluginConfig } from "./config.js";
import { screenForCapture } from "./screen.js";

/** Replies that say nothing: a turn that ended on one has nothing worth keeping. */
const SILENT = /^\s*(?:NO_REPLY|HEARTBEAT_OK|NO|OK)\s*$/i;
/** Below this the exchange is an acknowledgement, not a memory. */
const MIN_CHARS = 40;
/** How much of a stored learning's confidence a captured turn claims: it was said, not concluded. */
const CAPTURE_CONFIDENCE = 0.5;

interface Message {
  role?: unknown;
  content?: unknown;
}

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((b) => (b && typeof b === "object" && (b as { type?: unknown }).type === "text" && typeof (b as { text?: unknown }).text === "string" ? (b as { text: string }).text : ""))
    .filter(Boolean)
    .join("\n");
}

/** The last user message and the assistant text that answered it, from a run's messages. */
export function lastExchange(messages: readonly unknown[]): { user: string; assistant: string } | undefined {
  let assistantAt = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as Message | null;
    if (m?.role === "assistant" && textOf(m.content).trim()) {
      assistantAt = i;
      break;
    }
  }
  if (assistantAt < 0) return undefined;
  for (let i = assistantAt - 1; i >= 0; i--) {
    const m = messages[i] as Message | null;
    if (m?.role === "user" && textOf(m.content).trim()) {
      return { user: textOf(m.content).trim(), assistant: textOf((messages[assistantAt] as Message).content).trim() };
    }
  }
  return undefined;
}

/** `User: ...` and `Assistant: ...` cut so the pair fits `maxChars`, the user's share a third. */
export function composeTurn(user: string, assistant: string, maxChars: number): string {
  const userRoom = Math.floor(maxChars / 3);
  const cut = (s: string, n: number): string => (s.length > n ? `${s.slice(0, Math.max(0, n - 1))}…` : s);
  const u = cut(user, userRoom);
  const a = cut(assistant, maxChars - u.length - 20);
  return `User: ${u}\nAssistant: ${a}`;
}

export class Capturer {
  private inFlight = 0;
  private readonly pending = new Set<Promise<void>>();

  constructor(
    private readonly config: CortexPluginConfig,
    private readonly source: ClientSource,
    private readonly warn: (message: string) => void = () => undefined,
  ) {}

  /** True when this agent's finished turns are kept. */
  allows(agentId: string | undefined): boolean {
    return agentId !== undefined && this.config.capture.agents.includes(agentId);
  }

  /** Starts the write and returns at once. Returns what it did, for tests and logs. */
  start(agentId: string | undefined, messages: readonly unknown[], success: boolean): "started" | string {
    if (!this.allows(agentId)) return "agent not on the allowlist";
    if (!success) return "the turn failed";
    const ex = lastExchange(messages);
    if (!ex) return "no exchange";
    if (SILENT.test(ex.assistant)) return "a silent reply";
    const user = screenForCapture(ex.user);
    const assistant = screenForCapture(ex.assistant);
    if (!user.ok) return `user text ${user.reason}`;
    if (!assistant.ok) return `reply ${assistant.reason}`;
    const prefix = `[${agentId}] `;
    const text = `${prefix}${composeTurn(user.text, assistant.text, this.config.capture.maxChars - prefix.length)}`;
    if (text.length < MIN_CHARS) return "too short";
    if (this.inFlight >= this.config.capture.maxInFlight) return "too many captures in flight";
    const client = this.source.get();
    if (!client) return "no CortexDB client";
    this.inFlight++;
    const job = client
      .storeLearning({ text, learningKind: "other", confidence: CAPTURE_CONFIDENCE })
      .then(() => undefined)
      .catch((e: unknown) => this.warn(`teeny-cortexdb: capture failed: ${e instanceof Error ? e.message : String(e)}`))
      .finally(() => {
        this.inFlight--;
        this.pending.delete(job);
      });
    this.pending.add(job);
    return "started";
  }

  /** Resolves when the writes in flight have settled (tests, and a clean stop). */
  async settled(): Promise<void> {
    await Promise.all([...this.pending]);
  }
}
