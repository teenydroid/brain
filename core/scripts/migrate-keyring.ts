#!/usr/bin/env node
// One-shot, operator-run: move Teeny's wallet from OpenHuman's keyring to the
// TypeScript core's store. Run it ONCE, by the operator, on the box, while the
// old spine is stopped:
//
//   node core/scripts/migrate-keyring.ts --check  <flags>     read, verify, print; write nothing
//   node core/scripts/migrate-keyring.ts          <flags>     the same, then write the new store
//
// Flags (every path is named; nothing is guessed, and nothing under secrets/ is
// read unless it is the file you name):
//
//   --old-state FILE          OpenHuman's wallet-state.json (<workspace>/state/wallet-state.json)
//   --old-workspace DIR       the old OPENHUMAN_WORKSPACE, which holds dev-keychain.json
//   --old-master-key-file F   the old OPENHUMAN_KEYRING_MASTER_KEY_FILE (secrets/openhuman.key)
//   --old-storage-db FILE     the old OPENHUMAN_STORAGE_URL's sqlite file (only when the
//                             phrase is held there, which is where a core with a storage
//                             backend keeps it)
//   --new-state-dir DIR       the new core's state directory (it gets wallet/wallet-state.json)
//   --new-master-key-file F   the new core's master key file (default: the old one, which is
//                             the same secrets/openhuman.key the new core is booted with)
//   --user-id ID              the keyring user id, when more than one is found
//   --owner-id ID             who owns the recovery phrase document in the storage database
//                             (`<ID>:wallet.mnemonic`), when it holds more than one; this is
//                             not the keyring user id (".openhuman" there, "teeny" here)
//
// It decrypts the recovery phrase (two layers: the storage document under the
// old master key, then the enc2 value under the keyring's `secretstore.master_key`),
// proves it by deriving the Solana address the old state lists, seals it again
// under the new master key, and writes the new state next to nothing else. It
// prints ONLY public addresses. It never prints, logs or returns the phrase, a
// seed, a key or a ciphertext. It refuses to overwrite an existing new store.
//
// Self-contained on purpose (Node's built-ins, node:sqlite, and the npm
// packages the core already depends on): Node >= 24.16 runs this file as it is.
import { createCipheriv, createDecipheriv, createPrivateKey, createPublicKey, createHmac, hkdfSync, randomBytes } from "node:crypto";
import { chmodSync, closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, statSync, writeSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { base58 } from "@scure/base";
import { mnemonicToSeedSync, validateMnemonic } from "@scure/bip39";
import { wordlist } from "@scure/bip39/wordlists/english";

const USAGE =
  "usage: migrate-keyring.ts [--check] --old-state FILE --old-workspace DIR --old-master-key-file FILE --new-state-dir DIR" +
  " [--old-storage-db FILE] [--new-master-key-file FILE] [--user-id ID] [--owner-id ID]";

class Refusal extends Error {}

type WalletAccount = { chain: string; address: string; derivationPath: string; index?: number; label?: string };
type OldState = {
  consentGranted?: boolean;
  source?: string;
  mnemonicWordCount?: number;
  encryptedMnemonic?: string | null;
  accounts?: WalletAccount[];
};

export type Args = {
  check: boolean;
  oldState: string;
  oldWorkspace: string;
  oldMasterKeyFile: string;
  oldStorageDb?: string;
  newStateDir: string;
  newMasterKeyFile?: string;
  userId?: string;
  ownerId?: string;
};

export function parseArgs(argv: readonly string[]): Args {
  const flags = new Map<string, string>();
  let check = false;
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i] as string;
    if (arg === "--check") {
      check = true;
    } else if (arg.startsWith("--") && i + 1 < argv.length) {
      flags.set(arg.slice(2), argv[++i] as string);
    } else {
      throw new Refusal(USAGE);
    }
  }
  const need = (name: string): string => {
    const value = flags.get(name);
    if (!value) throw new Refusal(`missing --${name}\n${USAGE}`);
    return value;
  };
  const known = ["old-state", "old-workspace", "old-master-key-file", "old-storage-db", "new-state-dir", "new-master-key-file", "user-id", "owner-id"];
  for (const name of flags.keys()) if (!known.includes(name)) throw new Refusal(`unknown flag --${name}\n${USAGE}`);
  const args: Args = {
    check,
    oldState: need("old-state"),
    oldWorkspace: need("old-workspace"),
    oldMasterKeyFile: need("old-master-key-file"),
    newStateDir: need("new-state-dir"),
  };
  const storage = flags.get("old-storage-db");
  if (storage) args.oldStorageDb = storage;
  const next = flags.get("new-master-key-file");
  if (next) args.newMasterKeyFile = next;
  const user = flags.get("user-id");
  if (user) args.userId = user;
  const owner = flags.get("owner-id");
  if (owner) args.ownerId = owner;
  return args;
}

