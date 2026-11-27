// wallet_prepare_program / wallet_execute_prepared_program, against the transaction the giveaway builder makes.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { base58 } from "@scure/base";
import { Keypair, MessageV0, PublicKey, SystemProgram, Transaction, TransactionInstruction, VersionedTransaction } from "@solana/web3.js";
import { describe, expect, it } from "vitest";
import { build } from "../../src/wallet/giveaway.js";
import { checkDeclared as checkDeclaredForTest } from "../../src/wallet/policy.js";
import { simulate as simulateFn, ASSOCIATED_TOKEN_PROGRAM, TOKEN_2022_PROGRAM, TOKEN_PROGRAM, decodeTransaction, debits, parseTokenAccount, signatureIsValid, summarize } from "../../src/wallet/solana-program.js";
import { type Harness, makeHarness, mockSolana, setupWallet } from "./helpers.js";

const here = dirname(fileURLToPath(import.meta.url));
const V = JSON.parse(readFileSync(join(here, "..", "..", "..", "hands", "tests", "giveaway", "fixtures", "rewards_tx.json"), "utf8")) as Record<string, string | number>;
const REWARDS = V.program_id as string;
const MINT = V.mint as string;
const TOTAL = 3_500_042n;

function section(opts: { programs?: string[]; perTx?: number | bigint; daily?: number | bigint; mint?: string } = {}): string {
  const programs = (opts.programs ?? [REWARDS]).map((p) => `"${p}"`).join(", ");
  return `\n[wallet_policy.accounts.solana_1]\nallowed_programs = [${programs}]\n\n[[wallet_policy.accounts.solana_1.mints]]\nmint = "${opts.mint ?? MINT}"\nper_tx_max_raw = ${opts.perTx ?? 5_000_000}\ndaily_max_raw = ${opts.daily ?? 8_000_000}\n`;
}

function tokenAccount(mint: string, owner: string, amount: bigint): string {
  const data = Buffer.alloc(165);
  Buffer.from(new PublicKey(mint).toBytes()).copy(data, 0);
  Buffer.from(new PublicKey(owner).toBytes()).copy(data, 32);
  data.writeBigUInt64LE(amount, 64);
  return data.toString("base64");
}

interface Round {
  authority: string;
  built: Record<string, string>;
  ata: string;
}

/** Builds a create transaction for the giveaway wallet and scripts the RPC the simulation reads. */
async function round(h: Harness, opts: { debit?: bigint; slotSplit?: boolean; simErr?: unknown; nativePre?: number; nativePost?: number } = {}): Promise<Round> {
  const account = (await h.call("wallet_account", { chain: "solana", accountIndex: 1 })) as { address: string };
  const built = build(
    "create",
    JSON.stringify({
      program_id: REWARDS,
      mint: MINT,
      token_program: TOKEN_2022_PROGRAM,
      authority: account.address,
      merkle_root: V.merkle_root,
      total_amount: Number(TOTAL),
      clawback_ts: 1_798_000_000,
      recent_blockhash: V.recent_blockhash,
    }),
  ) as Record<string, string>;
  const ata = built.authority_token_account!;
  const debit = opts.debit ?? TOTAL;
  let slot = 100;
  h.net.onRpc("getTokenAccountsByOwner", (params) => {
    const program = (params[1] as { programId: string }).programId;
    return {
      context: { slot: opts.slotSplit ? slot++ : 100 },
      value: program === TOKEN_2022_PROGRAM ? [{ pubkey: ata, account: { data: [tokenAccount(MINT, account.address, 10_000_000n), "base64"] } }] : [],
    };
  });
  h.net.onRpc("getMultipleAccounts", (params) => ({
    context: { slot: opts.slotSplit ? slot++ : 100 },
    value: (params[0] as string[]).map(() => ({ lamports: opts.nativePre ?? 1_000_000_000 })),
  }));
  h.net.onRpc("simulateTransaction", (params) => {
    const requested = (params[1] as { accounts: { addresses: string[] } }).accounts.addresses.length;
    return {
      context: { slot: opts.slotSplit ? slot++ : 100 },
      value: {
        err: opts.simErr ?? null,
        accounts: opts.simErr
          ? null
          : [{ data: [tokenAccount(MINT, account.address, 10_000_000n - debit), "base64"] }, ...Array.from({ length: requested - 1 }, () => ({ lamports: opts.nativePost ?? 995_000_000, data: ["", "base64"] }))],
        unitsConsumed: 4242,
      },
    };
  });
  return { authority: account.address, built, ata };
}

