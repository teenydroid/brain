//! LI.FI, the default provider for the wallet module's swap and bridge
//! quotes (docs/hands.md, "`swap` and `bridge`").
//!
//! The wallet module asks its host for one unsigned transaction per swap or
//! bridge, in deBridge's terms: deBridge's field names, and deBridge's chain
//! id for Solana (7565164), which is how the module picks its signer. This
//! file turns such a body into one `GET /v1/quote` and LI.FI's answer back
//! into the envelope the module already signs from, so neither the core nor
//! the module changes:
//!
//! | Module body (after the router)               | LI.FI query                 |
//! |-----------------------------------------------|-----------------------------|
//! | `chainId` / `srcChainId`, `dstChainId`         | `fromChain`, `toChain`      |
//! | `tokenIn` / `srcChainTokenIn`                  | `fromToken`                 |
//! | `tokenOut` / `dstChainTokenOut`                | `toToken`                   |
//! | `tokenInAmount` / `srcChainTokenInAmount`      | `fromAmount`                |
//! | `senderAddress` / `srcChainOrderAuthorityAddress` | `fromAddress`            |
//! | `tokenOutRecipient` / `dstChainTokenOutRecipient` | `toAddress`              |
//! | `slippage` (percent, or `auto`)                | `slippage` (a fraction)     |
//!
//! Every query also asks for `order=CHEAPEST` and `denyBridges=near` (below,
//! in `request_for`). A field the module does not
//! send today is refused, not dropped, so nothing it adds later reaches LI.FI
//! unseen. EVM addresses go lowercased: LI.FI checks a mixed-case address's
//! EIP-55 checksum and refuses a wrong one, and lowercase carries none.
//!
//! The answer is LI.FI's JSON with `transactionRequest` replaced by `tx`, the
//! one transaction the module signs, and `"provider": "lifi"`:
//!
//! - EVM: `{to, data, value}`, with `value` in decimal wei (LI.FI sends hex,
//!   the module parses decimal). The module estimates gas itself, so LI.FI's
//!   `gasLimit` and `gasPrice` are not passed on.
//! - Solana: `{data}`, LI.FI's base64 transaction as `0x` hex, which is what
//!   the module decodes. It refreshes the blockhash before it signs.
//!
//! hands/wallet (convert.py) checks all of it before anything is signed.
//! `TEENY_LIFI_API` overrides the base URL (a test double); `LIFI_API_KEY`,
//! when set, goes as the `x-lifi-api-key` header, which only raises LI.FI's
//! rate limit. No log line carries a wallet address or the key.

use std::time::Duration;

use base64::Engine as _;
use openhuman_embed::__host::wallet_backend::WalletBackendCall;
use serde_json::{Map, Value};

/// LI.FI's chain id for Solana.
pub const LIFI_SOLANA: u64 = 1_151_111_081_099_710;
/// The id the wallet module (after deBridge) gives Solana.
pub const MODULE_SOLANA: u64 = 7_565_164;

const DEFAULT_BASE: &str = "https://li.quest";
const TIMEOUT: Duration = Duration::from_secs(30);
const ERROR_CHARS: usize = 300;

/// One module field and the LI.FI query field it becomes (`None`: known,
/// read for its check, never forwarded).
type Field = (&'static str, Option<&'static str>);

const SWAP: &[Field] = &[
    ("chainId", Some("fromChain")),
    ("tokenIn", Some("fromToken")),
    ("tokenOut", Some("toToken")),
    ("tokenInAmount", Some("fromAmount")),
    ("senderAddress", Some("fromAddress")),
    ("tokenOutRecipient", Some("toAddress")),
    ("slippage", None),
];

const BRIDGE: &[Field] = &[
    ("srcChainId", Some("fromChain")),
    ("dstChainId", Some("toChain")),
    ("srcChainTokenIn", Some("fromToken")),
    ("dstChainTokenOut", Some("toToken")),
    ("srcChainTokenInAmount", Some("fromAmount")),
    ("srcChainOrderAuthorityAddress", Some("fromAddress")),
    ("dstChainTokenOutRecipient", Some("toAddress")),
    ("dstChainTokenOutAmount", None),
    ("dstChainOrderAuthorityAddress", None),
];

/// The order the query is sent in.
const QUERY_ORDER: &[&str] = &[
    "fromChain",
    "toChain",
    "fromToken",
    "toToken",
    "fromAmount",
    "fromAddress",
    "toAddress",
];

