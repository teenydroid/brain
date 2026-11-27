import { randomUUID } from "node:crypto";
import { homedir } from "node:os";
import { RpcError, type CoreContext, type RpcMethods } from "../server/types.js";
import { nextRunMs, parseSchedule, type Schedule } from "./schedule.js";
import { runShell, shellEnv } from "./shell.js";
import { CronStore, storagePath, type JobBody, type JobRow } from "./store.js";

/**
 * How an agent job gets its turn run. P1's agent layer provides it. SESSIONKEY is
 * `cron:<job name>:<run id>`: one fresh isolated session per run. Resolve with
 * the turn's text, or `{ok:false}`; a throw or a rejection also fails the run.
 */
export type RunAgent = (
  agentId: string,
  prompt: string,
  sessionKey: string,
  signal: AbortSignal,
) => Promise<string | { ok: boolean; output: string } | void>;

export interface CronOptions {
  runAgent?: RunAgent;
  /** [cron] shell_timeout_secs; 1800 as bin/lib/core_sections/automation.py sets it. */
  shellTimeoutSecs?: number;
  /** An agent turn's wall-clock limit (OpenHuman/OpenClaw watchdog: 60 minutes). */
  agentTimeoutSecs?: number;
  pollMs?: number;
  now?: () => number;
}

const LEASE_MS = 2 * 3600 * 1000;
const LEASE_RENEW_MS = 30_000;

function str(params: Record<string, unknown>, key: string, required = false): string | undefined {
  const v = params[key];
  if (v === undefined || v === null) {
    if (required) throw new RpcError(`${key} is required`, -32602);
    return undefined;
  }
  if (typeof v !== "string") throw new RpcError(`${key} must be a string`, -32602);
  if (required && v === "") throw new RpcError(`${key} is required`, -32602);
  return v;
}

function checkDelivery(raw: unknown): void {
  if (raw === undefined || raw === null) return;
  const mode = (raw as { mode?: unknown }).mode;
  if (typeof raw !== "object" || (mode !== undefined && mode !== "none")) {
    throw new RpcError("delivery.mode must be 'none': a job delivers nothing, its hands speak", -32602);
  }
}

export interface Cron {
  methods: RpcMethods;
  /** Opens the store, fails runs a dead process left open, and begins polling. */
  start(ctx: CoreContext): void;
  /** Stops polling and waits for the runs in flight. */
  stop(): Promise<void>;
  setAgentRunner(fn: RunAgent | undefined): void;
  /** Runs everything due now; the poll loop calls it. Exposed for tests. */
  tick(): Promise<void>;
  /** Resolves when no run is in flight. */
  idle(): Promise<void>;
}

