import { afterEach, describe, expect, it, vi } from "vitest";

describe("quiet", () => {
  afterEach(() => vi.restoreAllMocks());

  it("drops bigint-buffer's pure-JS notice and passes every other warning through", async () => {
    const seen: unknown[][] = [];
    const real = console.warn;
    console.warn = (...a: unknown[]) => void seen.push(a);
    try {
      await import("../src/quiet.js");
      console.warn("bigint: Failed to load bindings, pure JS will be used (try npm run rebuild?)");
      console.warn("something else", 1);
    } finally {
      const patched = console.warn;
      console.warn = real;
      void patched;
    }
    expect(seen).toEqual([["something else", 1]]);
  });

  it("recognises only that line", async () => {
    const { isNoise } = await import("../src/quiet.js");
    expect(isNoise(["bigint: Failed to load bindings, pure JS will be used (try npm run rebuild?)"])).toBe(true);
    expect(isNoise(["bigint: something else"])).toBe(false);
    expect(isNoise([new Error("x")])).toBe(false);
  });
});