/// One GET to LI.FI: the path and its query.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub path: &'static str,
    pub query: Vec<(String, String)>,
}

/// The LI.FI request for one module call, or why it cannot be made.
pub fn request_for(call: &WalletBackendCall) -> Result<Request, String> {
    let (body, fields, swap) = match call {
        WalletBackendCall::SwapTransaction(body) => (body, SWAP, true),
        WalletBackendCall::BridgeTransaction(body) => (body, BRIDGE, false),
        WalletBackendCall::Routes => {
            return Err("LI.FI answers swap and bridge quotes only".to_string())
        }
    };
    let object = body
        .as_object()
        .ok_or_else(|| "LI.FI request body is not an object".to_string())?;
    if let Some(unknown) = object
        .keys()
        .find(|key| !fields.iter().any(|(name, _)| name == key))
    {
        return Err(format!("LI.FI does not take the field {unknown}"));
    }
    let mut named = Map::new();
    for &(field, query) in fields {
        let Some(text) = scalar(object, field)? else {
            continue;
        };
        if let Some(query) = query {
            named.insert(query.to_string(), Value::String(text));
        }
    }
    if swap {
        let chain = named.get("fromChain").cloned();
        if let Some(chain) = chain {
            named.insert("toChain".to_string(), chain);
        }
    } else if let Some(amount) = scalar(object, "dstChainTokenOutAmount")? {
        if amount != "auto" {
            return Err("LI.FI quotes from the input; dstChainTokenOutAmount is auto".to_string());
        }
    }
    let mut query = Vec::new();
    for &name in QUERY_ORDER {
        let text = named
            .get(name)
            .and_then(Value::as_str)
            .ok_or_else(|| format!("LI.FI field {} is missing", module_name(fields, name)))?;
        let text = match name {
            "fromChain" | "toChain" => chain_id(text)?,
            _ => lower_if_evm(text),
        };
        query.push((name.to_string(), text));
    }
    if swap {
        if let Some(slippage) = scalar(object, "slippage")? {
            if slippage != "auto" {
                query.push(("slippage".to_string(), fraction(&slippage)?));
            }
        }
    }
    query.push(("order".to_string(), "CHEAPEST".to_string()));
    // NEAR Intents sends the input to a deposit address and keeps the
    // recipient off-chain, so nothing in the signed transaction says where
    // the money lands; hands/wallet refuses such a quote. Not asking for it
    // lets LI.FI offer a route the hand can check instead.
    query.push(("denyBridges".to_string(), "near".to_string()));
    Ok(Request {
        path: "/v1/quote",
        query,
    })
}

/// The module's name for a LI.FI query field, for an error.
fn module_name(fields: &[Field], query: &str) -> &'static str {
    fields
        .iter()
        .find(|(_, q)| *q == Some(query))
        .map_or("chainId", |(name, _)| name)
}

/// A string or a whole number, trimmed; `None` when absent, null or empty.
fn scalar(object: &Map<String, Value>, field: &str) -> Result<Option<String>, String> {
    let text = match object.get(field) {
        None | Some(Value::Null) => return Ok(None),
        Some(Value::String(text)) => text.trim().to_string(),
        Some(Value::Number(number)) if number.is_u64() => number.to_string(),
        Some(_) => {
            return Err(format!(
                "LI.FI field {field} is not a string or a whole number"
            ))
        }
    };
    Ok(if text.is_empty() { None } else { Some(text) })
}

/// The module's chain id as LI.FI's: Solana's differs, EVM ids are the same.
fn chain_id(text: &str) -> Result<String, String> {
    let id: u64 = text
        .parse()
        .map_err(|_| format!("chain id {text} is not a number"))?;
    Ok(if id == MODULE_SOLANA { LIFI_SOLANA } else { id }.to_string())
}

fn lower_if_evm(text: &str) -> String {
    let is_evm = text.len() == 42
        && text.starts_with("0x")
        && text[2..].bytes().all(|b| b.is_ascii_hexdigit());
    if is_evm {
        text.to_ascii_lowercase()
    } else {
        text.to_string()
    }
}

