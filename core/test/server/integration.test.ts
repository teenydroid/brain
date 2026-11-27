// The real server on a random port, driven by the callers the contract was
// recorded from: hand.core (hands/lib/teeny_hands/hand.py, the Python client),
// curl with the flags bin/oh uses, and bin/oh itself where jq is installed.
import { execFile, spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { promisify } from "node:util";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { assertMatchesContract, loadContract } from "../contract.js";
import { REPO, startTestCore, type TestCore } from "../helpers.js";

// The server runs in this process: a blocking spawnSync here would deadlock it, so every child is async.
const run = promisify(execFile);
async function sh(cmd: string, args: string[], env: Record<string, string>): Promise<{ code: number; stdout: string; stderr: string }> {
  try {
    const r = await run(cmd, args, { env, encoding: "utf8" });
    return { code: 0, stdout: r.stdout, stderr: r.stderr };
  } catch (e) {
    const err = e as { code?: number; stdout?: string; stderr?: string };
    return { code: typeof err.code === "number" ? err.code : 1, stdout: err.stdout ?? "", stderr: err.stderr ?? "" };
  }
}
const have = (bin: string): boolean => spawnSync("bash", ["-c", `command -v ${bin}`]).status === 0;

let t: TestCore;
beforeAll(async () => {
  t = await startTestCore();
  t.runner.reply = { text: "a reply from the fake gateway", costUsd: 0.01 };
});
afterAll(async () => {
  await t.core.stop();
});

async function pyCore(method: string, params: unknown): Promise<{ ok: boolean; out: string }> {
  const code = [
    "import json, sys",
    "sys.path.insert(0, sys.argv[3])",
    "from teeny_hands import hand",
    "try:",
    "    print(json.dumps(hand.core(sys.argv[1], json.loads(sys.argv[2]))))",
    "except hand.CoreError as e:",
    "    print('CoreError:', e); sys.exit(3)",
  ].join("\n");
  const r = await sh("python3", ["-I", "-c", code, method, JSON.stringify(params), join(REPO, "hands", "lib")], { PATH: process.env.PATH ?? "", OPENHUMAN_CORE_URL: t.url, OPENHUMAN_CORE_TOKEN: t.token, TEENY_STATE: t.stateDir, TEENY_SECRETS: join(t.root, "secrets") });
  return { ok: r.code === 0, out: (r.stdout || r.stderr).trim() };
}

describe.skipIf(!have("python3"))("hand.core, the Python client every hand uses", () => {
  it.each([
    "config_get_agent_settings",
    "get_config",
    "cost_report",
    "encrypt_secret",
    "inference_agent_chat",
    "inference_agent_chat_simple",
    "config_update_agent_settings",
    "inference_update_model_settings",
    "auth_store_provider_credentials",
  ])("%s answers what its contract says", async (method) => {
    const r = await pyCore(method, loadContract(method).params.example);
    expect(r.ok, r.out).toBe(true);
    assertMatchesContract(method, JSON.parse(r.out));
  });

  it("raises CoreError with the handler's message for a refused call", async () => {
    const r = await pyCore("cost_report", {});
    expect(r.ok).toBe(false);
    expect(r.out).toMatch(/CoreError: openhuman\.cost_report: days must be/);
  });

  it("raises CoreError 'core answered HTTP 401' for a wrong token", async () => {
    const code = "import sys\nsys.path.insert(0, sys.argv[1])\nfrom teeny_hands import hand\ntry:\n    hand.core('get_config', {})\nexcept hand.CoreError as e:\n    print(e)";
    const r = await sh("python3", ["-I", "-c", code, join(REPO, "hands", "lib")], { PATH: process.env.PATH ?? "", OPENHUMAN_CORE_URL: t.url, OPENHUMAN_CORE_TOKEN: "wrong" });
    expect(r.stdout).toContain("core answered HTTP 401");
  });
});

describe.skipIf(!have("curl"))("curl, with the flags bin/oh uses", () => {
  it("POSTs the JSON-RPC request with the bearer in a header file and gets {jsonrpc, id, result}", async () => {
    const headers = join(t.root, "hdr");
    writeFileSync(headers, `Authorization: Bearer ${t.token}\n`);
    const request = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "openhuman.config_get_agent_settings", params: {} });
    const out = await sh("curl", ["-sS", "--fail-with-body", "--max-time", "30", `${t.url}/rpc`, "-H", `@${headers}`, "-H", "content-type: application/json", "--data-binary", request], {});
    const answer = JSON.parse(out.stdout) as { jsonrpc: string; id: number; result: { agent_timeout_secs: number } };
    expect(answer).toMatchObject({ jsonrpc: "2.0", id: 1 });
    expect(typeof answer.result.agent_timeout_secs).toBe("number");
  });

  it("GET /health is what bin/spine and bin/doctor poll", async () => {
    expect((await sh("curl", ["-fsS", "-o", "/dev/null", "-w", "%{http_code}", `${t.url}/health`], {})).stdout).toBe("200");
  });

  it("fails curl (HTTP 401) without the token, as bin/oh reports 'core unreachable'", async () => {
    const r = await sh("curl", ["-sS", "--fail-with-body", `${t.url}/rpc`, "-H", "content-type: application/json", "--data-binary", "{}"], {});
    expect(r.code).not.toBe(0);
  });
});

describe.skipIf(!(have("jq") && have("curl")))("bin/oh itself", () => {
  const oh = (method: string, params: string) => {
    mkdirSync(join(t.root, "state"), { recursive: true });
    return sh(join(REPO, "bin", "oh"), [method, params], { PATH: process.env.PATH ?? "", TEENY_ROOT: t.root, TEENY_STATE: t.stateDir, TEENY_SECRETS: join(t.root, "secrets"), OPENHUMAN_CORE_URL: t.url, OPENHUMAN_CORE_TOKEN: t.token });
  };

  it("prints the result, and an error's message to stderr with exit 1", async () => {
    const ok = await oh("config_get_agent_settings", "{}");
    expect(JSON.parse(ok.stdout)).toHaveProperty("agent_timeout_secs");
    const bad = await oh("cost_report", "{}");
    expect(bad.code).toBe(1);
    expect(bad.stderr).toMatch(/days must be/);
  });
});
