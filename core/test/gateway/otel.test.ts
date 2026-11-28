import { describe, expect, it } from "vitest";
import { ensureOtelPlugin, otelFromEnv, safeOtelEndpoint, trustedInstallAt, type OpenclawRun } from "../../src/gateway/otel.js";

describe("the OTLP endpoint the Gateway may send spans to", () => {
  it.each(["http://otel-collector:4318", "http://127.0.0.1:4318", "http://localhost:4318", "http://[::1]:4318", "https://otel.example.com"])(
    "takes %s",
    (url) => expect(safeOtelEndpoint(url)).toBe(true),
  );

  it.each([
    "http://otel.example.com:4318", // plain http off the box
    "http://10.0.0.5:4318", // an address, not a compose service name
    "http://1234:4318",
    // A dotless name that is not deploy/compose.yaml's collector: Docker forwards a name it cannot resolve to the
    // host's DNS, where a search domain could send it, and the prompts on its spans, off the box.
    "http://collector:4318",
    "http://langfuse-web:3000",
    "http://pk:sk@otel-collector:4318", // credentials in the config file
    "http://otel-collector:4318/?key=abc",
    "ftp://otel-collector",
    "not a url",
  ])("refuses %s", (url) => expect(safeOtelEndpoint(url)).toBe(false));
});

describe("tracing from the core's environment", () => {
  it("is off without TEENY_OTEL_URL, and says nothing", () => {
    expect(otelFromEnv({})).toEqual({ otel: undefined });
  });

  it("is on with the collector's URL, capturing content unless told not to", () => {
    expect(otelFromEnv({ TEENY_OTEL_URL: "http://otel-collector:4318/" })).toEqual({ otel: { endpoint: "http://otel-collector:4318", captureContent: true } });
    expect(otelFromEnv({ TEENY_OTEL_URL: "http://otel-collector:4318", TEENY_OTEL_CAPTURE: "0" }).otel?.captureContent).toBe(false);
  });

  it("stays off, with a reason, for an unsafe URL", () => {
    const unsafe = otelFromEnv({ TEENY_OTEL_URL: "http://traces.example.com:4318" });
    expect(unsafe.otel).toBeUndefined();
    expect(unsafe.otel === undefined && unsafe.why).toMatch(/not https/);
  });
});

const inspected = (over: Record<string, unknown> = {}): string =>
  JSON.stringify({ plugin: { id: "diagnostics-otel", origin: "global", trustedOfficialInstall: true, version: "2026.11.10", ...over } });

/** A stand-in CLI: answers inspect from `state`, and an install flips it to the trusted install at the asked version. */
function fakeCli(initial: string, installCode = 0, installs: string = inspected()): { run: OpenclawRun; calls: string[][] } {
  let state = initial;
  const calls: string[][] = [];
  const run: OpenclawRun = async (args) => {
    calls.push(args);
    if (args[1] === "inspect") return { code: state ? 0 : 1, stdout: state };
    if (installCode === 0) state = installs;
    return { code: installCode, stdout: "" };
  };
  return { run, calls };
}

describe("the official diagnostics-otel install", () => {
  it("counts only the trusted official install at the Gateway's version", () => {
    expect(trustedInstallAt(inspected(), "2026.11.10")).toBe(true);
    expect(trustedInstallAt(`[plugins] note\n${inspected()}`, "2026.11.10")).toBe(true);
    // A copy through plugins.load.paths: it loads, but the Gateway gives it no diagnostic events (2026-11-28).
    expect(trustedInstallAt(inspected({ origin: "config", trustedOfficialInstall: undefined }), "2026.11.10")).toBe(false);
    expect(trustedInstallAt(inspected({ version: "2026.11.9" }), "2026.11.10")).toBe(false);
    expect(trustedInstallAt("", "2026.11.10")).toBe(false);
    expect(trustedInstallAt("not json {", "2026.11.10")).toBe(false);
  });

  it("does nothing when it is already there", async () => {
    const cli = fakeCli(inspected());
    expect(await ensureOtelPlugin(cli.run, "2026.11.10")).toEqual({ ok: true, installed: false });
    expect(cli.calls).toEqual([["plugins", "inspect", "diagnostics-otel", "--json"]]);
  });

  it("installs the pinned version from the npm registry, leaving activation to the core's config", async () => {
    const cli = fakeCli("");
    expect(await ensureOtelPlugin(cli.run, "2026.11.10")).toEqual({ ok: true, installed: true });
    expect(cli.calls[1]).toEqual(["plugins", "install", "npm:@openclaw/diagnostics-otel@2026.11.10", "--pin", "--no-enable"]);
  });

  it("moves an install of another version to the Gateway's", async () => {
    const cli = fakeCli(inspected({ version: "2026.11.9" }));
    expect(await ensureOtelPlugin(cli.run, "2026.11.10")).toEqual({ ok: true, installed: true });
  });

  it("fails, so tracing stays off, when the install fails or is not trusted", async () => {
    expect(await ensureOtelPlugin(fakeCli("", 1).run, "2026.11.10")).toMatchObject({ ok: false, why: expect.stringMatching(/exited 1/) });
    const untrusted = fakeCli("", 0, inspected({ trustedOfficialInstall: false }));
    expect(await ensureOtelPlugin(untrusted.run, "2026.11.10")).toMatchObject({ ok: false, why: expect.stringMatching(/not the trusted official install/) });
  });

  it("gives up inside its budget when the registry hangs, so the spine's boot deadline is never at risk", async () => {
    // A clock the fake CLI moves: inspect is quick, the install hangs until the runner's timeout kills it.
    let clock = 0;
    const timeouts: number[] = [];
    const run: OpenclawRun = async (args, timeoutMs) => {
      timeouts.push(timeoutMs);
      if (args[1] === "inspect") {
        clock += 2_500;
        return { code: 1, stdout: "" };
      }
      clock += timeoutMs;
      return { code: 143, stdout: "" };
    };
    const result = await ensureOtelPlugin(run, "2026.11.10", 60_000, () => clock);
    expect(result).toMatchObject({ ok: false, why: expect.stringMatching(/took longer than 60s/) });
    expect(clock).toBeLessThanOrEqual(60_000);
    expect(timeouts).toEqual([60_000, 57_500]);
  });

  it("does not start the next step once the budget is spent", async () => {
    let clock = 0;
    const calls: string[][] = [];
    const run: OpenclawRun = async (args) => {
      calls.push(args);
      clock += args[1] === "inspect" ? 1_000 : 59_000; // the install finishes just as the budget runs out
      return { code: 0, stdout: "" };
    };
    expect(await ensureOtelPlugin(run, "2026.11.10", 60_000, () => clock)).toMatchObject({ ok: false, why: expect.stringMatching(/took longer/) });
    expect(calls.map((c) => c[1])).toEqual(["inspect", "install"]);
  });
});
