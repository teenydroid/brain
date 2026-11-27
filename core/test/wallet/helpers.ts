// Shared test plumbing: a throwaway state dir with a throwaway master key, a
// context whose log is captured, a scripted network, and a wallet set up from
// a generated (never a real) recovery phrase.
import { randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { CoreContext, RpcMethods } from "../../src/server/types.js";
import { createMethods } from "../../src/wallet/index.js";
import type { Deps } from "../../src/wallet/endpoints.js";
import { deriveAllAccounts, generatePhrase, seedOf } from "../../src/wallet/derive.js";

export interface LogLine {
  level: string;
  msg: string;
  fields?: Record<string, unknown>;
}

export interface Harness {
  dir: string;
  stateDir: string;
  keyFile: string;
  configFile: string;
  ctx: CoreContext;
  logs: LogLine[];
  net: MockNet;
  methods: RpcMethods;
  clock: { now: number };
  call(method: string, params?: Record<string, unknown>): Promise<unknown>;
  env(extra: Record<string, string>): void;
}

export const USDC_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU";

export function makeHarness(options: { env?: Record<string, string>; config?: string } = {}): Harness {
  const dir = mkdtempSync(join(tmpdir(), "wallet-test-"));
  const stateDir = join(dir, "state");
  mkdirSync(stateDir, { recursive: true });
  const keyFile = join(dir, "openhuman.key");
  writeFileSync(keyFile, randomBytes(32).toString("hex"), { mode: 0o600 });
  const configFile = join(dir, "config.toml");
  writeFileSync(configFile, options.config ?? "", { mode: 0o600 });
  const logs: LogLine[] = [];
  const envMap: Record<string, string | undefined> = {
    OPENHUMAN_KEYRING_MASTER_KEY_FILE: keyFile,
    TEENY_CORE_CONFIG: configFile,
    OPENHUMAN_SOLANA_CLUSTER: "devnet",
    OPENHUMAN_WALLET_RPC_SOLANA: "https://solana.test.invalid/rpc",
    OPENHUMAN_X402_PER_REQUEST_MAX: "100000",
    OPENHUMAN_X402_DAILY_MAX: "500000",
    OPENHUMAN_X402_MONTHLY_MAX: "5000000",
    ...(options.env ?? {}),
  };
  const ctx: CoreContext = {
    env: envMap,
    root: dir,
    stateDir,
    log: (level, msg, fields) => {
      logs.push({ level, msg, ...(fields ? { fields } : {}) });
    },
  };
  const net = new MockNet();
  const clock = { now: 1_800_000_000_000 };
  const deps: Partial<Deps> = {
    fetch: net.fetch,
    now: () => clock.now,
    resolve: async () => ["93.184.216.34"],
  };
  const methods = createMethods(deps);
  return {
    dir,
    stateDir,
    keyFile,
    configFile,
    ctx,
    logs,
    net,
    methods,
    clock,
    call: async (method, params = {}) => {
      const handler = methods[method];
      if (!handler) throw new Error(`no such method ${method}`);
      return handler(params, ctx);
    },
    env: (extra) => Object.assign(envMap, extra),
  };
}

/** A fresh throwaway phrase and its derived accounts. Never a real wallet. */
export function throwawayWallet(words = 12): { phrase: string; accounts: ReturnType<typeof deriveAllAccounts> } {
  const phrase = generatePhrase(words);
  return { phrase, accounts: deriveAllAccounts(seedOf(phrase)) };
}

/** Sets a wallet up through the RPC methods, as bin/wallet-setup does. Returns the phrase. */
export async function setupWallet(h: Harness, phrase?: string): Promise<{ phrase: string; status: Record<string, unknown> }> {
  const words = phrase ?? generatePhrase(12);
  const derived = (await h.call("wallet_derive_recovery_material", { phrase: words })) as { accounts: Array<Record<string, unknown>> };
  const encrypted = (await h.call("encrypt_secret", { plaintext: words })) as string;
  const status = (await h.call("wallet_setup", {
    consentGranted: true,
    source: "generated",
    mnemonicWordCount: words.split(" ").length,
    encryptedMnemonic: encrypted,
    // as bin/wallet-setup sends them: chain, address and path only
    accounts: derived.accounts.map((a) => ({ chain: a.chain, address: a.address, derivationPath: a.derivationPath })),
  })) as Record<string, unknown>;
  return { phrase: words, status };
}

export function policyToml(extra = ""): string {
  return extra;
}

// --- a scripted network ------------------------------------------------------------

export interface RecordedCall {
  url: string;
  method: string;
  body?: unknown;
  headers: Record<string, string>;
}

type RpcHandlerFn = (params: unknown[], call: RecordedCall) => unknown;
type HttpHandlerFn = (call: RecordedCall, init: RequestInit | undefined) => Response | Promise<Response>;

export class MockNet {
  readonly calls: RecordedCall[] = [];
  private readonly rpc = new Map<string, RpcHandlerFn>();
  private readonly http: Array<{ match: (url: string) => boolean; handler: HttpHandlerFn }> = [];

  /** Answer JSON-RPC METHOD (for any URL) with the result of FN. A thrown Error answers an RPC error. */
  onRpc(method: string, fn: RpcHandlerFn): void {
    this.rpc.set(method, fn);
  }

  onHttp(match: (url: string) => boolean, handler: HttpHandlerFn): void {
    this.http.unshift({ match, handler });
  }

  rpcCalls(method: string): RecordedCall[] {
    return this.calls.filter((c) => (c.body as { method?: string } | undefined)?.method === method);
  }

  fetch = async (input: Parameters<typeof fetch>[0], init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const headers: Record<string, string> = {};
    new Headers(init?.headers).forEach((v, k) => {
      headers[k] = v;
    });
    let body: unknown;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    const call: RecordedCall = { url, method: init?.method ?? "GET", body, headers };
    this.calls.push(call);
    for (const h of this.http) if (h.match(url)) return h.handler(call, init);
    const rpcMethod = (body as { method?: string } | undefined)?.method;
    if (rpcMethod && this.rpc.has(rpcMethod)) {
      try {
        const result = this.rpc.get(rpcMethod)!((body as { params: unknown[] }).params, call);
        return json({ jsonrpc: "2.0", id: 1, result });
      } catch (error) {
        return json({ jsonrpc: "2.0", id: 1, error: { code: -32000, message: (error as Error).message } });
      }
    }
    return new Response(`no mock for ${call.method} ${rpcMethod ?? url.split("?")[0]}`, { status: 599 });
  };
}

export function json(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json", ...headers } });
}

// --- chain mocks --------------------------------------------------------------------

import { base58 as bs58 } from "@scure/base";
import { keccak256, parseTransaction } from "viem";
import { decodeTransaction } from "../../src/wallet/solana-program.js";

export const BLOCKHASH = bs58.encode(Buffer.alloc(32, 7));

/** Answers the Solana RPC calls a transfer makes; every sent transaction is kept, and its first signature is the answer. */
export function mockSolana(net: MockNet): { sent: Uint8Array[] } {
  const sent: Uint8Array[] = [];
  net.onRpc("getLatestBlockhash", () => ({ context: { slot: 1 }, value: { blockhash: BLOCKHASH, lastValidBlockHeight: 100 } }));
  net.onRpc("sendTransaction", (params) => {
    const wire = new Uint8Array(Buffer.from(params[0] as string, "base64"));
    sent.push(wire);
    return bs58.encode(decodeTransaction(wire).signatures[0]!);
  });
  net.onRpc("getBalance", () => ({ context: { slot: 1 }, value: 2_500_000_000 }));
  return { sent };
}

export function mockEvm(net: MockNet): { sent: string[] } {
  const sent: string[] = [];
  net.onRpc("eth_getTransactionCount", () => "0x5");
  net.onRpc("eth_estimateGas", () => "0x5208");
  net.onRpc("eth_getBlockByNumber", () => ({ baseFeePerGas: "0x3b9aca00" }));
  net.onRpc("eth_maxPriorityFeePerGas", () => "0x59682f00");
  net.onRpc("eth_sendRawTransaction", (params) => {
    sent.push(params[0] as string);
    return keccak256(params[0] as `0x${string}`);
  });
  net.onRpc("eth_getBalance", () => "0xde0b6b3a7640000");
  return { sent };
}

export function parseEvm(raw: string): ReturnType<typeof parseTransaction> {
  return parseTransaction(raw as `0x${string}`);
}

export interface AssetCapToml {
  chain: string;
  symbol: string;
  evmNetwork?: string;
  tokenAddress?: string;
  perTx?: bigint | number;
  daily?: bigint | number;
  weekly?: bigint | number;
}

/** A `[wallet_policy]` the way bin/lib/wallet_config.py writes it. */
export function policyConfig(assets: AssetCapToml[], accounts = ""): string {
  let out = "[wallet_policy]\nautomation_enabled = true\napply_to_interactive = true\n";
  for (const a of assets) {
    out += `\n[[wallet_policy.assets]]\nchain = "${a.chain}"\nsymbol = "${a.symbol}"\n`;
    if (a.evmNetwork) out += `evm_network = "${a.evmNetwork}"\n`;
    if (a.tokenAddress) out += `token_address = "${a.tokenAddress}"\n`;
    if (a.perTx !== undefined) out += `per_tx_max_raw = ${a.perTx}\n`;
    if (a.daily !== undefined) out += `daily_max_raw = ${a.daily}\n`;
    if (a.weekly !== undefined) out += `recipient_weekly_max_raw = ${a.weekly}\n`;
  }
  return out + accounts;
}