// --- OpenHuman's envelopes (tinystoragedrivers-secrets) ----------------------------------

function keyFromHexFile(file: string, what: string): Buffer {
  if (!existsSync(file)) throw new Refusal(`${what} does not exist`);
  if ((statSync(file).mode & 0o022) !== 0) throw new Refusal(`${what} can be written by group or others; fix its mode first`);
  return keyFromHex(readFileSync(file, "utf8"), what);
}

function keyFromHex(text: string, what: string): Buffer {
  const hex = text.trim();
  if (!/^[0-9a-fA-F]{64}$/.test(hex)) throw new Refusal(`${what} is not 64 hex characters`);
  return Buffer.from(hex, "hex");
}

// enc2:<hex(nonce(12) || ciphertext || tag(16))>, ChaCha20-Poly1305, no associated data
function openEnc2(key: Buffer, value: string, what: string): Buffer {
  if (!value.startsWith("enc2:") || !/^[0-9a-fA-F]*$/.test(value.slice(5)) || value.length % 2 === 0) {
    throw new Refusal(`${what} is not an enc2 value`);
  }
  const blob = Buffer.from(value.slice(5), "hex");
  if (blob.length < 28) throw new Refusal(`${what} is too short`);
  try {
    const decipher = createDecipheriv("chacha20-poly1305", key, blob.subarray(0, 12), { authTagLength: 16 });
    decipher.setAuthTag(blob.subarray(blob.length - 16));
    return Buffer.concat([decipher.update(blob.subarray(12, blob.length - 16)), decipher.final()]);
  } catch {
    throw new Refusal(`${what} does not open under the key given (wrong key, or the file was changed)`);
  }
}

function sealEnc2(key: Buffer, plaintext: Buffer): string {
  const nonce = randomBytes(12);
  const cipher = createCipheriv("chacha20-poly1305", key, nonce, { authTagLength: 16 });
  const body = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return `enc2:${Buffer.concat([nonce, body, cipher.getAuthTag()]).toString("hex")}`;
}

// DerivedKeys: HKDF-SHA256(salt none, master) expanded with info = label || scope
function storageKey(master: Buffer, scope: string): Buffer {
  return Buffer.from(hkdfSync("sha256", master, Buffer.alloc(0), Buffer.concat([Buffer.from("tinystoragedrivers-secrets/v1:"), Buffer.from(scope)]), 32));
}

// --- finding the old ciphertext ----------------------------------------------------------

function readKeychain(workspace: string): Record<string, string> {
  const file = join(workspace, "dev-keychain.json");
  if (!existsSync(file)) return {};
  try {
    const parsed: unknown = JSON.parse(readFileSync(file, "utf8"));
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("not an object");
    return parsed as Record<string, string>;
  } catch {
    throw new Refusal("dev-keychain.json is not readable JSON; it is left as it is");
  }
}

function findUserId(keychain: Record<string, string>, given: string | undefined): string {
  if (given) return given;
  const users = Object.keys(keychain)
    .filter((k) => k.endsWith(":secretstore.master_key"))
    .map((k) => k.slice(0, -":secretstore.master_key".length));
  if (users.length !== 1) throw new Refusal(`found ${users.length} keyring users with a secretstore.master_key; name one with --user-id`);
  return users[0] as string;
}

type SqliteDb = { prepare(sql: string): { all(...params: unknown[]): Array<Record<string, unknown>> }; close(): void };

