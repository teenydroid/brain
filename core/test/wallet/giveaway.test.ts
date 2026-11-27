import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createPublicKey, verify } from "node:crypto";
import { describe, expect, it } from "vitest";
import { giveawayTxMain } from "../../src/wallet/cli.js";
import { GiveawayError, build, createData } from "../../src/wallet/giveaway.js";
import { decodeTransaction } from "../../src/wallet/solana-program.js";

const here = dirname(fileURLToPath(import.meta.url));
interface Case {
  name: string;
  op: string;
  stdin: string;
  code: number;
  stdout: string;
  stderr: string;
}
const rust = JSON.parse(readFileSync(join(here, "fixtures", "giveaway_tx_rust.json"), "utf8")) as { cases: Case[] };
// made by @solana/web3.js the way web/src/lib/distributor.ts encodes the program; hands/tests/giveaway reads it too
const vectors = JSON.parse(readFileSync(join(here, "..", "..", "..", "hands", "tests", "giveaway", "fixtures", "rewards_tx.json"), "utf8")) as Record<string, string | number>;

/** Where serde's own wording is not reproduced, only the prefix is held. */
const PREFIX_ONLY = new Set(["err_total_overflow", "err_not_json", "err_empty_input", "err_array"]);
const withoutPosition = (text: string): string => text.replace(/ at line \d+ column \d+/, "");

describe("giveaway-tx parity with spine/src/giveaway_tx.rs", () => {
  const ok = rust.cases.filter((c) => c.code === 0);
  const bad = rust.cases.filter((c) => c.code !== 0);

  it("has recorded cases from the Rust binary", () => {
    expect(ok.length).toBeGreaterThanOrEqual(5);
    expect(bad.length).toBeGreaterThanOrEqual(20);
  });

  for (const c of ok) {
    it(`${c.name}: stdout is byte for byte what the Rust binary printed`, () => {
      const got = giveawayTxMain([c.op], c.stdin);
      expect(got.code).toBe(0);
      expect(got.stderr).toBe("");
      expect(got.stdout).toBe(c.stdout);
    });
  }

  for (const c of bad) {
    it(`${c.name}: exit 2 and the same error`, () => {
      const got = giveawayTxMain([c.op], c.stdin);
      expect(got.code).toBe(c.code);
      expect(got.stdout).toBe("");
      const theirs = (JSON.parse(c.stderr) as { error: string }).error;
      const ours = (JSON.parse(got.stderr) as { error: string }).error;
      if (PREFIX_ONLY.has(c.name)) expect(ours.startsWith("bad create request:")).toBe(true);
      else expect(ours).toBe(withoutPosition(theirs));
    });
  }

  it("matches the @solana/web3.js vectors the Rust crate's own tests are held to", () => {
    const create = JSON.parse(
      giveawayTxMain(
        ["create"],
        JSON.stringify({
          program_id: vectors.program_id,
          mint: vectors.mint,
          token_program: vectors.token_program,
          authority: vectors.authority,
          merkle_root: vectors.merkle_root,
          total_amount: Number(vectors.total_amount),
          clawback_ts: vectors.clawback_ts,
          recent_blockhash: vectors.recent_blockhash,
          seed_secret_hex: vectors.seed_secret_hex,
        }),
      ).stdout,
    ) as Record<string, unknown>;
    expect(create.transaction_b64).toBe(vectors.create_transaction_b64);
    expect(create.distribution).toBe(vectors.distribution);
    expect(create.vault).toBe(vectors.vault);
    expect(create.seed_pubkey).toBe(vectors.seed_pubkey);
    expect(create.authority_token_account).toBe(vectors.authority_token_account);
    expect(create.event_authority).toBe(vectors.event_authority);
    expect(create.data_hex).toBe(vectors.create_data_hex);
    expect(create.distribution_bump).toBe(vectors.distribution_bump);
    const close = JSON.parse(
      giveawayTxMain(
        ["close"],
        JSON.stringify({
          program_id: vectors.program_id,
          mint: vectors.mint,
          token_program: vectors.token_program,
          authority: vectors.authority,
          distribution_seed: vectors.seed_pubkey,
          distributor: vectors.distribution,
          recent_blockhash: vectors.recent_blockhash,
        }),
      ).stdout,
    ) as Record<string, unknown>;
    expect(close.transaction_b64).toBe(vectors.close_transaction_b64);
    expect(close.data_hex).toBe("08");
  });

  it("builds the create data the rewards page's vector shows", () => {
    const root = Buffer.from("47dfbe7fa0962b4e379252f841e5d0bec0bb3632a3478e6754fc2196b8bb7bcb", "hex");
    expect(Buffer.from(createData(253, root, 3_500_042n, 1_798_000_000n)).toString("hex")).toBe(
      "05fd000a6835000000000047dfbe7fa0962b4e379252f841e5d0bec0bb3632a3478e6754fc2196b8bb7bcb0a68350000000000804d2b6b00000000",
    );
  });

  it("makes a fresh throwaway seed every time (injectable here), which has signed the message and leaves the wallet's slot empty", () => {
    const request = JSON.stringify({
      program_id: vectors.program_id,
      mint: vectors.mint,
      token_program: vectors.token_program,
      authority: vectors.authority,
      merkle_root: vectors.merkle_root,
      total_amount: 3_500_042,
      clawback_ts: 1_798_000_000,
      recent_blockhash: vectors.recent_blockhash,
    });
    const a = JSON.parse(giveawayTxMain(["create"], request).stdout) as Record<string, string>;
    const b = JSON.parse(giveawayTxMain(["create"], request).stdout) as Record<string, string>;
    expect(a.seed_pubkey).not.toBe(b.seed_pubkey);
    expect(a.distribution).not.toBe(b.distribution);
    const bytes = Buffer.from(a.transaction_b64!, "base64");
    expect(bytes[0]).toBe(2); // two signers: the giveaway wallet, then the seed
    expect(bytes.subarray(1, 65).every((x) => x === 0)).toBe(true); // the wallet's slot is left for the core
    const decoded = decodeTransaction(bytes);
    const spki = Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(decoded.accountKeys[1]!)]);
    expect(verify(null, decoded.message, createPublicKey({ key: spki, format: "der", type: "spki" }), decoded.signatures[1]!)).toBe(true);
    // a fixed source of randomness gives a fixed seed
    const fixed = (): Uint8Array => new Uint8Array(32).fill(9);
    const x = build("create", request, { randomBytes: fixed });
    const y = build("create", request, { randomBytes: fixed });
    expect(x).toEqual(y);
    expect(x.seed_pubkey).toBe(vectors.seed_pubkey);
  });

  it("treats a null seed_secret_hex as absent, as serde does for an Option", () => {
    const request = JSON.stringify({ program_id: vectors.program_id, mint: vectors.mint, token_program: vectors.token_program, authority: vectors.authority, merkle_root: vectors.merkle_root, total_amount: 5, clawback_ts: 1_798_000_000, recent_blockhash: vectors.recent_blockhash, seed_secret_hex: null });
    expect(giveawayTxMain(["create"], request).code).toBe(0);
  });

  it("raises GiveawayError for the usage case", () => {
    expect(() => build("send", "{}")).toThrow(GiveawayError);
  });
});
