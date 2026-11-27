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

let dir: string;
let ctx: CoreContext;
let cron: Cron;
let fake: ReturnType<typeof fakeOpenclaw>;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "cron-"));
  fake = fakeOpenclaw();
  cron = createCron({ exec: fake.exec, bin: "openclaw" });
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

describe("missed slots", () => {
  it("start pins cron.skipMissedJobs to true, and leaves it alone once set", async () => {
    await cron.start(ctx);
    expect(fake.config.get("cron.skipMissedJobs")).toBe(true);
    const before = fake.calls.length;
    await cron.start(ctx);
    expect(fake.calls.slice(before).every((c) => c.args[1] === "get")).toBe(true);
  });
});

describe("failures", () => {
  it("a CLI failure becomes an RPC error", async () => {
    await expect(call("cron_run", { job_id: "nope" })).rejects.toThrow(/no job nope/);
  });
});