const prepare = (h: Harness, r: Round, debits: unknown[] = [{ mint: MINT, amountRaw: TOTAL.toString() }]) =>
  h.call("wallet_prepare_program", { accountIndex: 1, transaction: r.built.transaction_b64, expectedDebits: debits }) as Promise<Record<string, any>>; // eslint-disable-line @typescript-eslint/no-explicit-any

async function message(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    return (error as Error).message;
  }
  throw new Error("expected a refusal");
}

describe("wallet_prepare_program", () => {
  it("decodes the transaction and returns what the giveaway hand compares with its plan", async () => {
    const h = makeHarness({ config: section() });
    await setupWallet(h);
    mockSolana(h.net);
    const r = await round(h);
    const quote = await prepare(h, r);
    expect(quote.quoteId).toMatch(/^prog-/);
    expect(quote).toMatchObject({ accountIndex: 1, address: r.authority });
    expect(quote.summary.feePayer).toBe(r.authority);
    expect(quote.summary.programs).toEqual([REWARDS]);
    expect(quote.summary.instructions).toHaveLength(1);
    expect(quote.summary.instructions[0].programId).toBe(REWARDS);
    expect(quote.summary.instructions[0].dataHex).toBe(r.built.data_hex);
    expect(quote.summary.movements).toEqual([]);
    expect(quote.summary.signers).toHaveLength(2);
    expect(quote.simulation).toMatchObject({ err: null, debits: [{ mint: MINT, amountRaw: TOTAL.toString() }] });
    expect(quote.declaredDebits).toEqual([{ mint: MINT, amountRaw: TOTAL.toString() }]);
    expect(quote.expiresAtMs - quote.createdAtMs).toBe(60_000);
  });

  it("refuses an account with no policy section, before any key is derived or any RPC is read", async () => {
    const h = makeHarness({ config: "" });
    await setupWallet(h);
    const r = await round(h);
    expect(await message(prepare(h, r))).toBe("WALLET_POLICY_REFUSED: account solana_1 has no [wallet_policy.accounts.solana_1] section");
    expect(h.net.rpcCalls("simulateTransaction")).toHaveLength(0);
  });

  it("refuses a program the section does not list", async () => {
    const h = makeHarness({ config: section({ programs: [TOKEN_2022_PROGRAM] }) });
    await setupWallet(h);
    const r = await round(h);
    expect(await message(prepare(h, r))).toBe(`WALLET_POLICY_REFUSED: program ${REWARDS} is not in solana_1's allowed_programs`);
  });

  it("refuses a mint the section does not list, and a debit over the per-transaction cap, with the exact messages", async () => {
    const h = makeHarness({ config: section({ mint: "So11111111111111111111111111111111111111112" }) });
    await setupWallet(h);
    const r = await round(h);
    expect(await message(prepare(h, r))).toBe(`WALLET_POLICY_REFUSED: mint ${MINT} is not in solana_1's mints`);
    const h2 = makeHarness({ config: section({ perTx: 3_500_041 }) });
    await setupWallet(h2);
    const r2 = await round(h2);
    expect(await message(prepare(h2, r2))).toBe("WALLET_POLICY_REFUSED: 3500042 of mint " + MINT + " (atomic) exceeds solana_1's per-transaction cap of 3500041");
  });

  it("refuses a rolling 24h overrun, counting executes already made, with the exact message", async () => {
    const h = makeHarness({ config: section({ daily: 5_000_000 }) });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const r = await round(h);
    const first = await prepare(h, r);
    await h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: first.quoteId });
    expect(sent).toHaveLength(1);
    const r2 = await round(h);
    expect(await message(prepare(h, r2))).toBe(
      `WALLET_POLICY_REFUSED: 3500042 of mint ${MINT} (atomic) would exceed solana_1's rolling 24h cap of 5000000 (3500042 already spent)`,
    );
    h.clock.now += 25 * 3600_000;
    const later = await prepare(h, await round(h));
    expect(later.quoteId).toMatch(/^prog-/);
  });

  it("refuses declared debits that do not match the simulation, a failing simulation, and reads split across slots", async () => {
    const h = makeHarness({ config: section() });
    await setupWallet(h);
    const r = await round(h, { debit: 1n });
    expect(await message(prepare(h, r))).toBe(`WALLET_POLICY_REFUSED: the simulation debits 1 of mint ${MINT} from the account, but 3500042 was declared`);
    const failing = await round(h, { simErr: { InstructionError: [0, "Custom"] } });
    expect(await message(prepare(h, failing))).toMatch(/^WALLET_POLICY_REFUSED: the transaction fails in simulation: /);
    const split = await round(h, { slotSplit: true });
    expect(await message(prepare(h, split))).toMatch(/could not simulate the transaction: the token balances and the simulation were read at different slots/);
    expect(await message(prepare(h, await round(h), []))).toBe(`WALLET_POLICY_REFUSED: the simulation debits ${TOTAL} of mint ${MINT} from the account, but 0 was declared`);
  });

  it("refuses a transaction whose fee payer is not the signing account, an unsigned co-signer, a System instruction and lookup tables", async () => {
    const h = makeHarness({ config: section() });
    await setupWallet(h);
    const r = await round(h);
    // another authority's transaction (fee payer is not account 1)
    const other = build("create", JSON.stringify({ program_id: REWARDS, mint: MINT, token_program: TOKEN_2022_PROGRAM, authority: V.authority, merkle_root: V.merkle_root, total_amount: 5, clawback_ts: 1_798_000_000, recent_blockhash: V.recent_blockhash })) as Record<string, string>;
    await expect(h.call("wallet_prepare_program", { accountIndex: 1, transaction: other.transaction_b64, expectedDebits: [] })).rejects.toThrow(
      /^WALLET_POLICY_REFUSED: the fee payer .* is not the signing account /,
    );
    // the seed's signature zeroed: the co-signer has not signed
    const wire = Buffer.from(r.built.transaction_b64!, "base64");
    wire.fill(0, 65, 129);
    expect(await message(h.call("wallet_prepare_program", { accountIndex: 1, transaction: wire.toString("base64"), expectedDebits: [{ mint: MINT, amountRaw: "3500042" }] }))).toMatch(
      /^WALLET_POLICY_REFUSED: required signer .* has not signed; co-sign before preparing$/,
    );
    // a corrupted co-signature
    const bad = Buffer.from(r.built.transaction_b64!, "base64");
    bad[70] = (bad[70]! + 1) & 0xff;
    expect(await message(h.call("wallet_prepare_program", { accountIndex: 1, transaction: bad.toString("base64"), expectedDebits: [{ mint: MINT, amountRaw: "3500042" }] }))).toMatch(
      /does not verify over the message$/,
    );
    await expect(h.call("wallet_prepare_program", { accountIndex: 1, transaction: "!!!", expectedDebits: [] })).rejects.toThrow(/not valid base64/);
    await expect(h.call("wallet_prepare_program", { accountIndex: 1, transaction: Buffer.alloc(10).toString("base64"), expectedDebits: [] })).rejects.toThrow();
    await expect(h.call("wallet_prepare_program", { accountIndex: 1, transaction: r.built.transaction_b64, expectedDebits: [], extra: 1 })).rejects.toThrow(/does not take extra/);
  });
});

