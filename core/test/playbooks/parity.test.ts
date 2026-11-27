import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";
import { loadPlaybook, PlaybookError, type Playbook } from "../../src/playbooks/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "..", "..");
const dumper = join(here, "dump_playbooks.py");

type Dump = Record<string, { ok: Playbook } | { error: string }>;

function pythonDump(brain: string): Dump | undefined {
  const run = spawnSync("python3", ["-I", dumper, brain, join(root, "hands")], { encoding: "utf8" });
  if (run.error || run.status !== 0) {
    if (run.status === null || /No module named|tomllib|ModuleNotFoundError/.test(run.stderr)) return undefined;
    throw new Error(`dump_playbooks.py failed: ${run.stderr}`);
  }
  return JSON.parse(run.stdout) as Dump;
}

function tsDump(brain: string): Dump {
  const out: Dump = {};
  for (const file of readdirSync(join(brain, "playbooks")).filter((f) => f.endsWith(".md")).sort()) {
    const name = file.slice(0, -3);
    try {
      out[name] = { ok: loadPlaybook(brain, name, { handsDir: join(root, "hands"), repoRoot: root }) };
    } catch (exc) {
      if (!(exc instanceof PlaybookError)) throw exc;
      out[name] = { error: exc.message };
    }
  }
  return out;
}

const python = pythonDump(join(root, "brain"));
const tmp = mkdtempSync(join(tmpdir(), "playbook-parity-"));
afterAll(() => rmSync(tmp, { recursive: true, force: true }));

