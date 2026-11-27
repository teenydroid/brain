// An in-memory stand-in for `openclaw automations ... --json`: it parses the flags the
// core sends (the ones in docs cli/cron.md) into stored jobs, the way the Gateway would.
import type { Exec, ExecResult } from "../../src/cron/automations.js";

export interface Call {
  args: string[];
  env: Record<string, string | undefined>;
}

export function fakeOpenclaw(): { exec: Exec; calls: Call[]; jobs: Map<string, Record<string, unknown>>; config: Map<string, unknown>; runs: Record<string, unknown>[] } {
  const jobs = new Map<string, Record<string, unknown>>();
  const config = new Map<string, unknown>([["cron.skipMissedJobs", true]]);
  const runs: Record<string, unknown>[] = [];
  const calls: Call[] = [];
  let n = 0;
  const ok = (v: unknown): ExecResult => ({ code: 0, stdout: JSON.stringify(v), stderr: "" });
  const fail = (m: string): ExecResult => ({ code: 1, stdout: "", stderr: m });
  const flag = (args: string[], name: string): string | undefined => {
    const i = args.indexOf(name);
    return i >= 0 ? args[i + 1] : undefined;
  };
  const apply = (job: Record<string, unknown>, args: string[]) => {
    const name = flag(args, "--name");
    if (name !== undefined) job.name = name;
    const cron = flag(args, "--cron");
    if (cron !== undefined) {
      job.schedule = { kind: "cron", expr: cron, tz: flag(args, "--tz") ?? "UTC" };
      (job.state as Record<string, unknown>).nextRunAtMs = Date.parse("2026-11-27T10:05:00Z");
    }
    const at = flag(args, "--at");
    if (at !== undefined) {
      job.schedule = { kind: "at", at };
      (job.state as Record<string, unknown>).nextRunAtMs = Date.parse(at);
    }
    const argv = flag(args, "--command-argv");
    if (argv !== undefined) job.payload = { kind: "command", argv: JSON.parse(argv), cwd: flag(args, "--command-cwd"), timeoutSeconds: Number(flag(args, "--timeout-seconds")) };
    const message = flag(args, "--message");
    if (message !== undefined) job.payload = { kind: "agentTurn", message };
    const agent = flag(args, "--agent");
    if (agent !== undefined) job.agentId = agent;
    const session = flag(args, "--session");
    if (session !== undefined) job.sessionTarget = session;
    if (args.includes("--no-deliver")) job.delivery = { mode: "none" };
    if (args.includes("--keep-after-run")) job.deleteAfterRun = false;
  };
  const exec: Exec = async (_file, argv, opts) => {
    calls.push({ args: argv, env: opts.env });
    if (argv[0] === "config") {
      if (argv[1] === "get") return config.has(argv[2]!) ? ok(config.get(argv[2]!)) : fail("not set");
      if (argv[1] === "set") {
        config.set(argv[2]!, JSON.parse(argv[3]!));
        return { code: 0, stdout: "", stderr: "" };
      }
    }
    if (argv[0] !== "automations") return fail("unknown");
    const [, sub, ...rest] = argv;
    const id = rest[0];
    switch (sub) {
      case "list":
        return ok({ jobs: [...jobs.values()] });
      case "add": {
        const job: Record<string, unknown> = { id: `job-${++n}`, enabled: true, createdAtMs: 1, state: {} };
        apply(job, rest);
        jobs.set(job.id as string, job);
        return ok(job);
      }
      case "runs": {
        const all = rest[0] === "--all";
        const id = all ? undefined : rest[0];
        const limit = Number(rest[rest.indexOf("--limit") + 1]);
        const offset = Number(rest[rest.indexOf("--offset") + 1]);
        const mine = runs.filter((r) => id === undefined || r.jobId === id);
        return ok({ entries: mine.slice(offset, offset + limit) });
      }
      case "get":
        return jobs.has(id!) ? ok(jobs.get(id!)) : fail(`no job ${id}`);
      case "edit":
        if (!jobs.has(id!)) return fail(`no job ${id}`);
        apply(jobs.get(id!)!, rest.slice(1));
        return ok(jobs.get(id!));
      case "enable":
      case "disable": {
        const j = jobs.get(id!);
        if (!j) return fail(`no job ${id}`);
        j.enabled = sub === "enable";
        return ok(j);
      }
      case "remove":
        return jobs.delete(id!) ? ok({ ok: true, removed: true }) : fail(`no job ${id}`);
      case "run":
        return jobs.has(id!) ? ok({ ok: true, enqueued: true, runId: "run-1" }) : fail(`no job ${id}`);
    }
    return fail(`unknown subcommand ${sub}`);
  };
  return { exec, calls, jobs, config, runs };
}
