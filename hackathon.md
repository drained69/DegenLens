# Telegraph Hackathon Season I - Winning Ideas

> **Dates**: Aug 17 - Sep 7, 2026 (Hackathon 1, $5K USD)
> **Tracks**: Miners ($2K) | Script Authors ($1K) | Applications ($2K)
> **Key Insight**: Judges want proof that the *quality flywheel* works — not pretty demos. Real performance, real demand, real rankings.

---

## How to Win Each Track (Summary)

| Track | 75% of Score | 25% of Score |
|-------|-------------|-------------|
| **Miner** | Normalized performance (your Canonical Score vs. best miner in your Intent) | X engagement & updates |
| **Script Author** | Telegraph's automated eval of your script (accuracy, gaming-resistance) | X engagement & updates |
| **Application** | Users, usage, creativity, must use Telegraph miners | X engagement & posts |

**Critical guardrail**: An Intent needs >= 3 active miners AND >= 100 real requests from Track 3 apps to be eligible for global prizes. This means coordination across tracks matters.

---

## Strategy: Pick the Right Intent

The biggest alpha is **choosing an Intent with low competition but high application demand**. The Intents page shows many canonical intents that have *zero live miners* — registering for one of these means you're instantly #1 on that leaderboard.

**High-opportunity Intents (likely underserved)**:
- `FRAUD_DETECTION` — high value, hard to build, few competitors expected
- `CVE_LOOKUP` — niche but extremely useful for security agents
- `SENTIMENT_ANALYSIS` — every crypto/trading app wants this
- `FACT_CHECK` — growing need, few quality solutions
- `SSL_VERIFICATION` / `URL_SCAN` — infrastructure monitoring is evergreen
- `CONTENT_MODERATION` — every social app needs this
- `TOKEN_HOLDER_COUNT` / `TVL_LOOKUP` — DeFi apps will eat these up

**Avoid**: `WEATHER_CHECK`/`WEATHER_FORECAST` (Zeus already dominates), `CRYPTO_PRICE` (CoinGecko is entrenched), `CHAT_COMPLETION` (Gemini/OpenAI miners exist).

---

## Track 1: Miner Ideas (Ranked by Win Probability)

### 1. Real-Time Sentiment Oracle (SENTIMENT_ANALYSIS) — TOP PICK

**What**: Wrap a pipeline that aggregates Twitter/Reddit/Telegram sentiment for any token or topic using a fine-tuned LLM + real-time social scraping.

**Why this wins**:
- Tier B (LLM-Judge) intent — quality differentiation matters more than exact matching
- Extremely high demand from Track 3 app builders (trading bots, portfolio dashboards)
- No entrenched competitor likely
- Cross-intent synergy: apps that use SENTIMENT_ANALYSIS will also use CRYPTO_PRICE, creating demand for multiple miners

**Tech stack**: Python FastAPI wrapping Twitter/Reddit APIs + sentiment model (fine-tuned Llama or Mistral). YAML config pointing to your hosted endpoint. Deploy on Railway/Fly.io.

**Scoring edge**: Ground truth for sentiment is squishy — a well-calibrated model that returns structured sentiment scores (bullish/bearish/neutral with confidence) will dominate word-overlap scoring scripts.

---

### 2. On-Chain Intelligence Suite (TVL_LOOKUP + TOKEN_HOLDER_COUNT + WALLET_BALANCE_CHECK)

**What**: A single miner registered across 3 related Intents, wrapping DeFiLlama, Etherscan, and Alchemy APIs into a unified on-chain data layer.

**Why this wins**:
- **Three leaderboards** = three chances to rank #1
- Deterministic (Tier A) intents — exact correctness matters, and if your API is accurate you score perfectly
- These are the building blocks every DeFi application in Track 3 needs
- The 100-request guardrail is easy to hit when Track 3 apps consume all three intents

**Tech stack**: Node.js/Express wrapping DeFiLlama API, Etherscan API, Alchemy SDK. Cache layer with 30s TTL for rate limit management.

---

### 3. Security Intelligence Miner (CVE_LOOKUP + URL_SCAN + SSL_VERIFICATION)

**What**: Wrap NVD/MITRE APIs for CVE data, Google Safe Browsing / VirusTotal for URL scanning, and SSL Labs API for cert verification. Package as a security-focused miner serving 3 deterministic intents.

**Why this wins**:
- Near-zero competition expected — security intents are niche
- Deterministic scoring = if you return the right data, you get a perfect score
- Very useful for security agent Track 3 apps
- Easy to build and keep operational (these APIs are stable and well-documented)

**Tech stack**: Go or Python service wrapping NVD API + VirusTotal + SSL Labs. Simple YAML with three intent declarations.

---

### 4. AI Content Detector (AI_TEXT_DETECTION + DEEPFAKE_DETECTION)

