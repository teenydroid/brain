// A thin client for OpenClaw automations, through the `openclaw automations` CLI
// with --json (node_modules/openclaw/docs/cli/cron.md). The CLI talks to the
// running Gateway and needs operator.admin for add, edit, remove and run.
import { execFile } from "node:child_process";

export interface ExecResult {
  code: number;
  stdout: string;
  stderr: string;
}
export type Exec = (file: string, args: string[], opts: { env: Record<string, string | undefined> }) => Promise<ExecResult>;

export const defaultExec: Exec = (file, args, opts) =>
  new Promise((resolve) => {
    execFile(file, args, { env: opts.env, maxBuffer: 16 * 1024 * 1024, timeout: 120_000 }, (error, stdout, stderr) => {
      const code = error ? (typeof (error as { code?: unknown }).code === "number" ? ((error as { code: number }).code) : 1) : 0;
      resolve({ code, stdout: String(stdout), stderr: error && !stderr ? error.message : String(stderr) });
    });
  });

/** The fields of an automation job (docs: cli/cron.md, the CronJob schema) that Teeny reads. */
export interface OcJob {
  id: string;
  name?: string;
  agentId?: string;
  enabled?: boolean;
  deleteAfterRun?: boolean;
  createdAtMs?: number;
  schedule?: { kind: string; expr?: string; tz?: string; at?: string; staggerMs?: number };
  sessionTarget?: string;
  payload?: { kind?: string; message?: string; text?: string; argv?: string[]; cwd?: string; timeoutSeconds?: number };
  delivery?: { mode?: string };
  state?: { nextRunAtMs?: number; runningAtMs?: number; lastRunAtMs?: number; lastRunStatus?: string; lastError?: string };
}

/** A run-history entry (docs: the CronRunLogEntry schema). */
export interface OcRun {
  jobId?: string;
  runAtMs?: number;
  ts?: number;
  durationMs?: number;
  status?: string;
  summary?: string;
  error?: string;
}

export class AutomationsError extends Error {}

export class Automations {
  constructor(
    private readonly bin: string,
    private readonly env: Record<string, string | undefined>,
    private readonly exec: Exec = defaultExec,
  ) {}

  private async run(args: string[], json = true): Promise<unknown> {
    const argv = ["automations", ...args, ...(json ? ["--json"] : [])];
    const r = await this.exec(this.bin, argv, { env: this.env });
    if (r.code !== 0) throw new AutomationsError((r.stderr || r.stdout).trim() || `openclaw automations ${args[0]} failed`);
    const text = r.stdout.trim();
    if (text === "") return null;
    try {
      return JSON.parse(text);
    } catch {
      throw new AutomationsError(`openclaw automations ${args[0]} printed no JSON: ${text.slice(0, 200)}`);
    }
  }

  /** Every job, disabled ones too. The output is {jobs: [...]} or a bare array. */
  async list(): Promise<OcJob[]> {
    return unwrapJobs(await this.run(["list", "--all"]));
  }

  async get(id: string): Promise<OcJob> {
    const job = unwrapJob(await this.run(["get", id]));
    if (!job) throw new AutomationsError(`no job ${id}`);
    return job;
  }

  async add(args: string[]): Promise<OcJob> {
    const job = unwrapJob(await this.run(["add", ...args]));
    if (!job) throw new AutomationsError("automations add returned no job");
    return job;
  }

  async edit(id: string, args: string[]): Promise<void> {
    await this.run(["edit", id, ...args]);
  }

  async setEnabled(id: string, enabled: boolean): Promise<void> {
    await this.run([enabled ? "enable" : "disable", id]);
  }

  async remove(id: string): Promise<void> {
    await this.run(["remove", id]);
  }

  /** Force-runs now; resolves once the Gateway has accepted it (`{ok, enqueued, runId}`). */
  async runNow(id: string): Promise<{ runId?: string }> {
    const out = (await this.run(["run", id])) as { runId?: string } | null;
    return { runId: out?.runId };
  }

  /** One page of run history, newest first: of one job, or of every job when ID is undefined (`runs --all`). */
  async runs(id: string | undefined, limit: number, offset: number): Promise<OcRun[]> {
    const args = id === undefined ? ["runs", "--all"] : ["runs", id];
    const out = await this.run([...args, "--limit", String(limit), "--offset", String(offset)]);
    if (Array.isArray(out)) return out as OcRun[];
    const o = out as { entries?: OcRun[]; runs?: OcRun[]; items?: OcRun[] } | null;
    return o?.entries ?? o?.runs ?? o?.items ?? [];
  }

  /** A `config get` of a dotted path; null when it is unset. */
  async configGet(path: string): Promise<unknown> {
    const r = await this.exec(this.bin, ["config", "get", path], { env: this.env });
    if (r.code !== 0) return null;
    const text = r.stdout.trim();
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
}

function unwrapJobs(out: unknown): OcJob[] {
  if (Array.isArray(out)) return out as OcJob[];
  if (out && typeof out === "object" && Array.isArray((out as { jobs?: unknown }).jobs)) return (out as { jobs: OcJob[] }).jobs;
  return [];
}

function unwrapJob(out: unknown): OcJob | null {
  if (!out || typeof out !== "object") return null;
  const o = out as { job?: OcJob; id?: unknown };
  if (o.job && typeof o.job === "object") return o.job;
  return typeof o.id === "string" ? (out as OcJob) : null;
}
