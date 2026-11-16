//! `teeny-spine giveaway-tx create|close`: the unsigned transactions of a Tiny
//! Drops round, built here because building one needs what Python's standard
//! library does not have (an ed25519 key and signature for the seed signer,
//! and the on-curve test a program address depends on).
//!
//! It reads one JSON object on stdin and writes one on stdout. It holds no
//! key of Teeny's, reads nothing from the network and signs nothing that can
//! move a token: the giveaway wallet (Solana index 1, `m/44'/501'/1'/0'`)
//! signs inside the core, after `wallet.prepare_program` has checked the
//! transaction against `[wallet_policy.accounts.solana_1]`
//! (hands/lib/teeny_hands/giveaway/onchain.py drives it).
//!
//! The program is the Solana Foundation Rewards program; every fact below is
//! the one web/src/lib/distributor.ts encodes (docs/drops-schema.md, "The
//! program"), and the test vectors in
//! hands/tests/giveaway/fixtures/rewards_tx.json were made by
//! `@solana/web3.js` the same way, so the two cannot drift apart unseen.
//!
//!   distribution  PDA ["merkle_distribution", mint, authority, seed]
//!   event auth.   PDA ["event_authority"]
//!   vault         ATA(distribution, mint, token program)
//!
//! `create` (CreateMerkleDistribution, discriminator 5): data
//! `5 | bump | revocable 0 | u64 amount | root[32] | u64 total_amount | i64 clawback_ts`,
//! with amount = total_amount (the program moves exactly that from the
//! authority's token account into the vault, so the vault is the round's hard
//! cap). `seed` is a fresh throwaway keypair made here, which signs the
//! message at once and is then forgotten: its only job is to make the
//! distribution's address unique. The fee payer and authority are the
//! giveaway wallet; its signature slot is left empty for the core.
//!
//!   in:  {program_id, mint, token_program, authority, merkle_root (hex),
//!         total_amount, clawback_ts, recent_blockhash}
//!   out: {transaction_b64, distribution, distribution_bump, vault, seed_pubkey,
//!         authority_token_account, event_authority, data_hex}
//!
//! `close` (CloseMerkleDistribution, discriminator 8, data `[8]`): what is left
//! in the vault goes back to the authority's token account. Refused unless
//! `distributor` is the address `distribution_seed` derives.
//!
//!   in:  {program_id, mint, token_program, authority, distribution_seed,
//!         distributor, recent_blockhash}
//!   out: {transaction_b64, distribution, vault, authority_token_account,
//!         event_authority, data_hex}
//!
//! Both messages are legacy, compiled the way `@solana/web3.js`
//! `TransactionMessage.compileToLegacyMessage` does: the fee payer first,
//! then every key in first-use order (each instruction's program before its
//! accounts), grouped writable signers, read-only signers, writable
//! non-signers, read-only non-signers.

use std::io::{Read, Write};

use base64::Engine as _;
use curve25519_dalek::edwards::CompressedEdwardsY;
use ed25519_dalek::{Signer, SigningKey};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// The subcommand `main` hands over to [`run`].
pub const COMMAND: &str = "giveaway-tx";

type Key = [u8; 32];

const SYSTEM_PROGRAM: Key = [0u8; 32];
const ATA_PROGRAM: &str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
const IX_CREATE_MERKLE_DISTRIBUTION: u8 = 5;
const IX_CLOSE_MERKLE_DISTRIBUTION: u8 = 8;
const PDA_MARKER: &[u8] = b"ProgramDerivedAddress";

fn key(what: &str, text: &str) -> Result<Key, String> {
    let bytes = bs58::decode(text)
        .into_vec()
        .map_err(|_| format!("{what} is not base58"))?;
    bytes
        .try_into()
        .map_err(|_| format!("{what} is not a 32-byte address"))
}

fn b58(key: &Key) -> String {
    bs58::encode(key).into_string()
}

