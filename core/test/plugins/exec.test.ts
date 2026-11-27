import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { link, makeFixture, blocked, rewritten, write, type Fixture } from "./fixture.js";

async function run(f: Fixture, agent: string, command: string, extra: Record<string, unknown> = {}) {
  return f.call(agent, "exec", { command, ...extra });
}

describe("exec allowlist: the mind", () => {
  it("runs a hand by repo-relative path, made absolute so the shell runs the file that was judged", async () => {
    const f = makeFixture();
    const d = await run(f, "mind", "hands/stats top --max 3");
    expect(blocked(d)).toBeUndefined();
    expect(rewritten(d)?.command).toBe(`${join(f.root, "hands/stats")} top --max 3`);
    expect(rewritten(d)?.workdir).toBe(f.home);
  });

  it("runs a hand by absolute path, and the broker client in its home", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", `${f.root}/hands/gh whoami`))).toBeUndefined();
    const d = await run(f, "mind", `bin/teeny pulse`);
    expect(blocked(d)).toBeUndefined();
    expect(rewritten(d)?.command).toBe(`${join(f.home, "bin/teeny")} pulse`);
  });

  it("does not run hands/lib, a README, a non-executable file or a hand that is not there", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", "hands/lib/mod.py"))).toMatch(/not an allowed program/);
    expect(blocked(await run(f, "mind", "hands/README.md"))).toMatch(/not a hand/);
    expect(blocked(await run(f, "mind", "hands/not-exec"))).toMatch(/not an executable hand/);
    expect(blocked(await run(f, "mind", "hands/nope"))).toMatch(/not an executable hand/);
    expect(blocked(await run(f, "mind", "hands/../bin/doctor"))).toMatch(/not an allowed program/);
    expect(blocked(await run(f, "mind", "../../bin/doctor"))).toMatch(/not an allowed program/);
    expect(blocked(await run(f, "mind", `${f.root}/bin/doctor`))).toMatch(/not an allowed program/);
  });

  it("refuses the hands that move money or hold keys", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", "hands/wallet pay --to x --amount 1"))).toMatch(/closed to this agent/);
    expect(blocked(await run(f, "mind", "hands/keychain secret --host x"))).toMatch(/closed to this agent/);
  });

  it("refuses a hand reached through a link that leads elsewhere", async () => {
    const f = makeFixture();
    link(join(f.root, "bin/doctor"), join(f.root, "hands/sneaky"));
    expect(blocked(await run(f, "mind", "hands/sneaky"))).toMatch(/not an allowed program/);
    // a planted file in its own home named like a hand never runs: hands/ is always the repo's
    write(join(f.home, "hands", "x"), "#!/bin/sh\nid\n", 0o755);
    expect(rewritten(await run(f, "mind", "hands/x post"))?.command).toBe(`${join(f.root, "hands/x")} post`);
    // but running that planted file by its own path is not allowed
    expect(blocked(await run(f, "mind", "./hands/x"))).toMatch(/not an allowed program/);
    expect(blocked(await run(f, "mind", `${f.home}/work/a.txt`))).toMatch(/not an allowed program/);
  });

  it("refuses a bare program that is not on its list, and says how hands are run", async () => {
    const f = makeFixture();
    for (const c of ["curl https://x", "python3 -c 1", "sh -c id", "bash -c id", "env", "xargs ls", "sudo ls", "rm -rf work", "gh whoami", "x post", "eval ls", "source a", ". a", "exec ls", "nohup ls", "cd ..", "kill 1", "tee f", "sed -i s/a/b/ f", "awk 1", "perl -e 1", "node -e 1"]) {
      expect(blocked(await run(f, "mind", c)), c).toMatch(/not on this agent's command list|not an allowed program/);
    }
    expect(blocked(await run(f, "mind", "gh whoami"))).toMatch(/hands\/<name>/);
  });

  it("refuses every chaining and hidden-command trick that could run what is not listed", async () => {
    const f = makeFixture();
    for (const c of [
      "ls; curl x",
      "ls && python3 -c 1",
      "ls || sh",
      "ls | sh",
      "hands/x post `id`",
      "hands/x post $(id)",
      'hands/x post "$(id)"',
      "hands/x post $HOME",
      "hands/x post &",
      "ls\nsh",
      "hands/x post > /etc/passwd",
      "cat < /etc/passwd",
      "cat <<EOF",
      "ls (id)",
      "FOO=1 hands/x post",
      "DRY_RUN=0 hands/x post",
      "PATH=/tmp hands/stats",
      "'hands/x' post",
      "hands/x\\ post",
    ]) {
      expect(blocked(await run(f, "mind", c)), c).toBeDefined();
    }
  });

  it("lets listed programs be chained, each judged on its own", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", "ls work | head -n 3 && echo done; date"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "ls work | head -n 3 && curl x"))).toMatch(/curl/);
    expect(blocked(await run(f, "mind", "cat work/a.txt | hands/x post && hands/wallet pay"))).toMatch(/closed to this agent/);
  });

  it("judges the paths a listed program is given", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", "cat work/a.txt"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "cat ../../hands/x"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "cat ../../secrets/teeny.env"))).toMatch(/closed to agents/);
    expect(blocked(await run(f, "mind", "cat /etc/passwd"))).toMatch(/may read/);
    expect(blocked(await run(f, "mind", "ls ../../brain"))).toMatch(/may read/); // the live brain is outside the shell's jail
    expect(blocked(await run(f, "mind", "ls ../../state"))).toMatch(/may read/);
    expect(blocked(await run(f, "mind", "head --lines=../../../secrets/x work/a.txt"))).toMatch(/may read/);
    expect(blocked(await run(f, "mind", "grep --file=../../secrets/teeny.env x"))).toMatch(/closed/);
    expect(blocked(await run(f, "mind", "grep -f/etc/passwd x"))).toMatch(/carries a path/);
    expect(blocked(await run(f, "mind", "grep -f ../../secrets/teeny.env x"))).toMatch(/closed/);
    link(join(f.root, "secrets"), join(f.home, "work", "s"));
    expect(blocked(await run(f, "mind", "cat work/s/teeny.env"))).toMatch(/closed/);
    expect(blocked(await run(f, "mind", "cat ~/.ssh/id_rsa"))).toMatch(/may read/);
  });

  it("judges redirect targets and $(cat PATH)", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", "echo hi > work/out.txt"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "echo hi >> work/out.txt 2>&1"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "echo hi > /dev/null"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "echo hi > ../../hands/x"))).toMatch(/redirect.*may write/);
    expect(blocked(await run(f, "mind", "echo hi > ../../secrets/teeny.env"))).toMatch(/closed/);
    expect(blocked(await run(f, "mind", "cat < ../../secrets/teeny.env"))).toMatch(/closed/);
    expect(blocked(await run(f, "mind", `bin/teeny pr "$(cat work/a.txt)"`))).toBeUndefined();
    expect(blocked(await run(f, "mind", `bin/teeny pr "$(cat ../../secrets/teeny.env)"`))).toMatch(/closed/);
  });

  it("find cannot run or write; sort, tail, uniq and date cannot write or hang", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", "find work -name '*.txt'"))).toBeUndefined();
    for (const c of ["find work -exec rm {} ;", "find work -delete", "find work -fprint x", "find work -execdir id ;", "find ../../secrets -name x"]) {
      expect(blocked(await run(f, "mind", c)), c).toBeDefined();
    }
    expect(blocked(await run(f, "mind", "sort -o work/x work/a.txt"))).toMatch(/writes a file/);
    expect(blocked(await run(f, "mind", "sort --output=work/x work/a.txt"))).toMatch(/writes a file/);
    expect(blocked(await run(f, "mind", "tail -f work/a.txt"))).toMatch(/never ends/);
    expect(blocked(await run(f, "mind", "uniq work/a.txt work/b.txt"))).toMatch(/second/);
    expect(blocked(await run(f, "mind", "date -s 2020-01-01"))).toMatch(/clock/);
  });

  it("writers write only where the mind may write", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", "mkdir work/new"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "cp ../../hands/x work/x"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "cp work/a.txt ../../hands/x"))).toMatch(/may write/);
    expect(blocked(await run(f, "mind", "mv work/a.txt ../../hands/y"))).toMatch(/may write/);
    expect(blocked(await run(f, "mind", "mv ../../hands/x work/x"))).toMatch(/may write/);
    expect(blocked(await run(f, "mind", "cp -t ../../hands work/a.txt"))).toMatch(/hides its destination/);
    expect(blocked(await run(f, "mind", "ln -s ../../secrets work/s"))).toMatch(/closed/);
    expect(blocked(await run(f, "mind", "touch ../../secrets/x"))).toMatch(/closed/);
  });

  it("build tools are allowed by name; git cannot be pointed at a program or config, URLs are not paths", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", "git clone https://github.com/a/b work/b"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "timeout 60 npm test"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "timeout 60 curl x"))).toMatch(/curl/);
    expect(blocked(await run(f, "mind", "timeout abc ls"))).toMatch(/duration/);
    expect(blocked(await run(f, "mind", "ulimit -v 1000000 && ls"))).toBeUndefined();
    expect(blocked(await run(f, "mind", "ulimit -v $(id)"))).toBeDefined();
    expect(blocked(await run(f, "mind", "git clone file:///x work/b"))).toMatch(/file: URL/);
    expect(blocked(await run(f, "mind", "git -c core.sshCommand=id clone x"))).toMatch(/can run a program/);
    expect(blocked(await run(f, "mind", "git --upload-pack=id clone x"))).toMatch(/can run a program/);
    expect(blocked(await run(f, "mind", "git config --global x y"))).toMatch(/not allowed/);
    expect(blocked(await run(f, "mind", "git clone ../../secrets work/s"))).toMatch(/closed/);
    expect(blocked(await run(f, "mind", "npm install ../../secrets"))).toMatch(/closed/);
    expect(blocked(await run(f, "mind", "make", { workdir: "../../hands" }))).toMatch(/writes where it runs/);
  });

  it("checks the working folder, and never passes env, elevation, another host, a node or a durable job", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "mind", "ls", { workdir: "work" }))).toBeUndefined();
    expect(blocked(await run(f, "mind", "ls", { workdir: "../../secrets" }))).toMatch(/workdir/);
    expect(blocked(await run(f, "mind", "ls", { workdir: "/" }))).toMatch(/workdir/);
    expect(blocked(await run(f, "mind", "ls", { env: { DRY_RUN: "0" } }))).toMatch(/environment/);
    expect(blocked(await run(f, "mind", "ls", { env: { PATH: "/tmp" } }))).toMatch(/environment/);
    expect(blocked(await run(f, "mind", "ls", { env: {} }))).toBeUndefined();
    expect(blocked(await run(f, "mind", "ls", { elevated: true }))).toMatch(/elevated/);
    expect(blocked(await run(f, "mind", "ls", { host: "node" }))).toMatch(/gateway host/);
    expect(blocked(await run(f, "mind", "ls", { host: "sandbox" }))).toMatch(/gateway host/);
    expect(blocked(await run(f, "mind", "ls", { node: "n1" }))).toMatch(/node/);
    expect(blocked(await run(f, "mind", "ls", { durable: true }))).toMatch(/durable/);
    expect(blocked(await f.call("mind", "exec", {}))).toMatch(/command string/);
    expect(blocked(await f.call("mind", "exec", { command: 7 }))).toMatch(/command string/);
  });

  it("DRY_RUN and the kill switches pass through unchanged: the call is never given an env", async () => {
    const f = makeFixture();
    const d = rewritten(await run(f, "mind", "hands/x post"));
    expect(d).toBeDefined();
    expect(Object.keys(d ?? {}).sort()).toEqual(["command", "workdir"]);
  });
});

