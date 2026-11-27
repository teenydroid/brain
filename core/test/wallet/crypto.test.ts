import { describe, expect, it } from "vitest";
import { chmodSync, writeFileSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createDecipheriv, randomBytes } from "node:crypto";
import { decryptEnc2, encryptEnc2, keyFromHex, loadMasterKey } from "../../src/wallet/crypto.js";

describe("enc2 envelope", () => {
  it("is nonce(12) || ciphertext || tag(16) in lowercase hex, ChaCha20-Poly1305, no associated data", () => {
    const key = randomBytes(32);
    const value = encryptEnc2(key, "hello wallet");
    expect(value).toMatch(/^enc2:[0-9a-f]+$/);
    const blob = Buffer.from(value.slice(5), "hex");
    expect(blob.length).toBe(12 + "hello wallet".length + 16);
    // decrypt by hand, independent of decryptEnc2
    const d = createDecipheriv("chacha20-poly1305", key, blob.subarray(0, 12), { authTagLength: 16 });
    d.setAuthTag(blob.subarray(blob.length - 16));
    expect(Buffer.concat([d.update(blob.subarray(12, blob.length - 16)), d.final()]).toString()).toBe("hello wallet");
    expect(decryptEnc2(key, value).toString()).toBe("hello wallet");
  });

  it("uses a fresh nonce every time and refuses the wrong key or a tampered value", () => {
    const key = randomBytes(32);
    expect(encryptEnc2(key, "x")).not.toBe(encryptEnc2(key, "x"));
    const value = encryptEnc2(key, "secret");
    expect(() => decryptEnc2(randomBytes(32), value)).toThrow(/wrong key or tampered/);
    const flipped = `${value.slice(0, -2)}${value.endsWith("00") ? "01" : "00"}`;
    expect(() => decryptEnc2(key, flipped)).toThrow(/wrong key or tampered/);
    expect(() => decryptEnc2(key, "plain")).toThrow(/enc2/);
  });

  it("reads a master key as 64 hex characters, from a file nobody else can write, and names the variable not the value", () => {
    const dir = mkdtempSync(join(tmpdir(), "key-"));
    const file = join(dir, "k");
    writeFileSync(file, `${"ab".repeat(32)}\n`, { mode: 0o600 });
    expect(loadMasterKey({ OPENHUMAN_KEYRING_MASTER_KEY_FILE: file }).toString("hex")).toBe("ab".repeat(32));
    chmodSync(file, 0o666);
    expect(() => loadMasterKey({ OPENHUMAN_KEYRING_MASTER_KEY_FILE: file })).toThrow(/group or others can write/);
    writeFileSync(file, "nothex", { mode: 0o600 });
    chmodSync(file, 0o600);
    const message = (() => {
      try {
        loadMasterKey({ OPENHUMAN_KEYRING_MASTER_KEY_FILE: file });
        return "";
      } catch (e) {
        return (e as Error).message;
      }
    })();
    expect(message).toMatch(/OPENHUMAN_KEYRING_MASTER_KEY_FILE is not a valid key/);
    expect(message).not.toContain("nothex");
    expect(() => loadMasterKey({})).toThrow(/not set/);
    expect(() => loadMasterKey({ OPENHUMAN_KEYRING_MASTER_KEY: "ab".repeat(32), OPENHUMAN_KEYRING_MASTER_KEY_FILE: file })).toThrow(/only one/);
    expect(keyFromHex("ab".repeat(32))).toHaveLength(32);
  });
});
