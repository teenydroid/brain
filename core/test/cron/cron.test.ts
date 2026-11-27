import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createCron, type Cron } from "../../src/cron/index.js";
import { commandOf, parseSchedule, shellArgv } from "../../src/cron/map.js";
import type { CoreContext } from "../../src/server/types.js";
import { assertMatchesContract, loadContract } from "../contract.js";
import { fakeOpenclaw } from "./fake.js";

const GATEWAY_ENV = () => ({ OPENCLAW_GATEWAY_TOKEN: "tok-fake", OPENCLAW_STATE_DIR: "/gw/state", OPENCLAW_CONFIG_PATH: "/gw/openclaw.json" });
let dir: string;
let ctx: CoreContext;
let cron: Cron;
let fake: ReturnType<typeof fakeOpenclaw>;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "cron-"));
  fake = fakeOpenclaw();
  cron = createCron({ exec: fake.exec, bin: "openclaw", gatewayEnv: GATEWAY_ENV });
  ctx = { env: { HOME: "/home/fake", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", OPENHUMAN_ACTION_DIR: dir }, root: dir, stateDir: dir, log: () => {} };
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const call = (m: string, p: Record<string, unknown> = {}) => cron.methods[m]!(p, ctx);
const shellJob = (command: string, extra: Record<string, unknown> = {}) => ({
  name: "teeny.t", job_type: "shell", command, schedule: { kind: "cron", expr: "*/5 * * * *", tz: "Asia/Kolkata" }, delivery: { mode: "none" }, ...extra,
});
const flagOf = (args: string[], f: string) => args[args.indexOf(f) + 1];
const added = () => fake.calls.find((c) => c.args[1] === "add")!.args;

describe("methods match the contract", () => {
  it("cron_add / list / update / remove / run", async () => {
    const job = (await call("cron_add", loadContract("cron_add").params.example)) as Record<string, unknown>;
    assertMatchesContract("cron_add", job);
    expect(job).toMatchObject({ name: "teeny.heartbeat", job_type: "shell", command: "bin/heartbeat", enabled: true });
    expect(job.next_run).toBe("2026-11-27T10:05:00.000Z");
    const listed = await call("cron_list");
    assertMatchesContract("cron_list", listed);
    expect(listed).toHaveLength(1);
    expect(job).toMatchObject({ created_ms: 1, next_run_ms: Date.parse("2026-11-27T10:05:00Z") });
    assertMatchesContract("cron_update", await call("cron_update", { job_id: job.id, patch: { schedule: { kind: "cron", expr: "30 6 * * *", tz: "UTC" }, enabled: true } }));
    expect(((await call("cron_list")) as { schedule: { expr: string } }[])[0]!.schedule.expr).toBe("30 6 * * *");
    assertMatchesContract("cron_run", await call("cron_run", { job_id: job.id }));
    assertMatchesContract("cron_remove", await call("cron_remove", { job_id: job.id }));
    expect(await call("cron_list")).toEqual([]);
    await expect(call("cron_remove", { job_id: job.id })).rejects.toThrow(/no job/);
  });

  it("enabled false is a disable, and a disabled job lists enabled false with no next_run", async () => {
    const job = (await call("cron_add", shellJob("true"))) as { id: string };
    await call("cron_update", { job_id: job.id, patch: { enabled: false } });
    expect(fake.calls.some((c) => c.args[1] === "disable")).toBe(true);
    const [listed] = (await call("cron_list")) as Record<string, unknown>[];
    expect(listed).toMatchObject({ enabled: false });
    expect(listed).not.toHaveProperty("next_run");
    await call("cron_update", { job_id: job.id, patch: { enabled: true } });
    expect(((await call("cron_list")) as { enabled: boolean }[])[0]!.enabled).toBe(true);
  });

  it("an agent job is an agent-turn payload in an isolated session, nothing delivered", async () => {
    const job = (await call("cron_add", { name: "mind.nap", job_type: "agent", agent_id: "mind", prompt: "wake up", session_target: "isolated", schedule: { kind: "at", at: "2026-11-27T12:00:00Z" }, delivery: { mode: "none" }, delete_after_run: true })) as Record<string, unknown>;
    assertMatchesContract("cron_add", job);
    expect(job).toMatchObject({ job_type: "agent", agent_id: "mind", prompt: "wake up", session_target: "isolated" });
    const a = added();
    expect(flagOf(a, "--agent")).toBe("mind");
    expect(flagOf(a, "--message")).toBe("wake up");
    expect(flagOf(a, "--session")).toBe("isolated");
    expect(flagOf(a, "--at")).toBe("2026-11-27T12:00:00.000Z");
    expect(a).toContain("--no-deliver");
    expect(a).not.toContain("--keep-after-run");
  });

  it("refuses other delivery, a missing prompt, bad schedules", async () => {
    await expect(call("cron_add", shellJob("true", { delivery: { mode: "announce" } }))).rejects.toThrow();
    await expect(call("cron_add", { name: "x", job_type: "agent", agent_id: "a", schedule: { kind: "cron", expr: "* * * * *" }, delivery: { mode: "none" } })).rejects.toThrow();
    expect(() => parseSchedule({ kind: "cron", expr: "nope", tz: "UTC" })).toThrow();
    expect(() => parseSchedule({ kind: "cron", expr: "* * * * *", tz: "Mars/Base" })).toThrow();
    expect(() => parseSchedule({ kind: "weekly" })).toThrow();
  });
});

describe("what a shell job is handed to OpenClaw", () => {
  it("uses exact argv, cwd, tz, --exact and a 1800 s limit", async () => {
    await call("cron_add", shellJob("bin/tick --detach"));
    const a = added();
    expect(flagOf(a, "--cron")).toBe("*/5 * * * *");
    expect(flagOf(a, "--tz")).toBe("Asia/Kolkata");
    expect(a).toContain("--exact");
    expect(a).not.toContain("--command"); // never the login shell form
    expect(flagOf(a, "--command-cwd")).toBe(dir);
    expect(JSON.parse(flagOf(a, "--command-argv")!)).toEqual(shellArgv("bin/tick --detach", { HOME: "/home/fake", PATH: "/usr/bin:/bin", LANG: "C.UTF-8" }));
    expect(Number(flagOf(a, "--timeout-seconds"))).toBe(1830);
    expect(commandOf(JSON.parse(flagOf(a, "--command-argv")!))).toBe("bin/tick --detach");
  });

  it("setShellTimeoutSecs sets the limit of the jobs added or updated afterwards, and refuses nonsense", async () => {
    cron.setShellTimeoutSecs(900);
    await call("cron_add", shellJob("bin/tick --detach"));
    const a = added();
    expect(JSON.parse(flagOf(a, "--command-argv")!).slice(5, 8)).toEqual(["timeout", "-k", "10"]);
    expect(JSON.parse(flagOf(a, "--command-argv")!)[8]).toBe("900");
    expect(Number(flagOf(a, "--timeout-seconds"))).toBe(930);
    for (const bad of [0, -5, 1.5, Number.NaN]) expect(() => cron.setShellTimeoutSecs(bad), String(bad)).toThrow(/positive integer/);
    await call("cron_add", shellJob("bin/x"));
    expect(Number(flagOf(fake.calls.filter((c) => c.args[1] === "add").at(-1)!.args, "--timeout-seconds"))).toBe(930);
  });

  it("the wrapper runs the command with exactly HOME, PATH and LANG", () => {
    const out = join(dir, "env.txt");
    mkdirSync(join(dir, "act"));
    const argv = shellArgv(`env | sort > ${out}; pwd >> ${out}`, { HOME: "/home/fake", PATH: process.env.PATH ?? "/usr/bin:/bin", LANG: "C.UTF-8" });
    const r = spawnSync(argv[0]!, argv.slice(1), { cwd: join(dir, "act"), env: { SECRET_TOKEN: "leak", PATH: process.env.PATH }, encoding: "utf8" });
    expect(r.status).toBe(0);
    const lines = readFileSync(out, "utf8").trim().split("\n");
    const names = lines.filter((l) => l.includes("=")).map((l) => l.split("=")[0]).filter((n) => !["PWD", "SHLVL", "_", "OLDPWD"].includes(n!));
    expect(names).toEqual(["HOME", "LANG", "PATH"]);
    expect(lines).toContain("HOME=/home/fake");
    expect(lines.at(-1)).toBe(join(dir, "act"));
  });

  it("the wrapper kills a command past the limit, children included", () => {
    const marker = join(dir, "survived");
    const argv = shellArgv(`(sleep 3; touch ${marker}) & sleep 30`, { HOME: "/h", PATH: process.env.PATH ?? "/usr/bin:/bin", LANG: "C.UTF-8" }, 1);
    const t = Date.now();
    const r = spawnSync(argv[0]!, argv.slice(1), { env: {}, encoding: "utf8" });
    expect(r.status).toBe(124);
    expect(Date.now() - t).toBeLessThan(5000);
    const wait = spawnSync("sleep", ["3.5"]);
    expect(wait.status).toBe(0);
    expect(() => readFileSync(marker)).toThrow();
  });

  it("a command patch re-wraps, and a name edit does not touch the payload", async () => {
    const job = (await call("cron_add", shellJob("true"))) as { id: string };
    await call("cron_update", { job_id: job.id, patch: { command: "bin/new" } });
    const edit = fake.calls.filter((c) => c.args[1] === "edit").at(-1)!.args;
    expect(commandOf(JSON.parse(flagOf(edit, "--command-argv")!))).toBe("bin/new");
    const listed = (await call("cron_list")) as { command: string }[];
    expect(listed[0]!.command).toBe("bin/new");
  });
});

describe("the Gateway environment", () => {
  it("reaches the openclaw CLI, and never a shell job", async () => {
    await call("cron_add", shellJob("bin/x"));
    const env = fake.calls.at(-1)!.env;
    expect(env).toMatchObject({ OPENCLAW_GATEWAY_TOKEN: "tok-fake", OPENCLAW_STATE_DIR: "/gw/state", OPENCLAW_CONFIG_PATH: "/gw/openclaw.json" });
    const argv = JSON.parse(flagOf(added(), "--command-argv")!) as string[];
    expect(argv.join(" ")).not.toContain("tok-fake");
    expect(argv.filter((a) => a.includes("=") && !a.startsWith("sh"))).toEqual(["HOME=/home/fake", "PATH=/usr/bin:/bin", "LANG=C.UTF-8"]);
  });

  it("is read afresh for every call (the token changes when the Gateway restarts)", async () => {
    let token = "one";
    cron.setGatewayEnv(() => ({ OPENCLAW_GATEWAY_TOKEN: token }));
    await call("cron_list");
    token = "two";
    await call("cron_list");
    expect(fake.calls.filter((c) => c.args[1] === "list").map((c) => c.env.OPENCLAW_GATEWAY_TOKEN)).toEqual(["one", "two"]);
  });

  it("fails closed without one", async () => {
    cron.setGatewayEnv(undefined);
    await expect(call("cron_list")).rejects.toThrow(/closed/);
    expect(fake.calls).toHaveLength(0);
  });
});

describe("missed slots", () => {
  it("only verifies cron.skipMissedJobs: it never sets it", async () => {
    await cron.start(ctx);
    expect(fake.calls.every((c) => c.args[0] === "config" && c.args[1] === "get")).toBe(true);
    await call("cron_list");
    expect(fake.calls.some((c) => c.args[1] === "set")).toBe(false);
  });

  it("stays closed while the key is not true, and opens once it is", async () => {
    fake.config.delete("cron.skipMissedJobs");
    const logs: string[] = [];
    ctx = { ...ctx, log: (l, m) => logs.push(`${l} ${m}`) };
    await cron.start(ctx);
    expect(logs.some((l) => l.startsWith("error") && l.includes("skipMissedJobs"))).toBe(true);
    for (const m of ["cron_list", "cron_runs"]) await expect(call(m)).rejects.toThrow(/skipMissedJobs/);
    await expect(call("cron_add", shellJob("true"))).rejects.toThrow(/closed/);
    await expect(call("cron_run", { job_id: "x" })).rejects.toThrow(/closed/);
    expect(fake.calls.some((c) => c.args[1] === "set" || c.args[1] === "add")).toBe(false);
    fake.config.set("cron.skipMissedJobs", true); // the config was rewritten and the Gateway restarted
    expect(await call("cron_list")).toEqual([]);
  });

  it("a key set to false is closed too", async () => {
    fake.config.set("cron.skipMissedJobs", false);
    await expect(call("cron_list")).rejects.toThrow(/skipMissedJobs/);
  });
});

describe("cron_runs", () => {
  it("answers the run history in the rows the readers use, paging through the Gateway", async () => {
    const job = (await call("cron_add", shellJob("true"))) as { id: string };
    for (let n = 0; n < 450; n++) fake.runs.push({ jobId: job.id, runAtMs: 1000 + n, ts: 1005 + n, durationMs: 5, status: n === 0 ? "error" : "ok", summary: `s${n}`, ...(n === 0 ? { error: "boom" } : {}) });
    fake.runs.push({ jobId: "other", runAtMs: 5, ts: 6, status: "ok" });
    cron = createCron({ exec: fake.exec, bin: "openclaw", gatewayEnv: GATEWAY_ENV, now: () => 2000 });
    const all = (await call("cron_runs")) as Record<string, unknown>[];
    assertMatchesContract("cron_runs", all);
    expect(all).toHaveLength(451);
    expect(all[0]).toMatchObject({ job_id: job.id, started_ms: 1000, finished_ms: 1005, seq: 1000, duration_ms: 5, status: "error", output: "s0\nboom" });
    const mine = (await call("cron_runs", { job_id: job.id, limit: 300 })) as unknown[];
    expect(mine).toHaveLength(300);
    expect(fake.calls.filter((c) => c.args[1] === "runs").some((c) => c.args.includes("--all"))).toBe(true);
    assertMatchesContract("cron_runs", loadContract("cron_runs").response.example);
  });

  it("with no limit, reads back through the whole window and not just a fixed number of rows", async () => {
    const day = 24 * 3600 * 1000;
    const now = 100 * day;
    cron = createCron({ exec: fake.exec, bin: "openclaw", gatewayEnv: GATEWAY_ENV, now: () => now });
    // 4000 runs in the last ten days (newest first, as the Gateway lists them), then one past the window.
    for (let n = 0; n < 4000; n++) fake.runs.push({ jobId: "j", runAtMs: now - n * 200, ts: now - n * 200 + 5, status: "ok" });
    for (let n = 0; n < 600; n++) fake.runs.push({ jobId: "j", runAtMs: now - 20 * day - n, ts: now - 20 * day - n + 5, status: "ok" });
    const all = (await call("cron_runs")) as unknown[];
    expect(all.length).toBeGreaterThanOrEqual(4000);
    expect(all.length).toBeLessThan(4600);
    // an explicit limit is still honoured
    expect(await call("cron_runs", { limit: 10 })).toHaveLength(10);
  });

  it("a job's runs carry its id even when the entry omits it", async () => {
    fake.runs.push({ jobId: "j", runAtMs: 1, ts: 2, status: "ok" });
    const rows = (await call("cron_runs", { job_id: "j" })) as { job_id: string }[];
    expect(rows.map((r) => r.job_id)).toEqual(["j"]);
  });
});

describe("failures", () => {
  it("a CLI failure becomes an RPC error", async () => {
    await expect(call("cron_run", { job_id: "nope" })).rejects.toThrow(/no job nope/);
  });
});