// The phrase's document is `<owner>:wallet.mnemonic` in coll 'secrets', and the owner is not the keyring's user id:
// on the live box the keychain's only user is ".openhuman" while the document's id is "teeny:wallet.mnemonic". So
// the document is found by its suffix, and a store that holds several is refused rather than guessed at.
async function storageCiphertext(db: string, oldMaster: Buffer, ownerId?: string): Promise<string | undefined> {
  if (!existsSync(db)) throw new Refusal("--old-storage-db does not exist");
  const sqlite = (await import("node:sqlite")) as unknown as { DatabaseSync: new (path: string, options: { readOnly: boolean }) => SqliteDb };
  const handle = new sqlite.DatabaseSync(db, { readOnly: true });
  try {
    const tables = handle
      .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%docs'")
      .all()
      .map((r) => String(r.name))
      .filter((name) => /^[A-Za-z0-9_]+$/.test(name));
    const found: Array<{ scope: string; id: string; ciphertext: string }> = [];
    const suffix = ":wallet.mnemonic";
    for (const table of tables) {
      const rows = handle
        // LIKE is case-insensitive for ASCII, so '%:WALLET.MNEMONIC' would match too: substr compares exactly.
        .prepare(`SELECT scope, id, body FROM ${table} WHERE coll = 'secrets' AND substr(id, -length(?)) = ? AND deleted = 0`)
        .all(suffix, suffix);
      for (const row of rows) {
        const body = JSON.parse(String(row.body)) as { ciphertext?: unknown };
        if (typeof body.ciphertext === "string") found.push({ scope: String(row.scope), id: String(row.id), ciphertext: body.ciphertext });
      }
    }
    // --owner-id narrows several documents to the one that owner holds; without a match it is still a refusal.
    if (found.length > 1 && ownerId) {
      const mine = found.filter((f) => f.id === `${ownerId}${suffix}`);
      if (mine.length === 1) found.splice(0, found.length, mine[0] as (typeof found)[number]);
    }
    if (found.length > 1) throw new Refusal(`the storage database holds ${found.length} recovery phrase documents (*:wallet.mnemonic); it will not guess which is the wallet's (--owner-id names the owner)`);
    const one = found[0];
    if (!one) return undefined;
    const key = storageKey(oldMaster, one.scope);
    try {
      return openEnc2(key, one.ciphertext, "the stored recovery phrase document").toString("utf8");
    } finally {
      key.fill(0);
    }
  } finally {
    handle.close();
  }
  return undefined;
}

// --- the account proof: SLIP-0010 ed25519 at m/44'/501'/0'/0' ----------------------------

function solanaAddress(seed: Uint8Array, path: string): string {
  let I = createHmac("sha512", "ed25519 seed").update(seed).digest();
  for (const part of path.split("/").slice(1)) {
    const data = Buffer.alloc(37);
    Buffer.from(I.subarray(0, 32)).copy(data, 1);
    data.writeUInt32BE((Number(part.slice(0, -1)) | 0x80000000) >>> 0, 33);
    I = createHmac("sha512", I.subarray(32)).update(data).digest();
  }
  const priv = createPrivateKey({ key: Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), I.subarray(0, 32)]), format: "der", type: "pkcs8" });
  const spki = createPublicKey(priv).export({ format: "der", type: "spki" });
  return base58.encode(new Uint8Array(spki.subarray(spki.length - 32)));
}

// --- the migration -----------------------------------------------------------------------

export type Report = { addresses: Array<{ chain: string; address: string; index?: number }>; wrote: boolean; source: string };

