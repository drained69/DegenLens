import { TelegraphClient } from "@degenlens/shared";
import { wrapFetchWithPayment, x402Client } from "@x402/fetch";
import { ExactEvmScheme } from "@x402/evm";
import { privateKeyToAccount } from "viem/accounts";
import { resilientFetch } from "@/lib/fetch-fallback";

const nodeUrl =
  process.env.TELEGRAPH_NODE_URL ?? "https://devnode.telegraphprotocol.com";
const localMinerUrl = process.env.LOCAL_MINER_URL ?? "http://localhost:8787";
/** The combined Railway deployment runs the miner beside the web app. */
export const telegraphMinerId = process.env.TELEGRAPH_MINER_ID ?? "local";
export const telegraphNodeUrl = nodeUrl;
export const telegraphPaymentConfigured = Boolean(process.env.EVM_PRIVATE_KEY);

if (
  process.env.TELEGRAPH_MINER_ID &&
  !/^\d+$/.test(process.env.TELEGRAPH_MINER_ID)
) {
  throw new Error("TELEGRAPH_MINER_ID must be an active numeric miner ID");
}

const paidFetch = process.env.EVM_PRIVATE_KEY
  ? wrapFetchWithPayment(
      resilientFetch,
      new x402Client().register(
        "eip155:*",
        new ExactEvmScheme(
          privateKeyToAccount(process.env.EVM_PRIVATE_KEY as `0x${string}`),
        ),
      ),
    )
  : resilientFetch;

/**
 * Server-side Telegraph client. In dev, we call the local DegenMiner directly to skip
 * the x402 payment loop. In production, EVM_PRIVATE_KEY enables the x402 payment wrapper.
 */
export const telegraph = new TelegraphClient({
  nodeUrl,
  localMinerUrl,
  paidFetch,
});
