import { TelegraphClient } from "@degenlens/shared";

const nodeUrl =
  process.env.TELEGRAPH_NODE_URL ?? "https://devnode.telegraphprotocol.com";
const localMinerUrl = process.env.LOCAL_MINER_URL ?? "http://localhost:8787";
/** The registered miner to use for endpoint-specific requests. */
export const telegraphMinerId = process.env.TELEGRAPH_MINER_ID ?? "local";
export const telegraphNodeUrl = nodeUrl;
export const telegraphPaymentConfigured = Boolean(process.env.EVM_PRIVATE_KEY);

/**
 * Server-side Telegraph client. In dev, we call the local DegenMiner directly to skip
 * the x402 payment loop. In production, EVM_PRIVATE_KEY enables the x402 payment wrapper.
 */
export const telegraph = new TelegraphClient({
  nodeUrl,
  localMinerUrl,
});
