import { RpcError, type CoreContext, type RpcMethods } from "../server/types.js";
import { Automations, AutomationsError, defaultExec, type Exec } from "./automations.js";
import { SHELL_TIMEOUT_SECS, parseSchedule, runView, scheduleFlags, shellFlags, view, type ShellEnv } from "./map.js";

export interface CronOptions {
  /** The openclaw binary; OPENCLAW_BIN or `openclaw`. */
  bin?: string;
  exec?: Exec;
  shellTimeoutSecs?: number;
  /**
   * The environment the `openclaw` CLI needs to reach and authenticate to the Gateway: its token,
   * state dir, config path and URL/port (main.ts's gatewayEnv()). It is for the CLI only and is
   * never put in a shell job's environment. Without it every cron_* call fails closed.
   */
  gatewayEnv?: () => Record<string, string | undefined>;
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
const actionDir = (ctx: CoreContext): string => ctx.env.OPENHUMAN_ACTION_DIR || ctx.root;

export interface Cron {
  methods: RpcMethods;
  /** Verifies cron.skipMissedJobs; logs an error and leaves cron closed when it is not true. */
  start(ctx: CoreContext): Promise<void>;
  stop(): Promise<void>;
  /** Injects the Gateway environment (see CronOptions.gatewayEnv). */
  setGatewayEnv(fn: (() => Record<string, string | undefined>) | undefined): void;
}

const RUNS_PAGE = 200;
const RUNS_DEFAULT_LIMIT = 3000;

export function createCron(opts: CronOptions = {}): Cron {
  let gatewayEnv = opts.gatewayEnv;
  const client = (ctx: CoreContext): Automations => {
    if (!gatewayEnv) throw new RpcError("cron is closed: no Gateway environment was given to the cron module", -32000);
    const bin = opts.bin ?? gatewayEnv().OPENCLAW_BIN ?? ctx.env.OPENCLAW_BIN ?? "openclaw";
    // The CLI gets the process environment under the Gateway's, so PATH and HOME still resolve it.
    const env = { ...ctx.env, ...gatewayEnv() };
    // Rebuilt each call: the Gateway's token and port can change when it restarts.
    return new Automations(bin, env, opts.exec ?? defaultExec);
  };
  const timeout = opts.shellTimeoutSecs ?? SHELL_TIMEOUT_SECS;
  let verified = false;
  /**
   * cron.skipMissedJobs must be true (Teeny's no-replay rule; the generated Gateway config sets it).
   * It is only checked here, never set: the core rewrites the config at every start, and the
   * Gateway reads the key at startup, so a late `config set` would not hold. Cron stays closed
   * until a check passes.
   */
  const verify = async (ctx: CoreContext): Promise<void> => {
    if (verified) return;
    const value = await client(ctx).configGet("cron.skipMissedJobs");
    if (value !== true) {
      throw new RpcError("cron is closed: cron.skipMissedJobs is not true in the Gateway config, so a restart would replay missed slots", -32000);
    }
    verified = true;
  };
  const wrap = async <T>(ctx: CoreContext, f: (c: Automations) => Promise<T>): Promise<T> => {
    try {
      await verify(ctx);
      return await f(client(ctx));
    } catch (error) {
      if (error instanceof AutomationsError) throw new RpcError(error.message, -32000);
      throw error;
    }
  };

  const methods: RpcMethods = {
    cron_list: (_p, ctx) => wrap(ctx, async (c) => (await c.list()).map(view)),

    cron_runs: (params, ctx) =>
      wrap(ctx, async (c) => {
        const id = str(params, "job_id");
        const limit = typeof params.limit === "number" && params.limit > 0 ? Math.floor(params.limit) : RUNS_DEFAULT_LIMIT;
        const out: Record<string, unknown>[] = [];
        for (let offset = 0; out.length < limit; offset += RUNS_PAGE) {
          const want = Math.min(RUNS_PAGE, limit - out.length);
          const page = await c.runs(id, want, offset);
          for (const r of page) {
            const jobId = r.jobId ?? id;
            if (jobId !== undefined) out.push(runView({ ...r, jobId }));
          }
          if (page.length < want) break;
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
          args.push("--agent", str(params, "agent_id", true) as string, "--message", str(params, "prompt", true) as string);
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
        if (args.length > 0) await c.edit(id, args);
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
  };
}
