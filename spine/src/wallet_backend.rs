//! The spine's answer to the wallet module's swap and bridge quotes.

use openhuman_embed::__host::wallet_backend::WalletBackendCall;

/// Who quotes a swap or a bridge.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Provider {
    Lifi,
    Debridge,
}

/// The provider a call names, and the call as that provider takes it.
pub fn route(call: WalletBackendCall) -> Result<(Provider, WalletBackendCall), String> {
    Ok((Provider::Debridge, call))
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
