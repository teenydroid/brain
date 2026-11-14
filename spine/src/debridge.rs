//! Teeny's deBridge backend: the wallet module's swap and bridge quotes,
//! straight from deBridge's public API. LI.FI is the default provider
//! (`lifi.rs`); `wallet_backend.rs` routes each call to one or the other.
//!
//! The wallet module (tinywallet) builds every swap and bridge from one
//! unsigned transaction it asks its host for (`WalletBackend`: the routes, a
//! swap transaction, a bridge transaction). Upstream answers that through the
//! tinyhumans backend proxy, which needs a signed-in OpenHuman session. Teeny
//! has none, so without this every `web3_swap_quote` and `web3_bridge_quote`
//! failed with "web3 requires a signed-in session".
//!
//! This handler answers the same three calls from deBridge itself, and returns
//! deBridge's JSON unchanged (the module reads only `tx`; hands/wallet reads
//! the estimation to check the quote before it executes anything):
//!
//! | Call                | deBridge                                 |
//! |---------------------|------------------------------------------|
//! | `Routes`            | `GET /v1.0/supported-chains-info`        |
//! | `SwapTransaction`   | `GET /v1.0/chain/transaction` (one chain) |
//! | `BridgeTransaction` | `GET /v1.0/dln/order/create-tx` (DLN)    |
//!
//! Only the query fields named below are forwarded, so nothing the module adds
//! later reaches deBridge unseen. `TEENY_DEBRIDGE_API` overrides the base URL
//! (a test double); `DEBRIDGE_API_KEY`, when set, is sent as deBridge's
//! `accesstoken` for the higher rate limit. Nothing here signs or moves funds:
//! the module signs only after `web3_*_execute` with `confirmed: true`, and
//! hands/wallet decides whether that call is ever made. No log line carries a
//! wallet address or the key.

use std::time::Duration;

use openhuman_embed::__host::wallet_backend::WalletBackendCall;
use serde_json::Value;

const DEFAULT_BASE: &str = "https://dln.debridge.finance";
const TIMEOUT: Duration = Duration::from_secs(30);
const ERROR_CHARS: usize = 300;

const SWAP_FIELDS: &[&str] = &[
    "chainId",
    "tokenIn",
    "tokenInAmount",
    "tokenOut",
    "tokenOutRecipient",
    "senderAddress",
    "slippage",
];

const BRIDGE_FIELDS: &[&str] = &[
    "srcChainId",
    "srcChainTokenIn",
    "srcChainTokenInAmount",
    "dstChainId",
    "dstChainTokenOut",
    "dstChainTokenOutAmount",
    "dstChainTokenOutRecipient",
    "srcChainOrderAuthorityAddress",
    "dstChainOrderAuthorityAddress",
];

/// One GET to deBridge: the path and its query.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub path: &'static str,
    pub query: Vec<(String, String)>,
}

/// The deBridge request for one module call, or why it cannot be made.
pub fn request_for(call: &WalletBackendCall) -> Result<Request, String> {
    match call {
        WalletBackendCall::Routes => Ok(Request {
            path: "/v1.0/supported-chains-info",
            query: Vec::new(),
        }),
        WalletBackendCall::SwapTransaction(body) => Ok(Request {
            path: "/v1.0/chain/transaction",
            query: query(body, SWAP_FIELDS, &["chainId", "tokenIn", "tokenInAmount", "tokenOut", "tokenOutRecipient"])?,
        }),
        WalletBackendCall::BridgeTransaction(body) => Ok(Request {
            path: "/v1.0/dln/order/create-tx",
            query: query(
                body,
                BRIDGE_FIELDS,
                &["srcChainId", "srcChainTokenIn", "srcChainTokenInAmount", "dstChainId", "dstChainTokenOut", "dstChainTokenOutRecipient"],
            )?,
        }),
    }
}

/// The named fields of `body` as query pairs, in the listed order. A string
/// or a whole number is forwarded as written; anything else is refused.
fn query(body: &Value, fields: &[&str], required: &[&str]) -> Result<Vec<(String, String)>, String> {
    let object = body
        .as_object()
        .ok_or_else(|| "deBridge request body is not an object".to_string())?;
    let mut out = Vec::new();
    for &field in fields {
        let text = match object.get(field) {
            None | Some(Value::Null) => continue,
            Some(Value::String(text)) => text.trim().to_string(),
            Some(Value::Number(number)) if number.is_u64() || number.is_i64() => number.to_string(),
            Some(_) => return Err(format!("deBridge field {field} is not a string or a whole number")),
        };
        if text.is_empty() {
            continue;
        }
        out.push((field.to_string(), text));
    }
    for &field in required {
        if !out.iter().any(|(name, _)| name == field) {
            return Err(format!("deBridge field {field} is missing"));
        }
    }
    Ok(out)
}

