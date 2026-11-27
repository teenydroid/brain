import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { hostOnList, judgeAllowedFetch, judgeUrl, maskSecrets, openclawLiveUrl, pickTab, pictureName, picturePaths, prunePictures, readHostList, savePicture } from "../../plugins/teeny-guardrails/browser.js";
import { blocked, makeFixture, rewritten, write } from "./fixture.js";

const call = (f: ReturnType<typeof makeFixture>, agent: string, params: Record<string, unknown>, session = "s1") =>
  f.guard.beforeToolCall({ toolName: "browser", params }, { agentId: agent, sessionKey: session, runId: `${session}-${Math.random()}` });

describe("the browser deny list and allow list", () => {
  it("matches a host and its subdomains, never a lookalike", () => {
    expect(hostOnList("x.com", ["x.com"])).toBe(true);
    expect(hostOnList("www.x.com", ["x.com"])).toBe(true);
    expect(hostOnList("X.COM.", ["x.com"])).toBe(true);
    expect(hostOnList("notx.com", ["x.com"])).toBe(false);
    expect(hostOnList("x.com.evil.example", ["x.com"])).toBe(false);
  });

  it("opens public https pages and refuses everything else", () => {
    const f = makeFixture();
    const deny = join(f.root, "deploy/browser-deny.txt");
    expect(judgeUrl("https://example.org/a?b=1", deny).ok).toBe(true);
    expect(judgeUrl("https://x.com/home", deny).reason).toMatch(/deny list/);
    expect(judgeUrl("https://mobile.twitter.com/", deny).reason).toMatch(/deny list/);
    expect(judgeUrl("https://T.CO/abc", deny).ok).toBe(true); // t.co is not listed in this fixture
    expect(judgeUrl("http://example.org", deny).reason).toMatch(/https pages only/);
    expect(judgeUrl("file:///etc/passwd", deny).reason).toMatch(/https pages only/);
    expect(judgeUrl("javascript:alert(1)", deny).reason).toMatch(/https pages only/);
    expect(judgeUrl("data:text/html,x", deny).reason).toMatch(/https pages only/);
    expect(judgeUrl("https://user:pw@example.org/", deny).reason).toMatch(/credentials/);
    expect(judgeUrl("https://127.0.0.1/", deny).reason).toMatch(/not a public address/);
    expect(judgeUrl("https://[::1]/", deny).reason).toMatch(/not a public address/);
    expect(judgeUrl("https://10.1.2.3/", deny).reason).toMatch(/not a public address/);
    expect(judgeUrl("https://169.254.169.254/latest", deny).reason).toMatch(/not a public address/);
    expect(judgeUrl("https://localhost/", deny).reason).toMatch(/not a public address/);
    expect(judgeUrl("not a url", deny).reason).toMatch(/not a URL/);
    expect(judgeUrl(undefined, deny).reason).toMatch(/needs a url/);
  });

  it("an unreadable deny list refuses every page, like OpenHuman", () => {
    expect(judgeUrl("https://example.org/", "/nonexistent/deny.txt").reason).toMatch(/no page opens/);
    expect(judgeUrl("https://example.org/", undefined).reason).toMatch(/no page opens/);
    expect(() => readHostList(undefined, "x")).toThrow();
  });

  it("the raw http tools reach only deploy/browser-allow.txt", () => {
    const f = makeFixture();
    const allow = join(f.root, "deploy/browser-allow.txt");
    expect(judgeAllowedFetch("https://api.example.org/v1", allow).ok).toBe(true);
    expect(judgeAllowedFetch("https://sub.api.example.org/v1", allow).ok).toBe(true);
    expect(judgeAllowedFetch("https://example.org/", allow).reason).toMatch(/not on deploy/);
    expect(judgeAllowedFetch("http://api.example.org/", allow).reason).toMatch(/plain https/);
    expect(judgeAllowedFetch("https://api.example.org/", "/nonexistent").reason).toMatch(/cannot be read/);
  });
});

