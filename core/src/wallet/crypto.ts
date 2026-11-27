// OpenHuman's secret envelope, byte for byte (tinystoragedrivers-secrets
// `crypto`): ChaCha20-Poly1305, a fresh 12-byte nonce, no associated data.
//
//   blob   = nonce(12) || ciphertext || tag(16)
//   enc2   = "enc2:" + lowercase hex(blob)
//
// A value this module writes is readable by OpenHuman's `decrypt_enc2` under
// the same 32-byte key, and the other way round.
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";

export const KEY_LEN = 32;
const NONCE_LEN = 12;
const TAG_LEN = 16;
export const ENC2_PREFIX = "enc2:";

export function encryptBlob(key: Uint8Array, plaintext: Uint8Array): Buffer {
  const nonce = randomBytes(NONCE_LEN);
  const cipher = createCipheriv("chacha20-poly1305", key, nonce, { authTagLength: TAG_LEN });
  const body = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return Buffer.concat([nonce, body, cipher.getAuthTag()]);
}

export function decryptBlob(key: Uint8Array, blob: Uint8Array): Buffer {
  if (blob.length < NONCE_LEN + TAG_LEN) throw new Error("encrypted blob is too short to hold a nonce and tag");
  const buf = Buffer.from(blob);
  const nonce = buf.subarray(0, NONCE_LEN);
  const tag = buf.subarray(buf.length - TAG_LEN);
  const body = buf.subarray(NONCE_LEN, buf.length - TAG_LEN);
  try {
    const decipher = createDecipheriv("chacha20-poly1305", key, nonce, { authTagLength: TAG_LEN });
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(body), decipher.final()]);
  } catch {
    throw new Error("decryption failed: wrong key or tampered data");
  }
}

export function encryptEnc2(key: Uint8Array, plaintext: string): string {
  return ENC2_PREFIX + encryptBlob(key, Buffer.from(plaintext, "utf8")).toString("hex");
}

export function decryptEnc2(key: Uint8Array, value: string): Buffer {
  if (!value.startsWith(ENC2_PREFIX)) throw new Error("value is not an enc2: ciphertext");
  const hex = value.slice(ENC2_PREFIX.length);
  if (hex.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(hex)) throw new Error("malformed enc2 hex");
  return decryptBlob(key, Buffer.from(hex, "hex"));
}

/** A key from 64 hex characters (what OpenHuman's `key_from_hex` takes). */
export function keyFromHex(text: string): Buffer {
  const hex = text.trim();
  if (hex.length !== KEY_LEN * 2) throw new Error(`expected ${KEY_LEN * 2} hex characters, got ${hex.length}`);
  if (!/^[0-9a-fA-F]+$/.test(hex)) throw new Error("value is not valid hex");
  return Buffer.from(hex, "hex");
}

export const MASTER_KEY_ENV = "OPENHUMAN_KEYRING_MASTER_KEY";
export const MASTER_KEY_FILE_ENV = "OPENHUMAN_KEYRING_MASTER_KEY_FILE";

/**
 * The keyring master key the operator supplied, as OpenHuman resolves it:
 * exactly one of the inline variable and the key-file variable, 64 hex
 * characters, a file no group or other may write. Errors name the variable,
 * never the value.
 */
export function loadMasterKey(env: Readonly<Record<string, string | undefined>>): Buffer {
  const inline = (env[MASTER_KEY_ENV] ?? "").trim();
  const file = (env[MASTER_KEY_FILE_ENV] ?? "").trim();
  if (inline && file) throw new Error(`set only one of ${MASTER_KEY_ENV} and ${MASTER_KEY_FILE_ENV}`);
  if (!inline && !file) throw new Error(`the wallet's master key is not set (${MASTER_KEY_FILE_ENV})`);
  if (inline) return decodeKey(MASTER_KEY_ENV, inline);
  if (!existsSync(file)) throw new Error(`${MASTER_KEY_FILE_ENV} names a file that does not exist`);
  const mode = statSync(file).mode & 0o777;
  if ((mode & 0o022) !== 0) throw new Error(`${MASTER_KEY_FILE_ENV} names a file that group or others can write`);
  return decodeKey(MASTER_KEY_FILE_ENV, readFileSync(file, "utf8"));
}

function decodeKey(name: string, text: string): Buffer {
  try {
    return keyFromHex(text);
  } catch (error) {
    throw new Error(`${name} is not a valid key (${(error as Error).message})`);
  }
}
