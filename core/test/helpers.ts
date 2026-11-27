// Shared by the server, agents and integration tests: a core on a random port
// over scratch directories, a fake master key and a fake Gateway runner.
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { startCore, type RunningCore } from "../src/main.js";
import type { AgentRunRequest, AgentRunResult, AgentRunner } from "../src/gateway/client.js";

export const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const FAKE_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff";
export const TOKEN = "test-token-not-a-secret";

export class FakeRunner implements AgentRunner {
  calls: AgentRunRequest[] = [];
  reply: AgentRunResult | ((req: AgentRunRequest) => AgentRunResult | Promise<AgentRunResult>) = { text: "a reply" };

  async run(req: AgentRunRequest): Promise<AgentRunResult> {
    this.calls.push(req);
    return typeof this.reply === "function" ? this.reply(req) : this.reply;
  }
}

export interface TestCore {
  core: RunningCore;
  url: string;
  token: string;
  root: string;
  stateDir: string;
  runner: FakeRunner;
  env: Record<string, string>;
  rpc: (method: string, params?: Record<string, unknown>) => Promise<{ result?: unknown; error?: { code: number; message: string } }>;
}

export async function startTestCore(extraEnv: Record<string, string> = {}): Promise<TestCore> {
  const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
  const secrets = join(root, "secrets");
  mkdirSync(secrets, { recursive: true, mode: 0o700 });
  writeFileSync(join(secrets, "openhuman.key"), `${FAKE_KEY}\n`, { mode: 0o600 });
  const env: Record<string, string> = {
    PATH: process.env.PATH ?? "",
    TEENY_ROOT: root,
    OPENHUMAN_WORKSPACE: join(REPO, "brain"),
    OPENHUMAN_CORE_HOST: "127.0.0.1",
    OPENHUMAN_CORE_PORT: "0",
    OPENHUMAN_CORE_TOKEN: TOKEN,
    OPENHUMAN_KEYRING_MASTER_KEY_FILE: join(secrets, "openhuman.key"),
    ...extraEnv,
  };
  const runner = new FakeRunner();
  const core = await startCore(env, { runner, noGateway: true, log: () => undefined });
  const url = `http://127.0.0.1:${core.port}`;
  const rpc: TestCore["rpc"] = async (method, params = {}) => {
    const res = await fetch(`${url}/rpc`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: `openhuman.${method}`, params }),
    });
    return (await res.json()) as { result?: unknown; error?: { code: number; message: string } };
  };
  return { core, url, token: TOKEN, root, stateDir: join(root, "state"), runner, env, rpc };
}
