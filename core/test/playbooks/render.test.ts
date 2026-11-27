import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { loadPlaybook, renderAgentTurn, shellQuote, type Playbook } from "../../src/playbooks/index.js";
import { readdirSync } from "node:fs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const opts = { handsDir: join(root, "hands"), repoRoot: root };
const brain = join(root, "brain");

describe("renderAgentTurn", () => {
  it("carries the inputs to run first, the body, the answer shape and the acting hand", () => {
    const book = loadPlaybook(brain, "pin-bump", opts);
    const turn = renderAgentTurn(book);
    const inputs = turn.indexOf("## inputs");
    const body = turn.indexOf("This turns **capability**");
    const answer = turn.indexOf("## your answer");
    expect(body).toBeGreaterThan(0);
    expect(inputs).toBeGreaterThan(body);
    expect(answer).toBeGreaterThan(inputs);
    expect(turn).toContain("1. `upstream`: `DRY_RUN=1 hands/upstream releases`");
    expect(turn).toContain("Required. If it comes back empty");
    expect(turn).toContain("`hands/upstream propose`");
    expect(turn).toContain("at most 1 a day");
    expect(turn).toContain("the hand does not count that for you");
    expect(turn).toContain("surface `github`");
  });

  it("names hands/x for post, reply and like", () => {
    const base: Playbook = { name: "t", run: "act", max_per_run: 1, nudge: false, description: "a test", media: [], output: "post", surface: "x", max_per_day: 2, inputs: [], prompt: "Draft." };
    expect(renderAgentTurn(base)).toContain("`hands/x post`");
    expect(renderAgentTurn(base)).toContain("`hands/x post-thread`");
    expect(renderAgentTurn(base)).toContain("nothing to run first");
    expect(renderAgentTurn({ ...base, output: "reply" })).toContain("`hands/x reply --to");
    expect(renderAgentTurn({ ...base, output: "like" })).toContain("`hands/x like");
    // a loader-accepted "hand: mail send" (space after the colon) renders as the hand and verb, not "hands/ mail"
    expect(renderAgentTurn({ ...base, output: "hand: mail send" })).toContain("`hands/mail send`");
    expect(renderAgentTurn({ ...base, output: "hand:mail send" })).toContain("`hands/mail send`");
    const none = renderAgentTurn({ ...base, output: "none" });
    expect(none).toContain("Call no hand to act");
    expect(none).not.toContain("hands/x post");
  });

  it("marks optional inputs, nudges, media and quotes awkward arguments", () => {
    const book: Playbook = {
      name: "t", run: "act", max_per_run: 1, nudge: true, description: "a test.", media: ["image"], output: "post", surface: "x", max_per_day: 1, prompt: "Draft.",
      inputs: [
        { name: "a", argv: ["hands/x", "search", "two words"], required: false, optional: true },
        { name: "b", argv: ["datajob", "trend"], required: false, optional: false },
      ],
    };
    const turn = renderAgentTurn(book);
    expect(turn).toContain("`DRY_RUN=1 hands/x search 'two words'`");
    expect(turn).toContain("Optional. If the hand fails");
    expect(turn).toContain("python3 bin/lib/datajob.py brain trend");
    expect(turn).toContain("a habit, not an order");
    expect(turn).toContain("hands/x upload --file");
    expect(turn).toContain("a test.\n");
    expect(turn).not.toContain("a test..");
  });

  it("renders every real playbook, with every input command, and only through a hand", () => {
    for (const file of readdirSync(join(brain, "playbooks")).filter((f) => f.endsWith(".md"))) {
      const book = loadPlaybook(brain, file.slice(0, -3), opts);
      const turn = renderAgentTurn(book);
      for (const input of book.inputs) expect(turn, file).toContain(`\`${input.name}\``);
      expect(turn, file).toContain(book.prompt);
      expect(renderAgentTurn(book), file).toBe(turn);
    }
  });

  it("shellQuote leaves plain words and quotes the rest", () => {
    expect(shellQuote("--max")).toBe("--max");
    expect(shellQuote("it's")).toBe(`'it'\\''s'`);
    expect(shellQuote("")).toBe("''");
  });
});
