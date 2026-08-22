#!/usr/bin/env node

import { createPublicClient, formatUnits, http } from "viem";
import { baseSepolia } from "viem/chains";
import { privateKeyToAccount } from "viem/accounts";
import { ExactEvmScheme } from "@x402/evm";
import { wrapFetchWithPayment, x402Client } from "@x402/fetch";

const nodeUrl = (process.env.TELEGRAPH_NODE_URL ?? "https://devnode.telegraphprotocol.com").replace(/\/$/, "");
const minerId = process.env.TELEGRAPH_MINER_ID;
const endpoint = process.env.TELEGRAPH_ENDPOINT ?? "/casino/stats";
const privateKey = process.env.EVM_PRIVATE_KEY;
const rpcUrl = process.env.BASE_SEPOLIA_RPC_URL ??
  (process.env.ALCHEMY_KEY ? `https://base-sepolia.g.alchemy.com/v2/${process.env.ALCHEMY_KEY}` : undefined);

if (!privateKey) {
  console.error("EVM_PRIVATE_KEY is required; no payment was attempted.");
  process.exit(2);
}
if (!minerId || !/^\d+$/.test(minerId)) {
  console.error("TELEGRAPH_MINER_ID must be the active numeric miner ID from /api/miners; no payment was attempted.");
  process.exit(2);
}

const catalog = await (await fetch(`${nodeUrl}/api/miners`)).json();
const miner = catalog.find((entry) => String(entry.id) === minerId);
if (!miner || miner.activation_status !== "active") {
  console.error(`Miner ${minerId} is not active in the live catalog; no payment was attempted.`);
  process.exit(2);
}
const declared = (miner.endpoints ?? []).some((entry) => entry.path === endpoint);
if (!declared) {
  console.error(`Endpoint ${endpoint} is not declared by miner ${minerId}; no payment was attempted.`);
  process.exit(2);
}

const account = privateKeyToAccount(privateKey);
const publicClient = rpcUrl ? createPublicClient({ chain: baseSepolia, transport: http(rpcUrl) }) : undefined;
const usdc = "0x036CbD53842c5426634e7929541eC2318f3dCF7e";
const erc20Balance = publicClient ? await publicClient.readContract({
  address: usdc,
  abi: [{ name: "balanceOf", type: "function", stateMutability: "view", inputs: [{ name: "owner", type: "address" }], outputs: [{ type: "uint256" }] }],
  functionName: "balanceOf",
  args: [account.address],
}) : null;
if (erc20Balance !== null) {
  console.log(`payer=${account.address} baseSepoliaUSDC=${formatUnits(erc20Balance, 6)}`);
  if (erc20Balance < 10000n) {
    console.error("Wallet has less than the documented $0.01 minimum; no payment was attempted.");
    process.exit(2);
  }
}

const paidFetch = wrapFetchWithPayment(
  fetch,
  new x402Client().register("eip155:*", new ExactEvmScheme(account)),
);
const request = {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ method: "POST", endpoint, payload: { slug: "stake", hours: 24 } }),
};
console.log(`request=POST ${nodeUrl}/engine/v1/ask/${minerId}`);
console.log("payment=enabled; the next request may spend testnet USDC");
const response = await paidFetch(`${nodeUrl}/engine/v1/ask/${minerId}`, request);
const body = await response.json();
console.log(JSON.stringify({
  http_status: response.status,
  miner_id: body.miner_id,
  miner_name: body.miner_name,
  endpoint: body.endpoint,
  cost_usd: body.cost_usd,
  signal_hash: body.signal_hash,
  payment_response: response.headers.get("payment-response"),
  result: body.result,
}, null, 2));
if (!response.ok || !body.signal_hash) process.exit(1);

const signal = await fetch(`${nodeUrl}/engine/v1/signal/${body.signal_hash}`);
console.log(JSON.stringify({ signal_status: signal.status, signal: await signal.json() }, null, 2));
