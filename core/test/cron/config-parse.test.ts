import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { cronShellTimeoutSecs } from "../../src/main.js";

// smol-toml 1.4.2 looped forever on `a=[1 #` with no trailing newline, and wireCron reads the config
// before the server listens, so a malformed TEENY_CORE_CONFIG would have hung startup.
const MALFORMED = "a=[1 #";

describe("a malformed core config", () => {
  it("makes the pinned TOML parser throw instead of hanging (run in a child so a hang cannot freeze the suite)", () => {
    const script = `import("smol-toml").then((m) => { try { m.parse(${JSON.stringify(MALFORMED)}); } catch { process.exit(0); } process.exit(3); });`;
    const done = spawnSync(process.execPath, ["-e", script], { cwd: process.cwd(), timeout: 10_000 });
    expect(done.error, "the parser hung").toBeUndefined();
    expect(done.status).toBe(0);
  });

  it("leaves the cron shell timeout at its default", () => {
    const dir = mkdtempSync(join(tmpdir(), "cfg-"));
    writeFileSync(join(dir, "config.toml"), MALFORMED);
    expect(cronShellTimeoutSecs({ TEENY_CORE_CONFIG: join(dir, "config.toml") }, dir)).toBe(1800);
  });
});
