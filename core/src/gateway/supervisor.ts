// Runs `openclaw gateway` as a child, restarts it when it dies (backoff), and
// stops it on SIGTERM. The spawn function is injected so tests drive a fake.
import { spawn as nodeSpawn, type ChildProcess, type SpawnOptions } from "node:child_process";
import { openclawCommand } from "./cli.js";

export type SpawnFn = (cmd: string, args: string[], opts: SpawnOptions) => ChildProcess;

export interface SupervisorOptions {
  /** Called before every (re)start: write the config, refresh the environment. Returns the child's env. */
  prepare: () => Promise<Record<string, string>> | Record<string, string>;
  port: number;
  spawn?: SpawnFn;
  command?: { cmd: string; args: string[] };
  log?: (level: "info" | "warn" | "error", msg: string, fields?: Record<string, unknown>) => void;
  backoffMs?: { initial: number; max: number };
  /** A run longer than this resets the backoff. */
  stableMs?: number;
  /** How long stop() waits after SIGTERM before SIGKILL. */
  killAfterMs?: number;
}

export class GatewaySupervisor {
  private child: ChildProcess | undefined;
  private stopping = false;
  private timer: NodeJS.Timeout | undefined;
  private failures = 0;
  private startedAt = 0;
  private reloading = false;
  private reloadDone: (() => void) | undefined;
  /** Set while launch() is between prepare() and the spawn, so a restart cannot start a second Gateway. */
  private launching: Promise<void> | undefined;
  private restarts = 0;
  private readonly spawnFn: SpawnFn;
  private readonly log: NonNullable<SupervisorOptions["log"]>;

  constructor(private readonly opts: SupervisorOptions) {
    this.spawnFn = opts.spawn ?? ((c, a, o) => nodeSpawn(c, a, o));
    this.log = opts.log ?? (() => undefined);
  }

  get running(): boolean {
    return this.child !== undefined && this.child.exitCode === null && !this.child.killed;
  }

  get restartCount(): number {
    return this.restarts;
  }

  async start(): Promise<void> {
    this.stopping = false;
    await this.launchOrRetry();
  }

  /** launch(), and when prepare() or the spawn throws, a retry on the same backoff as a crash: a Gateway never stays down for want of one more try. */
  private async launchOrRetry(): Promise<void> {
    try {
      await this.launch();
    } catch (e) {
      this.log("error", "gateway could not be started; will retry", { error: e instanceof Error ? e.message : String(e) });
      this.scheduleRetry();
    }
  }

  private scheduleRetry(): void {
    if (this.stopping || this.timer) return;
    const { initial, max } = this.opts.backoffMs ?? { initial: 1000, max: 30_000 };
    const delay = Math.min(max, initial * 2 ** this.failures);
    this.failures += 1;
    this.restarts += 1;
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.launchOrRetry();
    }, delay);
  }

  private launch(): Promise<void> {
    const run = this.launchInner();
    this.launching = run;
    const clear = (): void => {
      if (this.launching === run) this.launching = undefined;
    };
    run.then(clear, clear);
    return run;
  }

  private async launchInner(): Promise<void> {
    if (this.stopping) return;
    const env = await this.opts.prepare();
    if (this.stopping) return;
    const { cmd, args } = this.opts.command ?? openclawCommand();
    // schema docs: `openclaw gateway run --port --bind` (docs/cli/gateway/running.md)
    const child = this.spawnFn(cmd, [...args, "gateway", "run", "--port", String(this.opts.port), "--bind", "loopback"], {
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.child = child;
    this.startedAt = Date.now();
    this.log("info", "gateway started", { pid: child.pid });
    // The Gateway's own logs may name config paths but never carry the token; still keep them at debug-ish volume.
    child.stdout?.on("data", () => undefined);
    child.stderr?.on("data", (d: Buffer) => this.log("warn", "gateway stderr", { text: d.toString("utf8").slice(0, 500) }));
    // A child that failed to spawn emits "error" and may never emit "exit" (Node docs): that is a
    // death like any other, so it takes the same restart path, once even when both events arrive.
    let done = false;
    const finish = (code: number | null, signal: NodeJS.Signals | null): void => {
      if (done) return;
      done = true;
      this.exited(child, code, signal);
    };
    child.on("error", (e) => {
      this.log("error", "gateway child error", { error: e.message });
      if (child.pid === undefined) finish(null, null); // no pid: it never started. With one, it is a kill or IPC error and the process is still ours.
    });
    child.once("exit", (code, signal) => finish(code, signal));
  }

  private exited(child: ChildProcess, code: number | null, signal: NodeJS.Signals | null): void {
    if (this.child === child) this.child = undefined;
    if (this.stopping) return;
    if (this.reloading) {
      // restartNow(): an intended restart on fresh config, no backoff and no failure counted.
      this.reloading = false;
      void this.launchOrRetry().finally(() => {
        this.reloadDone?.();
        this.reloadDone = undefined;
      });
      return;
    }
    const { initial, max } = this.opts.backoffMs ?? { initial: 1000, max: 30_000 };
    if (Date.now() - this.startedAt > (this.opts.stableMs ?? 60_000)) this.failures = 0;
    const delay = Math.min(max, initial * 2 ** this.failures);
    this.failures += 1;
    this.restarts += 1;
    this.log("warn", "gateway exited; restarting", { code, signal, delayMs: delay });
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.launchOrRetry();
    }, delay);
  }

  /**
   * Restart on freshly prepared config (settings changed); resolves once the new Gateway
   * is spawned. Starts it when it is not running. A launch still in progress is waited
   * for first (never two Gateways), and a Gateway that ignores SIGTERM is killed after
   * killAfterMs, as stop() does.
   */
  async restartNow(): Promise<void> {
    if (this.launching) await this.launching.catch(() => undefined);
    const child = this.child;
    if (!child || child.exitCode !== null) {
      if (this.timer) clearTimeout(this.timer);
      this.timer = undefined;
      await this.launchOrRetry();
      return;
    }
    this.reloading = true;
    await new Promise<void>((resolve) => {
      this.reloadDone = resolve;
      const kill = setTimeout(() => child.kill("SIGKILL"), this.opts.killAfterMs ?? 10_000);
      child.once("exit", () => clearTimeout(kill));
      child.kill("SIGTERM");
    });
  }

  async stop(): Promise<void> {
    this.stopping = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
    this.reloadDone?.();
    this.reloadDone = undefined;
    const child = this.child;
    if (!child || child.exitCode !== null) return;
    await new Promise<void>((resolve) => {
      const kill = setTimeout(() => child.kill("SIGKILL"), this.opts.killAfterMs ?? 10_000);
      child.once("exit", () => {
        clearTimeout(kill);
        resolve();
      });
      child.kill("SIGTERM");
    });
  }
}
