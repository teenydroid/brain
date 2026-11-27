// Runs one agent turn on the Gateway. The CLI is the transport: `openclaw
// agent --json` is Gateway-backed (docs/cli/agent.md: "Run one agent turn
// through the Gateway"), with the session key naming the thread. The runner is
// an interface so tests drive a fake without a Gateway.
import { execFile } from "node:child_process";
import { openclawCommand } from "./cli.js";

export interface AgentRunRequest {
  agentId: string;
  /** Full session key, agent:<id>:<thread>. */
  sessionKey: string;
  message: string;
  /** provider/model; absent means the agent's configured model. */
  model?: string;
  cwd?: string;
  timeoutSecs: number;
}

export interface AgentRunResult {
  text: string;
  model?: string;
  provider?: string;
  usage?: { input?: number; output?: number; total?: number };
  costUsd?: number;
}

export interface AgentRunner {
  run(req: AgentRunRequest): Promise<AgentRunResult>;
}

export class AgentRunError extends Error {
  constructor(
    message: string,
    public readonly kind: string = "agent_error",
  ) {
    super(message);
    this.name = "AgentRunError";
  }
}

export type Exec = (
  cmd: string,
  args: string[],
  opts: { env: NodeJS.ProcessEnv; timeout: number; maxBuffer: number; stdin?: string },
) => Promise<{ stdout: string; stderr: string; code: number }>;

const defaultExec: Exec = (cmd, args, opts) =>
  new Promise((resolve) => {
    const child = execFile(cmd, args, { env: opts.env, timeout: opts.timeout, maxBuffer: opts.maxBuffer, encoding: "utf8" }, (err, stdout, stderr) => {
      const code = err ? (typeof (err as { code?: unknown }).code === "number" ? (err as { code: number }).code : 1) : 0;
      resolve({ stdout, stderr, code });
    });
    child.stdin?.end(opts.stdin ?? "");
  });

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

/** The reply text from the shapes `openclaw agent --json` documents: {final}, {payloads:[{text}]}, or the same under `result`. */
export function parseAgentJson(stdout: string): AgentRunResult {
  let doc: unknown;
  try {
    doc = JSON.parse(stdout);
  } catch {
    throw new AgentRunError(`openclaw did not answer JSON: ${stdout.slice(0, 200)}`, "bad_output");
  }
  if (!isObj(doc)) throw new AgentRunError("openclaw answered JSON that is not an object", "bad_output");
  if (doc.ok === false || doc.status === "error" || doc.status === "timeout") {
    const err = isObj(doc.error) ? doc.error : {};
    throw new AgentRunError(String(err.message ?? doc.status ?? "agent run failed"), String(err.kind ?? doc.status ?? "agent_error"));
  }
  const body: Obj = isObj(doc.result) ? doc.result : doc;
  const payloads = Array.isArray(body.payloads) ? body.payloads : Array.isArray(doc.payloads) ? doc.payloads : [];
  const fromPayloads = payloads
    .map((p) => (isObj(p) && typeof p.text === "string" ? p.text : ""))
    .filter(Boolean)
    .join("\n\n");
  const final = typeof body.final === "string" ? body.final : typeof doc.final === "string" ? doc.final : "";
  const meta = isObj(body.meta) ? body.meta : {};
  const agentMeta = isObj(meta.agentMeta) ? meta.agentMeta : {};
  const pick = (k: string): unknown => agentMeta[k] ?? body[k] ?? doc[k];
  const usage = pick("usage");
  // The gateway-backed envelope carries cost under usage.cost.total; the exec envelope as costUsd.
  const usageCost = isObj(usage) && isObj(usage.cost) ? usage.cost.total : undefined;
  const cost = pick("costUsd") ?? usageCost;
  return {
    text: final || fromPayloads,
    ...(typeof pick("model") === "string" ? { model: pick("model") as string } : {}),
    ...(typeof pick("provider") === "string" ? { provider: pick("provider") as string } : {}),
    ...(isObj(usage) ? { usage: usage as AgentRunResult["usage"] } : {}),
    ...(typeof cost === "number" ? { costUsd: cost } : {}),
  };
}

export interface CliRunnerOptions {
  /** Env for the openclaw CLI: OPENCLAW_STATE_DIR, OPENCLAW_CONFIG_PATH and the Gateway token. */
  env: () => NodeJS.ProcessEnv;
  exec?: Exec;
  command?: { cmd: string; args: string[] };
}

export class CliAgentRunner implements AgentRunner {
  constructor(private readonly opts: CliRunnerOptions) {}

  private call(args: string[], timeoutMs: number, stdin?: string): ReturnType<Exec> {
    const { cmd, args: lead } = this.opts.command ?? openclawCommand();
    return (this.opts.exec ?? defaultExec)(cmd, [...lead, ...args], {
      env: this.opts.env(),
      timeout: timeoutMs,
      maxBuffer: 64 * 1024 * 1024,
      ...(stdin !== undefined ? { stdin } : {}),
    });
  }

  async run(req: AgentRunRequest): Promise<AgentRunResult> {
    const budgetMs = (req.timeoutSecs + 30) * 1000;
    if (req.cwd) {
      // schema docs (gateway/protocol/rpc-session-control.md): sessions.create takes key, agentId, cwd.
      const made = await this.call(
        ["gateway", "call", "sessions.create", "--params", JSON.stringify({ key: req.sessionKey, agentId: req.agentId, cwd: req.cwd })],
        60_000,
      );
      if (made.code !== 0 && !/exist/i.test(made.stdout + made.stderr)) {
        throw new AgentRunError(`could not open the session in ${req.cwd}: ${(made.stderr || made.stdout).trim().slice(0, 300)}`, "session_create");
      }
    }
    const args = ["agent", "--agent", req.agentId, "--session-key", req.sessionKey, "--message-file", "-", "--json", "--timeout", String(req.timeoutSecs)];
    if (req.model) args.push("--model", req.model);
    // The message goes over stdin, never argv: it can be long and `ps` can read argv.
    const out = await this.call(args, budgetMs, req.message);
    if (out.code !== 0 && !out.stdout.trim()) {
      throw new AgentRunError(`openclaw agent exited ${out.code}: ${out.stderr.trim().slice(0, 300)}`);
    }
    return parseAgentJson(out.stdout);
  }
}
