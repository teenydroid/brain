// inbound_offer, inbound_results, inbound_result_ack: OpenHuman's inbound queue, which fed
// per-surface agents when TEENY_INBOX=core. This core does not have one. The default
// (TEENY_INBOX=mind) never calls these; the methods answer a plain error rather than
// "method not found" so a caller that does reach them says why, and bin/doctor fails a box
// that sets TEENY_INBOX=core.
import { RpcError, type RpcMethods } from "./types.js";

export const NOT_SUPPORTED =
  "the inbound queue is not supported by this core: unset TEENY_INBOX=core (the default, `mind`, feeds the stimuli inbox)";

const refuse = async (): Promise<never> => {
  throw new RpcError(NOT_SUPPORTED, -32601);
};

export const inboundMethods: RpcMethods = {
  inbound_offer: refuse,
  inbound_results: refuse,
  inbound_result_ack: refuse,
};
