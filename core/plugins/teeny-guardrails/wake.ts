// The inbox-pass block put in front of the mind's turn (see wakeContext).
import { execFile } from "node:child_process";
import type { ThreadContextPolicy, WakeContextPolicy } from "./policy.js";

export type RunCommand = (file: string, args: string[], opts: { cwd: string; env: Record<string, string>; timeoutMs: number }) => Promise<string>;

const runCommand: RunCommand = (file, args, opts) =>
  new Promise((resolve, reject) => {
    execFile(file, args, { cwd: opts.cwd, env: opts.env, timeout: opts.timeoutMs, maxBuffer: 1024 * 1024 }, (error, stdout) => (error ? reject(error) : resolve(stdout)));
  });

/**
 * The reply-pass block (bin/reply-queue): what waits in the inbox, put in front of the mind's turn so a beat does
 * not spend tool calls listing it. A read-only command with only HOME, PATH and LANG in its environment; like
 * recall it fails open (no block, the turn goes on) and is cut to `maxChars`.
 */
export async function wakeContext(
  policy: WakeContextPolicy | undefined,
  agentId: string | undefined,
  run: RunCommand = runCommand,
  log: (line: string) => void = () => undefined,
): Promise<string | undefined> {
  if (!policy || !agentId || !policy.agents.includes(agentId)) return undefined;
  try {
    const [file, ...args] = policy.argv as [string, ...string[]];
    const env = { HOME: process.env.HOME ?? "/", PATH: process.env.PATH ?? "/usr/local/bin:/usr/bin:/bin", LANG: process.env.LANG ?? "C.UTF-8" };
    const out = (await run(file, args, { cwd: policy.cwd, env, timeoutMs: policy.timeoutMs })).trim();
    // One line per run of an agent that gets the block, so the log says whether a run was given it.
    log(out ? `teeny-guardrails: inbox pass injected for ${agentId} (${Math.min(out.length, policy.maxChars)} chars)` : `teeny-guardrails: inbox pass for ${agentId} is empty, nothing injected`);
    return out ? out.slice(0, policy.maxChars) : undefined;
  } catch (e) {
    log(`teeny-guardrails: inbox pass for ${agentId} failed, nothing injected: ${(e as Error).message}`);
    return undefined;
  }
}

/** The conversation id a thread session's key ends in, or undefined: digits only, as X's ids are. */
export function conversationOf(sessionKey: string | undefined, agentId: string, prefix: string): string | undefined {
  const head = `agent:${agentId}:${prefix}`;
  if (!sessionKey || !sessionKey.startsWith(head)) return undefined;
  const id = sessionKey.slice(head.length);
  return /^\d{1,25}$/.test(id) ? id : undefined;
}

/**
 * The thread block (bin/x-thread CONVERSATION): one X conversation as the ledgers know it, who is in it, and what
 * Teeny said lately, put in front of a turn of the x-reply agent that runs in that conversation's session. The
 * session itself carries what was said in earlier turns of the same thread; this adds what the ledgers saw that no
 * turn did. Same rules as the inbox pass: a read-only command with a bare environment, failing open, cut to maxChars.
 */
export async function threadContext(
  policy: ThreadContextPolicy | undefined,
  agentId: string | undefined,
  sessionKey: string | undefined,
  run: RunCommand = runCommand,
  log: (line: string) => void = () => undefined,
): Promise<string | undefined> {
  if (!policy || !agentId || !policy.agents.includes(agentId)) return undefined;
  const conversation = conversationOf(sessionKey, agentId, policy.sessionPrefix);
  if (!conversation) {
    log(`teeny-guardrails: thread context for ${agentId} skipped, the session is not one X conversation's`);
    return undefined;
  }
  try {
    const [file, ...args] = policy.argv as [string, ...string[]];
    const env = { HOME: process.env.HOME ?? "/", PATH: process.env.PATH ?? "/usr/local/bin:/usr/bin:/bin", LANG: process.env.LANG ?? "C.UTF-8" };
    const out = (await run(file, [...args, conversation], { cwd: policy.cwd, env, timeoutMs: policy.timeoutMs })).trim();
    log(out ? `teeny-guardrails: thread context injected for ${agentId} (${Math.min(out.length, policy.maxChars)} chars)` : `teeny-guardrails: thread context for ${agentId} is empty`);
    return out ? out.slice(0, policy.maxChars) : undefined;
  } catch (e) {
    log(`teeny-guardrails: thread context for ${agentId} failed, nothing injected: ${(e as Error).message}`);
    return undefined;
  }
}