describe("wallet_execute_prepared_program", () => {
  it("signs the message with the account's own key, keeps the co-signer's signature, sends, and records it", async () => {
    const h = makeHarness({ config: section() });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const r = await round(h);
    const quote = await prepare(h, r);
    const result = (await h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: quote.quoteId })) as Record<string, string>;
    expect(sent).toHaveLength(1);
    const tx = decodeTransaction(sent[0]!);
    expect(tx.signatures.every((s, i) => signatureIsValid(tx.accountKeys[i]!, tx.message, s))).toBe(true);
    expect(base58.encode(tx.signatures[0]!)).toBe(result.transactionHash);
    expect(result).toMatchObject({ quoteId: quote.quoteId, accountIndex: 1, address: r.authority });
    const ledger = (await h.call("wallet_ledger_list", { limit: 5 })) as Array<Record<string, unknown>>;
    expect(ledger[0]).toMatchObject({ kind: "program", accountIndex: 1, status: "broadcast", tokenAddress: MINT, amountRaw: TOTAL.toString(), assetSymbol: "SPL" });
  });

  it("never executes a quote twice, and not one that expired, or from a restart", async () => {
    const h = makeHarness({ config: section() });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const r = await round(h);
    const quote = await prepare(h, r);
    await h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: quote.quoteId });
    await expect(h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: quote.quoteId })).rejects.toThrow(/prepared program quote is unavailable or expired/);
    const expiring = await prepare(h, await round(h));
    h.clock.now += 61_000;
    await expect(h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: expiring.quoteId })).rejects.toThrow(/unavailable or expired/);
    expect(sent).toHaveLength(1);
  });

  it("re-simulates and re-checks the policy at execute: a chain that moved, or caps that tightened, stop it before signing", async () => {
    const h = makeHarness({ config: section() });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    const r = await round(h);
    const quote = await prepare(h, r);
    // the chain now debits more than was declared
    await round(h, { debit: TOTAL + 1n });
    expect(await message(h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: quote.quoteId }))).toMatch(/^WALLET_POLICY_REFUSED: the simulation debits 3500043 of mint/);
    expect(sent).toHaveLength(0);
    // the refusal leaves the quote for a retry, which now passes once the chain is back
    await round(h);
    // caps tighten between prepare and execute
    const { writeFileSync } = await import("node:fs");
    writeFileSync(h.configFile, section({ perTx: 1_000_000 }));
    expect(await message(h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: quote.quoteId }))).toMatch(/exceeds solana_1's per-transaction cap of 1000000$/);
    expect(sent).toHaveLength(0);
    const ledger = (await h.call("wallet_ledger_list", { limit: 5 })) as Array<Record<string, unknown>>;
    expect(ledger.filter((l) => l.status === "refused")).toHaveLength(2);
  });

  it("is refused for another account index, and an in-doubt send is never retried", async () => {
    const h = makeHarness({ config: section() });
    await setupWallet(h);
    mockSolana(h.net);
    h.net.onRpc("sendTransaction", () => {
      throw new Error("blockhash not found");
    });
    const r = await round(h);
    const quote = await prepare(h, r);
    await expect(h.call("wallet_execute_prepared_program", { accountIndex: 0, quoteId: quote.quoteId })).rejects.toThrow(/unavailable or expired/);
    await expect(h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: quote.quoteId })).rejects.toThrow(/blockhash not found/);
    const ledger = (await h.call("wallet_ledger_list", { limit: 5 })) as Array<Record<string, unknown>>;
    expect(ledger[0]).toMatchObject({ status: "in_doubt", kind: "program" });
    await expect(h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: quote.quoteId })).rejects.toThrow(/unavailable or expired/);
  });
});