/// A slippage percent ("1", "0.5") as LI.FI's fraction ("0.01", "0.005"),
/// exactly: the decimal point moves two places, no float on the way.
fn fraction(percent: &str) -> Result<String, String> {
    let bad = || format!("slippage {percent} is not a percent from 0 to 100");
    let (whole, frac) = percent.split_once('.').unwrap_or((percent, ""));
    if whole.is_empty()
        || !whole.bytes().all(|b| b.is_ascii_digit())
        || !frac.bytes().all(|b| b.is_ascii_digit())
    {
        return Err(bad());
    }
    let whole: u32 = whole.parse().map_err(|_| bad())?;
    if whole > 100 || (whole == 100 && frac.bytes().any(|b| b != b'0')) {
        return Err(bad());
    }
    let digits = format!("{whole:03}{frac}");
    let (int, rest) = digits.split_at(digits.len() - 2 - frac.len());
    let text = format!("{}.{rest}", int.trim_start_matches('0'));
    let text = text.trim_end_matches('0').trim_end_matches('.');
    Ok(match text {
        "" => "0".to_string(),
        t if t.starts_with('.') => format!("0{t}"),
        t => t.to_string(),
    })
}

/// LI.FI's quote as the envelope the wallet module signs from.
pub fn translate(answer: Value) -> Result<Value, String> {
    let Value::Object(mut quote) = answer else {
        return Err("LI.FI sent a quote that is not an object".to_string());
    };
    let from_chain = quote
        .get("action")
        .and_then(|action| action.get("fromChainId"))
        .and_then(Value::as_u64)
        .ok_or_else(|| "LI.FI's quote does not say which chain it starts on".to_string())?;
    let Some(Value::Object(request)) = quote.remove("transactionRequest") else {
        return Err("LI.FI's quote carries no transaction".to_string());
    };
    let text = |key: &str| request.get(key).and_then(Value::as_str).map(str::trim);
    let tx = if from_chain == LIFI_SOLANA {
        let blob =
            text("data").ok_or_else(|| "LI.FI's Solana transaction has no data".to_string())?;
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(blob)
            .map_err(|_| "LI.FI's Solana transaction is not base64".to_string())?;
        serde_json::json!({ "data": format!("0x{}", hex::encode(bytes)) })
    } else {
        let to = text("to").ok_or_else(|| "LI.FI's EVM transaction has no `to`".to_string())?;
        let data = text("data").unwrap_or("0x");
        let value = wei(request.get("value"))?;
        serde_json::json!({ "to": to, "data": data, "value": value })
    };
    quote.insert("tx".to_string(), tx);
    quote.insert("provider".to_string(), Value::String("lifi".to_string()));
    Ok(Value::Object(quote))
}

/// An EVM value (hex `0x…` or decimal, absent for none) as decimal wei.
fn wei(value: Option<&Value>) -> Result<String, String> {
    let bad = || "LI.FI's EVM transaction value is not a number".to_string();
    let number = match value {
        None | Some(Value::Null) => 0,
        Some(Value::String(text)) => {
            let text = text.trim();
            match text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
                Some("") => 0,
                Some(hex) => u128::from_str_radix(hex, 16).map_err(|_| bad())?,
                None => text.parse::<u128>().map_err(|_| bad())?,
            }
        }
        Some(Value::Number(number)) => number.as_u64().map(u128::from).ok_or_else(bad)?,
        Some(_) => return Err(bad()),
    };
    Ok(number.to_string())
}

