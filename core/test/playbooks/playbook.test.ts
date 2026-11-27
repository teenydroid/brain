// Ported from the Python tests that exercise bin/lib/playbook.py:
// hands/tests/test_playbook_skills.py, test_datajob.py, wallet/test_buy.py,
// x402/test_job.py, surplus/test_job.py, giveaway/test_fence.py,
// reel/test_ledger.py, reddit/test_playbook.py, test_ladder.py, test_upstream.py.
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseToml } from "smol-toml";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  loadPlaybook,
  NOT_OUTPUTS,
  PlaybookError,
  shlexSplit,
  skillBody,
  VERB_OUTPUTS,
} from "../../src/playbooks/index.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const REAL = { handsDir: join(root, "hands"), repoRoot: root };

let tmp: string;
let brain: string;
beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "playbook-"));
  brain = join(tmp, "brain");
  mkdirSync(join(brain, "playbooks"), { recursive: true });
});
afterEach(() => rmSync(tmp, { recursive: true, force: true }));

function load(front: string, name = "p") {
  writeFileSync(join(brain, "playbooks", `${name}.md`), `+++\ndescription = "x"\n${front}\n+++\ngo`);
  return loadPlaybook(brain, name, { repoRoot: root });
}

describe("skills a playbook names", () => {
  beforeEach(() => {
    mkdirSync(join(brain, "skills", "pond"), { recursive: true });
    writeFileSync(join(brain, "skills", "pond", "SKILL.md"), "---\nname: pond\ndescription: the pond\n---\n\n# Pond\n\nids are whole.\n");
  });
  const book = (extra: string) => {
    writeFileSync(join(brain, "playbooks", "t.md"), `+++\ndescription = "a test"\noutput = "none"\n${extra}\n+++\nDraft nothing.\n`);
    return loadPlaybook(brain, "t", { repoRoot: root });
  };

  it("rides in the prompt without its front matter", () => {
    const prompt = book('skills = ["pond"]').prompt;
    expect(prompt.startsWith("Draft nothing.")).toBe(true);
    expect(prompt).toContain("# Pond\n\nids are whole.");
    expect(prompt).not.toContain("description: the pond");
  });

  it("is absent when none is named", () => {
    expect(book("").prompt).toBe("Draft nothing.");
  });

  it("is refused when missing or malformed", () => {
    for (const extra of ['skills = ["lake"]', 'skills = "pond"', 'skills = ["../pond"]', 'skills = ["pond", "pond"]']) {
      expect(() => book(extra), extra).toThrow(PlaybookError);
    }
  });

  it("skillBody keeps text with no front matter and trims", () => {
    expect(skillBody("  plain \n")).toBe("plain");
    expect(skillBody("---\nname: a\n---\nbody\n")).toBe("body");
  });
});

describe("the real playbooks", () => {
  const dir = join(root, "brain", "playbooks");
  const names = readdirSync(dir).filter((f) => f.endsWith(".md")).sort();

  it("every moltbook playbook carries the moltbook skill", () => {
    for (const file of names) {
      const text = readFileSync(join(dir, file), "utf8");
      const meta = parseToml(text.split("+++")[1] as string) as { surface?: string; output?: string; skills?: string[] };
      if (meta.surface === "moltbook" || (meta.output ?? "").includes("hand:moltbook")) {
        expect(meta.skills ?? [], file).toContain("moltbook");
      }
    }
  });

  it("no playbook carries a one-off write", () => {
    for (const file of names) {
      const text = readFileSync(join(dir, file), "utf8");
      expect(text.toLowerCase(), file).not.toContain("one-off dispatch");
      expect(text, file).not.toContain("dispatch-guard");
    }
  });

  it("all of them load", () => {
    for (const file of names) expect(() => loadPlaybook(join(root, "brain"), file.slice(0, -3), REAL), file).not.toThrow();
  });

  it("reddit-browse parses and only queues a read; its verbs hold no signup or profile", () => {
    const got = loadPlaybook(join(root, "brain"), "reddit-browse", REAL);
    expect([got.output, got.surface]).toEqual(["hand:reddit browse", "reddit"]);
    expect([got.inputs[0]?.argv, got.inputs[0]?.required]).toEqual([["hands/reddit", "due"], true]);
    expect(VERB_OUTPUTS.reddit).not.toContain("signup");
    expect(VERB_OUTPUTS.reddit).not.toContain("profile");
  });

  it("fence-proposal and pin-bump can only propose", () => {
    expect(loadPlaybook(join(root, "brain"), "fence-proposal", REAL).output).toBe("hand:ladder propose");
    expect(VERB_OUTPUTS.ladder).toEqual(["propose"]);
    expect(loadPlaybook(join(root, "brain"), "pin-bump", REAL).output).toBe("hand:upstream propose");
    expect(VERB_OUTPUTS.upstream).toEqual(["propose"]);
  });
});

describe("data jobs", () => {
  it("an input names a job that exists, by a plain name", () => {
    mkdirSync(join(brain, "datajobs"), { recursive: true });
    writeFileSync(join(brain, "datajobs", "trend.py"), "print(1)\n");
    writeFileSync(join(brain, "playbooks", "p.md"), '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nname = "trend"\nrun = "datajob:trend"\n+++\ngo\n');
    expect(loadPlaybook(brain, "p", { repoRoot: root }).inputs[0]?.argv).toEqual(["datajob", "trend"]);
    writeFileSync(join(brain, "playbooks", "q.md"), '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "datajob:nope"\n+++\ngo\n');
    expect(() => loadPlaybook(brain, "q", { repoRoot: root })).toThrow(PlaybookError);
    writeFileSync(join(brain, "playbooks", "r.md"), '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "datajob:../x"\n+++\ngo\n');
    expect(() => loadPlaybook(brain, "r", { repoRoot: root })).toThrow(PlaybookError);
  });
});

