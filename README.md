# DegenLens

**Verifiable on-chain gambling intelligence.**

DegenLens is a real-time intelligence terminal for crypto casinos. It tracks deposits, withdrawals, treasuries, player activity, and fairness across operators — then layers AI analysis, alerts, and agent access on top. Every data point is sourced through [Telegraph Protocol](https://telegraphprotocol.com) miners, scored by competing validators, and finalized with a verifiable `signal_hash`.

> Built for Telegraph Hackathon Season I (Aug 17 – Sep 7, 2026) across all three tracks: Miner, Script Author, and Application.

---

## Why DegenLens

Crypto casinos move enormous volume every week. The public data that exists is centralized, unauditable, and stops if one scraper goes down. DegenLens changes that:

1. **Decentralized sourcing** — multiple Telegraph miners compete to serve gambling intelligence. The best miner gets the traffic.
2. **AI analysis** — trend detection, anomaly alerts, sentiment, and natural-language queries. Not just raw tables.
3. **Verifiable** — every response carries a Telegraph `signal_hash`, validated by BFT consensus.
4. **Agent-native** — any AI agent can query DegenLens via Telegraph MCP. No custom casino integrations required.
5. **Live streaming** — WebSocket subscriptions push deposit/withdrawal and news signals as they settle.

---

## Features

### Overview dashboard
Market-wide deposits and withdrawals (1d / 7d / 14d / 30d), casino cards with volume, market share, and trend, plus a live bet stream and live on-chain flow.

**Market Pulse** — a short AI summary of what actually changed: which operators are growing, which are bleeding, and whether the move looks structural or one-off.

### Casino profiles
Per-operator deep dive:

- Deposit / withdrawal time series
- Depositor count and market share over time
- Revenue estimate from miner consensus
- Social sentiment (`SENTIMENT_ANALYSIS`)
- Latest news (`NEWS_SEARCH`)
- Anomaly flags on unusual volume spikes or drops
- Fairness score breakdown

### Player boards
Top winners, losers, and most wagered. Search by handle. Cross-casino wallet linking when the same address deposits to multiple operators.

### Wallet explorer
Paste any address. Trace it across networks, see casino associations with confidence, balance history, and an AI risk note (velocity, clustering, low-fairness destinations).

### Fairness leaderboard
Composite 0–100 score per casino:

| Dimension | Weight |
|-----------|--------|
| Fund Safety | 28% |
| Feed Integrity | 28% |
| Terms | 22% |
| House Edge | 12% |
| Provable Fairness | 10% |

Plus AI terms comparison (playthrough requirements vs. industry norms) and historical fairness trend.

### AI chat
Natural-language queries through Telegraph MCP:

- “Which casino has the highest deposit growth this week?”
- “Is this volume drop a trend or a one-off?”
- “Compare fairness and volume for these two operators.”
- “Alert me if any casino’s withdrawals exceed deposits by $5M in a day.”

### Alerts
Telegram bot + web UI. Thresholds on weekly deposits, net outflow, large first-time wallets, and low-fairness destinations.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    DATA SOURCES                           │
│                                                           │
│  On-Chain          Off-Chain              Social/News     │
│  ─────────         ──────────             ───────────     │
│  Casino wallets    Public bet feeds       Twitter          │
│  (ETH, SOL, BSC)  (operator APIs)        Reddit           │
│  DEX activity      Casino terms pages     Telegram         │
│  Token transfers   RTP databases          News sites       │
│  Smart contracts   Provable fairness      Casino forums    │
└──────────┬──────────────┬─────────────────┬──────────────┘
           │              │                 │
           ▼              ▼                 ▼
┌──────────────────────────────────────────────────────────┐
│              TELEGRAPH MINER LAYER                        │
│                                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐  │
│  │ Chain Miner  │ │ Feed Miner   │ │ Sentiment Miner  │  │
│  │ (on-chain    │ │ (bet feed    │ │ (social/news     │  │
│  │  tx + wallet │ │  scraping +  │ │  aggregation)    │  │
│  │  tracking)   │ │  RTP calc)   │ │                  │  │
│  └──────┬───────┘ └──────┬───────┘ └────────┬─────────┘  │
│         │                │                   │            │
│         ▼                ▼                   ▼            │
│  ┌─────────────────────────────────────────────────────┐  │
│  │          Validators + BFT Consensus                 │  │
│  │     WASM scoring → commit-reveal → finalized        │  │
│  └─────────────────────────────────────────────────────┘  │
│         │                │                   │            │
│         ▼                ▼                   ▼            │
│     Engine API      WebSocket Feed     On-Chain Records   │
└──────┬───────────────┬────────────────────┬──────────────┘
       │               │                    │
       ▼               ▼                    ▼
┌──────────────────────────────────────────────────────────┐
│                     DEGENLENS APP                         │
│                                                           │
│  Dashboard    Casino     Player    Wallet    Fairness     │
│  (overview,   Profiles   Boards    Explorer  Rating       │
│   live flow)  (deep      (winners, (trace    (composite   │
│               stats)     losers)   addrs)    scoring)     │
│                                                           │
│  AI Chat    Alerts       API       Anomaly                │
│  (NL        (Telegram    (devs)    Detection              │
│   queries)   bot)                  (auto)                 │
└──────────────────────────────────────────────────────────┘
```

---

## Telegraph integration

### Intents

| Intent | What DegenLens uses it for | Tier |
|--------|---------------------------|------|
| `WALLET_BALANCE_CHECK` | Casino treasury tracking across labeled wallet clusters | A (Deterministic) |
| `ONCHAIN_TX_LOOKUP` | Deposit / withdrawal flow observation | A (Deterministic) |
| `CRYPTO_PRICE` | Normalize volumes to USD across ETH, SOL, USDT, etc. | A (Deterministic) |
| `FRAUD_DETECTION` | Wash trading, artificial volume, sybil depositors | A (Deterministic) |
| `WEB_SEARCH` | Casino news, regulatory actions, license changes | B (LLM-Judge) |
| `NEWS_SEARCH` / `NEWS_HEADLINES` | Breaking industry news, exploit alerts | B (LLM-Judge) |
| `SENTIMENT_ANALYSIS` | Community sentiment per casino (Twitter / Reddit / Telegram) | B (LLM-Judge) |
| `TWITTER_SEARCH` | Live social buzz — complaints, scam allegations, promotions | B (LLM-Judge) |
| `CONTENT_EXTRACTION` | Structure casino terms, RTP tables, bonus conditions | B (LLM-Judge) |
| `RESEARCH_SYNTHESIS` | Comparison reports and market trend analysis | B (LLM-Judge) |

### Protocol features

| Feature | Role in DegenLens |
|---------|-------------------|
| **Engine API** (`/engine/v1/ask`) | On-demand queries, auto-routed to the best miner |
| **WebSocket subscriptions** | Pushed alerts on large deposits or breaking news |
| **x402 micropayments** | ~$0.01 per query. No per-casino or per-explorer API deals |
| **Miner competition** | Best miner gets ~70% of traffic. Quality improves automatically |
| **Signal verification** | Every stat ships with an on-chain `signal_hash` |
| **MCP server** | Agents ask questions in natural language — no crypto code required |
| **Daemon feed** | Background wallet monitoring every 3 hours, building history |

### Query example

```typescript
const casinoData = await fetch('https://devnode.telegraphprotocol.com/engine/v1/ask', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'PAYMENT-SIGNATURE': paymentSig,
  },
  body: JSON.stringify({
    query: 'What are the top 10 crypto casinos by deposit volume this week?',
  }),
});
// Response includes signal_hash for verification

