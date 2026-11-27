// Wallet setup state and the vault that opens it.
//
// Compatibility with OpenHuman (see README.md): the state file has the same
// camelCase shape as OpenHuman's `wallet-state.json`, and `encryptedMnemonic`
// is the same `enc2:` envelope `encrypt_secret` produced. Here the envelope is
// sealed directly under the operator's keyring master key
// (OPENHUMAN_KEYRING_MASTER_KEY_FILE); OpenHuman sealed it under a separate
// "user master" kept in its keyring. core/scripts/migrate-keyring.ts re-seals.
import { join } from "node:path";
import type { CoreContext } from "../server/types.js";
import { decryptEnc2, loadMasterKey } from "./crypto.js";
import {
  MNEMONIC_WORD_COUNTS,
  type WalletAccount,
  type WalletChain,
  WALLET_CHAINS,
  SECONDARY_ACCOUNTS,
  accountDerivationPath,
  seedOf,
} from "./derive.js";
import { fail, trackSecret } from "./errors.js";
import { readJsonFile, withMutex, writeFileAtomic } from "./util.js";

export const WALLET_NOT_CONFIGURED_MESSAGE = "wallet is not configured; run wallet setup first";

export interface StoredWalletState {
  consentGranted: boolean;
  source: "generated" | "imported";
  mnemonicWordCount: number;
  encryptedMnemonic: string;
  accounts: WalletAccount[];
  updatedAtMs: number;
}

export function walletDir(ctx: CoreContext): string {
  return join(ctx.stateDir, "wallet");
}

const stateFile = (ctx: CoreContext): string => join(walletDir(ctx), "wallet-state.json");

export function loadState(ctx: CoreContext): StoredWalletState | undefined {
  const raw = readJsonFile<StoredWalletState>(stateFile(ctx));
  if (!raw) return undefined;
  const checked = validateAccounts(raw.accounts ?? [], false);
  if (!raw.encryptedMnemonic || typeof checked === "string") {
    throw fail("the stored wallet state failed validation; restore it or run wallet setup with force");
  }
  return raw;
}

export function saveState(ctx: CoreContext, state: StoredWalletState): void {
  writeFileAtomic(stateFile(ctx), JSON.stringify(state, null, 2));
}

export function withStateLock<T>(ctx: CoreContext, body: () => Promise<T>): Promise<T> {
  return withMutex(`wallet-state:${ctx.stateDir}`, body);
}

export function isPrimary(account: WalletAccount): boolean {
  return (account.index ?? 0) === 0;
}

/**
 * Normalises and checks the accounts a setup names (port of validate_setup).
 * Returns the normalised list, or the first problem as a string.
 *
 * One deliberate difference: an index-0 account sitting at a secondary
 * account's path is taken as that secondary account (index and label filled
 * in) instead of refused. bin/wallet-setup copies only chain, address and
 * derivationPath from the derived accounts, which drops `index`, so the
 * giveaway account would otherwise always read as a primary aliasing one.
 */
export function validateAccounts(accounts: WalletAccount[], strictAlias: boolean): string | WalletAccount[] {
  const normalized: WalletAccount[] = [];
  for (const account of accounts) {
    if (!(WALLET_CHAINS as readonly string[]).includes(account.chain)) return `wallet setup account '${String(account.chain)}' is not a known chain`;
    const address = String(account.address ?? "").trim();
    let derivationPath = String(account.derivationPath ?? "").trim();
    if (!address) return `wallet setup account '${account.chain}' is missing an address`;
    if (!derivationPath) return `wallet setup account '${account.chain}' is missing a derivation path`;
    let index = account.index ?? 0;
    let label = typeof account.label === "string" && account.label.trim() ? account.label.trim() : undefined;
    if (index === 0) {
      const alias = SECONDARY_ACCOUNTS.find((s) => s.chain === account.chain && s.derivationPath === derivationPath);
      if (alias) {
        if (strictAlias) {
          return `wallet setup account '${account.chain}' index 0 uses a secondary account path; a primary account must not alias one`;
        }
        index = alias.index;
        label = label ?? alias.label;
      }
    }
    if (index > 0) {
      if (accountDerivationPath(account.chain, index) !== derivationPath) {
        return `wallet setup account '${account.chain}' index ${index} is not a known secondary account`;
      }
      if (normalized.some((e) => e.chain === account.chain && (e.index ?? 0) === index)) {
        return `wallet setup lists '${account.chain}' index ${index} more than once`;
      }
    }
    derivationPath = derivationPath.trim();
    normalized.push({
      chain: account.chain,
      address,
      derivationPath,
      ...(index !== 0 ? { index } : {}),
      ...(label !== undefined ? { label } : {}),
    });
  }
  for (const chain of WALLET_CHAINS) {
    const count = normalized.filter((a) => a.chain === chain && isPrimary(a)).length;
    if (count !== 1) return `wallet setup must include exactly one '${chain}' account`;
  }
  return normalized;
}

export function checkWordCount(count: number): string | undefined {
  return (MNEMONIC_WORD_COUNTS as readonly number[]).includes(count)
    ? undefined
    : `unsupported mnemonic word count ${count}; expected one of ${MNEMONIC_WORD_COUNTS.join(", ")}`;
}

/**
 * Opens the wallet's recovery phrase just long enough to run BODY with the
 * BIP-39 seed, then wipes the seed. The phrase and seed never leave this
 * function except as BODY's own (public) result.
 */
export async function withSeed<T>(ctx: CoreContext, body: (seed: Uint8Array, state: StoredWalletState) => Promise<T> | T): Promise<T> {
  const state = loadState(ctx);
  if (!state) throw fail(WALLET_NOT_CONFIGURED_MESSAGE);
  let key: Buffer;
  try {
    key = loadMasterKey(ctx.env);
  } catch (error) {
    throw fail(`the wallet cannot sign: ${(error as Error).message}`);
  }
  let seed: Uint8Array | undefined;
  try {
    let phrase: string;
    try {
      const plain = decryptEnc2(key, state.encryptedMnemonic);
      phrase = plain.toString("utf8");
      plain.fill(0);
    } catch {
      throw fail("failed to decrypt the recovery phrase");
    }
    trackSecret(phrase);
    seed = seedOf(phrase);
    return await body(seed, state);
  } finally {
    key.fill(0);
    seed?.fill(0);
  }
}

/** The stored account INDEX of CHAIN: a known secondary account missing from the state is derived on first use. */
export async function ensureAccount(ctx: CoreContext, chain: WalletChain, index: number): Promise<WalletAccount> {
  return withStateLock(ctx, async () => {
    const state = loadState(ctx);
    if (!state) throw fail(WALLET_NOT_CONFIGURED_MESSAGE);
    const found = state.accounts.find((a) => a.chain === chain && (a.index ?? 0) === index);
    if (found) return found;
    const path = accountDerivationPath(chain, index);
    if (!path || index === 0) {
      throw fail(index === 0 ? `no wallet account derived for chain '${chain}'` : `no wallet account derived for chain '${chain}' index ${index}`);
    }
    const { addressOf } = await import("./derive.js");
    const secondary = SECONDARY_ACCOUNTS.find((a) => a.chain === chain && a.index === index);
    const account = await withSeed(ctx, (seed) => ({
      chain,
      address: addressOf(seed, chain, path),
      derivationPath: path,
      index,
      ...(secondary ? { label: secondary.label } : {}),
    }));
    saveState(ctx, { ...state, accounts: [...state.accounts, account] });
    return account;
  });
}
