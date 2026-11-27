import { describe, expect, it, vi } from "vitest";
import { run } from "../src/main.js";

describe("main", () => {
  it("serve says it is not implemented and fails", () => {
    const write = vi.spyOn(process.stderr, "write").mockReturnValue(true);
    expect(run(["serve"])).toBe(1);
    expect(String(write.mock.calls[0]?.[0])).toContain("not implemented");
    write.mockRestore();
  });

  it("anything else prints usage", () => {
    const write = vi.spyOn(process.stderr, "write").mockReturnValue(true);
    expect(run([])).toBe(2);
    write.mockRestore();
  });
});
