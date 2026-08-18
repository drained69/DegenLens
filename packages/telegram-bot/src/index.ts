/**
 * DegenLens Telegram bot.
 *
 * Subscribes to Telegraph's WebSocket signal feed (Daemon-produced signals every 3h) and
 * fires Telegram messages when configured thresholds trigger.
 *
 * Requires:
 *   - EVM_PRIVATE_KEY + EVM_WALLET_ADDRESS with ≥$1 USDC in Diamond escrow
 *   - TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
 */

import "dotenv/config";
import WebSocket from "ws";
import { privateKeyToAccount } from "viem/accounts";

const {
  TELEGRAPH_WS_URL = "wss://devnode.telegraphprotocol.com/engine/ws",
  EVM_WALLET_ADDRESS,
  EVM_PRIVATE_KEY,
  TELEGRAM_BOT_TOKEN,
  TELEGRAM_CHAT_ID,
} = process.env;

const INTENTS = [
  "ONCHAIN_TX_LOOKUP",
  "FRAUD_DETECTION",
  "NEWS_SEARCH",
  "SENTIMENT_ANALYSIS",
];

async function tg(text: string) {
  if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) {
    console.log("[dry-run]", text);
    return;
  }
  await fetch(`https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: TELEGRAM_CHAT_ID,
      text,
      parse_mode: "Markdown",
      disable_web_page_preview: true,
    }),
  });
}

async function main() {
  if (!EVM_WALLET_ADDRESS || !EVM_PRIVATE_KEY) {
    throw new Error(
      "EVM_WALLET_ADDRESS and EVM_PRIVATE_KEY are required for WebSocket auth.",
    );
  }

  const account = privateKeyToAccount(
    (EVM_PRIVATE_KEY.startsWith("0x")
      ? EVM_PRIVATE_KEY
      : `0x${EVM_PRIVATE_KEY}`) as `0x${string}`,
  );
  const url = `${TELEGRAPH_WS_URL}?wallet_address=${EVM_WALLET_ADDRESS}`;
  const ws = new WebSocket(url);

  ws.on("open", () => {
    console.log("[ws] connected");
    ws.send(JSON.stringify({ action: "auth_wallet" }));
  });

  ws.on("message", async (raw) => {
    const msg = JSON.parse(raw.toString());

    switch (msg.type) {
      case "wallet_challenge": {
        const signature = await account.signMessage({
          message: msg.data.message,
        });
        ws.send(JSON.stringify({ action: "wallet_verify", signature }));
        break;
      }
      case "wallet_verified":
        console.log("[ws] verified — subscribing");
        ws.send(
          JSON.stringify({
            action: "subscribe",
            intents: INTENTS,
            spend_limit_usdc: 5_000_000, // $5 per session
          }),
        );
        break;
      case "subscribed":
        console.log("[ws] subscription id:", msg.data.subscription_id);
        break;
      case "result":
        await handleSignal(msg.data);
        break;
      case "limit_reached":
        console.warn("[ws] spend limit reached — reconnect logic required");
        break;
      case "error":
        console.error("[ws] error:", msg.data);
        break;
    }
  });

  ws.on("close", () => {
    console.log("[ws] closed — reconnecting in 5s");
    setTimeout(() => main(), 5_000);
  });

  // Ping every 30s so the socket stays alive
  setInterval(
    () =>
      ws.readyState === WebSocket.OPEN &&
      ws.send(JSON.stringify({ action: "ping" })),
    30_000,
  );
}

async function handleSignal(data: any) {
  const intent = data.intent;
  const result = data.execution?.result;
  if (!intent || !result) return;

  if (intent === "FRAUD_DETECTION" && result.verdict === "critical") {
    await tg(
      `🚨 *CRITICAL fraud signal*\n` +
        `Address: \`${result.address}\`\n` +
        `Score: ${(result.score * 100).toFixed(0)}/100\n` +
        `Signals: ${result.signals?.join("; ") ?? "n/a"}`,
    );
  }

  if (
    intent === "ONCHAIN_TX_LOOKUP" &&
    Math.abs(result.net_flow_usd ?? 0) > 5_000_000
  ) {
    await tg(
      `📊 *Large flow* @ ${result.name}\n` +
        `Net: $${(result.net_flow_usd / 1e6).toFixed(2)}M in ${result.window_hours}h`,
    );
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