/// deBridge's own message from an error body (`errorMessage`, else
/// `message`), cut short. Never the request, which carries addresses.
pub fn error_message(status: u16, body: &str) -> String {
    let detail = serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|value| {
            value
                .get("errorMessage")
                .or_else(|| value.get("message"))
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_default();
    let detail: String = detail.chars().take(ERROR_CHARS).collect();
    if detail.is_empty() {
        format!("deBridge answered HTTP {status}")
    } else {
        format!("deBridge answered HTTP {status}: {detail}")
    }
}

fn base_url() -> String {
    std::env::var("TEENY_DEBRIDGE_API")
        .ok()
        .map(|value| value.trim().trim_end_matches('/').to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| DEFAULT_BASE.to_string())
}

async fn fetch(request: Request) -> Result<Value, String> {
    let mut query = request.query;
    if let Some(key) = std::env::var("DEBRIDGE_API_KEY").ok().filter(|key| !key.trim().is_empty()) {
        query.push(("accesstoken".to_string(), key.trim().to_string()));
    }
    let client = reqwest::Client::builder()
        .timeout(TIMEOUT)
        .redirect(reqwest::redirect::Policy::none())
        .user_agent("teeny-spine")
        .build()
        .map_err(|_| "could not build the deBridge client".to_string())?;
    let url = format!("{}{}", base_url(), request.path);
    log::debug!("[teeny-debridge] GET {}", request.path);
    let response = client
        .get(url)
        .query(&query)
        .send()
        .await
        .map_err(|error| format!("could not reach deBridge ({})", if error.is_timeout() { "timeout" } else { "connect" }))?;
    let status = response.status().as_u16();
    let text = response
        .text()
        .await
        .map_err(|_| "deBridge sent a body that could not be read".to_string())?;
    if !(200..300).contains(&status) {
        return Err(error_message(status, &text));
    }
    serde_json::from_str(&text).map_err(|_| "deBridge sent something that is not JSON".to_string())
}

/// One module call, answered from deBridge.
pub async fn answer(call: WalletBackendCall) -> Result<Value, String> {
    fetch(request_for(&call)?).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn pairs(request: &Request) -> Vec<(&str, &str)> {
        request.query.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect()
    }

    #[test]
    fn routes_reads_the_supported_chains() {
        let request = request_for(&WalletBackendCall::Routes).unwrap();
        assert_eq!(request.path, "/v1.0/supported-chains-info");
        assert!(request.query.is_empty());
    }

    #[test]
    fn swap_forwards_only_the_known_fields() {
        let body = json!({
            "chainId": 8453,
            "tokenIn": "0x0000000000000000000000000000000000000000",
            "tokenInAmount": "1000",
            "tokenOut": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "tokenOutRecipient": "0xabc",
            "senderAddress": "0xabc",
            "slippage": "auto",
            "affiliateFeeRecipient": "0xevil",
        });
        let request = request_for(&WalletBackendCall::SwapTransaction(body)).unwrap();
        assert_eq!(request.path, "/v1.0/chain/transaction");
        let got = pairs(&request);
        assert_eq!(got[0], ("chainId", "8453"));
        assert!(got.iter().all(|(k, _)| *k != "affiliateFeeRecipient"));
        assert_eq!(got.len(), 7);
    }

    #[test]
    fn bridge_goes_to_dln_create_tx() {
        let body = json!({
            "srcChainId": 8453,
            "srcChainTokenIn": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "srcChainTokenInAmount": "5000000",
            "dstChainId": 7565164,
            "dstChainTokenOut": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "dstChainTokenOutAmount": "auto",
            "dstChainTokenOutRecipient": "So1ana",
            "srcChainOrderAuthorityAddress": "0xabc",
            "dstChainOrderAuthorityAddress": "So1ana",
        });
        let request = request_for(&WalletBackendCall::BridgeTransaction(body)).unwrap();
        assert_eq!(request.path, "/v1.0/dln/order/create-tx");
        assert_eq!(pairs(&request)[3], ("dstChainId", "7565164"));
    }

    #[test]
    fn a_missing_recipient_is_refused_not_defaulted() {
        let body = json!({"chainId": 1, "tokenIn": "a", "tokenInAmount": "1", "tokenOut": "b"});
        let error = request_for(&WalletBackendCall::SwapTransaction(body)).unwrap_err();
        assert!(error.contains("tokenOutRecipient"), "{error}");
    }

    #[test]
    fn a_non_scalar_field_is_refused() {
        let body = json!({"chainId": {"x": 1}, "tokenIn": "a", "tokenInAmount": "1", "tokenOut": "b", "tokenOutRecipient": "c"});
        assert!(request_for(&WalletBackendCall::SwapTransaction(body)).is_err());
    }

    #[test]
    fn errors_carry_debridges_message_not_the_request() {
        assert_eq!(
            error_message(400, r#"{"errorCode":8,"errorMessage":"insufficient liquidity"}"#),
            "deBridge answered HTTP 400: insufficient liquidity"
        );
        assert_eq!(error_message(502, "<html>"), "deBridge answered HTTP 502");
    }
}