/// LI.FI's own message from an error body, cut short. Never the request,
/// which carries addresses.
pub fn error_message(status: u16, body: &str) -> String {
    let detail = serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|value| {
            value
                .get("message")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_default();
    let detail: String = detail.chars().take(ERROR_CHARS).collect();
    if detail.is_empty() {
        format!("LI.FI answered HTTP {status}")
    } else {
        format!("LI.FI answered HTTP {status}: {detail}")
    }
}

fn base_url() -> String {
    std::env::var("TEENY_LIFI_API")
        .ok()
        .map(|value| value.trim().trim_end_matches('/').to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| DEFAULT_BASE.to_string())
}

async fn fetch(request: Request) -> Result<Value, String> {
    let client = reqwest::Client::builder()
        .timeout(TIMEOUT)
        .redirect(reqwest::redirect::Policy::none())
        .user_agent("teeny-spine")
        .build()
        .map_err(|_| "could not build the LI.FI client".to_string())?;
    let mut get = client
        .get(format!("{}{}", base_url(), request.path))
        .query(&request.query);
    if let Some(key) = std::env::var("LIFI_API_KEY")
        .ok()
        .filter(|key| !key.trim().is_empty())
    {
        get = get.header("x-lifi-api-key", key.trim());
    }
    log::debug!("[teeny-lifi] GET {}", request.path);
    let response = get.send().await.map_err(|error| {
        format!(
            "could not reach LI.FI ({})",
            if error.is_timeout() {
                "timeout"
            } else {
                "connect"
            }
        )
    })?;
    let status = response.status().as_u16();
    let text = response
        .text()
        .await
        .map_err(|_| "LI.FI sent a body that could not be read".to_string())?;
    if !(200..300).contains(&status) {
        return Err(error_message(status, &text));
    }
    serde_json::from_str(&text).map_err(|_| "LI.FI sent something that is not JSON".to_string())
}

/// One module call, answered from LI.FI.
pub async fn answer(call: WalletBackendCall) -> Result<Value, String> {
    translate(fetch(request_for(&call)?).await?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const OWN_SOL: &str = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin";
    const OWN_EVM: &str = "0x1111111111111111111111111111111111111111";
    const BASE_USDC: &str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
    const SOL_USDC: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";

    fn pairs(request: &Request) -> Vec<(&str, &str)> {
        request
            .query
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect()
    }

    /// The bridge body the wallet module builds (`quote_bridge`), after the
    /// router has taken the provider tag off `dstChainTokenOutAmount`.
    fn bridge_body() -> Value {
        json!({
            "srcChainId": 7565164,
            "srcChainTokenIn": "11111111111111111111111111111111",
            "srcChainTokenInAmount": "100000000",
            "dstChainId": 8453,
            "dstChainTokenOut": BASE_USDC,
            "dstChainTokenOutAmount": "auto",
            "dstChainTokenOutRecipient": OWN_EVM,
            "srcChainOrderAuthorityAddress": OWN_SOL,
            "dstChainOrderAuthorityAddress": OWN_EVM,
        })
    }

    fn swap_body() -> Value {
        json!({
            "chainId": 7565164,
            "tokenIn": "11111111111111111111111111111111",
            "tokenInAmount": "100000000",
            "tokenOut": SOL_USDC,
            "tokenOutRecipient": OWN_SOL,
            "senderAddress": OWN_SOL,
            "slippage": "1",
        })
    }

    #[test]
    fn a_bridge_asks_for_a_quote_from_and_to_teenys_own_addresses() {
        let request = request_for(&WalletBackendCall::BridgeTransaction(bridge_body())).unwrap();
        assert_eq!(request.path, "/v1/quote");
        assert_eq!(
            pairs(&request),
            vec![
                ("fromChain", "1151111081099710"),
                ("toChain", "8453"),
                ("fromToken", "11111111111111111111111111111111"),
                ("toToken", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"),
                ("fromAmount", "100000000"),
                ("fromAddress", OWN_SOL),
                ("toAddress", OWN_EVM),
                ("order", "CHEAPEST"),
                ("denyBridges", "near"),
            ]
        );
    }

    #[test]
    fn a_swap_stays_on_one_chain_with_its_slippage_as_a_fraction() {
        let request = request_for(&WalletBackendCall::SwapTransaction(swap_body())).unwrap();
        let got = pairs(&request);
        assert_eq!(
            &got[..2],
            &[
                ("fromChain", "1151111081099710"),
                ("toChain", "1151111081099710")
            ]
        );
        assert!(got.contains(&("toAddress", OWN_SOL)));
        assert!(got.contains(&("slippage", "0.01")), "{got:?}");
    }

    #[test]
    fn auto_slippage_leaves_it_to_lifi_and_a_fraction_is_exact() {
        let mut body = swap_body();
        body["slippage"] = json!("auto");
        let request = request_for(&WalletBackendCall::SwapTransaction(body.clone())).unwrap();
        assert!(pairs(&request).iter().all(|(k, _)| *k != "slippage"));
        body["slippage"] = json!("0.5");
        let request = request_for(&WalletBackendCall::SwapTransaction(body.clone())).unwrap();
        assert!(pairs(&request).contains(&("slippage", "0.005")));
        for bad in ["-1", "abc", "101", "1e3"] {
            body["slippage"] = json!(bad);
            assert!(
                request_for(&WalletBackendCall::SwapTransaction(body.clone())).is_err(),
                "{bad}"
            );
        }
    }

    #[test]
    fn an_unknown_field_is_refused_not_dropped() {
        let mut body = swap_body();
        body["affiliateFeeRecipient"] = json!("0xevil");
        let error = request_for(&WalletBackendCall::SwapTransaction(body)).unwrap_err();
        assert!(error.contains("affiliateFeeRecipient"), "{error}");
        let mut body = bridge_body();
        body["toAddress"] = json!("0xevil");
        assert!(request_for(&WalletBackendCall::BridgeTransaction(body)).is_err());
    }

    #[test]
    fn a_missing_recipient_is_refused_not_defaulted() {
        let mut body = bridge_body();
        body.as_object_mut()
            .unwrap()
            .remove("dstChainTokenOutRecipient");
        let error = request_for(&WalletBackendCall::BridgeTransaction(body)).unwrap_err();
        assert!(error.contains("dstChainTokenOutRecipient"), "{error}");
    }

    #[test]
    fn a_bridge_for_a_fixed_output_is_refused() {
        // LI.FI quotes from the input; an exact output is deBridge's idea.
        let mut body = bridge_body();
        body["dstChainTokenOutAmount"] = json!("5000000");
        assert!(request_for(&WalletBackendCall::BridgeTransaction(body)).is_err());
    }

    #[test]
    fn a_non_scalar_field_is_refused() {
        let mut body = swap_body();
        body["tokenIn"] = json!({"x": 1});
        assert!(request_for(&WalletBackendCall::SwapTransaction(body)).is_err());
    }

    #[test]
    fn routes_are_not_lifis_to_answer() {
        assert!(request_for(&WalletBackendCall::Routes).is_err());
    }

    #[test]
    fn a_solana_quote_becomes_the_hex_blob_the_module_signs() {
        let recorded: Value = serde_json::from_str(include_str!(
            "../../hands/tests/wallet/fixtures/lifi/sol_to_base_usdc.json"
        ))
        .unwrap();
        let base64 = recorded["transactionRequest"]["data"]
            .as_str()
            .unwrap()
            .to_string();
        let envelope = translate(recorded.clone()).unwrap();
        let hex_blob = envelope["tx"]["data"].as_str().unwrap();
        use base64::Engine as _;
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(&base64)
            .unwrap();
        assert_eq!(hex_blob, format!("0x{}", hex::encode(bytes)));
        assert!(envelope["tx"].get("to").is_none());
        assert_eq!(envelope["provider"], "lifi");
        assert_eq!(envelope["tool"], recorded["tool"]);
        assert_eq!(envelope["estimate"], recorded["estimate"]);
        assert!(
            envelope.get("transactionRequest").is_none(),
            "one transaction, the one signed"
        );
    }

    #[test]
    fn an_evm_quote_keeps_to_and_data_and_gives_the_value_in_decimal_wei() {
        let recorded: Value = serde_json::from_str(include_str!(
            "../../hands/tests/wallet/fixtures/lifi/base_eth_to_usdc_swap.json"
        ))
        .unwrap();
        let envelope = translate(recorded.clone()).unwrap();
        let tx = &envelope["tx"];
        assert_eq!(tx["to"], recorded["transactionRequest"]["to"]);
        assert_eq!(tx["data"], recorded["transactionRequest"]["data"]);
        assert_eq!(tx["value"], "5000000000000000");
        assert_eq!(
            tx.as_object().unwrap().len(),
            3,
            "only what the module signs: {tx}"
        );
    }

    #[test]
    fn a_quote_without_a_usable_transaction_is_refused() {
        let solana = json!({"action": {"fromChainId": 1151111081099710_u64}, "transactionRequest": {"data": "not base64!"}});
        assert!(translate(solana).is_err());
        let evm = json!({"action": {"fromChainId": 8453}, "transactionRequest": {"to": OWN_EVM, "data": "0x", "value": "0xzz"}});
        assert!(translate(evm).is_err());
        let none = json!({"action": {"fromChainId": 8453}});
        assert!(translate(none).is_err());
        let no_chain = json!({"transactionRequest": {"to": OWN_EVM, "data": "0x"}});
        assert!(translate(no_chain).is_err());
    }

    #[test]
    fn errors_carry_lifis_message_not_the_request() {
        assert_eq!(
            error_message(
                404,
                r#"{"message":"No available quotes for the requested transfer","code":1002}"#
            ),
            "LI.FI answered HTTP 404: No available quotes for the requested transfer"
        );
        assert_eq!(error_message(502, "<html>"), "LI.FI answered HTTP 502");
    }
}