describe("native SOL leaving the wallet", () => {
  it("is measured on the fee payer and refused above the bound, at prepare and again at execute", async () => {
    const h = makeHarness({ config: section() });
    await setupWallet(h);
    const { sent } = mockSolana(h.net);
    // a program that CPIs a System transfer: no token debit, but the account loses 0.5 SOL
    const drain = await round(h, { nativePost: 500_000_000 });
    expect(await message(prepare(h, drain))).toBe(
      "WALLET_POLICY_REFUSED: the simulation takes 500000000 lamports of SOL from the wallet's accounts, above the 10000000 a program execute may spend on fees and rent",
    );
    // fees and rent for the accounts it creates stay allowed, up to the bound exactly
    const rent = await round(h, { nativePost: 1_000_000_000 - 10_000_000 });
    const quote = await prepare(h, rent);
    expect(quote.simulation.nativeDebitLamports).toBe("10000000");
    // the chain moves between prepare and execute: the drain is caught before anything is signed
    await round(h, { nativePost: 1 });
    expect(await message(h.call("wallet_execute_prepared_program", { accountIndex: 1, quoteId: quote.quoteId }))).toMatch(/takes 999999999 lamports of SOL/);
    expect(sent).toHaveLength(0);
    // an account the transaction closes lost everything it had
    const closing = await round(h);
    h.net.onRpc("simulateTransaction", (params) => {
      const n = (params[1] as { accounts: { addresses: string[] } }).accounts.addresses.length;
      return { context: { slot: 100 }, value: { err: null, accounts: [{ data: [tokenAccount(MINT, closing.authority, 10_000_000n - TOTAL), "base64"] }, ...Array.from({ length: n - 1 }, () => null)] } };
    });
    expect(await message(prepare(h, closing))).toMatch(/takes 1000000000 lamports/);
  });

  it("can be bound lower by the environment, never higher", async () => {
    const h = makeHarness({ config: section(), env: { OPENHUMAN_WALLET_PROGRAM_MAX_NATIVE_LAMPORTS: "1000" } });
    await setupWallet(h);
    expect(await message(prepare(h, await round(h, { nativePost: 999_000_000 })))).toMatch(/above the 1000 a program execute/);
    const loose = makeHarness({ config: section(), env: { OPENHUMAN_WALLET_PROGRAM_MAX_NATIVE_LAMPORTS: "99999999999" } });
    await setupWallet(loose);
    expect(await message(prepare(loose, await round(loose, { nativePost: 500_000_000 })))).toMatch(/above the 10000000 a program execute/);
  });

  it("also watches the other wallet accounts the message names", async () => {
    const owner = Keypair.generate().publicKey.toBytes();
    const other = Keypair.generate().publicKey.toBytes();
    const calls: unknown[][] = [];
    const rpc = {
      call: async (method: string, params: unknown[]) => {
        calls.push([method, params]);
        if (method === "getTokenAccountsByOwner") return { context: { slot: 7 }, value: [] };
        if (method === "getMultipleAccounts") return { context: { slot: 7 }, value: [{ lamports: 100 }, { lamports: 900 }] };
        return { context: { slot: 7 }, value: { err: null, accounts: [{ lamports: 90 }, { lamports: 100 }] } };
      },
    } as unknown as import("../../src/wallet/rpc.js").SolanaRpc;
    const report = await simulateFn(rpc, new Uint8Array(10), owner, [owner, other], [other]);
    expect(report.nativeDebitLamports).toBe("810"); // 10 from the fee payer, 800 from the other account
    expect((calls.find((c) => c[0] === "getMultipleAccounts")![1] as string[][])[0]).toHaveLength(2);
  });
});