describe("browser tool calls", () => {
  it("opens and navigates through the deny list, and pins the profile", async () => {
    const f = makeFixture();
    expect(rewritten(await call(f, "mind", { action: "open", url: "https://example.org/" }))?.profile).toBe("view");
    expect(blocked(await call(f, "mind", { action: "open", url: "https://x.com/" }))).toMatch(/deny list/);
    expect(blocked(await call(f, "mind", { action: "navigate", url: "https://www.x.com/" }))).toMatch(/deny list/);
    expect(blocked(await call(f, "mind", { action: "open", url: "http://example.org/" }))).toMatch(/https/);
    expect(rewritten(await call(f, "operator", { action: "open", url: "https://example.org/" }))?.profile).toBe("teeny");
    expect(blocked(await call(f, "operator", { action: "open", url: "https://twitter.com/x" }))).toMatch(/deny list/);
  });

  it("the mind's and the worker's browser is view-only: no act, no emulate, no evaluate, no other profile", async () => {
    const f = makeFixture();
    for (const agent of ["mind", "worker"]) {
      expect(blocked(await call(f, agent, { action: "act", request: { kind: "click", ref: "e1" } })), agent).toMatch(/action act is not allowed/);
      expect(blocked(await call(f, agent, { action: "emulate", device: "x" })), agent).toMatch(/not allowed/);
      expect(blocked(await call(f, agent, { action: "snapshot" }))).toBeUndefined();
      expect(blocked(await call(f, agent, { action: "screenshot" }))).toBeUndefined();
      expect(blocked(await call(f, agent, { action: "text", targetId: "t1" }))).toBeUndefined();
      expect(blocked(await call(f, agent, { action: "snapshot", profile: "user" }))).toMatch(/not this agent's/);
      expect(blocked(await call(f, agent, { action: "snapshot", profile: "teeny" }))).toMatch(/not this agent's/);
      expect(blocked(await call(f, agent, { action: "snapshot", node: "n1" }))).toMatch(/node/);
      expect(blocked(await call(f, agent, { action: "snapshot", target: "node" }))).toMatch(/gateway host/);
      expect(blocked(await call(f, agent, { action: "snapshot", dashboard: "d" }))).toMatch(/dashboards/);
      expect(blocked(await call(f, agent, {}))).toMatch(/no action/);
    }
  });

  it("the operator may take only the unattended kinds", async () => {
    const f = makeFixture();
    for (const kind of ["click", "fill", "type", "press", "select", "check", "wait", "scroll", "hover"]) {
      expect(blocked(await call(f, "operator", { action: "act", request: { kind, ref: "e1", text: "hello" } })), kind).toBeUndefined();
    }
    for (const kind of ["evaluate", "drag", "upload", "download", "close", "resize", "click-coords"]) {
      expect(blocked(await call(f, "operator", { action: "act", request: { kind } })), kind).toMatch(/not allowed/);
    }
    expect(blocked(await call(f, "operator", { action: "act", request: { kind: "evaluate", fn: "x" } }))).toMatch(/evaluate/);
    expect(blocked(await call(f, "operator", { action: "act" }))).toMatch(/without a kind/);
    // a batch is only its steps, and one bad step refuses the batch
    expect(blocked(await call(f, "operator", { action: "act", request: { kind: "batch", actions: [{ kind: "click" }, { kind: "type" }] } }))).toBeUndefined();
    expect(blocked(await call(f, "operator", { action: "act", request: { kind: "batch", actions: [{ kind: "click" }, { kind: "evaluate" }] } }))).toMatch(/evaluate/);
    expect(blocked(await call(f, "operator", { action: "act", request: { kind: "batch" } }))).toMatch(/not allowed/);
  });

  it("an agent with no browser has none", async () => {
    const f = makeFixture();
    expect(blocked(await f.call("babysitter", "browser", { action: "open", url: "https://example.org" }))).toMatch(/no browser tool/);
    expect(blocked(await f.call("evolver", "browser", { action: "open", url: "https://example.org" }))).toMatch(/no browser tool/);
  });
});

describe("keychain fill", () => {
  const SECRET = "hunter2-fake-password";
  /** where the browser says the tab is, which a test moves like a click or a redirect would */
  let live = "https://login.example.org/signin";
  const asked: string[] = [];
  const looked: { profile: string; targetId: string | undefined }[] = [];

  async function setup(url = "https://login.example.org/signin") {
    asked.length = 0;
    looked.length = 0;
    live = url;
    const f = makeFixture({
      fill: async (program, h) => {
        asked.push(`${program.split("/").pop()} ${h}`);
        return SECRET;
      },
      liveUrl: async (profile, targetId) => {
        looked.push({ profile, targetId });
        return live;
      },
    });
    expect(blocked(await call(f, "operator", { action: "open", url, targetId: "t1" }, "kc"))).toBeUndefined();
    return { f, asked };
  }
  const typed = (text: string, extra: Record<string, unknown> = {}) => ({ action: "act", targetId: "t1", request: { kind: "type", ref: "e5", text, ...extra } });

  it("swaps the placeholder for the password only on that site's own page, asking bin/keychain-fill for the live host", async () => {
    const { f, asked } = await setup();
    const d = rewritten(await call(f, "operator", typed("{{keychain:example.org}}"), "kc"));
    expect((d?.request as { text: string }).text).toBe(SECRET);
    expect(asked).toEqual(["keychain-fill login.example.org"]);
    // once for the tab-bound action check, once for the keychain check
    expect(looked).toEqual([{ profile: "teeny", targetId: "t1" }, { profile: "teeny", targetId: "t1" }]);
    expect(d?.profile).toBe("teeny");
  });

  it("reads the tab's URL right before the type, so a redirect or a click that moved the tab is caught", async () => {
    const { f, asked } = await setup();
    // the guard saw the tab opened on example.org; a click since sent it to a lookalike
    live = "https://login.example.org.evil.test/signin";
    expect(blocked(await call(f, "operator", typed("{{keychain:example.org}}"), "kc"))).toMatch(/this tab is on login.example.org.evil.test/);
    live = "https://example.org@evil.test/";
    expect(blocked(await call(f, "operator", typed("{{keychain:example.org}}"), "kc"))).toMatch(/credentials/);
    live = "http://login.example.org/";
    expect(blocked(await call(f, "operator", typed("{{keychain:example.org}}"), "kc"))).toMatch(/https pages only/);
    live = "about:blank";
    expect(blocked(await call(f, "operator", typed("{{keychain:example.org}}"), "kc"))).toMatch(/not a site's own https page/);
    expect(asked).toEqual([]);
    // back on the site it types; each type asks the browser again
    live = "https://example.org/account";
    expect(rewritten(await call(f, "operator", typed("{{keychain:example.org}}"), "kc"))).toBeDefined();
    expect(asked).toEqual(["keychain-fill example.org"]);
  });

  it("refuses when the browser cannot say where the tab is, or does not name it", async () => {
    const f = makeFixture({ fill: async () => SECRET, liveUrl: async () => { throw new Error("the browser did not answer"); } });
    expect(blocked(await call(f, "operator", typed("{{keychain:example.org}}"), "kc"))).toMatch(/could not confirm which page/);
    const g = makeFixture({ fill: async () => SECRET, liveUrl: async () => { throw new Error("more than one tab fits: name the tab"); } });
    expect(blocked(await call(g, "operator", typed("{{keychain:example.org}}"), "kc"))).toMatch(/more than one tab/);
  });

  it("refuses the placeholder for another site, for anything but typing, and for two sites", async () => {
    const { f, asked } = await setup();
    expect(blocked(await call(f, "operator", typed("{{keychain:other.org}}"), "kc"))).toMatch(/only on other.org's own pages/);
    expect(blocked(await call(f, "operator", typed("{{keychain:xexample.org}}"), "kc"))).toMatch(/own pages/);
    expect(blocked(await call(f, "operator", typed("{{keychain:example.org}} {{keychain:other.org}}"), "kc"))).toMatch(/one keychain site/);
    expect(blocked(await call(f, "operator", { action: "act", targetId: "t1", request: { kind: "click", text: "{{keychain:example.org}}" } }, "kc"))).toMatch(/typed or filled/);
    expect(asked).toEqual([]);
  });

  it("the mind and the worker never get the keychain", async () => {
    const f = makeFixture({ fill: async () => SECRET, liveUrl: async () => "https://example.org/" });
    for (const agent of ["mind", "worker"]) {
      expect(blocked(await call(f, agent, { action: "act", request: { kind: "type", text: "{{keychain:example.org}}" } }))).toMatch(/not allowed/);
    }
  });

  it("fails closed when the keychain gives nothing, and masks the password in every result", async () => {
    const g = makeFixture({ fill: async () => "", liveUrl: async () => "https://example.org/" });
    expect(blocked(await call(g, "operator", typed("{{keychain:example.org}}"), "kc"))).toMatch(/no password/);
    const h = makeFixture({ fill: async () => { throw new Error("boom"); }, liveUrl: async () => "https://example.org/" });
    expect(blocked(await call(h, "operator", typed("{{keychain:example.org}}"), "kc"))).toMatch(/no password/);
    const { f } = await setup();
    await call(f, "operator", typed("{{keychain:example.org}}"), "kc");
    const masked = maskSecrets({ content: [{ type: "text", text: `typed ${SECRET} into the field` }], nested: [SECRET] }, f.guard.browserState.secrets);
    expect(JSON.stringify(masked)).not.toContain(SECRET);
    expect(JSON.stringify(masked)).toContain("[keychain]");
  });

  it("reads tab lists in the shapes the CLI documents: suggestedTargetId, tabId, label, raw targetId", () => {
    const doc = { tabs: [{ suggestedTargetId: "t1", tabId: "t1", label: "docs", targetId: "ABCDEF123456", url: "https://example.org/a" }, { suggestedTargetId: "t2", tabId: "t2", targetId: "ZZZZZZ654321", url: "https://other.org/" }] };
    expect(pickTab(doc, "t1")).toBe("https://example.org/a");
    expect(pickTab(doc, "docs")).toBe("https://example.org/a");
    expect(pickTab(doc, "ZZZZZZ")).toBe("https://other.org/");
    expect(pickTab([{ tabId: "t9", url: "https://solo.org/" }], undefined)).toBe("https://solo.org/");
    expect(() => pickTab(doc, undefined)).toThrow(/more than one/);
    expect(() => pickTab(doc, "t7")).toThrow(/no such tab/);
    expect(() => pickTab(doc, "t")).toThrow();
    expect(() => pickTab({}, undefined)).toThrow(/no such tab/);
  });

  it("asks the Gateway's own openclaw CLI, with the operator's profile, and fails closed when it errors or answers badly", async () => {
    const dir = join(makeFixture().root, "fake dir"); // a space in the path must not matter
    mkdirSync(dir, { recursive: true });
    const ok = join(dir, "ok.sh");
    writeFileSync(ok, `#!/bin/sh\necho "$@" > "$ARGV_OUT"\necho '{"tabs":[{"tabId":"t1","url":"https://example.org/x"}]}'\n`, { mode: 0o755 });
    const bad = join(dir, "bad.sh");
    writeFileSync(bad, "#!/bin/sh\necho not json\n", { mode: 0o755 });
    const fail = join(dir, "fail.sh");
    writeFileSync(fail, "#!/bin/sh\nexit 3\n", { mode: 0o755 });
    process.env.ARGV_OUT = join(dir, "argv");
    expect(await openclawLiveUrl({ cmd: ok, args: ["x.mjs"] })("teeny", "t1")).toBe("https://example.org/x");
    expect(readFileSync(join(dir, "argv"), "utf8").trim()).toBe("x.mjs browser --json --browser-profile teeny tabs");
    delete process.env.ARGV_OUT;
    await expect(openclawLiveUrl({ cmd: bad, args: [] })("teeny", "t1")).rejects.toThrow();
    await expect(openclawLiveUrl({ cmd: fail, args: [] })("teeny", "t1")).rejects.toThrow(/did not answer/);
  });
});

describe("the screenshot sink", () => {
  it("names pictures so they sort by time, and keeps the newest 24 of its own, nothing else", () => {
    const dir = join(makeFixture().root, "pics");
    mkdirSync(dir, { recursive: true });
    write(join(dir, "notes.txt"), "keep me");
    write(join(dir, "1700000000000-000001.gif"), "not ours");
    const src = join(dir, "..", "src.png");
    writeFileSync(src, "\x89PNG");
    const kept: string[] = [];
    for (let i = 0; i < 30; i++) kept.push(savePicture(dir, src, 24, 1_789_369_260_000 + i * 1000) as string);
    const ours = readdirSync(dir).filter((n) => /^\d{13}-\d{6}\.(png|jpeg)$/.test(n)).sort();
    expect(ours).toHaveLength(24);
    expect(ours[0]).toBe(kept[6]?.split("/").pop());
    expect(readdirSync(dir)).toContain("notes.txt");
    expect(readdirSync(dir)).toContain("1700000000000-000001.gif");
    expect(readdirSync(dir).some((n) => n.endsWith(".part"))).toBe(false);
    expect(pictureName(1_789_369_260_000, 7, "jpg")).toBe("1789369260000-000007.jpeg");
    prunePictures(join(dir, "missing"), 3);
  });

  it("refuses what is not a picture, and finds picture paths a result names", () => {
    const dir = join(makeFixture().root, "pics2");
    expect(savePicture(dir, "/nonexistent.png", 24)).toBeUndefined();
    const txt = join(dir, "..", "a.txt");
    writeFileSync(txt, "x");
    expect(savePicture(dir, txt, 24)).toBeUndefined();
    expect(existsSync(dir)).toBe(false);
    expect(picturePaths({ content: [{ type: "text", text: "saved to /tmp/x/shot-1.png and /tmp/y.JPG" }] })).toEqual(["/tmp/x/shot-1.png", "/tmp/y.JPG"]);
  });
});

describe("review findings: the browser", () => {
  it("a tab that has come to a denied host is not read or acted on again, whatever the last open said", async () => {
    let live = "https://example.org/";
    const f = makeFixture({ liveUrl: async () => live });
    expect(blocked(await call(f, "operator", { action: "open", url: "https://example.org/", targetId: "t1" }, "s"))).toBeUndefined();
    expect(blocked(await call(f, "operator", { action: "act", targetId: "t1", request: { kind: "click", ref: "e1" } }, "s"))).toBeUndefined();
    // the click followed a link, or a redirect, to a host on deploy/browser-deny.txt
    for (const url of ["https://x.com/home", "https://www.twitter.com/", "https://127.0.0.1/", "https://localhost./"]) {
      live = url;
      for (const params of [
        { action: "act", targetId: "t1", request: { kind: "press", key: "Enter" } },
        { action: "snapshot", targetId: "t1" },
        { action: "text", targetId: "t1" },
        { action: "screenshot", targetId: "t1" },
        { action: "requests", targetId: "t1" },
      ]) {
        expect(blocked(await call(f, "operator", params, "s")), `${url} ${String(params.action)}`).toMatch(/may not be on|close the tab/);
        if (params.action !== "act") expect(blocked(await call(f, "mind", params, "s")), `mind ${url} ${String(params.action)}`).toMatch(/may not be on|close the tab/);
      }
    }
    // closing it is still allowed
    expect(blocked(await call(f, "operator", { action: "close", targetId: "t1" }, "s"))).toBeUndefined();
    // and a tab that cannot be read is refused rather than trusted
    const g = makeFixture({ liveUrl: async () => { throw new Error("the browser did not answer"); } });
    expect(blocked(await call(g, "operator", { action: "snapshot" }, "s"))).toMatch(/could not confirm which page/);
  });

  it("an act request names its tab too: the keychain check follows the tab the type goes to", async () => {
    const seen: (string | undefined)[] = [];
    const f = makeFixture({
      fill: async () => "pw",
      liveUrl: async (_p, id) => {
        seen.push(id);
        return id === "tabB" ? "https://evil.test/" : "https://example.org/";
      },
    });
    const d = await call(f, "operator", { action: "act", request: { kind: "type", targetId: "tabB", text: "{{keychain:example.org}}" } }, "s");
    expect(blocked(d)).toMatch(/tab is on evil.test|own pages/);
    expect(seen).toContain("tabB");
    // the same tab named twice is fine; two different tabs in one call are refused
    expect(blocked(await call(f, "operator", { action: "act", targetId: "tabA", request: { kind: "click", targetId: "tabA" } }, "s"))).toBeUndefined();
    expect(blocked(await call(f, "operator", { action: "act", targetId: "tabA", request: { kind: "type", targetId: "tabB", text: "{{keychain:example.org}}" } }, "s"))).toMatch(/only one tab/);
    expect(blocked(await call(f, "operator", { action: "act", request: { kind: "batch", actions: [{ kind: "click", targetId: "tabA" }, { kind: "type", targetId: "tabB", text: "x" }] } }, "s"))).toMatch(/only one tab/);
  });

  it("a trailing dot does not make a private host public", () => {
    const deny = "/nonexistent-not-read-before-private";
    for (const u of ["https://localhost./", "https://LOCALHOST../", "https://metadata.google.internal./", "https://printer.local./", "https://app.localhost./", "https://127.0.0.1./", "https://[::1]/"]) {
      const f = makeFixture();
      expect(judgeUrl(u, join(f.root, "deploy/browser-deny.txt")).reason, u).toMatch(/not a public address/);
    }
    void deny;
  });
});