**What**: Wrap a state-of-the-art AI text detection model (GPTZero API or open-source alternative) and pair it with image/video deepfake detection.

**Why this wins**:
- ItsAI and BitMind already serve these, but if you can *beat their scores*, you take the 70% routing share
- Media authenticity is a hot-button issue — Track 3 apps in this space get attention
- These are Tier A (deterministic) — binary correct/incorrect, so a more accurate model wins definitively

**Risk**: Competing head-to-head with existing miners. Only pursue if you have access to a genuinely superior detection model.

---

### 5. Financial Data Aggregator (FINANCIAL_DATA + CURRENCY_EXCHANGE + STOCK_PRICE)

**What**: Wrap Yahoo Finance, Alpha Vantage, and ExchangeRate APIs into a single financial data miner.

**Why this wins**:
- Traditional finance intents with clear ground truth
- Huge application potential (trading bots, portfolio trackers, arbitrage agents)
- Multiple intents = multiple leaderboard entries

**Tech stack**: Python + yfinance + Alpha Vantage SDK. FastAPI with structured JSON responses matching expected schemas.

---

## Track 2: Script Author Ideas (Ranked by Win Probability)

### 1. Semantic Similarity Scorer — TOP PICK

**What**: A WASM scoring module that goes beyond word-overlap to use cosine similarity on pre-computed word embeddings. Compile a lightweight embedding model into WASM.