describe.skipIf(python === undefined)("playbook.py parity", () => {
  it("parses every real playbook exactly as playbook.py does", () => {
    const brain = join(root, "brain");
    const want = python as Dump;
    const got = tsDump(brain);
    expect(Object.keys(want).length).toBeGreaterThan(30);
    expect(Object.keys(got).sort()).toEqual(Object.keys(want).sort());
    for (const name of Object.keys(want)) expect(got[name], name).toEqual(want[name]);
    for (const name of Object.keys(want)) expect(want[name], `${name} must parse`).toHaveProperty("ok");
  });

  it("refuses and accepts the same synthetic playbooks, with the same message", () => {
    const brain = join(tmp, "brain");
    mkdirSync(join(brain, "playbooks"), { recursive: true });
    mkdirSync(join(brain, "datajobs"), { recursive: true });
    mkdirSync(join(brain, "skills", "pond"), { recursive: true });
    writeFileSync(join(brain, "datajobs", "trend.py"), "print(1)\n");
    writeFileSync(join(brain, "skills", "pond", "SKILL.md"), "---\nname: pond\ndescription: the pond\n---\n\n# Pond\n\nids are whole.\n");
    const cases: Record<string, string> = {
      "ok-min": '+++\ndescription = "x"\noutput = "none"\n+++\ngo\n',
      "ok-skill": '+++\ndescription = " x "\noutput = "none"\nskills = ["pond"]\n+++\n\n go \n',
      "ok-input": '+++\ndescription = "x"\noutput = "post"\nmedia = ["image", "meme"]\n[[inputs]]\nname = "m"\nrun = "hands/x mentions --status \'a b\'"\nrequired = true\n[[inputs]]\nrun = "hands/x402 log --max 5"\noptional = true\n+++\ngo',
      "ok-datajob": '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nname = "trend"\nrun = "datajob:trend"\n+++\ngo',
      "ok-plus": '+++\ndescription = "x"\noutput = "none"\n+++\nbody with +++ inside\n',
      "ok-nudge": '+++\ndescription = "x"\noutput = "like"\nnudge = true\nsurface = "x"\nmax_per_day = 100\n+++\ngo',
      "ok-giveaway": '+++\ndescription = "x"\noutput = "hand:giveaway open"\nsurface = "x"\n+++\ngo',
      "no-front": "just words\n",
      "empty-prompt": '+++\ndescription = "x"\noutput = "none"\n+++\n  \n',
      "unknown-key": '+++\ndescription = "x"\noutput = "none"\nzed = 1\nalpha = 2\n+++\ngo',
      "no-description": '+++\noutput = "none"\n+++\ngo',
      "blank-description": '+++\ndescription = "  "\noutput = "none"\n+++\ngo',
      "no-output": '+++\ndescription = "x"\n+++\ngo',
      "bad-output": '+++\ndescription = "x"\noutput = "shout"\n+++\ngo',
      "bad-hand-output": '+++\ndescription = "x"\noutput = "hand:x"\n+++\ngo',
      "guard-output": '+++\ndescription = "x"\noutput = "hand:guard check"\n+++\ngo',
      "reel-output": '+++\ndescription = "x"\noutput = "hand:reel make"\n+++\ngo',
      "gh-verb": '+++\ndescription = "x"\noutput = "hand:gh push"\n+++\ngo',
      "pay": '+++\ndescription = "x"\noutput = "hand:wallet pay"\n+++\ngo',
      "address": '+++\ndescription = "x"\noutput = "hand:wallet address"\n+++\ngo',
      "buy-tiny-in": '+++\ndescription = "x"\noutput = "post"\n[[inputs]]\nrun = "hands/wallet buy-tiny"\n+++\ngo',
      "settle": '+++\ndescription = "x"\noutput = "hand:giveaway settle"\n+++\ngo',
      "status-out": '+++\ndescription = "x"\noutput = "hand:giveaway status"\n+++\ngo',
      "no-hand": '+++\ndescription = "x"\noutput = "hand:nothing go"\n+++\ngo',
      "bad-surface": '+++\ndescription = "x"\noutput = "none"\nsurface = "fax"\n+++\ngo',
      "max-0": '+++\ndescription = "x"\noutput = "none"\nmax_per_day = 0\n+++\ngo',
      "max-101": '+++\ndescription = "x"\noutput = "none"\nmax_per_day = 101\n+++\ngo',
      "max-bool": '+++\ndescription = "x"\noutput = "none"\nmax_per_day = true\n+++\ngo',
      "max-str": '+++\ndescription = "x"\noutput = "none"\nmax_per_day = "2"\n+++\ngo',
      "too-many-inputs": `+++\ndescription = "x"\noutput = "none"\n${'[[inputs]]\nrun = "hands/x whoami"\n'.repeat(9)}+++\ngo`,
      "input-extra-key": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "hands/x whoami"\nwhy = 1\n+++\ngo',
      "input-no-run": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nname = "a"\n+++\ngo',
      "input-both": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "hands/x whoami"\nrequired = true\noptional = true\n+++\ngo',
      "input-bad-bool": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "hands/x whoami"\nrequired = "yes"\n+++\ngo',
      "input-bad-name": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nname = "Bad"\nrun = "hands/x whoami"\n+++\ngo',
      "input-not-hand": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "ls -la"\n+++\ngo',
      "input-no-such-hand": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "hands/nope read"\n+++\ngo',
      "input-quote": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "hands/x \\"open"\n+++\ngo',
      "input-backslash": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "hands/x trailing\\\\"\n+++\ngo',
      "input-money": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "hands/wallet"\n+++\ngo',
      "input-secret": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "hands/keychain get"\n+++\ngo',
      "input-secret-ok": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "hands/keychain list"\n+++\ngo',
      "datajob-missing": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "datajob:nope"\n+++\ngo',
      "datajob-dots": '+++\ndescription = "x"\noutput = "none"\n[[inputs]]\nrun = "datajob:../x"\n+++\ngo',
      "media-bad": '+++\ndescription = "x"\noutput = "post"\nmedia = ["gif"]\n+++\ngo',
      "media-dup": '+++\ndescription = "x"\noutput = "post"\nmedia = ["image", "image"]\n+++\ngo',
      "media-reply": '+++\ndescription = "x"\noutput = "reply"\nmedia = ["image"]\n+++\ngo',
      "nudge-str": '+++\ndescription = "x"\noutput = "none"\nnudge = "yes"\n+++\ngo',
      "nudge-hand": '+++\ndescription = "x"\noutput = "hand:mail send"\nnudge = true\n+++\ngo',
      "skill-missing": '+++\ndescription = "x"\noutput = "none"\nskills = ["lake"]\n+++\ngo',
      "skill-str": '+++\ndescription = "x"\noutput = "none"\nskills = "pond"\n+++\ngo',
      "skill-dots": '+++\ndescription = "x"\noutput = "none"\nskills = ["../pond"]\n+++\ngo',
      "skill-dup": '+++\ndescription = "x"\noutput = "none"\nskills = ["pond", "pond"]\n+++\ngo',
    };
    for (const [name, text] of Object.entries(cases)) writeFileSync(join(brain, "playbooks", `${name}.md`), text);
    const want = pythonDump(brain) as Dump;
    const got = tsDump(brain);
    for (const name of Object.keys(cases)) {
      expect(want[name], `python ${name}`).toBeDefined();
      expect(got[name], name).toEqual(want[name]);
    }
    const oks = Object.keys(cases).filter((n) => "ok" in (want[n] as object));
    expect(oks.sort()).toEqual(["ok-datajob", "ok-giveaway", "ok-input", "ok-min", "ok-nudge", "ok-plus", "ok-skill", "input-secret-ok"].sort());
  });

  it("reads the guard's surfaces from its source", async () => {
    const { guardSurfaces } = await import("../../src/playbooks/index.js");
    const surfaces = guardSurfaces(root);
    expect(surfaces).toContain("x");
    expect(surfaces).toContain("rentahuman");
    expect(surfaces).toContain("moltbook");
  });
});

describe("playbook.py parity availability", () => {
  it("has python 3.11+ to compare against (skips the parity suite, loudly, if not)", () => {
    if (python === undefined) console.warn("python3 with tomllib is not available: the parity suite was skipped");
    expect(true).toBe(true);
  });
});