export async function migrate(args: Args, log: (line: string) => void): Promise<Report> {
  const newFile = join(args.newStateDir, "wallet", "wallet-state.json");
  if (existsSync(newFile)) throw new Refusal("the new store already holds a wallet; this never overwrites. Move it aside yourself if you mean to replace it");
  if (!existsSync(args.oldState)) throw new Refusal("--old-state does not exist");
  let old: OldState;
  try {
    old = JSON.parse(readFileSync(args.oldState, "utf8")) as OldState;
  } catch {
    throw new Refusal("--old-state is not readable JSON");
  }
  if (!Array.isArray(old.accounts) || old.accounts.length === 0) throw new Refusal("the old wallet state lists no accounts");
  const oldSolana = old.accounts.find((a) => a.chain === "solana" && (a.index ?? 0) === 0);
  if (!oldSolana) throw new Refusal("the old wallet state has no Solana account");

  const keychain = readKeychain(args.oldWorkspace);
  const userId = findUserId(keychain, args.userId);
  const oldMaster = keyFromHexFile(args.oldMasterKeyFile, "the old master key file");
  const toWipe: Buffer[] = [oldMaster];
  try {
    // 1. the ciphertext of the phrase, wherever this core kept it
    let layer1: string | undefined;
    let source = "";
    if (typeof old.encryptedMnemonic === "string" && old.encryptedMnemonic.trim() !== "") {
      layer1 = old.encryptedMnemonic.trim();
      source = "the old wallet-state.json";
    } else if (typeof keychain[`${userId}:wallet.mnemonic`] === "string") {
      layer1 = (keychain[`${userId}:wallet.mnemonic`] as string).trim();
      source = "dev-keychain.json";
    } else if (args.oldStorageDb) {
      layer1 = (await storageCiphertext(args.oldStorageDb, oldMaster, args.ownerId))?.trim();
      source = "the old storage database";
    }
    if (!layer1) {
      throw new Refusal("found no stored recovery phrase (looked in the old state, dev-keychain.json" + (args.oldStorageDb ? " and the storage database" : "; pass --old-storage-db if the old core had OPENHUMAN_STORAGE_URL") + ")");
    }
    // 2. the enc2 value is sealed under the keyring's own secretstore.master_key
    const userMaster = keyFromHex(keychain[`${userId}:secretstore.master_key`] ?? "", "the keyring's secretstore.master_key");
    toWipe.push(userMaster);
    const phraseBytes = openEnc2(userMaster, layer1, "the encrypted recovery phrase");
    toWipe.push(phraseBytes);
    const phrase = phraseBytes.toString("utf8");
    const words = phrase.trim().split(/\s+/).length;
    if (!validateMnemonic(phrase.trim().split(/\s+/).join(" "), wordlist)) throw new Refusal("the decrypted phrase is not a valid BIP-39 phrase");
    // 3. prove it: it must derive the Solana address the old state lists
    const seed = mnemonicToSeedSync(phrase.trim().split(/\s+/).join(" "));
    let derived: string;
    let giveaway: string;
    try {
      derived = solanaAddress(seed, "m/44'/501'/0'/0'");
      giveaway = solanaAddress(seed, "m/44'/501'/1'/0'");
    } finally {
      seed.fill(0);
    }
    if (derived !== oldSolana.address) throw new Refusal("the decrypted phrase does not derive the old state's Solana address; nothing was written");
    const addresses = old.accounts.map((a) => ({ chain: a.chain, address: a.address, ...(a.index ? { index: a.index } : {}) }));
    if (!addresses.some((a) => a.chain === "solana" && a.index === 1)) addresses.push({ chain: "solana", address: giveaway, index: 1 });
    for (const a of addresses) log(`${a.chain}${a.index ? ` (account ${a.index})` : ""}: ${a.address}`);

    if (args.check) {
      log("check only: nothing was written");
      return { addresses, wrote: false, source };
    }
    // 4. seal under the new master key and write, refusing to overwrite
    const newMaster = args.newMasterKeyFile ? keyFromHexFile(args.newMasterKeyFile, "the new master key file") : Buffer.from(oldMaster);
    toWipe.push(newMaster);
    const state = {
      consentGranted: old.consentGranted ?? true,
      source: old.source === "generated" ? "generated" : "imported",
      mnemonicWordCount: old.mnemonicWordCount ?? words,
      encryptedMnemonic: sealEnc2(newMaster, Buffer.from(phrase.trim().split(/\s+/).join(" "), "utf8")),
      accounts: old.accounts,
      updatedAtMs: Date.now(),
    };
    mkdirSync(dirname(newFile), { recursive: true, mode: 0o700 });
    const fd = openSync(newFile, "wx", 0o600); // wx: never overwrites, even if one appeared since the check
    try {
      writeSync(fd, JSON.stringify(state, null, 2));
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
    chmodSync(newFile, 0o600);
    log(`wrote ${newFile}`);
    return { addresses, wrote: true, source };
  } finally {
    for (const buf of toWipe) buf.fill(0);
  }
}

async function main(argv: readonly string[]): Promise<number> {
  try {
    const report = await migrate(parseArgs(argv), (line) => process.stdout.write(`${line}\n`));
    process.stdout.write(report.wrote ? "done: start the new core; the old keyring is untouched and can be archived\n" : "");
    return 0;
  } catch (error) {
    process.stderr.write(`migrate-keyring: ${error instanceof Refusal ? error.message : "failed (no details are printed: they could name a secret)"}\n`);
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main(process.argv.slice(2));
}
