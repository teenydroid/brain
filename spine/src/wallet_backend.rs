//! The spine's answer to the wallet module's swap and bridge quotes, from
//! LI.FI or deBridge (`lifi.rs`, `debridge.rs`).
//!
//! The module asks its host for each unsigned transaction through the core's
//! wallet-backend hook, which forwards every call to the *first* handler
//! registered: there is no per-call handler id. So one handler is installed
//! here and it picks the provider per call. hands/wallet (convert.py) names
//! the provider by tagging the one free-text field each body carries, the
//! field that holds no address or amount:
//!
//! | Call   | Field                    | Tagged             | Forwarded |
//! |--------|--------------------------|--------------------|-----------|
//! | swap   | `slippage`               | `lifi:1`           | `1`       |
//! | bridge | `dstChainTokenOutAmount` | `debridge:auto`    | `auto`    |
//!
//! The tag comes off before the provider sees the body. An unknown tag is
//! refused. An untagged call (buy-tiny, the module's own tools), and the
//! routes list, go to deBridge as they always have, since those callers read
//! deBridge's shape. A call routed to the wrong provider cannot slip through:
//! hands/wallet refuses an answer that is not the provider's own shape (a
//! LI.FI answer says `"provider": "lifi"`).

use openhuman_embed::__host::wallet_backend::{register_wallet_backend, WalletBackendCall};
use serde_json::Value;

use crate::{debridge, lifi};

/// The id the handler registers under. The core forwards to the first
/// registered handler; [`install`] runs before the host starts, so this one
/// is ahead of the tinyhumans proxy the host registers later.
pub const HANDLER_ID: &str = "teeny-convert";

/// Who quotes a swap or a bridge.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Provider {
    Lifi,
    Debridge,
}

/// The provider a call names, and the call as that provider takes it.
pub fn route(call: WalletBackendCall) -> Result<(Provider, WalletBackendCall), String> {
    let (mut body, field, swap) = match call {
        WalletBackendCall::SwapTransaction(body) => (body, "slippage", true),
        WalletBackendCall::BridgeTransaction(body) => (body, "dstChainTokenOutAmount", false),
        WalletBackendCall::Routes => return Ok((Provider::Debridge, WalletBackendCall::Routes)),
    };
    let tagged = body
        .get(field)
        .and_then(Value::as_str)
        .and_then(|text| text.split_once(':'))
        .map(|(tag, rest)| (tag.to_string(), rest.to_string()));
    let provider = match tagged {
        None => Provider::Debridge,
        Some((tag, rest)) => {
            let provider = match tag.as_str() {
                "lifi" => Provider::Lifi,
                "debridge" => Provider::Debridge,
                _ => return Err(format!("no swap or bridge provider is called {tag:?}")),
            };
            body[field] = Value::String(rest);
            provider
        }
    };
    let call = if swap {
        WalletBackendCall::SwapTransaction(body)
    } else {
        WalletBackendCall::BridgeTransaction(body)
    };
    Ok((provider, call))
}

/// Registers the handler. Call once, before the host starts.
pub fn install() {
    register_wallet_backend(HANDLER_ID, |_config, call| {
        Box::pin(async move {
            match route(call)? {
                (Provider::Lifi, call) => lifi::answer(call).await,
                (Provider::Debridge, call) => debridge::answer(call).await,
            }
        })
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn swap(slippage: Value) -> WalletBackendCall {
        WalletBackendCall::SwapTransaction(json!({"chainId": 8453, "tokenIn": "a", "slippage": slippage}))
    }

    fn bridge(amount: Value) -> WalletBackendCall {
        WalletBackendCall::BridgeTransaction(json!({"srcChainId": 8453, "dstChainTokenOutAmount": amount}))
    }

    fn body(call: &WalletBackendCall) -> &Value {
        match call {
            WalletBackendCall::SwapTransaction(body) | WalletBackendCall::BridgeTransaction(body) => body,
            WalletBackendCall::Routes => panic!("no body"),
        }
    }

    #[test]
    fn a_tagged_swap_goes_to_its_provider_with_the_tag_taken_off() {
        let (provider, call) = route(swap(json!("lifi:1"))).unwrap();
        assert_eq!(provider, Provider::Lifi);
        assert_eq!(body(&call)["slippage"], "1");
        assert_eq!(body(&call)["tokenIn"], "a");
        let (provider, call) = route(swap(json!("debridge:0.5"))).unwrap();
        assert_eq!(provider, Provider::Debridge);
        assert_eq!(body(&call)["slippage"], "0.5");
    }

    #[test]
    fn a_tagged_bridge_goes_to_its_provider_with_the_tag_taken_off() {
        let (provider, call) = route(bridge(json!("lifi:auto"))).unwrap();
        assert_eq!(provider, Provider::Lifi);
        assert_eq!(body(&call)["dstChainTokenOutAmount"], "auto");
        let (provider, call) = route(bridge(json!("debridge:auto"))).unwrap();
        assert_eq!(provider, Provider::Debridge);
        assert_eq!(body(&call)["dstChainTokenOutAmount"], "auto");
    }

    #[test]
    fn an_untagged_call_stays_with_debridge_unchanged() {
        // buy-tiny and the module's own tools send no tag, and read deBridge's shape.
        for call in [swap(json!("0.5")), swap(Value::Null), bridge(json!("auto"))] {
            let (provider, routed) = route(call.clone()).unwrap();
            assert_eq!((provider, &routed), (Provider::Debridge, &call));
        }
        assert_eq!(route(WalletBackendCall::Routes).unwrap().0, Provider::Debridge);
    }

    #[test]
    fn an_unknown_provider_is_refused() {
        let error = route(swap(json!("jupiter:1"))).unwrap_err();
        assert!(error.contains("jupiter"), "{error}");
        assert!(route(bridge(json!("LIFI:auto"))).is_err(), "tags are exact");
    }
}
