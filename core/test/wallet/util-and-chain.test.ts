// Regression tests for the CodeRabbit review of #623: file helpers, ledger readers and EVM nonce ordering.
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { privateKeyToAccount } from "viem/accounts";
import { signAndBroadcast } from "../../src/wallet/evm-chain.js";
import { SpendLedger } from "../../src/wallet/ledger.js";
import type { EvmRpc } from "../../src/wallet/rpc.js";
import { readJsonFile, withFileLock, writeFileAtomic } from "../../src/wallet/util.js";

const tmp = (): string => mkdtempSync(join(tmpdir(), "wallet-util-"));

describe("readJsonFile and the ledger readers", () => {
  it("rejects a blank file and a non-object root instead of reading them as no history", () => {
    const dir = tmp();
    const file = join(dir, "x.json");
    writeFileSync(file, "   ");
    expect(() => readJsonFile(file)).toThrow(/is empty/);
    for (const text of ["null", "[]", "3", '"s"']) {
      writeFileSync(file, text);
      expect(() => readJsonFile(file)).toThrow(/not a JSON object/);
    }
    expect(readJsonFile(join(dir, "absent.json"))).toBeUndefined();
  });

  it("will not read a ledger with no records list, and leaves the file alone", async () => {
    const dir = tmp();
    const file = join(dir, "spend-ledger.json");
    writeFileSync(file, "{}");
    const ledger = new SpendLedger(dir);
    expect(() => ledger.records()).toThrow(/no records list/);
    await expect(ledger.reserve(() => { throw new Error("must not decide"); })).rejects.toThrow(/no records list/);
    expect(readFileSync(file, "utf8")).toBe("{}");
  });
});

describe("writeFileAtomic", () => {
  it("writes the whole payload, even a large one, and leaves no temp file", () => {
    const dir = tmp();
    const file = join(dir, "state.json");
    const data = JSON.stringify({ blob: "é".repeat(3_000_000) });
    writeFileAtomic(file, data);
    expect(readFileSync(file, "utf8")).toBe(data);
    expect(readdirSync(dir)).toEqual(["state.json"]);
  });

  it("leaves the destination unchanged and no temp file when the write cannot finish", () => {
    const dir = tmp();
    const file = join(dir, "state.json");
    writeFileAtomic(file, "good");
    // a directory where the destination should be makes the final rename fail
    const blocked = join(dir, "blocked");
    mkdirSync(join(blocked, "inner"), { recursive: true });
    expect(() => writeFileAtomic(blocked, "x")).toThrow();
    expect(readFileSync(file, "utf8")).toBe("good");
    expect(readdirSync(dir).sort()).toEqual(["blocked", "state.json"]);
  });
});

describe("withFileLock", () => {
  it("releases only a lock that still carries its own token", async () => {
    const dir = tmp();
    const file = join(dir, "f.json");
    await withFileLock(file, () => {
      // someone else reclaimed and now holds the lock
      writeFileSync(`${file}.lock`, "somebody-else", { mode: 0o600 });
    });
    expect(readFileSync(`${file}.lock`, "utf8")).toBe("somebody-else");
  });

  it("removes its own lock when done, and reclaims a stale one", async () => {
    const dir = tmp();
    const file = join(dir, "f.json");
    await withFileLock(file, () => undefined);
    expect(readdirSync(dir)).toEqual([]);
    writeFileSync(`${file}.lock`, "dead-holder");
    const old = new Date(Date.now() - 120_000);
    utimesSync(`${file}.lock`, old, old);
    let ran = false;
    await withFileLock(file, () => {
      ran = true;
    });
    expect(ran).toBe(true);
    expect(readdirSync(dir)).toEqual([]);
  });

  it("does not take a fresh lock another process holds", async () => {
    const dir = tmp();
    const file = join(dir, "f.json");
    writeFileSync(`${file}.lock`, "live-holder");
    const t = setTimeout(() => rmSync(`${file}.lock`, { force: true }), 200);
    let order = "";
    await withFileLock(file, () => {
      order += "body";
    });
    clearTimeout(t);
    expect(order).toBe("body");
  });
});

describe("EVM sends from one account", () => {
  it("read the nonce, sign and broadcast one at a time, so two sends never share a nonce", async () => {
    const key = `0x${"11".repeat(32)}` as const;
    const sender = privateKeyToAccount(key).address;
    let chainNonce = 0;
    const used: number[] = [];
    const rpc = {
      call: async (method: string, params: unknown[] = []) => {
        await new Promise((r) => setTimeout(r, 5));
        if (method === "eth_getTransactionCount") return `0x${chainNonce.toString(16)}`;
        if (method === "eth_estimateGas") return "0x5208";
        if (method === "eth_getBlockByNumber") return { baseFeePerGas: "0x1" };
        if (method === "eth_maxPriorityFeePerGas") return "0x1";
        if (method === "eth_sendRawTransaction") {
          const { parseTransaction } = await import("viem");
          const tx = parseTransaction(params[0] as `0x${string}`);
          used.push(tx.nonce ?? -1);
          await new Promise((r) => setTimeout(r, 15));
          chainNonce += 1;
          return `0x${"ab".repeat(32)}`;
        }
        throw new Error(method);
      },
    } as unknown as EvmRpc;
    const to = "0x000000000000000000000000000000000000dEaD";
    await Promise.all([1n, 2n, 3n].map((value) => signAndBroadcast(rpc, key, 8453, { to, value, data: "0x" })));
    expect(sender).toBeTruthy();
    expect(used).toEqual([0, 1, 2]);
  });
});
