import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { removeStaleApprovals } from "../../src/gateway/approvals.js";

describe("removeStaleApprovals", () => {
  it("removes a leftover exec-approvals.json (an allowlist or ask there would make exec stricter than the config) and leaves the rest", () => {
    const dir = mkdtempSync(join(tmpdir(), "approvals-"));
    writeFileSync(join(dir, "exec-approvals.json"), JSON.stringify({ version: 1, defaults: { security: "allowlist", ask: "always" } }));
    writeFileSync(join(dir, "openclaw.json"), "{}");
    expect(removeStaleApprovals(dir)).toEqual([join(dir, "exec-approvals.json")]);
    expect(existsSync(join(dir, "exec-approvals.json"))).toBe(false);
    expect(existsSync(join(dir, "openclaw.json"))).toBe(true);
    expect(removeStaleApprovals(dir)).toEqual([]);
  });
});