const ws = new WebSocket(
  'wss://devnode.telegraphprotocol.com/engine/ws?wallet_address=0x...'
);
ws.send(JSON.stringify({
  action: 'subscribe',
  intents: ['ONCHAIN_TX_LOOKUP', 'FRAUD_DETECTION', 'NEWS_SEARCH'],
  spend_limit_usdc: 5_000_000, // $5.00 session budget
}));
```

---

## Hackathon tracks

DegenLens is a full-stack entry. The app consumes the miner; the scorer ranks the miner; the loop generates real request volume.

```
Track 1: DegenMiner            Track 2: On-Chain Data Scorer
(Alchemy + explorers +         (WASM module scoring
 price feeds → Telegraph        numerical + address accuracy
 miner for gambling data)       for on-chain responses)
        │                               │
        │   miner serves the data       │   scorer ranks miners
        ▼                               ▼
    ┌───────────────────────────────────────┐
    │         Track 3: DegenLens App        │
    │                                       │
    │   Heavy consumption of the miner      │
    │   → miner ranking                     │
    │   → scorer evaluates quality          │
    │   → flywheel                          │
    └───────────────────────────────────────┘
```

### Track 1 — DegenMiner

A Telegraph miner wrapping on-chain data into verified gambling intelligence.

**Sources**: Etherscan / Basescan / Solscan, Alchemy / QuickNode, DeFiLlama (on-chain casino protocols), public operator bet feeds, CoinGecko for price normalization.

**Intents**: `ONCHAIN_TX_LOOKUP`, `WALLET_BALANCE_CHECK`, `FRAUD_DETECTION`.

```yaml
name: degenminer
description: "On-chain gambling intelligence — casino volumes, wallet tracking, deposit flows, and anomaly detection"
base_url: https://your-degenminer-api.com
min_price_usdc: 0.005
intents:
  - ONCHAIN_TX_LOOKUP
  - WALLET_BALANCE_CHECK
  - FRAUD_DETECTION
endpoints:
  - path: /casino/stats
    method: POST
    description: "Get deposit/withdrawal volume for a casino"
  - path: /casino/ranking
    method: GET
    description: "Ranked casinos by volume with market share"
  - path: /wallet/trace
    method: POST
    description: "Trace a wallet address for casino associations"
  - path: /anomaly/check
    method: POST
    description: "Check for suspicious deposit/withdrawal patterns"
