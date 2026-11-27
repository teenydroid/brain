import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { docVerbs, generate, listHands, repoRoot, write } from "../../scripts/gen-skills.js";

const root = repoRoot();
const committed = join(root, "brain", "skills", "hands");
const cleanup: string[] = [];
afterEach(() => {
  for (const d of cleanup.splice(0)) rmSync(d, { recursive: true, force: true });
});

function readTree(dir: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const name of readdirSync(dir).sort()) {
    const sub = join(dir, name);
    if (!statSync(sub).isDirectory()) {
      out.set(name, readFileSync(sub, "utf8"));
      continue;
    }
    for (const file of readdirSync(sub).sort()) out.set(`${name}/${file}`, readFileSync(join(sub, file), "utf8"));
  }
  return out;
}

describe("hand skills", () => {
  it("lists a skill for every executable in hands/, and nothing else", () => {
    const hands = listHands(root);
    expect(hands.length).toBeGreaterThan(40);
    expect(hands).not.toContain("lib");
    expect(hands).not.toContain("tests");
    expect(hands).not.toContain("README.md");
    expect([...readTree(committed).keys()].sort()).toEqual(hands.map((h) => `${h}/SKILL.md`).sort());
  });

  it("matches what the generator writes now (run it into a temp dir and diff)", () => {
    const tmp = mkdtempSync(join(tmpdir(), "gen-skills-"));
    cleanup.push(tmp);
    write(generate(root), tmp);
    const fresh = readTree(tmp);
    const have = readTree(committed);
    expect([...have.keys()]).toEqual([...fresh.keys()]);
    for (const [path, text] of fresh) {
      expect(have.get(path), `${path} is stale: run node --experimental-strip-types core/scripts/gen-skills.ts`).toBe(text);
    }
  });

  it("is deterministic and carries no timestamp", () => {
    const a = generate(root);
    const b = generate(root);
    expect([...a]).toEqual([...b]);
    for (const text of a.values()) expect(text).not.toMatch(/\b20\d\d-\d\d-\d\d[T ]\d\d:\d\d/);
  });

  it("gives every skill OpenClaw frontmatter: name, one-line description, a gate that works", () => {
    for (const [path, text] of generate(root)) {
      const hand = path.split("/")[0] as string;
      const m = /^---\nname: (.+)\ndescription: (.+)\nmetadata: (.+)\n---\n/.exec(text);
      expect(m, path).not.toBeNull();
      expect(m?.[1]).toBe(`hand-${hand}`);
      const description = JSON.parse(m?.[2] ?? "") as string;
      expect(description.length).toBeLessThanOrEqual(160);
      expect(description).not.toContain("\n");
      const meta = JSON.parse(m?.[3] ?? "") as { openclaw: { requires: { bins?: string[]; config: string[] } } };
      // not requires.bins: a bare hand name is not on PATH and collides with git and gh
      expect(meta.openclaw.requires.bins, hand).toBeUndefined();
      expect(meta.openclaw.requires.config).toEqual(["plugins.entries.teeny-guardrails.enabled"]);
    }
  });

  it("says each hand runs the guard, caps and DRY_RUN itself, and which verbs act", () => {
    for (const [path, text] of generate(root)) {
      expect(text, path).toContain("runs the guard, its caps and `DRY_RUN` itself");
      expect(text, path).toContain("## Side effects");
    }
    const x = generate(root).get("x/SKILL.md") ?? "";
    expect(x).toMatch(/- Acting \(side[^\n]*\bpost\b/);
    expect(x).toMatch(/- Job \(writes[^\n]*\bmark\b/);
    expect(x).toMatch(/- Read only: [^\n]*\bwhoami\b/);
  });

  it("reads verb rows from docs/hands.md by marker", () => {
    const doc = [
      "## `demo`: a demo",
      "",
      "| Verb | | Does |",
      "|---|---|---|",
      "| `look [--max N]` | R | Reads a list. It never writes. |",
      "| `send` | A | Sends one thing. |",
      "| `a\\|b` | O | Escaped bar. |",
      "| `body` | | Not a verb. |",
      "",
      "## `other`: not this one",
      "| `nope` | R | Not mine. |",
    ].join("\n");
    expect(docVerbs(doc, "demo")).toEqual([
      { usage: "`look [--max N]`", marker: "R", does: "Reads a list." },
      { usage: "`send`", marker: "A", does: "Sends one thing." },
      { usage: "`a|b`", marker: "O", does: "Escaped bar." },
    ]);
    expect(docVerbs(doc, "missing")).toEqual([]);
  });
});
