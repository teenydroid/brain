import { describe, expect, it } from "vitest";
import { mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { passthroughCommand, ensureGatewayToken, gatewayTokenFile, openclawEnv } from "../../src/gateway/passthrough.js";

describe("teeny-core openclaw", () => {
  it("points the CLI at the core's state and generated config, and passes no secret on", () => {
    const env = openclawEnv(
      { TEENY_ROOT: "/srv/teeny", TEENY_STATE: "/s", PATH: "/bin", HOME: "/h", OPENHUMAN_CORE_TOKEN: "no", LOCAL_OPENAI_API_KEY: "no", OPENCLAW_GATEWAY_TOKEN: "no" },
    );
    expect(env).toEqual({ PATH: "/bin", HOME: "/h", OPENCLAW_STATE_DIR: "/s/openclaw", OPENCLAW_CONFIG_PATH: "/s/openclaw/openclaw.json" });
  });

  it("keeps one Gateway token across restarts, private, and never puts it in the CLI's environment", () => {
    const dir = mkdtempSync(join(tmpdir(), "oc-token-"));
    const first = ensureGatewayToken(dir);
    expect(first).toMatch(/^[0-9a-f]{64}$/);
    expect(ensureGatewayToken(dir)).toBe(first);
    expect(statSync(gatewayTokenFile(dir)).mode & 0o777).toBe(0o600);
    const state = mkdtempSync(join(tmpdir(), "oc-state-"));
    const openclawDir = join(state, "openclaw");
    mkdirSync(openclawDir);
    writeFileSync(gatewayTokenFile(openclawDir), `${first}\n`);
    expect(openclawEnv({ TEENY_ROOT: "/r", TEENY_STATE: state })).not.toHaveProperty("OPENCLAW_GATEWAY_TOKEN");
    expect(readFileSync(gatewayTokenFile(openclawDir), "utf8").trim()).toBe(first);
  });

  it("runs the bundled package even when OPENCLAW_BIN names bin/openclaw, which calls it (no wrapper running itself)", () => {
    const withBin = passthroughCommand({ OPENCLAW_BIN: "/srv/teeny/bin/openclaw" });
    expect(withBin.cmd).toBe(process.execPath);
    expect(withBin.args[0]).toMatch(/openclaw\.mjs$/);
    expect(passthroughCommand({})).toEqual(withBin);
  });
});
