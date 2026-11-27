import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { addressOf, seedOf } from "../../src/wallet/derive.js";
import { makeHarness, setupWallet, throwawayWallet } from "./helpers.js";

describe("wallet setup", () => {
  it("is not configured until set up", async () => {
    const h = makeHarness();
    expect(await h.call("wallet_status")).toMatchObject({ configured: false, accounts: [] });
    await expect(h.call("wallet_balances")).rejects.toThrow(/wallet is not configured/);
  });

  it("sets up from what bin/wallet-setup sends, and keeps the giveaway account as an additional account", async () => {
    const h = makeHarness();
    const { phrase, status } = await setupWallet(h);
    expect(status).toMatchObject({ configured: true, onboardingCompleted: true, consentGranted: true, secretStored: true, source: "generated", mnemonicWordCount: 12 });
    const accounts = status.accounts as Array<{ chain: string; address: string }>;
    expect(accounts.map((a) => a.chain).sort()).toEqual(["btc", "evm", "solana", "tron"]);
    expect(accounts.find((a) => a.chain === "solana")!.address).toBe(addressOf(seedOf(phrase), "solana", "m/44'/501'/0'/0'"));
    const extra = status.additionalAccounts as Array<{ index: number; label: string; derivationPath: string }>;
    expect(extra).toHaveLength(1);
    expect(extra[0]).toMatchObject({ index: 1, label: "giveaway", derivationPath: "m/44'/501'/1'/0'" });
  });

  it("never stores the phrase in the clear", async () => {
    const h = makeHarness();
    const { phrase } = await setupWallet(h);
    const file = readFileSync(join(h.stateDir, "wallet", "wallet-state.json"), "utf8");
    expect(file).not.toContain(phrase.split(" ").slice(0, 3).join(" "));
    expect(JSON.parse(file).encryptedMnemonic).toMatch(/^enc2:[0-9a-f]+$/);
  });

  it("refuses to overwrite without force, and replaces with it", async () => {
    const h = makeHarness();
    const first = await setupWallet(h);
    await expect(setupWallet(h)).rejects.toThrow(/already configured; pass force=true/);
    const second = throwawayWallet(24);
    const derived = (await h.call("wallet_derive_recovery_material", { phrase: second.phrase })) as { accounts: Array<Record<string, unknown>> };
    const encrypted = (await h.call("encrypt_secret", { plaintext: second.phrase })) as string;
    const status = (await h.call("wallet_setup", {
      consentGranted: true,
      source: "imported",
      mnemonicWordCount: 24,
      encryptedMnemonic: encrypted,
      accounts: derived.accounts,
      force: true,
    })) as { accounts: Array<{ chain: string; address: string }> };
    expect(status.accounts.find((a) => a.chain === "solana")!.address).not.toBe(
      (first.status.accounts as Array<{ chain: string; address: string }>).find((a) => a.chain === "solana")!.address,
    );
  });

  it("refuses setup without consent, with a bad word count, or with accounts that do not derive from the stored phrase", async () => {
    const h = makeHarness();
    const w = throwawayWallet();
    const derived = (await h.call("wallet_derive_recovery_material", { phrase: w.phrase })) as { accounts: Array<Record<string, unknown>> };
    const encrypted = (await h.call("encrypt_secret", { plaintext: w.phrase })) as string;
    const base = { consentGranted: true, source: "generated", mnemonicWordCount: 12, encryptedMnemonic: encrypted, accounts: derived.accounts };
    await expect(h.call("wallet_setup", { ...base, consentGranted: false })).rejects.toThrow(/requires explicit consent/);
    await expect(h.call("wallet_setup", { ...base, mnemonicWordCount: 13 })).rejects.toThrow(/unsupported mnemonic word count 13/);
    await expect(h.call("wallet_setup", { ...base, encryptedMnemonic: "" })).rejects.toThrow(/requires encrypted mnemonic material/);
    await expect(h.call("wallet_setup", { ...base, accounts: derived.accounts.filter((a) => a.chain !== "tron") })).rejects.toThrow(
      /exactly one 'tron' account/,
    );
    const other = throwawayWallet();
    await expect(h.call("wallet_setup", { ...base, accounts: other.accounts })).rejects.toThrow(/does not derive from the stored phrase/);
    await expect(h.call("wallet_setup", { ...base, encryptedMnemonic: "enc2:00" })).rejects.toThrow(/does not open under this core's keyring master key/);
    expect(await h.call("wallet_status")).toMatchObject({ configured: false });
  });

  it("derives the giveaway account on demand and answers wallet_account", async () => {
    const h = makeHarness();
    const { phrase } = await setupWallet(h);
    const index1 = (await h.call("wallet_account", { chain: "solana", accountIndex: 1 })) as { address: string; derivationPath: string };
    expect(index1.derivationPath).toBe("m/44'/501'/1'/0'");
    expect(index1.address).toBe(addressOf(seedOf(phrase), "solana", "m/44'/501'/1'/0'"));
    const index0 = (await h.call("wallet_account", { chain: "solana", accountIndex: 0 })) as { address: string };
    expect(index0.address).toBe(addressOf(seedOf(phrase), "solana", "m/44'/501'/0'/0'"));
    await expect(h.call("wallet_account", { chain: "solana", accountIndex: 2 })).rejects.toThrow(/no wallet account derived for chain 'solana' index 2/);
  });

  it("generates phrases of the asked length and validates the word count of recovery material", async () => {
    const h = makeHarness();
    const out = (await h.call("wallet_generate_mnemonic", { wordCount: 24 })) as { phrase: string };
    expect(out.phrase.split(" ")).toHaveLength(24);
    await expect(h.call("wallet_generate_mnemonic", { wordCount: 7 })).rejects.toThrow(/unsupported mnemonic word count 7/);
    await expect(h.call("wallet_derive_recovery_material", { phrase: "one two three" })).rejects.toThrow(/12, 15, 18, 21, 24 words/);
    await expect(h.call("wallet_derive_recovery_material", { phrase: out.phrase.replace(/^\w+/, "zzzz") })).rejects.toThrow();
  });
});
