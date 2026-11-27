// Mnemonic -> accounts. Everything that touches a seed or a private key lives
// here and in the signing functions that call it; nothing in this file logs.
import { createHmac, createPrivateKey, sign as nodeSign } from "node:crypto";
import { HDKey } from "@scure/bip32";
import { generateMnemonic, mnemonicToSeedSync, validateMnemonic } from "@scure/bip39";
import { wordlist } from "@scure/bip39/wordlists/english";
import { bech32, createBase58check } from "@scure/base";
import { sha256 } from "@noble/hashes/sha256";
import { ripemd160 } from "@noble/hashes/ripemd160";
import { Keypair } from "@solana/web3.js";
import { privateKeyToAccount } from "viem/accounts";
import { bytesToHex } from "./util.js";

export type WalletChain = "evm" | "btc" | "solana" | "tron";
export const WALLET_CHAINS: readonly WalletChain[] = ["evm", "btc", "solana", "tron"];
export const MNEMONIC_WORD_COUNTS = [12, 15, 18, 21, 24] as const;

/** The primary (index 0) account of each chain, in OpenHuman's order. */
export const PRIMARY_ACCOUNTS: ReadonlyArray<readonly [WalletChain, string]> = [
  ["evm", "m/44'/60'/0'/0/0"],
  ["btc", "m/44'/0'/0'/0/0"],
  ["solana", "m/44'/501'/0'/0'"],
  ["tron", "m/44'/195'/0'/0/0"],
];

export interface SecondaryAccount {
  chain: WalletChain;
  index: number;
  derivationPath: string;
  label: string;
}

/** Solana index 1 is the giveaway wallet (Phantom / solana-keygen account-index convention). */
export const SECONDARY_ACCOUNTS: readonly SecondaryAccount[] = [
  { chain: "solana", index: 1, derivationPath: "m/44'/501'/1'/0'", label: "giveaway" },
];

export interface WalletAccount {
  chain: WalletChain;
  address: string;
  derivationPath: string;
  index?: number;
  label?: string;
}

export function accountDerivationPath(chain: WalletChain, index: number): string | undefined {
  if (index === 0) return PRIMARY_ACCOUNTS.find(([c]) => c === chain)?.[1];
  return SECONDARY_ACCOUNTS.find((a) => a.chain === chain && a.index === index)?.derivationPath;
}

export function generatePhrase(wordCount: number): string {
  if (!(MNEMONIC_WORD_COUNTS as readonly number[]).includes(wordCount)) {
    throw new Error(`unsupported mnemonic word count ${wordCount}; expected one of ${MNEMONIC_WORD_COUNTS.join(", ")}`);
  }
  return generateMnemonic(wordlist, (wordCount * 32) / 3);
}

/** Collapses whitespace; the phrase is otherwise taken as given. */
export function normalizePhrase(phrase: string): string {
  return phrase.trim().split(/\s+/).join(" ");
}

export function phraseIsValid(phrase: string): boolean {
  return validateMnemonic(normalizePhrase(phrase), wordlist);
}

/** The 64-byte BIP-39 seed (empty passphrase). Throws on an invalid phrase. */
export function seedOf(phrase: string): Uint8Array {
  const normalized = normalizePhrase(phrase);
  if (!(MNEMONIC_WORD_COUNTS as readonly number[]).includes(normalized.split(" ").length)) {
    throw new Error(`recovery phrase must contain one of ${MNEMONIC_WORD_COUNTS.join(", ")} words`);
  }
  if (!validateMnemonic(normalized, wordlist)) throw new Error("recovery phrase is not valid BIP-39");
  return mnemonicToSeedSync(normalized);
}

// SLIP-0010 ed25519 (hardened derivation only).
export function slip10Ed25519(seed: Uint8Array, path: string): Uint8Array {
  const parts = path.split("/");
  if (parts[0] !== "m") throw new Error("bad derivation path");
  let I = createHmac("sha512", "ed25519 seed").update(seed).digest();
  let key = I.subarray(0, 32);
  let chain = I.subarray(32);
  for (const part of parts.slice(1)) {
    if (!part.endsWith("'")) throw new Error("ed25519 derivation is hardened only");
    const index = Number(part.slice(0, -1));
    if (!Number.isInteger(index) || index < 0 || index >= 0x80000000) throw new Error("bad derivation path");
    const data = Buffer.alloc(37);
    data[0] = 0;
    Buffer.from(key).copy(data, 1);
    data.writeUInt32BE((index | 0x80000000) >>> 0, 33);
    I = createHmac("sha512", chain).update(data).digest();
    key = I.subarray(0, 32);
    chain = I.subarray(32);
  }
  return new Uint8Array(key);
}

export function solanaKeypair(seed: Uint8Array, path: string): Keypair {
  return Keypair.fromSeed(slip10Ed25519(seed, path));
}

function secp256k1Key(seed: Uint8Array, path: string): Uint8Array {
  const child = HDKey.fromMasterSeed(seed).derive(path);
  if (!child.privateKey) throw new Error("derivation produced no key");
  return child.privateKey;
}

export function evmPrivateKey(seed: Uint8Array, path: string): `0x${string}` {
  return `0x${bytesToHex(secp256k1Key(seed, path))}`;
}

const base58check = createBase58check(sha256);

function tronAddress(evmAddress: string): string {
  const payload = new Uint8Array(21);
  payload[0] = 0x41;
  payload.set(Buffer.from(evmAddress.slice(2), "hex"), 1);
  return base58check.encode(payload);
}

function btcAddress(seed: Uint8Array, path: string): string {
  const child = HDKey.fromMasterSeed(seed).derive(path);
  if (!child.publicKey) throw new Error("derivation produced no key");
  const program = ripemd160(sha256(child.publicKey));
  return bech32.encode("bc", [0, ...bech32.toWords(program)]);
}

/** The public address of CHAIN at PATH from SEED. */
export function addressOf(seed: Uint8Array, chain: WalletChain, path: string): string {
  switch (chain) {
    case "solana":
      return solanaKeypair(seed, path).publicKey.toBase58();
    case "evm":
      return privateKeyToAccount(evmPrivateKey(seed, path)).address;
    case "tron":
      return tronAddress(privateKeyToAccount(evmPrivateKey(seed, path)).address);
    case "btc":
      return btcAddress(seed, path);
  }
}

/** Every account recovery and import derive: the primaries, then the secondaries. */
export function deriveAllAccounts(seed: Uint8Array): WalletAccount[] {
  const out: WalletAccount[] = PRIMARY_ACCOUNTS.map(([chain, path]) => ({
    chain,
    address: addressOf(seed, chain, path),
    derivationPath: path,
  }));
  for (const a of SECONDARY_ACCOUNTS) {
    out.push({
      chain: a.chain,
      address: addressOf(seed, a.chain, a.derivationPath),
      derivationPath: a.derivationPath,
      index: a.index,
      label: a.label,
    });
  }
  return out;
}

const PKCS8_ED25519 = Buffer.from("302e020100300506032b657004220420", "hex");

/** An ed25519 signature by KEYPAIR over MESSAGE (RFC 8032, deterministic). */
export function signEd25519(keypair: Keypair, message: Uint8Array): Uint8Array {
  const priv = createPrivateKey({ key: Buffer.concat([PKCS8_ED25519, keypair.secretKey.subarray(0, 32)]), format: "der", type: "pkcs8" });
  return new Uint8Array(nodeSign(null, message, priv));
}