/// `PublicKey.findProgramAddressSync`: the first bump from 255 down whose
/// hash is not a point on ed25519.
fn find_program_address(seeds: &[&[u8]], program: &Key) -> Result<(Key, u8), String> {
    for bump in (0..=u8::MAX).rev() {
        let mut hasher = Sha256::new();
        for seed in seeds {
            hasher.update(seed);
        }
        hasher.update([bump]);
        hasher.update(program);
        hasher.update(PDA_MARKER);
        let candidate: Key = hasher.finalize().into();
        if CompressedEdwardsY(candidate).decompress().is_none() {
            return Ok((candidate, bump));
        }
    }
    Err("no program address for these seeds".to_string())
}

fn associated_token_address(owner: &Key, mint: &Key, token_program: &Key) -> Result<Key, String> {
    let ata = key("the ATA program", ATA_PROGRAM)?;
    Ok(find_program_address(&[owner, token_program, mint], &ata)?.0)
}

fn distribution_address(program: &Key, mint: &Key, authority: &Key, seed: &Key) -> Result<(Key, u8), String> {
    find_program_address(&[b"merkle_distribution", mint, authority, seed], program)
}

fn event_authority(program: &Key) -> Result<Key, String> {
    Ok(find_program_address(&[b"event_authority"], program)?.0)
}

#[derive(Clone, Copy)]
struct Meta {
    key: Key,
    signer: bool,
    writable: bool,
}

const fn meta(key: Key, signer: bool, writable: bool) -> Meta {
    Meta { key, signer, writable }
}

struct Instruction {
    program: Key,
    accounts: Vec<Meta>,
    data: Vec<u8>,
}

fn shortvec(out: &mut Vec<u8>, mut value: usize) {
    loop {
        let byte = (value & 0x7f) as u8;
        value >>= 7;
        if value == 0 {
            out.push(byte);
            return;
        }
        out.push(byte | 0x80);
    }
}

/// A compiled legacy message: its bytes and its signer keys, in slot order.
struct Message {
    bytes: Vec<u8>,
    signers: Vec<Key>,
}

fn compile(payer: &Key, instructions: &[Instruction], blockhash: &Key) -> Result<Message, String> {
    // (key, signer, writable) in first-use order, as CompiledKeys keeps them.
    let mut keys: Vec<Meta> = vec![meta(*payer, true, true)];
    let mut upsert = |m: Meta| match keys.iter_mut().find(|k| k.key == m.key) {
        Some(existing) => {
            existing.signer |= m.signer;
            existing.writable |= m.writable;
        }
        None => keys.push(m),
    };
    for ix in instructions {
        upsert(meta(ix.program, false, false));
        for account in &ix.accounts {
            upsert(*account);
        }
    }
    let group = |signer: bool, writable: bool| keys.iter().filter(move |k| k.signer == signer && k.writable == writable);
    let ordered: Vec<Meta> = group(true, true)
        .chain(group(true, false))
        .chain(group(false, true))
        .chain(group(false, false))
        .copied()
        .collect();
    if ordered.len() > 256 {
        return Err("too many accounts for one transaction".to_string());
    }
    let index = |k: &Key| ordered.iter().position(|m| &m.key == k).map(|i| i as u8);
    let signers: Vec<Key> = ordered.iter().filter(|m| m.signer).map(|m| m.key).collect();
    let readonly_signed = ordered.iter().filter(|m| m.signer && !m.writable).count();
    let readonly_unsigned = ordered.iter().filter(|m| !m.signer && !m.writable).count();

    let mut bytes = vec![signers.len() as u8, readonly_signed as u8, readonly_unsigned as u8];
    shortvec(&mut bytes, ordered.len());
    for m in &ordered {
        bytes.extend_from_slice(&m.key);
    }
    bytes.extend_from_slice(blockhash);
    shortvec(&mut bytes, instructions.len());
    for ix in instructions {
        bytes.push(index(&ix.program).ok_or("program missing from the keys")?);
        shortvec(&mut bytes, ix.accounts.len());
        for account in &ix.accounts {
            bytes.push(index(&account.key).ok_or("account missing from the keys")?);
        }
        shortvec(&mut bytes, ix.data.len());
        bytes.extend_from_slice(&ix.data);
    }
    Ok(Message { bytes, signers })
}

