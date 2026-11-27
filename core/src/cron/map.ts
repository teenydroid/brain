// Teeny's job shape (the contract) to and from OpenClaw automation jobs.
import { RpcError } from "../server/types.js";
import type { OcJob, OcRun } from "./automations.js";

export type Schedule = { kind: "cron"; expr: string; tz: string } | { kind: "at"; at: string };

/** [cron] shell_timeout_secs, as bin/lib/core_sections/automation.py sets it. */
export const SHELL_TIMEOUT_SECS = 1800;
/** The scheduler's own limit sits past the wrapper's, so `timeout` ends the run with a clean 124. */
export const SCHEDULER_GRACE_SECS = 30;

export function parseSchedule(raw: unknown): Schedule {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) throw new RpcError("schedule must be an object", -32602);
  const s = raw as Record<string, unknown>;
  if (s.kind === "at") {
    if (typeof s.at !== "string" || Number.isNaN(Date.parse(s.at))) throw new RpcError("schedule.at must be an RFC 3339 timestamp", -32602);
    return { kind: "at", at: new Date(Date.parse(s.at)).toISOString() };
  }
  if (s.kind === "cron") {
    if (typeof s.expr !== "string" || !/^\S+(\s+\S+){4,5}$/.test(s.expr.trim())) throw new RpcError("schedule.expr must be a 5 or 6 field cron expression", -32602);
    const tz = typeof s.tz === "string" && s.tz !== "" ? s.tz : "UTC";
    try {
      new Intl.DateTimeFormat("en-US", { timeZone: tz });
    } catch {
      throw new RpcError(`unknown time zone ${tz}`, -32602);
    }
    return { kind: "cron", expr: s.expr.trim(), tz };
  }
  throw new RpcError("schedule.kind must be 'cron' or 'at'", -32602);
}

/** CLI flags that set a schedule (docs cli/cron.md "Schedule types"). --exact turns staggering off. */
export function scheduleFlags(s: Schedule): string[] {
  return s.kind === "at" ? ["--at", s.at] : ["--cron", s.expr, "--tz", s.tz, "--exact"];
}

export interface ShellEnv {
  HOME: string;
  PATH: string;
  LANG: string;
}

/**
 * The argv a shell job runs. OpenClaw's own `--command` is `sh -lc` (a login
 * shell, which reads profile files) with the Gateway's environment; neither
 * is what OpenHuman gave. `--command-argv` runs exact argv, so the command is
 * wrapped: `env -i` clears the environment down to HOME, PATH and LANG, and
 * `timeout` enforces shell_timeout_secs on the whole command.
 */
export function shellArgv(command: string, env: ShellEnv, timeoutSecs = SHELL_TIMEOUT_SECS): string[] {
  return ["env", "-i", `HOME=${env.HOME}`, `PATH=${env.PATH}`, `LANG=${env.LANG}`, "timeout", "-k", "10", String(timeoutSecs), "sh", "-c", command];
}

/** The command a wrapper above holds (its last argument); any other argv is joined. */
export function commandOf(argv: readonly string[] | undefined): string | undefined {
  if (!argv || argv.length === 0) return undefined;
  const i = argv.indexOf("sh");
  if (argv[0] === "env" && i >= 0 && argv[i + 1] === "-c") return argv[i + 2];
  return argv.join(" ");
}

export function shellFlags(command: string, env: ShellEnv, cwd: string, timeoutSecs = SHELL_TIMEOUT_SECS): string[] {
  return [
    "--command-argv", JSON.stringify(shellArgv(command, env, timeoutSecs)),
    "--command-cwd", cwd,
    "--timeout-seconds", String(timeoutSecs + SCHEDULER_GRACE_SECS),
  ];
}

export function scheduleOf(job: OcJob): Schedule | { kind: string } | undefined {
  const s = job.schedule;
  if (!s) return undefined;
  if (s.kind === "cron") return { kind: "cron", expr: s.expr ?? "", tz: s.tz ?? "UTC" };
  if (s.kind === "at") return { kind: "at", at: s.at ?? "" };
  return { kind: s.kind };
}

/** A job as the contract's callers read it. Absent values are left out, never null. */
export function view(job: OcJob): Record<string, unknown> {
  const agent = job.payload?.kind === "agentTurn";
  const out: Record<string, unknown> = {
    id: job.id,
    name: job.name ?? job.id,
    job_type: agent ? "agent" : "shell",
    enabled: job.enabled !== false,
    delivery: { mode: "none" },
  };
  const schedule = scheduleOf(job);
  if (schedule) out.schedule = schedule;
  if (agent) {
    if (job.agentId !== undefined) out.agent_id = job.agentId;
    if (job.payload?.message !== undefined) out.prompt = job.payload.message;
    out.session_target = job.sessionTarget ?? "isolated";
  } else {
    const command = commandOf(job.payload?.argv);
    if (command !== undefined) out.command = command;
  }
  if (job.deleteAfterRun) out.delete_after_run = true;
  const st = job.state;
  if (st?.nextRunAtMs !== undefined && job.enabled !== false) out.next_run = new Date(st.nextRunAtMs).toISOString();
  if (st?.lastRunAtMs !== undefined) out.last_run = new Date(st.lastRunAtMs).toISOString();
  if (st?.lastRunStatus !== undefined) out.last_status = st.lastRunStatus;
  if (st?.runningAtMs !== undefined) out.running = true;
  // numeric twins for the Python readers (bin/lib/cron_store.py)
  if (job.createdAtMs !== undefined) out.created_ms = job.createdAtMs;
  if (st?.runningAtMs !== undefined) out.running_ms = st.runningAtMs;
  if (st?.lastRunAtMs !== undefined) out.last_run_ms = st.lastRunAtMs;
  if (st?.nextRunAtMs !== undefined) out.next_run_ms = st.nextRunAtMs;
  return out;
}

/** A run-history entry as the readers use it (the shape of the old cron.runs rows). */
export function runView(run: OcRun): Record<string, unknown> {
  const out: Record<string, unknown> = { job_id: run.jobId ?? "", status: run.status ?? "" };
  const started = run.runAtMs;
  let finished = run.ts;
  if (finished === undefined && started !== undefined && run.durationMs !== undefined) finished = started + run.durationMs;
  if (started !== undefined) out.started_ms = started;
  if (finished !== undefined) out.finished_ms = finished;
  const seq = started ?? finished;
  if (seq !== undefined) out.seq = seq;
  if (run.durationMs !== undefined) out.duration_ms = run.durationMs;
  out.output = [run.summary, run.error].filter((x) => x).join("\n");
  return out;
}
