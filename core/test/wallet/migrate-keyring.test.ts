// The one-shot migration, run the way the operator runs it: as `node scripts/migrate-keyring.ts`,
// against a keyring this test builds in OpenHuman's formats (from the Rust source):
//   - secretstore.master_key: 64 hex characters under "<user>:secretstore.master_key" in dev-keychain.json
//   - the wallet phrase: encrypt_secret's enc2 value (ChaCha20-Poly1305 under that master), kept in
//     wallet-state.json, or in dev-keychain.json "<user>:wallet.mnemonic", or - when the core had a storage
//     backend - in the `secrets` collection of its sqlite documents, each sealed again with
//     HKDF-SHA256(master, "tinystoragedrivers-secrets/v1:" + scope)
import { spawnSync } from "node:child_process";
import { createCipheriv, hkdfSync, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { Keypair } from "@solana/web3.js";
import { addressOf, deriveAllAccounts, generatePhrase, seedOf } from "../../src/wallet/derive.js";
import { createMethods } from "../../src/wallet/index.js";
import { encryptEnc2, keyFromHex } from "../../src/wallet/crypto.js";
import { MockNet, mockSolana, policyConfig } from "./helpers.js";
import type { CoreContext } from "../../src/server/types.js";

const CORE = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const USER = "brain-user";

interface Old {
  dir: string;
  workspace: string;
  state: string;
  masterFile: string;
  oldMaster: string;
  userMaster: string;
  phrase: string;
  layer1: string;
  db: string;
}

type Where = "state" | "keychain" | "storage";

function sealHkdf(master: string, scope: string, plaintext: string): string {
  const key = Buffer.from(hkdfSync("sha256", Buffer.from(master, "hex"), Buffer.alloc(0), Buffer.concat([Buffer.from("tinystoragedrivers-secrets/v1:"), Buffer.from(scope)]), 32));
  const nonce = randomBytes(12);
  const c = createCipheriv("chacha20-poly1305", key, nonce, { authTagLength: 16 });
  const body = Buffer.concat([c.update(plaintext, "utf8"), c.final()]);
  return `enc2:${Buffer.concat([nonce, body, c.getAuthTag()]).toString("hex")}`;
}

async function oldKeyring(where: Where, over: { phrase?: string; stateSolana?: string; users?: string[]; keychainUser?: string; docOwners?: string[] } = {}): Promise<Old> {
  const dir = mkdtempSync(join(tmpdir(), "oldring-"));
  const workspace = join(dir, "brain");
  mkdirSync(join(workspace, "state"), { recursive: true });
  const oldMaster = randomBytes(32).toString("hex");
  const userMaster = randomBytes(32).toString("hex");
  const masterFile = join(dir, "openhuman.key");
  writeFileSync(masterFile, `${oldMaster}\n`, { mode: 0o600 });
  const phrase = over.phrase ?? generatePhrase(24);
  const layer1 = encryptEnc2(keyFromHex(userMaster), phrase); // what encrypt_secret returned
  const accounts = deriveAllAccounts(seedOf(phrase)).filter((a) => !a.index);
  if (over.stateSolana) accounts.find((a) => a.chain === "solana")!.address = over.stateSolana;
  const state = join(workspace, "state", "wallet-state.json");
  writeFileSync(state, JSON.stringify({ consentGranted: true, source: "generated", mnemonicWordCount: phrase.split(" ").length, encryptedMnemonic: where === "state" ? layer1 : null, accounts, updatedAtMs: 1 }, null, 2));
  const keychain: Record<string, string> = {};
  if (over.keychainUser) keychain[`${over.keychainUser}:secretstore.master_key`] = userMaster;
  else for (const u of over.users ?? [USER]) keychain[`${u}:secretstore.master_key`] = u === USER ? userMaster : randomBytes(32).toString("hex");
  if (where === "keychain") keychain[`${USER}:wallet.mnemonic`] = layer1;
  writeFileSync(join(workspace, "dev-keychain.json"), JSON.stringify(keychain, null, 2));
  const db = join(dir, "storage.db");
  if (where === "storage") {
    const { DatabaseSync } = (await import("node:sqlite")) as unknown as { DatabaseSync: new (p: string) => { exec(sql: string): void; prepare(sql: string): { run(...a: unknown[]): void }; close(): void } };
    const sqlite = new DatabaseSync(db);
    // the document table, as tinystoragedrivers-connect's sqlite schema declares it
    sqlite.exec("CREATE TABLE docs (scope TEXT NOT NULL, coll TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, schema INTEGER NOT NULL DEFAULT 0, created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL, expires_ms INTEGER, deleted INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL, PRIMARY KEY (scope, coll, id)) WITHOUT ROWID");
    const scope = "t:local/agent:main";
    for (const owner of over.docOwners ?? [USER]) {
      sqlite.prepare("INSERT INTO docs (scope, coll, id, version, created_ms, updated_ms, body) VALUES (?, 'secrets', ?, 1, 1, 1, ?)").run(scope, `${owner}:wallet.mnemonic`, JSON.stringify({ ciphertext: sealHkdf(oldMaster, scope, layer1) }));
    }
    sqlite.prepare("INSERT INTO docs (scope, coll, id, version, created_ms, updated_ms, body) VALUES (?, 'secrets', ?, 1, 1, 1, ?)").run(scope, `${USER}:other.secret`, JSON.stringify({ ciphertext: sealHkdf(oldMaster, scope, "unrelated") }));
    sqlite.close();
  }
  return { dir, workspace, state, masterFile, oldMaster, userMaster, phrase, layer1, db };
}

function run(old: Old, extra: string[], newDir: string): { code: number | null; out: string; err: string } {
  const flags = ["--old-state", old.state, "--old-workspace", old.workspace, "--old-master-key-file", old.masterFile, "--new-state-dir", newDir];
  const done = spawnSync("node", ["scripts/migrate-keyring.ts", ...flags, ...extra], { cwd: CORE, encoding: "utf8" });
  return { code: done.status, out: done.stdout, err: done.stderr.split("\n").filter((l) => !/ExperimentalWarning|--trace-warnings|\(Use `node/.test(l)).join("\n") };
}

function forbidden(old: Old): string[] {
  const words = old.phrase.split(" ");
  return [old.phrase, words.slice(0, 3).join(" "), old.oldMaster, old.userMaster, old.layer1, Buffer.from(old.oldMaster, "hex").toString("base64"), Buffer.from(old.userMaster, "hex").toString("base64")];
}

function ctxFor(old: Old, stateDir: string): { ctx: CoreContext; net: MockNet; call: (m: string, p?: Record<string, unknown>) => Promise<unknown> } {
  const configFile = join(old.dir, "config.toml");
  writeFileSync(configFile, policyConfig([{ chain: "solana", symbol: "SOL", perTx: 1_000_000_000 }]));
  const ctx: CoreContext = {
    env: { OPENHUMAN_KEYRING_MASTER_KEY_FILE: old.masterFile, TEENY_CORE_CONFIG: configFile, OPENHUMAN_WALLET_RPC_SOLANA: "https://solana.test.invalid", OPENHUMAN_SOLANA_CLUSTER: "devnet" },
    root: old.dir,
    stateDir,
    log: () => undefined,
  };
  const net = new MockNet();
  const methods = createMethods({ fetch: net.fetch, now: () => 1_800_000_000_000 });
  return { ctx, net, call: (m, p = {}) => methods[m]!(p, ctx) };
}

describe("migrate-keyring.ts", () => {
  for (const where of ["state", "keychain", "storage"] as const) {
    it(`moves a wallet whose phrase OpenHuman kept in ${{ state: "wallet-state.json", keychain: "dev-keychain.json", storage: "its sqlite storage secrets" }[where]}, prints only public addresses, and the new core signs with it`, async () => {
      const old = await oldKeyring(where);
      const newDir = join(old.dir, "state");
      const result = run(old, where === "storage" ? ["--old-storage-db", old.db] : [], newDir);
      expect(result.err).toBe("");
      expect(result.code).toBe(0);
      const seed = seedOf(old.phrase);
      const solana = addressOf(seed, "solana", "m/44'/501'/0'/0'");
      expect(result.out).toContain(`solana: ${solana}`);
      expect(result.out).toContain(`solana (account 1): ${addressOf(seed, "solana", "m/44'/501'/1'/0'")}`);
      expect(result.out).toContain(`evm: ${addressOf(seed, "evm", "m/44'/60'/0'/0/0")}`);
      for (const secret of forbidden(old)) {
        expect(result.out).not.toContain(secret);
        expect(result.err).not.toContain(secret);
      }
      const file = join(newDir, "wallet", "wallet-state.json");
      const written = readFileSync(file, "utf8");
      for (const secret of forbidden(old).filter((s) => s !== old.layer1)) expect(written).not.toContain(secret);
      expect(JSON.parse(written).encryptedMnemonic).toMatch(/^enc2:/);
      expect(JSON.parse(written).encryptedMnemonic).not.toBe(old.layer1); // re-sealed under the new master key
      // the new core reads it and signs with the migrated key
      const { call, net } = ctxFor(old, newDir);
      const status = (await call("wallet_status")) as { configured: boolean; accounts: Array<{ chain: string; address: string }> };
      expect(status.configured).toBe(true);
      expect(status.accounts.find((a) => a.chain === "solana")!.address).toBe(solana);
      expect(((await call("wallet_account", { chain: "solana", accountIndex: 1 })) as { address: string }).address).toBe(addressOf(seed, "solana", "m/44'/501'/1'/0'"));
      const { sent } = mockSolana(net);
      const quote = (await call("wallet_prepare_transfer", { chain: "solana", toAddress: Keypair.generate().publicKey.toBase58(), amountRaw: "1" })) as { quoteId: string };
      await call("wallet_execute_prepared", { quoteId: quote.quoteId, confirmed: true });
      expect(sent).toHaveLength(1);
    });
  }

  it("finds the phrase by its document suffix when the keyring's user is not the document's owner (the live box: keyring '.openhuman', document 'teeny:wallet.mnemonic')", async () => {
    const old = await oldKeyring("storage", { keychainUser: ".openhuman", docOwners: ["teeny"] });
    const newDir = join(old.dir, "state");
    const result = run(old, ["--old-storage-db", old.db], newDir);
    expect(result.err).toBe("");
    expect(result.code).toBe(0);
    expect(result.out).toContain(`solana: ${addressOf(seedOf(old.phrase), "solana", "m/44'/501'/0'/0'")}`);
    expect(JSON.parse(readFileSync(join(newDir, "wallet", "wallet-state.json"), "utf8")).encryptedMnemonic).toMatch(/^enc2:/);
  });

  it("refuses a storage database that holds several *:wallet.mnemonic documents, and writes nothing", async () => {
    const old = await oldKeyring("storage", { keychainUser: ".openhuman", docOwners: ["teeny", "other"] });
    const newDir = join(old.dir, "state");
    const result = run(old, ["--old-storage-db", old.db], newDir);
    expect(result.code).not.toBe(0);
    expect(result.err).toMatch(/2 recovery phrase documents/);
    expect(existsSync(join(newDir, "wallet", "wallet-state.json"))).toBe(false);
  });

  it("--check reads, verifies and prints, and writes nothing", async () => {
    const old = await oldKeyring("state");
    const newDir = join(old.dir, "state");
    const result = run(old, ["--check"], newDir);
    expect(result.code).toBe(0);
    expect(result.out).toContain("check only: nothing was written");
    expect(existsSync(join(newDir, "wallet", "wallet-state.json"))).toBe(false);
  });

  it("refuses to overwrite an existing store, and leaves it exactly as it was", async () => {
    const old = await oldKeyring("keychain");
    const newDir = join(old.dir, "state");
    mkdirSync(join(newDir, "wallet"), { recursive: true });
    const file = join(newDir, "wallet", "wallet-state.json");
    writeFileSync(file, '{"sentinel":true}');
    const result = run(old, [], newDir);
    expect(result.code).toBe(1);
    expect(result.err).toMatch(/never overwrites/);
    expect(readFileSync(file, "utf8")).toBe('{"sentinel":true}');
    // and after a real migration, a second run is refused
    const fresh = join(old.dir, "state2");
    expect(run(old, [], fresh).code).toBe(0);
    const before = readFileSync(join(fresh, "wallet", "wallet-state.json"), "utf8");
    const again = run(old, [], fresh);
    expect(again.code).toBe(1);
    expect(readFileSync(join(fresh, "wallet", "wallet-state.json"), "utf8")).toBe(before);
  });

  it("refuses a phrase that does not match the old state's address, writing nothing and saying nothing secret", async () => {
    const old = await oldKeyring("state", { stateSolana: Keypair.generate().publicKey.toBase58() });
    const result = run(old, [], join(old.dir, "state"));
    expect(result.code).toBe(1);
    expect(result.err).toMatch(/does not derive the old state's Solana address/);
    expect(existsSync(join(old.dir, "state", "wallet"))).toBe(false);
    for (const secret of forbidden(old)) expect(result.out + result.err).not.toContain(secret);
  });

  it("refuses when the keyring's master key is wrong, or its file is writable by others, or a user id is ambiguous", async () => {
    const old = await oldKeyring("keychain");
    const wrongKey = join(old.dir, "wrong.key");
    writeFileSync(wrongKey, randomBytes(32).toString("hex"), { mode: 0o600 });
    // the user master is what opens the phrase; corrupting it in the keychain is the "wrong key" case
    const keychainFile = join(old.workspace, "dev-keychain.json");
    const keychain = JSON.parse(readFileSync(keychainFile, "utf8"));
    keychain[`${USER}:secretstore.master_key`] = randomBytes(32).toString("hex");
    writeFileSync(keychainFile, JSON.stringify(keychain));
    let result = run(old, [], join(old.dir, "state"));
    expect(result.code).toBe(1);
    expect(result.err).toMatch(/does not open under the key given/);
    expect(existsSync(join(old.dir, "state", "wallet"))).toBe(false);

    const loose = await oldKeyring("state");
    const { chmodSync } = await import("node:fs");
    chmodSync(loose.masterFile, 0o666);
    result = run(loose, [], join(loose.dir, "state"));
    expect(result.code).toBe(1);
    expect(result.err).toMatch(/can be written by group or others/);

    const two = await oldKeyring("state", { users: [USER, "someone-else"] });
    result = run(two, [], join(two.dir, "state"));
    expect(result.code).toBe(1);
    expect(result.err).toMatch(/found 2 keyring users .* name one with --user-id/);
    result = run(two, ["--user-id", USER], join(two.dir, "state"));
    expect(result.code).toBe(0);
  });

  it("explains a bad command line and says nothing else", async () => {
    const done = spawnSync("node", ["scripts/migrate-keyring.ts", "--bogus"], { cwd: CORE, encoding: "utf8" });
    expect(done.status).toBe(1);
    expect(done.stderr).toMatch(/usage: migrate-keyring.ts/);
  });
});
