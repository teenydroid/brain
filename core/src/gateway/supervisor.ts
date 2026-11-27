// Runs `openclaw gateway` as a child, restarts it when it dies (backoff), and
// stops it on SIGTERM. The spawn function is injected so tests drive a fake.
import { spawn as nodeSpawn, type ChildProcess, type SpawnOptions } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import { openclawCommand } from "./cli.js";

export type SpawnFn = (cmd: string, args: string[], opts: SpawnOptions) => ChildProcess;

/**
 * The proof that teeny-guardrails loaded. An agent's exec runs with OpenClaw's own allowlist off
 * (agents/definitions.ts), so a Gateway that came up without the plugin would run any command. The
 * plugin writes `file` once its hooks are registered; the supervisor removes it before every start, and a
 * Gateway still without it after maxMs is killed and restarted on the usual backoff, which never lets it
 * serve. The core's own runs of a shell agent are refused until the file is there (main.ts).
 */
export interface GuardrailsGate {
  file: string;
  /** True when the Gateway is serving, so the file is due now. Default: never; the file is waited for until maxMs (the Gateway listens before its plugins have registered). */
  listening?: () => Promise<boolean>;
  pollMs?: number;
  /** Give up waiting for either the file or the port after this long. */
  maxMs?: number;
}

export interface SupervisorOptions {
  gate?: GuardrailsGate;
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
    if (this.opts.gate) rmSync(this.opts.gate.file, { force: true });
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
    if (this.opts.gate) void this.watchGate(child, this.opts.gate);
  }

  private async watchGate(child: ChildProcess, gate: GuardrailsGate): Promise<void> {
    const pollMs = gate.pollMs ?? 100;
    const deadline = Date.now() + (gate.maxMs ?? 60_000);
    const listening = gate.listening ?? (async () => false);
    while (this.child === child && child.exitCode === null && Date.now() < deadline) {
      if (existsSync(gate.file)) return;
      if (await listening()) {
        // The plugin registers before the listener opens; look once more, then judge.
        if (existsSync(gate.file)) return;
        break;
      }
      await new Promise((r) => setTimeout(r, pollMs));
    }
    if (this.child !== child || child.exitCode !== null) return;
    this.log("error", "the Gateway is up without teeny-guardrails; stopping it (agents' exec has no other gate)", { file: gate.file });
    child.kill("SIGKILL");
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
