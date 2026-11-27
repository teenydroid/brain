import { RpcError, type CoreContext, type RpcMethods } from "../server/types.js";
import { Automations, AutomationsError, defaultExec, retryWhileUnreachable, type Exec } from "./automations.js";
import { SHELL_TIMEOUT_SECS, parseSchedule, runView, scheduleFlags, shellFlags, view, type ShellEnv } from "./map.js";

export interface CronOptions {
  /** The openclaw binary; OPENCLAW_BIN or `openclaw`. */
  bin?: string;
  exec?: Exec;
  shellTimeoutSecs?: number;
  /** The clock, in ms; only the run-history window reads it. */
  now?: () => number;
  /**
   * The environment the `openclaw` CLI needs to reach and authenticate to the Gateway: its token,
   * state dir, config path and URL/port (main.ts's gatewayEnv()). It is for the CLI only and is
   * never put in a shell job's environment. Without it every cron_* call fails closed.
   */
  gatewayEnv?: () => Record<string, string | undefined>;
  /** How long a call waits out a Gateway that is restarting (default 90 s, one try a second); 0 turns it off. */
  gatewayWaitMs?: number;
  /** For tests: the clock and the pause between tries. */
  retry?: { sleep?: (ms: number) => Promise<void>; now?: () => number; delayMs?: number };
}

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

/** What a shell job's process sees: HOME, PATH and LANG from the core's own environment. */
function shellEnv(ctx: CoreContext): ShellEnv {
  return { HOME: ctx.env.HOME ?? "/root", PATH: ctx.env.PATH ?? "/usr/local/bin:/usr/bin:/bin", LANG: ctx.env.LANG ?? "C.UTF-8" };
}
/**
 * The run deadline of an agent job: `[agent] agent_timeout_secs`, which bin/spine sets before it seeds cron
 * (TEENY_AGENT_TURN_SECS in bin/lib/teeny.sh). Without it OpenClaw's scheduler lets an isolated agent turn run
 * 60 minutes, so a 15-minute beat of the mind could outlast its slot. Nothing is passed when the core has no
 * such setting (a bare unit test), which keeps OpenClaw's own default.
 */
function agentTurnFlags(ctx: CoreContext): string[] {
  const secs = ctx.services?.store.agentTimeoutSecs;
  return Number.isInteger(secs) && (secs as number) > 0 ? ["--timeout-seconds", String(secs)] : [];
}
const actionDir = (ctx: CoreContext): string => ctx.env.OPENHUMAN_ACTION_DIR || ctx.root;

export interface Cron {
  methods: RpcMethods;
  /** Verifies cron.skipMissedJobs; logs an error and leaves cron closed when it is not true. */
  start(ctx: CoreContext): Promise<void>;
  stop(): Promise<void>;
  /** Injects the Gateway environment (see CronOptions.gatewayEnv). */
  setGatewayEnv(fn: (() => Record<string, string | undefined>) | undefined): void;
  /** Sets `[cron] shell_timeout_secs` for the shell jobs added or updated from now on. */
  setShellTimeoutSecs(secs: number): void;
}

/** How long a readiness probe lets the CLI run. */
export const PROBE_TIMEOUT_MS = 8000;
const RUNS_PAGE = 200;
/**
 * With no limit given, cron_runs answers the whole window evolve compares (the last seven days
 * against the seven before, so fifteen days with a margin) and not a fixed row count: the
 * pinned five-minute jobs alone write thousands of rows a week, and a history cut short would
 * make ordinary failures look like a new spike. RUNS_HARD_CAP only stops a runaway read.
 */
const RUNS_WINDOW_MS = 15 * 24 * 3600 * 1000;
const RUNS_HARD_CAP = 200_000;

