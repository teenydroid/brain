import { RpcError, type CoreContext, type RpcMethods } from "../server/types.js";
import { Automations, AutomationsError, defaultExec, type Exec } from "./automations.js";
import { SHELL_TIMEOUT_SECS, parseSchedule, scheduleFlags, shellFlags, view, type ShellEnv } from "./map.js";

export interface CronOptions {
  /** The openclaw binary; OPENCLAW_BIN or `openclaw`. */
  bin?: string;
  exec?: Exec;
  shellTimeoutSecs?: number;
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
  /** Makes sure the Gateway never replays a missed slot (cron.skipMissedJobs). */
  start(ctx: CoreContext): Promise<void>;
  stop(): Promise<void>;
}

export function createCron(opts: CronOptions = {}): Cron {
  const clients = new Map<string, Automations>();
  const client = (ctx: CoreContext): Automations => {
    const bin = opts.bin ?? ctx.env.OPENCLAW_BIN ?? "openclaw";
    let c = clients.get(bin);
    if (!c) {
      c = new Automations(bin, { ...ctx.env }, opts.exec ?? defaultExec);
      clients.set(bin, c);
    }
    return c;
  };
  const timeout = opts.shellTimeoutSecs ?? SHELL_TIMEOUT_SECS;
  const wrap = async <T>(f: () => Promise<T>): Promise<T> => {
    try {
      return await f();
    } catch (error) {
      if (error instanceof AutomationsError) throw new RpcError(error.message, -32000);
      throw error;
    }
  };

  const methods: RpcMethods = {
    cron_list: (_p, ctx) => wrap(async () => (await client(ctx).list()).map(view)),

    cron_add: (params, ctx) =>
      wrap(async () => {
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
        return view(await client(ctx).add(args));
      }),

    cron_update: (params, ctx) =>
      wrap(async () => {
        const id = str(params, "job_id", true) as string;
        const p = params.patch;
        if (typeof p !== "object" || p === null || Array.isArray(p)) throw new RpcError("patch must be an object", -32602);
        const patch = p as Record<string, unknown>;
        checkDelivery(patch.delivery);
        const c = client(ctx);
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
      wrap(async () => {
        const id = str(params, "job_id", true) as string;
        await client(ctx).remove(id);
        return { job_id: id, removed: true };
      }),

    cron_run: (params, ctx) =>
      wrap(async () => {
        const id = str(params, "job_id", true) as string;
        const { runId } = await client(ctx).runNow(id);
        return { job_id: id, status: "queued", ...(runId ? { run_id: runId } : {}) };
      }),
  };

  return {
    methods,
    async start(ctx) {
      // Teeny's rule (cron_policy.py: no catch-up for posting; bin/lib/catch_up.py makes up the
      // jobs that allow it, through cron_run) means the scheduler must not replay missed slots.
      // OpenClaw has one switch for that, global (docs automation/cron-jobs/managing-jobs.md,
      // "Configuration"): cron.skipMissedJobs.
      const c = client(ctx);
      if ((await c.configGet("cron.skipMissedJobs")) !== true) {
        await wrap(() => c.configSet("cron.skipMissedJobs", "true"));
        ctx.log("info", "set cron.skipMissedJobs = true");
      }
    },
    async stop() {},
  };
}