/// The wire bytes: every signer's slot, filled for the keys given, zeros
/// for the rest (the giveaway wallet's, which the core fills).
fn wire(message: &Message, signing: &[&SigningKey]) -> Vec<u8> {
    let mut out = Vec::with_capacity(1 + 64 * message.signers.len() + message.bytes.len());
    shortvec(&mut out, message.signers.len());
    for signer in &message.signers {
        match signing.iter().find(|k| &k.verifying_key().to_bytes() == signer) {
            Some(k) => out.extend_from_slice(&k.sign(&message.bytes).to_bytes()),
            None => out.extend_from_slice(&[0u8; 64]),
        }
    }
    out.extend_from_slice(&message.bytes);
    out
}

/// CreateMerkleDistribution's data, byte for byte as distributor.ts's
/// `createMerkleDistributionData` (revocable always 0, amount = total).
fn create_data(bump: u8, root: &Key, total_amount: u64, clawback_ts: i64) -> Vec<u8> {
    let mut data = vec![IX_CREATE_MERKLE_DISTRIBUTION, bump, 0];
    data.extend_from_slice(&total_amount.to_le_bytes());
    data.extend_from_slice(root);
    data.extend_from_slice(&total_amount.to_le_bytes());
    data.extend_from_slice(&clawback_ts.to_le_bytes());
    data
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CreateRequest {
    program_id: String,
    mint: String,
    token_program: String,
    authority: String,
    merkle_root: String,
    total_amount: u64,
    clawback_ts: i64,
    recent_blockhash: String,
    /// Tests only: the seed keypair's secret, for a reproducible vector.
    #[serde(default)]
    seed_secret_hex: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CloseRequest {
    program_id: String,
    mint: String,
    token_program: String,
    authority: String,
    distribution_seed: String,
    distributor: String,
    recent_blockhash: String,
}

fn fresh_seed() -> Result<SigningKey, String> {
    let mut secret = [0u8; 32];
    getrandom::getrandom(&mut secret).map_err(|e| format!("no randomness for the seed keypair: {e}"))?;
    Ok(SigningKey::from_bytes(&secret))
}

fn create(request: CreateRequest) -> Result<Value, String> {
    let program = key("program_id", &request.program_id)?;
    let mint = key("mint", &request.mint)?;
    let token_program = key("token_program", &request.token_program)?;
    let authority = key("authority", &request.authority)?;
    let blockhash = key("recent_blockhash", &request.recent_blockhash)?;
    let root: Key = hex::decode(&request.merkle_root)
        .ok()
        .and_then(|b| b.try_into().ok())
        .ok_or("merkle_root is not 32 bytes of hex")?;
    if request.total_amount == 0 {
        return Err("total_amount is above zero".to_string());
    }
    if request.clawback_ts <= 0 {
        return Err("clawback_ts is a unix time".to_string());
    }
    let seed_key = match &request.seed_secret_hex {
        Some(text) => {
            let secret: Key = hex::decode(text)
                .ok()
                .and_then(|b| b.try_into().ok())
                .ok_or("seed_secret_hex is not 32 bytes of hex")?;
            SigningKey::from_bytes(&secret)
        }
        None => fresh_seed()?,
    };
    let seed = seed_key.verifying_key().to_bytes();
    let (distribution, bump) = distribution_address(&program, &mint, &authority, &seed)?;
    let vault = associated_token_address(&distribution, &mint, &token_program)?;
    let authority_tokens = associated_token_address(&authority, &mint, &token_program)?;
    let events = event_authority(&program)?;
    let data = create_data(bump, &root, request.total_amount, request.clawback_ts);
    let ix = Instruction {
        program,
        accounts: vec![
            meta(authority, true, true), // payer
            meta(authority, true, false), // authority
            meta(seed, true, false),
            meta(distribution, false, true),
            meta(mint, false, false),
            meta(vault, false, true),
            meta(authority_tokens, false, true),
            meta(SYSTEM_PROGRAM, false, false),
            meta(token_program, false, false),
            meta(key("the ATA program", ATA_PROGRAM)?, false, false),
            meta(events, false, false),
            meta(program, false, false),
        ],
        data: data.clone(),
    };
    let message = compile(&authority, &[ix], &blockhash)?;
    let bytes = wire(&message, &[&seed_key]);
    Ok(json!({
        "transaction_b64": base64::engine::general_purpose::STANDARD.encode(bytes),
        "distribution": b58(&distribution),
        "distribution_bump": bump,
        "vault": b58(&vault),
        "seed_pubkey": b58(&seed),
        "authority_token_account": b58(&authority_tokens),
        "event_authority": b58(&events),
        "data_hex": hex::encode(data),
    }))
}

fn close(request: CloseRequest) -> Result<Value, String> {
    let program = key("program_id", &request.program_id)?;
    let mint = key("mint", &request.mint)?;
    let token_program = key("token_program", &request.token_program)?;
    let authority = key("authority", &request.authority)?;
    let seed = key("distribution_seed", &request.distribution_seed)?;
    let blockhash = key("recent_blockhash", &request.recent_blockhash)?;
    let (distribution, _) = distribution_address(&program, &mint, &authority, &seed)?;
    if b58(&distribution) != request.distributor {
        return Err("distributor is not the address distribution_seed derives for this mint and authority".to_string());
    }
    let vault = associated_token_address(&distribution, &mint, &token_program)?;
    let authority_tokens = associated_token_address(&authority, &mint, &token_program)?;
    let events = event_authority(&program)?;
    let data = vec![IX_CLOSE_MERKLE_DISTRIBUTION];
    let ix = Instruction {
        program,
        accounts: vec![
            meta(authority, true, true),
            meta(distribution, false, true),
            meta(mint, false, false),
            meta(vault, false, true),
            meta(authority_tokens, false, true),
            meta(token_program, false, false),
            meta(events, false, false),
            meta(program, false, false),
        ],
        data: data.clone(),
    };
    let message = compile(&authority, &[ix], &blockhash)?;
    let bytes = wire(&message, &[]);
    Ok(json!({
        "transaction_b64": base64::engine::general_purpose::STANDARD.encode(bytes),
        "distribution": b58(&distribution),
        "vault": b58(&vault),
        "authority_token_account": b58(&authority_tokens),
        "event_authority": b58(&events),
        "data_hex": hex::encode(data),
    }))
}

fn build(op: &str, input: &str) -> Result<Value, String> {
    match op {
        "create" => create(serde_json::from_str(input).map_err(|e| format!("bad create request: {e}"))?),
        "close" => close(serde_json::from_str(input).map_err(|e| format!("bad close request: {e}"))?),
        _ => Err(format!("usage: teeny-spine {COMMAND} create|close < request.json")),
    }
}

/// The subcommand: one request on stdin, the answer on stdout. Exit 0, or 2
/// with `{"error": ...}` on stderr.
pub fn run(args: &[String]) -> i32 {
    let op = args.first().map(String::as_str).unwrap_or("");
    let mut input = String::new();
    let result = std::io::stdin()
        .read_to_string(&mut input)
        .map_err(|e| format!("could not read the request: {e}"))
        .and_then(|_| build(op, &input));
    match result {
        Ok(value) => {
            let _ = writeln!(std::io::stdout(), "{value}");
            0
        }
        Err(error) => {
            let _ = writeln!(std::io::stderr(), "{}", json!({ "error": error }));
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};

    /// Made by @solana/web3.js the way web/src/lib/distributor.ts encodes the
    /// program (PR #302); hands/tests/giveaway/test_chain.py reads it too.
    const VECTORS: &str = include_str!("../../hands/tests/giveaway/fixtures/rewards_tx.json");

    fn vectors() -> Value {
        serde_json::from_str(VECTORS).unwrap()
    }

    fn s(v: &Value, k: &str) -> String {
        v[k].as_str().unwrap().to_string()
    }

    fn create_request(v: &Value, seed: Option<String>) -> CreateRequest {
        CreateRequest {
            program_id: s(v, "program_id"),
            mint: s(v, "mint"),
            token_program: s(v, "token_program"),
            authority: s(v, "authority"),
            merkle_root: s(v, "merkle_root"),
            total_amount: s(v, "total_amount").parse().unwrap(),
            clawback_ts: v["clawback_ts"].as_i64().unwrap(),
            recent_blockhash: s(v, "recent_blockhash"),
            seed_secret_hex: seed,
        }
    }

    fn close_request(v: &Value) -> CloseRequest {
        CloseRequest {
            program_id: s(v, "program_id"),
            mint: s(v, "mint"),
            token_program: s(v, "token_program"),
            authority: s(v, "authority"),
            distribution_seed: s(v, "seed_pubkey"),
            distributor: s(v, "distribution"),
            recent_blockhash: s(v, "recent_blockhash"),
        }
    }

    #[test]
    fn create_matches_the_web3_js_transaction_byte_for_byte() {
        let v = vectors();
        let out = create(create_request(&v, Some(s(&v, "seed_secret_hex")))).unwrap();
        assert_eq!(out["transaction_b64"], v["create_transaction_b64"]);
        for (ours, theirs) in [
            ("distribution", "distribution"),
            ("vault", "vault"),
            ("seed_pubkey", "seed_pubkey"),
            ("authority_token_account", "authority_token_account"),
            ("event_authority", "event_authority"),
            ("data_hex", "create_data_hex"),
        ] {
            assert_eq!(out[ours], v[theirs], "{ours}");
        }
        assert_eq!(out["distribution_bump"], v["distribution_bump"]);
    }

    #[test]
    fn close_matches_the_web3_js_transaction_byte_for_byte() {
        let v = vectors();
        let out = close(close_request(&v)).unwrap();
        assert_eq!(out["transaction_b64"], v["close_transaction_b64"]);
        assert_eq!(out["vault"], v["vault"]);
        assert_eq!(out["data_hex"], "08");
    }

    #[test]
    fn create_data_matches_the_rewards_page_vector() {
        // distributor.test.ts: [5][bump 253][revocable 0][amount 3500042][root][total 3500042][clawback 1798000000].
        let root: Key = hex::decode("47dfbe7fa0962b4e379252f841e5d0bec0bb3632a3478e6754fc2196b8bb7bcb")
            .unwrap()
            .try_into()
            .unwrap();
        assert_eq!(
            hex::encode(create_data(253, &root, 3_500_042, 1_798_000_000)),
            "05fd000a6835000000000047dfbe7fa0962b4e379252f841e5d0bec0bb3632a3478e6754fc2196b8bb7bcb0a68350000000000804d2b6b00000000"
        );
    }

    #[test]
    fn a_fresh_seed_is_new_every_time_and_has_signed_the_message() {
        let v = vectors();
        let a = create(create_request(&v, None)).unwrap();
        let b = create(create_request(&v, None)).unwrap();
        assert_ne!(a["seed_pubkey"], b["seed_pubkey"]);
        assert_ne!(a["distribution"], b["distribution"]);
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(a["transaction_b64"].as_str().unwrap())
            .unwrap();
        assert_eq!(bytes[0], 2, "two signers: the giveaway wallet, then the seed");
        assert!(bytes[1..65].iter().all(|b| *b == 0), "the wallet's slot is left for the core");
        let message = &bytes[1 + 128..];
        let seed = VerifyingKey::from_bytes(&key("seed", a["seed_pubkey"].as_str().unwrap()).unwrap()).unwrap();
        let signature = Signature::from_bytes(&bytes[65..129].try_into().unwrap());
        seed.verify(message, &signature).unwrap();
    }

    #[test]
    fn close_refuses_a_distributor_the_seed_does_not_derive() {
        let v = vectors();
        let mut request = close_request(&v);
        request.distributor = s(&v, "vault");
        assert!(close(request).unwrap_err().contains("distribution_seed derives"));
    }

    #[test]
    fn bad_requests_are_refused() {
        let v = vectors();
        let mut zero = create_request(&v, None);
        zero.total_amount = 0;
        assert!(create(zero).is_err());
        let mut root = create_request(&v, None);
        root.merkle_root = "abcd".into();
        assert!(create(root).is_err());
        assert!(build("create", r#"{"program_id": "x", "extra": 1}"#).is_err());
        assert!(build("send", "{}").unwrap_err().contains("usage"));
    }
}