export function createCron(opts: CronOptions = {}): Cron {
  const now = opts.now ?? Date.now;
  let runAgent = opts.runAgent;
  let store: CronStore | undefined;
  let storeCtx: CoreContext | undefined;
  let timer: NodeJS.Timeout | undefined;
  const running = new Map<string, Promise<void>>();

  const open = (ctx: CoreContext): CronStore => {
    if (!store) {
      store = new CronStore(storagePath(ctx.env, ctx.root));
      storeCtx = ctx;
    }
    return store;
  };
  const need = (): { s: CronStore; ctx: CoreContext } => {
    if (!store || !storeCtx) throw new RpcError("cron is not open", -32000);
    return { s: store, ctx: storeCtx };
  };

  const view = (s: CronStore, job: JobRow): Record<string, unknown> => {
    const out: Record<string, unknown> = {
      id: job.id,
      name: job.name,
      job_type: job.job_type,
      schedule: job.schedule,
      enabled: job.enabled,
      delivery: job.delivery,
    };
    if (job.command !== undefined) out.command = job.command;
    if (job.agent_id !== undefined) out.agent_id = job.agent_id;
    if (job.prompt !== undefined) out.prompt = job.prompt;
    if (job.session_target !== undefined) out.session_target = job.session_target;
    if (job.delete_after_run) out.delete_after_run = true;
    if (job.next_run_ms !== null) out.next_run = new Date(job.next_run_ms).toISOString();
    const last = s.runs(job.id, 1)[0];
    if (last) {
      out.last_run = new Date(last.started_ms).toISOString();
      out.last_status = last.status;
    }
    return out;
  };

  const nextFor = (schedule: Schedule, enabled: boolean): number | null =>
    enabled ? nextRunMs(schedule, now()) : null;

  const execute = async (job: JobRow, occurrenceMs: number): Promise<void> => {
    const { s, ctx } = need();
    const started = now();
    const runId = `run-${randomUUID()}`;
    const seq = s.nextSeq(job.id);
    s.putRun(runId, { job_id: job.id, status: "running", started_ms: started, seq }, started);
    const holder = `teeny-core-${process.pid}`;
    const lease = (released: boolean) =>
      s.putLease(job.id, occurrenceMs, { holder, epoch: 1, expires_ms: released ? 0 : now() + LEASE_MS, released }, now());
    lease(false);
    const renew = setInterval(() => {
      try {
        lease(false);
      } catch {
        /* a failed renewal only lets the claim go stale */
      }
    }, LEASE_RENEW_MS);
    let ok = false;
    let output = "";
    try {
      if (job.job_type === "shell") {
        const timeoutMs = (opts.shellTimeoutSecs ?? 1800) * 1000;
        const cwd = ctx.env.OPENHUMAN_ACTION_DIR || ctx.root;
        const env = shellEnv({ HOME: ctx.env.HOME ?? homedir(), PATH: ctx.env.PATH, LANG: ctx.env.LANG });
        const r = await runShell({ command: job.command ?? "", cwd, env, timeoutMs });
        ok = r.ok;
        output = r.output;
      } else if (!runAgent) {
        output = "no agent runner is configured";
      } else {
        const ac = new AbortController();
        const t = setTimeout(() => ac.abort(), (opts.agentTimeoutSecs ?? 3600) * 1000);
        try {
          const r = await runAgent(job.agent_id ?? "", job.prompt ?? "", `cron:${job.name}:${runId}`, ac.signal);
          if (typeof r === "string") {
            ok = true;
            output = r;
          } else if (r) {
            ok = r.ok;
            output = r.output;
          } else {
            ok = true;
          }
          if (ac.signal.aborted) {
            ok = false;
            output = `job timed out after ${opts.agentTimeoutSecs ?? 3600}s`;
          }
        } finally {
          clearTimeout(t);
        }
      }
    } catch (error) {
      ok = false;
      output = (error as Error).message || "run failed";
    } finally {
      clearInterval(renew);
    }
    const done = now();
    s.putRun(runId, { job_id: job.id, status: ok ? "ok" : "error", started_ms: started, finished_ms: done, seq, output }, done);
    lease(true);
    s.pruneRuns(job.id);
    if (ok && job.delete_after_run) s.removeJob(job.id, done);
    ctx.log(ok ? "info" : "warn", `cron ${job.name} ${ok ? "ok" : "failed"}`, { job: job.id, run: runId });
  };

  /** Starts a run now, unless one is already in flight. Advances next_run first. */
  const fire = (job: JobRow, manual: boolean): Promise<void> => {
    const { s } = need();
    const live = running.get(job.id);
    if (live) {
      if (manual) throw new RpcError(`job ${job.id} is already running`, -32000);
      return live;
    }
    const occurrence = job.next_run_ms ?? now();
    // The slot is claimed before the run: next_run moves to the following one.
    if (job.schedule.kind === "at") {
      if (!manual) s.putJob(job.id, { ...stripId(job), next_run_ms: null }, now());
    } else if (!manual) {
      s.putJob(job.id, { ...stripId(job), next_run_ms: nextFor(job.schedule, job.enabled) }, now());
    }
    const p = execute(job, occurrence)
      .catch((error: unknown) => need().ctx.log("error", `cron ${job.name} crashed`, { error: String(error) }))
      .finally(() => running.delete(job.id));
    running.set(job.id, p);
    return p;
  };

  const tick = async (): Promise<void> => {
    if (!store) return;
    const t = now();
    for (const job of store.listJobs()) {
      if (job.enabled && job.next_run_ms !== null && job.next_run_ms <= t && !running.has(job.id)) {
        void fire(job, false);
      }
    }
  };

  const methods: RpcMethods = {
    async cron_list(_params, ctx) {
      const s = open(ctx);
      return s.listJobs().map((j) => view(s, j));
    },

    async cron_add(params, ctx) {
      const s = open(ctx);
      const name = str(params, "name", true) as string;
      const jobType = str(params, "job_type", true);
      if (jobType !== "agent" && jobType !== "shell") throw new RpcError("job_type must be 'agent' or 'shell'", -32602);
      const schedule = parseSchedule(params.schedule);
      checkDelivery(params.delivery);
      const t = now();
      const body: JobBody = {
        name,
        job_type: jobType,
        expression: schedule.kind === "cron" ? schedule.expr : null,
        enabled: true,
        next_run_ms: nextRunMs(schedule, t),
        schedule,
        delivery: { mode: "none" },
        created_ms: t,
      };
      if (jobType === "shell") {
        body.command = str(params, "command", true) as string;
      } else {
        body.agent_id = str(params, "agent_id", true) as string;
        body.prompt = str(params, "prompt", true) as string;
        body.session_target = str(params, "session_target") ?? "isolated";
      }
      if (params.delete_after_run === true) body.delete_after_run = true;
      if (schedule.kind === "at" && body.next_run_ms === null) {
        throw new RpcError("schedule.at is in the past", -32602);
      }
      const id = randomUUID();
      s.putJob(id, body, t);
      return view(s, { ...body, id });
    },

    async cron_update(params, ctx) {
      const s = open(ctx);
      const id = str(params, "job_id", true) as string;
      const patch = params.patch;
      if (typeof patch !== "object" || patch === null || Array.isArray(patch)) throw new RpcError("patch must be an object", -32602);
      const job = s.getJob(id);
      if (!job) throw new RpcError(`no job ${id}`, -32000);
      const p = patch as Record<string, unknown>;
      const next: JobBody = { ...stripId(job) };
      if (p.enabled !== undefined) {
        if (typeof p.enabled !== "boolean") throw new RpcError("enabled must be a boolean", -32602);
        next.enabled = p.enabled;
      }
      if (p.schedule !== undefined) {
        next.schedule = parseSchedule(p.schedule);
        next.expression = next.schedule.kind === "cron" ? next.schedule.expr : null;
      }
      for (const key of ["command", "agent_id", "prompt", "name"] as const) {
        const v = str(p, key);
        if (v === undefined) continue;
        if (key === "command" && job.job_type !== "shell") throw new RpcError("command is for shell jobs", -32602);
        if ((key === "agent_id" || key === "prompt") && job.job_type !== "agent") throw new RpcError(`${key} is for agent jobs`, -32602);
        next[key] = v;
      }
      checkDelivery(p.delivery);
      // A schedule patch re-arms next_run (catch_up's rearm relies on it), and so does enabling.
      if (p.schedule !== undefined || p.enabled !== undefined) next.next_run_ms = nextFor(next.schedule, next.enabled);
      s.putJob(id, next, now());
      return view(s, { ...next, id });
    },

    async cron_remove(params, ctx) {
      const s = open(ctx);
      const id = str(params, "job_id", true) as string;
      if (!s.removeJob(id, now())) throw new RpcError(`no job ${id}`, -32000);
      s.removeRuns(id);
      return { job_id: id, removed: true };
    },

    async cron_run(params, ctx) {
      const s = open(ctx);
      const id = str(params, "job_id", true) as string;
      const job = s.getJob(id);
      if (!job) throw new RpcError(`no job ${id}`, -32000);
      void fire(job, true);
      return { job_id: id, status: "queued" };
    },
  };

  return {
    methods,
    start(ctx) {
      const s = open(ctx);
      const t = now();
      s.failInterrupted(t);
      // Slots that passed while the core was down are skipped, not replayed: a
      // posting job must not fire late (bin/lib/catch_up.py decides what is made up,
      // through cron_run). A one-off `at` wake still due fires once.
      for (const job of s.listJobs()) {
        if (!job.enabled || job.schedule.kind === "at") continue;
        if (job.next_run_ms === null || job.next_run_ms < t - 1000) {
          s.putJob(job.id, { ...stripId(job), next_run_ms: nextRunMs(job.schedule, t) }, t);
        }
      }
      timer = setInterval(() => void tick(), opts.pollMs ?? 1000);
      timer.unref();
    },
    async stop() {
      if (timer) clearInterval(timer);
      timer = undefined;
      await Promise.all([...running.values()]);
      store?.close();
      store = undefined;
    },
    setAgentRunner(fn) {
      runAgent = fn;
    },
    tick,
    async idle() {
      while (running.size > 0) await Promise.all([...running.values()]);
    },
  };
}

function stripId(job: JobRow): JobBody {
  const body: Partial<JobRow> = { ...job };
  delete body.id;
  return body as JobBody;
}