export function createCron(opts: CronOptions = {}): Cron {
  let gatewayEnv = opts.gatewayEnv;
  /** A probe asks once and answers fast: no waiting out a restarting Gateway, and the CLI is cut off after PROBE_TIMEOUT_MS. */
  const client = (ctx: CoreContext, probe = false): Automations => {
    if (!gatewayEnv) throw new RpcError("cron is closed: no Gateway environment was given to the cron module", -32000);
    const bin = opts.bin ?? gatewayEnv().OPENCLAW_BIN ?? ctx.env.OPENCLAW_BIN ?? "openclaw";
    // The CLI gets the process environment under the Gateway's, so PATH and HOME still resolve it.
    const env = { ...ctx.env, ...gatewayEnv() };
    // Rebuilt each call: the Gateway's token and port can change when it restarts.
    const given = opts.exec ?? defaultExec;
    const base: Exec = probe ? (file, args, o) => given(file, args, { ...o, timeoutMs: PROBE_TIMEOUT_MS }) : given;
    const wait = probe ? 0 : (opts.gatewayWaitMs ?? 90_000);
    return new Automations(bin, env, wait > 0 ? retryWhileUnreachable(base, { windowMs: wait, delayMs: opts.retry?.delayMs ?? 1000, ...(opts.retry?.sleep ? { sleep: opts.retry.sleep } : {}), ...(opts.retry?.now ? { now: opts.retry.now } : {}) }) : base);
  };
  let timeout = opts.shellTimeoutSecs ?? SHELL_TIMEOUT_SECS;
  let verified = false;
  /**
   * cron.skipMissedJobs must be true (Teeny's no-replay rule; the generated Gateway config sets it).
   * It is only checked here, never set: the core rewrites the config at every start, and the
   * Gateway reads the key at startup, so a late `config set` would not hold. Cron stays closed
   * until a check passes.
   */
  const verify = async (ctx: CoreContext, probe = false): Promise<void> => {
    if (verified) return;
    const value = await client(ctx, probe).configGet("cron.skipMissedJobs");
    if (value !== true) {
      throw new RpcError("cron is closed: cron.skipMissedJobs is not true in the Gateway config, so a restart would replay missed slots", -32000);
    }
    verified = true;
  };
  const wrap = async <T>(ctx: CoreContext, f: (c: Automations) => Promise<T>, probe = false): Promise<T> => {
    try {
      await verify(ctx, probe);
      return await f(client(ctx, probe));
    } catch (error) {
      if (error instanceof AutomationsError) throw new RpcError(error.message, -32000);
      throw error;
    }
  };

  const methods: RpcMethods = {
    // `probe: true` is bin/spine's readiness check: one try, bounded, so a Gateway that is down fails in seconds
    // and not after the 90 s a seeding call is willing to wait.
    cron_list: (params, ctx) => wrap(ctx, async (c) => (await c.list()).map(view), params.probe === true),

    cron_runs: (params, ctx) =>
      wrap(ctx, async (c) => {
        const id = str(params, "job_id");
        const explicit = typeof params.limit === "number" && params.limit > 0 ? Math.floor(params.limit) : undefined;
        const limit = explicit ?? RUNS_HARD_CAP;
        const cutoff = explicit === undefined ? (opts.now ?? Date.now)() - RUNS_WINDOW_MS : undefined;
        const out: Record<string, unknown>[] = [];
        for (let offset = 0; out.length < limit; offset += RUNS_PAGE) {
          const want = Math.min(RUNS_PAGE, limit - out.length);
          const page = await c.runs(id, want, offset);
          for (const r of page) {
            const jobId = r.jobId ?? id;
            if (jobId !== undefined) out.push(runView({ ...r, jobId }));
          }
          if (page.length < want) break;
          // Newest first: once a page's oldest run is past the window, nothing older is needed.
          const last = page[page.length - 1];
          const at = last?.ts ?? last?.runAtMs;
          if (cutoff !== undefined && at !== undefined && at < cutoff) break;
        }
        return out;
      }),

    cron_add: (params, ctx) =>
      wrap(ctx, async (c) => {
        const name = str(params, "name", true) as string;
        const jobType = str(params, "job_type", true);
        if (jobType !== "agent" && jobType !== "shell") throw new RpcError("job_type must be 'agent' or 'shell'", -32602);
        const schedule = parseSchedule(params.schedule);
        checkDelivery(params.delivery);
        // --no-deliver: nothing goes to a chat (docs cli/cron.md, "Delivery"); isolated: a fresh session per run.
        const args = ["--name", name, ...scheduleFlags(schedule), "--session", "isolated", "--no-deliver"];
        if (schedule.kind === "at" && params.delete_after_run !== true) args.push("--keep-after-run");
        if (jobType === "shell") {
          args.push(...shellFlags(str(params, "command", true) as string, shellEnv(ctx), actionDir(ctx), timeout));
        } else {
          args.push("--agent", str(params, "agent_id", true) as string, "--message", str(params, "prompt", true) as string, ...agentTurnFlags(ctx));
        }
        return view(await c.add(args));
      }),

    cron_update: (params, ctx) =>
      wrap(ctx, async (c) => {
        const id = str(params, "job_id", true) as string;
        const p = params.patch;
        if (typeof p !== "object" || p === null || Array.isArray(p)) throw new RpcError("patch must be an object", -32602);
        const patch = p as Record<string, unknown>;
        checkDelivery(patch.delivery);
        const job = await c.get(id);
        const agent = job.payload?.kind === "agentTurn";
        const args: string[] = [];
        const name = str(patch, "name");
        if (name !== undefined) args.push("--name", name);
        if (patch.schedule !== undefined) args.push(...scheduleFlags(parseSchedule(patch.schedule)));
        const command = str(patch, "command");
        if (command !== undefined) {
          if (agent) throw new RpcError("command is for shell jobs", -32602);
          args.push(...shellFlags(command, shellEnv(ctx), actionDir(ctx), timeout));
        }
        const prompt = str(patch, "prompt");
        const agentId = str(patch, "agent_id");
        if ((prompt !== undefined || agentId !== undefined) && !agent) throw new RpcError("prompt and agent_id are for agent jobs", -32602);
        if (prompt !== undefined) args.push("--message", prompt);
        if (agentId !== undefined) args.push("--agent", agentId);
        if (patch.enabled !== undefined && typeof patch.enabled !== "boolean") throw new RpcError("enabled must be a boolean", -32602);
        // Any update of an agent job (an edit, a re-enable, or a patch that changes nothing, as bin/seed-cron sends a
        // kept job) re-states its deadline when the one it holds is not the core's, so a job that predates the
        // setting, or a setting that has changed since, never keeps a stale one.
        const turn = agent ? agentTurnFlags(ctx) : [];
        const stale = turn.length > 0 && job.payload?.timeoutSeconds !== Number(turn[1]);
        if (args.length > 0 || stale) await c.edit(id, agent ? [...args, ...turn] : args);
        if (typeof patch.enabled === "boolean" && patch.enabled !== (job.enabled !== false)) await c.setEnabled(id, patch.enabled);
        return view(await c.get(id));
      }),

    cron_remove: (params, ctx) =>
      wrap(ctx, async (c) => {
        const id = str(params, "job_id", true) as string;
        await c.remove(id);
        return { job_id: id, removed: true };
      }),

    cron_run: (params, ctx) =>
      wrap(ctx, async (c) => {
        const id = str(params, "job_id", true) as string;
        const { runId } = await c.runNow(id);
        return { job_id: id, status: "queued", ...(runId ? { run_id: runId } : {}) };
      }),
  };

  return {
    methods,
    async start(ctx) {
      try {
        await verify(ctx);
        ctx.log("info", "cron: cron.skipMissedJobs is true");
      } catch (error) {
        ctx.log("error", `cron stays closed: ${(error as Error).message}`);
      }
    },
    async stop() {},
    setGatewayEnv(fn) {
      gatewayEnv = fn;
      verified = false;
    },
    setShellTimeoutSecs(secs) {
      if (!Number.isInteger(secs) || secs <= 0) throw new Error(`shell_timeout_secs must be a positive integer, got ${secs}`);
      timeout = secs;
    },
  };
}
