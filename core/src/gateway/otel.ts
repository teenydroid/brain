// Tracing the Gateway's turns to Langfuse (deploy/langfuse/README.md). The Gateway exports OTLP/HTTP through
// OpenClaw's own `diagnostics-otel` plugin to the otel-collector on deploy/compose.yaml's private `langfuse-net`,
// and the collector forwards to the private Langfuse with Langfuse's key, which never reaches the spine.
//
// Switched on by TEENY_OTEL_URL in the core's environment (bin/spine exports it from TRACES_OTLP_URL in
// secrets/spine.env). TEENY_OTEL_CAPTURE=0 keeps prompts, replies and tool payloads off the spans.
//
// The plugin must be OpenClaw's official install, not a copy: the Gateway hands its diagnostic event stream only to
// a `diagnostics-otel` that is bundled or `trustedOfficialInstall` (an install record from the npm registry). A copy
// loaded through plugins.load.paths loads, logs "internal diagnostics capability unavailable" and exports nothing
// (live test, 2026-11-28). So the core installs `npm:@openclaw/diagnostics-otel@<the Gateway's version>` into the
// Gateway's own state with OpenClaw's installer, once, before the Gateway starts.
import { spawn } from "node:child_process";

type Env = Readonly<Record<string, string | undefined>>;

export const OTEL_PACKAGE = "@openclaw/diagnostics-otel";

/**
 * Whether the Gateway may send spans to `url`. Captured content can hold anything a turn saw, so the rule is the
 * memory plugin's (https, or plain http on this machine) widened by one case: plain http to a bare compose service
 * name (a host with no dot, such as `otel-collector`), which resolves only on the box's own Docker networks. No
 * credentials in the URL, no query: nothing secret goes in the Gateway's config.
 */
export function safeOtelEndpoint(url: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) return false;
  const host = parsed.hostname.toLowerCase();
  if (!host) return false;
  if (parsed.protocol === "https:") return true;
  if (parsed.protocol !== "http:") return false;
  if (host === "localhost" || host === "[::1]" || /^127(\.\d{1,3}){3}$/.test(host)) return true;
  return /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(host) && !/^\d+$/.test(host);
}

export interface OtelInput {
  endpoint: string;
  captureContent: boolean;
}

export type OtelSetting = { otel: OtelInput } | { otel: undefined; why?: string };

/** The `otel` input of buildGatewayConfig from the core's environment, or why tracing stays off. */
export function otelFromEnv(env: Env): OtelSetting {
  const endpoint = (env.TEENY_OTEL_URL ?? "").trim().replace(/\/+$/, "");
  if (!endpoint) return { otel: undefined };
  if (!safeOtelEndpoint(endpoint)) {
    return { otel: undefined, why: "TEENY_OTEL_URL is not https, loopback, or http to a compose service name; turns are not traced" };
  }
  const captureContent = !/^(0|false|no|off)$/i.test((env.TEENY_OTEL_CAPTURE ?? "").trim());
  return { otel: { endpoint, captureContent } };
}

/** Runs the OpenClaw CLI on the Gateway's state and config; resolves with its exit code and stdout. */
export type OpenclawRun = (args: string[], timeoutMs: number) => Promise<{ code: number; stdout: string }>;

export function spawnOpenclaw(command: { cmd: string; args: string[] }, env: Record<string, string>): OpenclawRun {
  return (args, timeoutMs) =>
    new Promise((resolve) => {
      const child = spawn(command.cmd, [...command.args, ...args], { env, stdio: ["ignore", "pipe", "pipe"] });
      let stdout = "";
      child.stdout.on("data", (d: Buffer) => (stdout += d.toString("utf8")));
      child.stderr.resume();
      const timer = setTimeout(() => child.kill("SIGTERM"), timeoutMs);
      child.on("error", () => {
        clearTimeout(timer);
        resolve({ code: 127, stdout });
      });
      child.on("exit", (code) => {
        clearTimeout(timer);
        resolve({ code: code ?? 1, stdout });
      });
    });
}

/** What `openclaw plugins inspect diagnostics-otel --json` says of the install, if it is the trusted one at `version`. */
export function trustedInstallAt(stdout: string, version: string): boolean {
  const start = stdout.indexOf("{");
  if (start < 0) return false;
  let report: Record<string, unknown>;
  try {
    report = JSON.parse(stdout.slice(start)) as Record<string, unknown>;
  } catch {
    return false;
  }
  const plugin = (report.plugin && typeof report.plugin === "object" ? report.plugin : report) as Record<string, unknown>;
  return plugin.id === "diagnostics-otel" && plugin.trustedOfficialInstall === true && plugin.version === version;
}

/**
 * Makes sure the Gateway's state holds the official diagnostics-otel at `version`: inspects it, and installs it from
 * the npm registry when it is missing or another version. `--no-enable`: the core's config owns activation, so the
 * installer touches no allowlist or entry. Fails (and the caller leaves tracing off) when the registry cannot be
 * reached or the install is not trusted.
 */
export async function ensureOtelPlugin(run: OpenclawRun, version: string): Promise<{ ok: true; installed: boolean } | { ok: false; why: string }> {
  const inspect = (): Promise<{ code: number; stdout: string }> => run(["plugins", "inspect", "diagnostics-otel", "--json"], 60_000);
  if (trustedInstallAt((await inspect()).stdout, version)) return { ok: true, installed: false };
  const install = await run(["plugins", "install", `npm:${OTEL_PACKAGE}@${version}`, "--pin", "--no-enable"], 300_000);
  if (install.code !== 0) return { ok: false, why: `openclaw plugins install npm:${OTEL_PACKAGE}@${version} exited ${install.code}` };
  if (!trustedInstallAt((await inspect()).stdout, version)) {
    return { ok: false, why: `${OTEL_PACKAGE}@${version} installed but is not the trusted official install` };
  }
  return { ok: true, installed: true };
}