describe("exec: the other agents", () => {
  it("the babysitter runs no hand and no broker, only its listed commands in its home", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "babysitter", "hands/stats top"))).toMatch(/not an allowed program/);
    expect(blocked(await run(f, "babysitter", "bin/teeny pulse"))).toMatch(/not an allowed program/);
    expect(blocked(await run(f, "babysitter", "git status"))).toBeUndefined();
    expect(blocked(await run(f, "babysitter", "cat pr.json"))).toBeUndefined();
    expect(blocked(await run(f, "babysitter", "cat ../../home/x"))).toMatch(/may read/);
    expect(blocked(await run(f, "babysitter", "cat ../home/../../../state/home/work/a.txt"))).toMatch(/may read/); // the mind's home
  });

  it("the operator and the evolver have no shell at all", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "operator", "ls"))).toMatch(/no exec tool/);
    expect(blocked(await run(f, "evolver", "ls"))).toMatch(/no exec tool/);
  });

  it("the worker is held like the mind", async () => {
    const f = makeFixture();
    expect(blocked(await run(f, "worker", "hands/x post"))).toBeUndefined();
    expect(blocked(await run(f, "worker", "hands/wallet pay"))).toMatch(/closed to this agent/);
    expect(blocked(await run(f, "worker", "curl x"))).toMatch(/curl/);
  });
});
