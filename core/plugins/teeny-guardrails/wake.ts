// The inbox-pass block put in front of the mind's turn (see wakeContext).
import { execFile } from "node:child_process";
import type { WakeContextPolicy } from "./policy.js";

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
export async function wakeContext(policy: WakeContextPolicy | undefined, agentId: string | undefined, run: RunCommand = runCommand): Promise<string | undefined> {
  if (!policy || !agentId || !policy.agents.includes(agentId)) return undefined;
  try {
    const [file, ...args] = policy.argv as [string, ...string[]];
    const env = { HOME: process.env.HOME ?? "/", PATH: process.env.PATH ?? "/usr/local/bin:/usr/bin:/bin", LANG: process.env.LANG ?? "C.UTF-8" };
    const out = (await run(file, args, { cwd: policy.cwd, env, timeoutMs: policy.timeoutMs })).trim();
    return out ? out.slice(0, policy.maxChars) : undefined;
  } catch {
    return undefined;
  }
}
