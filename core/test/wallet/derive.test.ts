import { describe, expect, it } from "vitest";
import { addressOf, deriveAllAccounts, generatePhrase, seedOf, slip10Ed25519 } from "../../src/wallet/derive.js";

// The BIP-39 test phrase every wallet's documentation uses; it holds nothing.
const ABANDON = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about";

describe("derivation", () => {
  it("matches SLIP-0010's ed25519 test vector 1 (m/0')", () => {
    const seed = Buffer.from("000102030405060708090a0b0c0d0e0f", "hex");
    expect(Buffer.from(slip10Ed25519(seed, "m/0'")).toString("hex")).toBe("68e0fe46dfb67e368c75379acec591dad19df3cde26e63b93a8e704f1dade7a3");
  });

  it("derives the well-known EVM address of the test phrase at m/44'/60'/0'/0/0", () => {
    expect(addressOf(seedOf(ABANDON), "evm", "m/44'/60'/0'/0/0")).toBe("0x9858EfFD232B4033E47d90003D41EC34EcaEda94");
  });

  it("derives the well-known Solana address of the test phrase at m/44'/501'/0'/0'", () => {
    expect(addressOf(seedOf(ABANDON), "solana", "m/44'/501'/0'/0'")).toBe("HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk");
  });

  it("derives every chain's primary account and the giveaway account at index 1", () => {
    const accounts = deriveAllAccounts(seedOf(ABANDON));
    expect(accounts.map((a) => `${a.chain}:${a.index ?? 0}`)).toEqual(["evm:0", "btc:0", "solana:0", "tron:0", "solana:1"]);
    const btc = accounts.find((a) => a.chain === "btc")!;
    expect(btc.address).toMatch(/^bc1q[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{38}$/);
    expect(accounts.find((a) => a.chain === "tron")!.address).toMatch(/^T[1-9A-HJ-NP-Za-km-z]{33}$/);
    const giveaway = accounts.find((a) => a.index === 1)!;
    expect(giveaway.derivationPath).toBe("m/44'/501'/1'/0'");
    expect(giveaway.address).not.toBe(accounts.find((a) => a.chain === "solana" && !a.index)!.address);
  });

  it("generates phrases of every supported length and refuses others", () => {
    for (const n of [12, 15, 18, 21, 24]) expect(generatePhrase(n).split(" ")).toHaveLength(n);
    expect(() => generatePhrase(13)).toThrow(/unsupported mnemonic word count 13/);
  });

  it("refuses a phrase with a bad checksum", () => {
    expect(() => seedOf(ABANDON.replace("about", "abandon"))).toThrow(/not valid BIP-39/);
  });
});
