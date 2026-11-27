import { spawn } from "node:child_process";

export interface ShellResult {
  ok: boolean;
  timedOut: boolean;
  /** Same layout OpenHuman recorded: status, stdout, stderr. */
  output: string;
}

const OUTPUT_CAP = 64 * 1024;

/** The whole environment a cron shell job gets: HOME, PATH and LANG, nothing else. */
export function shellEnv(env: Readonly<Record<string, string | undefined>>): Record<string, string> {
  return {
    HOME: env.HOME ?? "/root",
    PATH: env.PATH ?? "/usr/local/bin:/usr/bin:/bin",
    LANG: env.LANG ?? "C.UTF-8",
  };
}

function clip(text: string): string {
  return text.length > OUTPUT_CAP ? `${text.slice(0, OUTPUT_CAP)}\n[output clipped]` : text;
}

/**
 * Runs COMMAND with `sh -c` in CWD under a hard wall-clock limit. The command
 * gets its own process group, and a timeout kills the whole group, so nothing it
 * started outlives it (the 300 s cap of G12 does not exist here).
 */
export function runShell(opts: {
  command: string;
  cwd: string;
  env: Record<string, string>;
  timeoutMs: number;
}): Promise<ShellResult> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    const child = spawn("sh", ["-c", opts.command], {
      cwd: opts.cwd,
      env: opts.env,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const finish = (result: ShellResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => {
      timedOut = true;
      try {
        if (child.pid) process.kill(-child.pid, "SIGKILL");
      } catch {
        child.kill("SIGKILL");
      }
    }, opts.timeoutMs);
    child.stdout.on("data", (d: Buffer) => {
      if (stdout.length <= OUTPUT_CAP) stdout += d.toString("utf8");
    });
    child.stderr.on("data", (d: Buffer) => {
      if (stderr.length <= OUTPUT_CAP) stderr += d.toString("utf8");
    });
    child.on("error", (error) => {
      finish({ ok: false, timedOut: false, output: `Command execution is unavailable: ${error.message}` });
    });
    child.on("close", (code, signal) => {
      if (timedOut) {
        finish({ ok: false, timedOut: true, output: `job timed out after ${opts.timeoutMs / 1000}s` });
        return;
      }
      const status = signal ? `signal: ${signal}` : `exit status: ${code}`;
      finish({
        ok: code === 0,
        timedOut: false,
        output: `status=${status}\nstdout:\n${clip(stdout).trim()}\nstderr:\n${clip(stderr).trim()}`,
      });
    });
  });
}