describe("money and secrets", () => {
  it("the loader refuses buy-tiny as an output and as an input", () => {
    for (const front of ['output = "hand:wallet buy-tiny"', 'output = "post"\n[[inputs]]\nrun = "hands/wallet buy-tiny"']) {
      expect(() => load(front, "greedy"), front).toThrow(PlaybookError);
    }
  });

  it("no paying x402 verb is an output or an input; log reads", () => {
    for (const verb of ["search", "contents", "crawl", "fund", "log"]) {
      expect(() => load(`output = "hand:x402 ${verb}"`), verb).toThrow(PlaybookError);
    }
    for (const run of ["hands/x402 search openhuman", "hands/x402 contents https://example.com", "hands/x402 fund"]) {
      expect(() => load(`output = "none"\n[[inputs]]\nrun = "${run}"`), run).toThrow(PlaybookError);
    }
    expect(load('output = "none"\n[[inputs]]\nrun = "hands/x402 log --max 5"').inputs[0]?.argv.slice(0, 2)).toEqual(["hands/x402", "log"]);
  });

  it("the surplus sweep and status are never an output or an input of sweep", () => {
    for (const verb of ["sweep", "status"]) expect(() => load(`output = "hand:surplus ${verb}"`), verb).toThrow(PlaybookError);
    expect(() => load('output = "none"\n[[inputs]]\nrun = "hands/surplus sweep"')).toThrow(PlaybookError);
    expect(load('output = "none"\n[[inputs]]\nrun = "hands/surplus status"').inputs[0]?.argv.slice(0, 2)).toEqual(["hands/surplus", "status"]);
  });

  it("the giveaway draw, settle and payout are never an output, and say so", () => {
    for (const verb of ["entries", "draw", "settle", "clawback"]) {
      expect(() => load(`output = "hand:giveaway ${verb}"`), verb).toThrow(/moves money/);
    }
  });

  it("only open and judge are giveaway outputs", () => {
    for (const verb of ["open", "judge"]) expect(load(`output = "hand:giveaway ${verb}"`).output).toBe(`hand:giveaway ${verb}`);
    for (const verb of ["status", "announcements", "pay", "add"]) {
      expect(() => load(`output = "hand:giveaway ${verb}"`), verb).toThrow(PlaybookError);
    }
  });

  it("a giveaway input runs only the read verbs", () => {
    for (const verb of ["entries", "draw", "settle", "clawback", "open", "judge"]) {
      expect(() => load(`output = "none"\n[[inputs]]\nname = "g"\nrun = "hands/giveaway ${verb}"`), verb).toThrow(PlaybookError);
    }
    for (const verb of ["status", "announcements"]) {
      expect(load(`output = "none"\n[[inputs]]\nname = "g"\nrun = "hands/giveaway ${verb}"`).inputs[0]?.argv.slice(0, 2)).toEqual(["hands/giveaway", verb]);
    }
  });

  it("the wallet still takes no output", () => {
    for (const verb of ["pay", "address"]) expect(() => load(`output = "hand:wallet ${verb}"`), verb).toThrow(PlaybookError);
  });

  it("a keychain input reads only list", () => {
    expect(() => load('output = "none"\n[[inputs]]\nrun = "hands/keychain get"')).toThrow(/read verb/);
    expect(load('output = "none"\n[[inputs]]\nrun = "hands/keychain list"').inputs).toHaveLength(1);
  });
});

describe("outputs", () => {
  it("a draft can never reach hands/reel", () => {
    expect(NOT_OUTPUTS).toContain("reel");
    writeFileSync(join(brain, "playbooks", "clip.md"), '+++\ndescription = "x"\noutput = "hand:reel make"\n+++\nmake one\n');
    expect(() => loadPlaybook(brain, "clip", REAL)).toThrow("hands/reel cannot be an output");
  });

  it("a bad name or a missing file is refused", () => {
    expect(() => loadPlaybook(brain, "../x", { repoRoot: root })).toThrow("bad playbook name '../x'");
    expect(() => loadPlaybook(brain, "nope", { repoRoot: root })).toThrow(/No such file/);
  });

  it("the body keeps a +++ that follows the front matter", () => {
    writeFileSync(join(brain, "playbooks", "p.md"), '+++\ndescription = "x"\noutput = "none"\n+++\nA +++ B\n');
    expect(loadPlaybook(brain, "p", { repoRoot: root }).prompt).toBe("A +++ B");
  });
});

describe("shlexSplit", () => {
  it("splits like Python's shlex.split", () => {
    expect(shlexSplit("hands/x mentions --status 'a b' \"c d\" e\\ f")).toEqual(["hands/x", "mentions", "--status", "a b", "c d", "e f"]);
    expect(shlexSplit('a "b\\"c" "d\\e"')).toEqual(["a", 'b"c', "d\\e"]);
    expect(shlexSplit("a '' b")).toEqual(["a", "", "b"]);
    expect(shlexSplit("   ")).toEqual([]);
    expect(() => shlexSplit("a 'b")).toThrow("No closing quotation");
    expect(() => shlexSplit("a\\")).toThrow("No escaped character");
  });
});