**Why this wins**:
- The reference example uses simple word overlap — *any* semantic approach is a massive upgrade
- For Tier B intents (LLM-Judge), the scorer that best captures "same meaning, different words" wins
- Judges care about: accuracy of miner rankings + resistance to gaming
- A semantic scorer is harder to game (you can't just keyword-stuff)

**Tech stack**: Rust + a small word2vec or GloVe embedding table (subset of vectors for relevant domains, keep under 32MB WASM limit). Cosine similarity between ground truth and miner answer embeddings.

**Gaming resistance**: Normalize inputs, strip formatting tricks, detect keyword repetition patterns. Add a penalty for answers that are suspiciously similar to the question (copy-paste gaming).

---

### 2. Multi-Signal Composite Scorer

**What**: A scoring module that combines: (1) exact match bonus, (2) key-entity extraction match (numbers, names, dates), (3) structural similarity (JSON schema match for structured intents), and (4) length-penalized word overlap.

**Why this wins**:
- Handles both Tier A (deterministic, exact data) and Tier B (open-ended) intents well
- Entity extraction catches the *important* parts — getting the price right matters more than getting filler words right
- Length penalty prevents verbose padding from inflating scores

**Tech stack**: Rust compiled to WASM. No external dependencies — pure string processing with regex-like pattern matching for entity extraction.

---

### 3. Domain-Adaptive Financial Scorer

**What**: A WASM module specifically tuned for financial intents (CRYPTO_PRICE, STOCK_PRICE, FINANCIAL_DATA). Scores based on: numerical accuracy (within tolerance bands), currency/unit correctness, recency of data, and completeness of response fields.

**Why this wins**:
- Specialized scorers outperform general-purpose ones in specific domains
- Financial data has clear ground truth — numbers either match or they don't
- Tolerance bands (e.g., price within 0.1% = score 0.95, within 1% = score 0.7) create a more useful ranking than binary exact-match
- If you build the best financial scorer, every financial miner's ranking depends on your code

**Tech stack**: Rust, parse JSON responses, extract numerical values, compute relative error. WASM binary stays tiny.

---

## Track 3: Application Ideas (Ranked by Win Probability)

### 1. Autonomous DeFi Risk Monitor — TOP PICK

**What**: An autonomous agent that continuously subscribes to Telegraph's WebSocket signal feed and combines multiple intents to monitor DeFi positions:
- `CRYPTO_PRICE` for real-time price alerts
- `TVL_LOOKUP` for protocol health monitoring
- `SENTIMENT_ANALYSIS` for social signal-based early warnings
- `WALLET_BALANCE_CHECK` for whale movement tracking

When risk thresholds are breached, it can trigger on-chain actions (alerts, position adjustments) via ERC-8183 callbacks.

**Why this wins**:
- **Uses multiple miners across multiple intents** — directly drives the quality flywheel
- **Generates real, sustained request volume** (continuous monitoring = hundreds of requests/day)
- **On-chain integration** shows Telegraph isn't just an API — it's infrastructure
- **Users**: anyone with DeFi positions (easy to onboard via Telegram bot interface)

**Tech stack**: Node.js agent with Telegraph MCP server + WebSocket subscription + Telegram bot for alerts + Base smart contract for ERC-8183 callbacks.

---

### 2. Verifiable News Intelligence Agent

**What**: A Claude/Cursor-integrated agent (via Telegraph MCP) that, when given a claim or news headline:
1. Searches news via `NEWS_SEARCH` / `NEWS_HEADLINES`
2. Cross-references with `FACT_CHECK` intent
3. Checks social sentiment via `SENTIMENT_ANALYSIS` / `TWITTER_SEARCH`
4. Returns a structured verification report with confidence scores

Users interact via a clean web UI or directly through Claude Desktop.

**Why this wins**:
- Uses 4+ intents = massive miner demand generation
- Timely topic (misinformation, AI-generated content)
- Easy to demo and get users (share verifications on X for the engagement score)
- Shows Telegraph as a *trust layer*, not just an API

**Tech stack**: Next.js frontend + Telegraph MCP server backend + Claude API for synthesis.

---

### 3. Smart Contract Security Scanner

**What**: An agent that takes a smart contract address or source code and:
1. Uses `CVE_LOOKUP` to check for known vulnerability patterns
2. Uses `URL_SCAN` to verify related frontend URLs
3. Uses `ONCHAIN_TX_LOOKUP` to analyze transaction patterns for suspicious activity
4. Uses `CONTENT_EXTRACTION` to parse and analyze the contract code
5. Returns a security risk score with actionable findings

**Why this wins**:
- Unique angle — no one else will build a security tool on Telegraph
- High utility for the crypto community
- Uses uncommon intents (drives demand to underserved miners)
- Great X engagement potential (post audit results of popular contracts)

---

### 4. Multi-Chain Portfolio Intelligence Dashboard

**What**: A real-time dashboard that aggregates:
- `CRYPTO_PRICE` for portfolio valuation
- `WALLET_BALANCE_CHECK` for multi-chain balances
- `TOKEN_HOLDER_COUNT` for social proof metrics
- `GAS_PRICE` for optimal transaction timing
- `SENTIMENT_ANALYSIS` for market mood per asset

Connect your wallet, see everything in one place, with all data sourced verifiably through Telegraph.

**Why this wins**:
- Visual, easy to understand, easy to get users
- Consumes 5+ intents continuously
- Natural viral loop (share portfolio insights on X)

**Tech stack**: Next.js + wagmi + Telegraph MCP or direct Engine API calls.

---

### 5. Autonomous Trading Copilot

**What**: A Telegram bot that acts as an AI trading assistant:
- Subscribes to `CRYPTO_PRICE` signals via WebSocket for real-time data
- Uses `SENTIMENT_ANALYSIS` for social alpha
- Uses `NEWS_SEARCH` for breaking news impact assessment
- Sends alerts with suggested actions based on multi-signal analysis

Users interact via Telegram commands. The bot generates the most request volume of any app.

**Why this wins**:
- Massive request volume = satisfies the 100-request guardrail easily
- Telegram bots are easy to distribute (link sharing)
- Trading audience is highly engaged on X
- Clear, measurable usage metrics

---

## Cross-Track Synergy Strategy (The Meta Play)

The hackathon's design **rewards coordination across tracks**. The smartest play:

1. **Build a Miner** (Track 1) for an underserved intent like `SENTIMENT_ANALYSIS`
2. **Build a Scorer** (Track 2) that evaluates that intent well
3. **Build an App** (Track 3) that heavily consumes your own miner

This creates a self-reinforcing loop:
- Your app generates demand → your miner gets requests → your miner scores high
- Your scorer accurately ranks responses → validators use it → your miner benefits from fair scoring
- You hit the 100-request guardrail easily because you control the demand

**Even entering just 2 of 3 tracks dramatically improves your odds.**

---

## X Engagement Strategy (25% of Every Track's Score)

Don't underestimate this. Post consistently:

1. **Day 1**: "Building [X] on @Telegraphprotoc for the hackathon. Here's why..." (vision post)
2. **Every 2-3 days**: Progress updates with screenshots, code snippets, architecture diagrams
3. **Technical deep-dives**: "How I built a semantic WASM scorer in Rust for Telegraph" (these get engagement from devs)
4. **Tag @Telegraphprotoc** on every post
5. **Engage with other builders** — comment on their posts, share insights
6. **Demo day thread**: Full walkthrough of your submission with metrics

---

## Quick-Start Checklist

- [ ] Register at [hackathon.telegraphprotocol.com](https://hackathon.telegraphprotocol.com)
- [ ] Join the Hackathon Discord (required)
- [ ] Clone [Telegraph-MCP](https://github.com/telegraphprotocol/Telegraph-MCP) and run locally
- [ ] Clone [telegraph-examples](https://github.com/telegraphprotocol/telegraph-examples) for reference code
- [ ] Fund a burner wallet with USDC on Base Sepolia (testnet)
- [ ] Check live intents: `curl https://devnode.telegraphprotocol.com/engine/v1/intents`
- [ ] Check live miners: `curl https://devnode.telegraphprotocol.com/api/miners`
- [ ] Pick your track(s) and intent(s)
- [ ] Start posting on X from day 1