```

Tier A intents score against exact on-chain ground truth. `FRAUD_DETECTION` is underserved. The DegenLens app is the demand engine.

**Stack**: Python FastAPI, Alchemy SDK, explorer APIs, CoinGecko. Seeded wallet-cluster database. Deploy on Railway or Fly.io.

### Track 2 — On-chain data scorer

A WASM scoring module built for wallet addresses and dollar amounts, not word overlap.

| Signal | Weight | Rule |
|--------|--------|------|
| Numerical precision | 50% | Relative error on extracted USD/token values. ≤1% → 0.95+, ≤5% → 0.8, >10% → 0.3 |
| Address accuracy | 25% | Checksum-aware exact match. One wrong character → 0 |
| Completeness | 15% | Requested fields present (volume, address, network, timestamp) |
| Recency | 10% | Penalize data older than ~10 blocks |

Generic keyword scorers fail on hex addresses and precise balances. This one cannot be gamed by stuffing text.

**Stack**: Rust → WASM. JSON parse, hex + numeric extract, tolerance bands. Target size under 1MB.

### Track 3 — DegenLens app

Next.js dashboard consuming Telegraph miners.

| Page | Route |
|------|--------|
| Overview | `/` |
| Casino profile | `/casino/[name]` |
| Player boards | `/players` |
| Wallet explorer | `/wallet/[address]` |
| Fairness | `/fairness` |
| AI chat | `/ask` |
| Alerts | `/alerts` |

**Stack**: Next.js 14, Tailwind CSS, Recharts, Supabase (cache / history), Telegraph MCP, Vercel.

---

## Request volume (projected)

| Source | Requests / day | Intent |
|--------|----------------|--------|
| Dashboard refresh (every 5 min) | ~288 | `ONCHAIN_TX_LOOKUP` |
| Casino profile loads | ~30 | `ONCHAIN_TX_LOOKUP` + `WALLET_BALANCE_CHECK` |
| Sentiment checks (hourly, per casino) | ~648 | `SENTIMENT_ANALYSIS` |
| News updates (hourly) | ~24 | `NEWS_SEARCH` |
| Wallet explorer | ~20 | `WALLET_BALANCE_CHECK` |
| Anomaly sweeps (every 15 min) | ~96 | `FRAUD_DETECTION` |
| AI chat | ~30 | `WEB_SEARCH` |
| Daemon WebSocket signals (every 3h) | ~8 | Mixed |
| Price normalization (every 5 min) | ~288 | `CRYPTO_PRICE` |
| **Total** | **~1,432 / day** | |

The hackathon 100-request eligibility guardrail clears on day one.

---

## Timeline (21 days)

### Week 1 — Core infrastructure
- [ ] Days 1–2: DegenMiner API — Alchemy + explorers, seed known casino wallet clusters
- [ ] Days 2–3: YAML config, register miner on Telegraph testnet (`ONCHAIN_TX_LOOKUP`, `WALLET_BALANCE_CHECK`)
- [ ] Days 3–4: WASM scorer in Rust (numerical + address accuracy)
- [ ] Days 5–7: Next.js — overview dashboard + casino ranking
- [ ] **X**: vision post, tag [@Telegraphprotoc](https://x.com/Telegraphprotoc)

### Week 2 — Intelligence layer
- [ ] Days 8–9: WebSocket live deposit / withdrawal flow
- [ ] Days 10–11: Per-casino sentiment + news
- [ ] Days 12–13: Wallet explorer + player boards
- [ ] Day 14: Telegram alerts + anomaly detection
- [ ] **X**: progress screenshots every 2–3 days

### Week 3 — Polish and volume
- [ ] Days 15–16: AI chat
- [ ] Days 17–18: Fairness page, mobile
- [ ] Days 19–20: Load test, keep miner live, sustain request volume
- [ ] Day 21: Demo video, metrics, submission
- [ ] **X**: final demo thread

---

## Why this works

- **Real market** — weekly crypto-casino flows are large enough that verified intelligence has actual users.
- **Telegraph-native** — deterministic on-chain data plus LLM-judged sentiment/news. Both scoring tiers, many intents.
- **Continuous demand** — gambling stats go stale immediately, so the dashboard keeps querying.
- **Shareable** — big wins, big losses, and operator comparisons travel on X.
- **Not a thin wrapper** — AI layer, alerts, verification, and agent access are the product, not a reskin of a public feed.

---

## Quick start

Hackathon setup (before code lands here):

- [ ] Register at [hackathon.telegraphprotocol.com](https://hackathon.telegraphprotocol.com)
- [ ] Join the Hackathon Discord
- [ ] Clone [Telegraph-MCP](https://github.com/telegraphprotocol/Telegraph-MCP) and run locally
- [ ] Clone [telegraph-examples](https://github.com/telegraphprotocol/telegraph-examples)
- [ ] Fund a burner wallet with USDC on Base Sepolia
- [ ] Live intents: `curl https://devnode.telegraphprotocol.com/engine/v1/intents`
- [ ] Live miners: `curl https://devnode.telegraphprotocol.com/api/miners`

Application, miner, and scorer source will land in this repo as the build proceeds.

---

## License

TBD.
