//! LI.FI, the default provider for the wallet module's swap and bridge quotes.

use openhuman_embed::__host::wallet_backend::WalletBackendCall;
use serde_json::Value;

/// One GET to LI.FI: the path and its query.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub path: &'static str,
    pub query: Vec<(String, String)>,
}

/// The LI.FI request for one module call, or why it cannot be made.
pub fn request_for(_call: &WalletBackendCall) -> Result<Request, String> {
    Err("not built yet".to_string())
}

/// LI.FI's quote as the envelope the wallet module signs from.
pub fn translate(_answer: Value) -> Result<Value, String> {
    Err("not built yet".to_string())
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
        request.query.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect()
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
            ]
        );
    }

    #[test]
    fn a_swap_stays_on_one_chain_with_its_slippage_as_a_fraction() {
        let request = request_for(&WalletBackendCall::SwapTransaction(swap_body())).unwrap();
        let got = pairs(&request);
        assert_eq!(&got[..2], &[("fromChain", "1151111081099710"), ("toChain", "1151111081099710")]);
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
            assert!(request_for(&WalletBackendCall::SwapTransaction(body.clone())).is_err(), "{bad}");
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
        body.as_object_mut().unwrap().remove("dstChainTokenOutRecipient");
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
        let recorded: Value =
            serde_json::from_str(include_str!("../../hands/tests/wallet/fixtures/lifi/sol_to_base_usdc.json")).unwrap();
        let base64 = recorded["transactionRequest"]["data"].as_str().unwrap().to_string();
        let envelope = translate(recorded.clone()).unwrap();
        let hex_blob = envelope["tx"]["data"].as_str().unwrap();
        use base64::Engine as _;
        let bytes = base64::engine::general_purpose::STANDARD.decode(&base64).unwrap();
        assert_eq!(hex_blob, format!("0x{}", hex::encode(bytes)));
        assert!(envelope["tx"].get("to").is_none());
        assert_eq!(envelope["provider"], "lifi");
        assert_eq!(envelope["tool"], recorded["tool"]);
        assert_eq!(envelope["estimate"], recorded["estimate"]);
        assert!(envelope.get("transactionRequest").is_none(), "one transaction, the one signed");
    }

    #[test]
    fn an_evm_quote_keeps_to_and_data_and_gives_the_value_in_decimal_wei() {
        let recorded: Value =
            serde_json::from_str(include_str!("../../hands/tests/wallet/fixtures/lifi/base_eth_to_usdc_swap.json")).unwrap();
        let envelope = translate(recorded.clone()).unwrap();
        let tx = &envelope["tx"];
        assert_eq!(tx["to"], recorded["transactionRequest"]["to"]);
        assert_eq!(tx["data"], recorded["transactionRequest"]["data"]);
        assert_eq!(tx["value"], "5000000000000000");
        assert_eq!(tx.as_object().unwrap().len(), 3, "only what the module signs: {tx}");
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
            error_message(404, r#"{"message":"No available quotes for the requested transfer","code":1002}"#),
            "LI.FI answered HTTP 404: No available quotes for the requested transfer"
        );
        assert_eq!(error_message(502, "<html>"), "LI.FI answered HTTP 502");
    }
}