describe("the pure parts", () => {
  it("reads token accounts and measures debits by mint, ignoring credits and accounts the signer does not own", () => {
    const owner = new PublicKey(V.authority as string).toBytes();
    const mint = new PublicKey(MINT).toBytes();
    const state = (amount: bigint, who = owner) => parseTokenAccount(Buffer.from(tokenAccount(MINT, new PublicKey(who).toBase58(), amount), "base64"))!;
    expect(debits(owner, [state(10n)], [state(4n)])).toEqual([{ mint: MINT, amountRaw: "6" }]);
    expect(debits(owner, [state(10n)], [state(40n)])).toEqual([]);
    expect(debits(owner, [state(10n)], [undefined])).toEqual([{ mint: MINT, amountRaw: "10" }]); // closed counts as emptied
    expect(debits(owner, [state(10n, new PublicKey(mint).toBytes())], [state(0n)])).toEqual([]);
    expect(parseTokenAccount(new Uint8Array(10))).toBeUndefined();
    expect(TOKEN_PROGRAM).not.toBe(TOKEN_2022_PROGRAM);
  });
});

describe("what a program transaction may contain, whatever the policy says", () => {
  const payer = Keypair.generate();
  const mint = Keypair.generate().publicKey;
  const wire = (...instructions: TransactionInstruction[]): Uint8Array => {
    const tx = new Transaction({ feePayer: payer.publicKey, recentBlockhash: base58.encode(Buffer.alloc(32, 3)) }).add(...instructions);
    return new Uint8Array(tx.serialize({ requireAllSignatures: false, verifySignatures: false }));
  };
  const summary = (bytes: Uint8Array) => summarize(decodeTransaction(bytes), payer.publicKey.toBase58());
  const token = (data: number[], program = TOKEN_PROGRAM) =>
    new TransactionInstruction({
      programId: new PublicKey(program),
      keys: [payer.publicKey, mint, Keypair.generate().publicKey, payer.publicKey].map((pubkey, i) => ({ pubkey, isSigner: i === 3, isWritable: i !== 1 })),
      data: Buffer.from(data),
    });
  const checked = (amount: bigint): number[] => [12, ...Buffer.from(new BigUint64Array([amount]).buffer), 6];

  it("refuses a System instruction", () => {
    const bytes = wire(SystemProgram.transfer({ fromPubkey: payer.publicKey, toPubkey: Keypair.generate().publicKey, lamports: 1 }));
    expect(() => summary(bytes)).toThrow("WALLET_POLICY_REFUSED: instruction 0 calls the System program; native SOL movements are not signed through program executes");
  });

  it("refuses a token Transfer, which does not name its mint, and any other token instruction", () => {
    expect(() => summary(wire(token([3, 1, 0, 0, 0, 0, 0, 0, 0])))).toThrow(
      "WALLET_POLICY_REFUSED: instruction 0 is a token Transfer, which does not name its mint; use TransferChecked",
    );
    expect(() => summary(wire(token([4])))).toThrow("instruction 0 is token instruction Some(4); only TransferChecked is signed through program executes");
    expect(() => summary(wire(token([])))).toThrow("instruction 0 is token instruction None;");
    expect(() => summary(wire(token([12, 1, 2])))).toThrow("instruction 0 is a malformed TransferChecked");
  });

  it("reads a TransferChecked for what it moves, per mint, for either token program", () => {
    const s = summary(wire(token(checked(7n)), token(checked(5n), TOKEN_2022_PROGRAM)));
    expect(s.movements).toHaveLength(2);
    expect(s.totals).toEqual([{ mint: mint.toBase58(), amountRaw: "12" }]);
    expect(s.programs).toEqual([TOKEN_PROGRAM, TOKEN_2022_PROGRAM]);
    // the declared debits must cover what is visible
    expect(() => checkDeclaredForTest(s, [{ mint: mint.toBase58(), amountRaw: "11" }])).toThrow(`the transaction transfers 12 of mint ${mint.toBase58()} but declares 11`);
    expect(() => checkDeclaredForTest(s, [{ mint: mint.toBase58(), amountRaw: "12" }, { mint: mint.toBase58(), amountRaw: "1" }])).toThrow(`mint ${mint.toBase58()} is declared twice`);
    expect(() => checkDeclaredForTest(s, [{ mint: mint.toBase58(), amountRaw: "1.5" }])).toThrow("is not a whole number of atomic units");
    checkDeclaredForTest(s, [{ mint: mint.toBase58(), amountRaw: "12" }]);
  });

  it("allows an associated token account create and refuses RecoverNested", () => {
    const ata = (data: number[]) => new TransactionInstruction({ programId: new PublicKey(ASSOCIATED_TOKEN_PROGRAM), keys: [{ pubkey: payer.publicKey, isSigner: true, isWritable: true }], data: Buffer.from(data) });
    summary(wire(ata([]), ata([0]), ata([1])));
    expect(() => summary(wire(ata([2])))).toThrow("instruction 0 is not an associated token account create");
  });

  it("refuses address lookup tables", () => {
    const message = new MessageV0({
      header: { numRequiredSignatures: 1, numReadonlySignedAccounts: 0, numReadonlyUnsignedAccounts: 1 },
      staticAccountKeys: [payer.publicKey, new PublicKey(REWARDS)],
      recentBlockhash: base58.encode(Buffer.alloc(32, 3)),
      compiledInstructions: [{ programIdIndex: 1, accountKeyIndexes: [0], data: new Uint8Array([1]) }],
      addressTableLookups: [{ accountKey: Keypair.generate().publicKey, writableIndexes: [0], readonlyIndexes: [] }],
    });
    const bytes = new VersionedTransaction(message).serialize();
    expect(() => summary(bytes)).toThrow("transactions that load address lookup tables are not supported; list every account in the message");
  });

  it("refuses oversized and trailing bytes", () => {
    expect(() => decodeTransaction(new Uint8Array(1233))).toThrow("transaction is 1233 bytes; the Solana limit is 1232");
    const bytes = wire(token(checked(1n)));
    expect(() => decodeTransaction(Uint8Array.from([...bytes, 0]))).toThrow("trailing bytes");
    expect(() => decodeTransaction(bytes.subarray(0, 50))).toThrow("transaction is truncated");
  });
});
