# DegenLens

**Evidence-backed intelligence for observable on-chain gambling activity.**

DegenLens is a [Telegraph Protocol](https://telegraphprotocol.com) **miner** and
investigation application. The miner exposes reusable transaction, wallet, flow,
and anomaly intelligence; the application packages those capabilities as a
standard operator intelligence product with a broad searchable directory,
market views, wallet and player analysis, transaction investigations, alerts,
and evidence-aware detail pages.

The product distinguishes direct chain observations, deterministic calculations,
and attribution claims. Directional wallet flow is not presented as proof of a
player deposit, casino revenue, solvency, or fraud.

> **Telegraph Hackathon Season I — Miner Track.**
> The miner is the reusable supply layer. The application is its investigation
> client and should use paid Telegraph calls when presented as Track 3 traffic.

---

## Contents

- [What a miner is](#what-a-miner-is)
- [Intent strategy](#intent-strategy)
- [What DegenMiner serves](#what-degenminer-serves)
- [Design decisions that protect the score](#design-decisions-that-protect-the-score)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Registering on Telegraph](#registering-on-telegraph)
- [Proving performance](#proving-performance)
- [License](#license)

---

## What a miner is

Telegraph is a permissionless marketplace for verified AI inference. An agent
sends a question, the Engine's router classifies it into an **intent** and picks
a miner, the miner answers, validators grade that answer inside a WASM scoring
module, and the result is committed on-chain as a `signal_hash`.

Miners are the **supply layer**. A miner doesn't ship a UI or own a customer —
it wraps an API, model, dataset, or tool and answers questions correctly for a
declared set of intents. It gets paid per answer via x402, and the best miner
for each intent takes roughly 70% of that intent's traffic.

DegenMiner wraps Alchemy RPC, a curated casino wallet-cluster registry, and
CoinGecko pricing into structured gambling intelligence.

---

## Intent strategy

The Miner Track scores **75% on normalized Canonical Score** against the best
miner in each intent. But an intent only becomes prize-eligible once it has
**≥3 active miners** and **≥100 requests** from Track 3 applications. That
creates a narrow sweet spot: enough competition to qualify, few enough rivals
to win.

Live network counts when this was written:

| Intent | Miners before us | With DegenMiner | Position |
|---|---|---|---|
| `ONCHAIN_TX_LOOKUP` | 2 | **3** | Hits eligibility exactly; 2 rivals to beat |
| `FRAUD_DETECTION` | 2 | **3** | Hits eligibility exactly; 2 rivals to beat |
| `WALLET_BALANCE_CHECK` | 0 | **1** | Uncontested, not yet prize-eligible |

Re-check before registering — the set moves:

```bash
curl https://devnode.telegraphprotocol.com/engine/v1/intents
```

We deliberately do **not** declare adjacent intents like `TVL_LOOKUP` or
`CRYPTO_PRICE`. Those queries are mostly about DeFi protocols and spot prices,
not casinos; answering them badly would drag the Canonical Score down across
the board. A miner is scored on how well it answers, not how much it claims
to cover.

---

## What DegenMiner serves

| Method | Path | Intent | Returns |
|---|---|---|---|
| POST | `/casino/stats` | `ONCHAIN_TX_LOOKUP` | Deposits, withdrawals, net flow, unique depositors |
| GET | `/casino/ranking` | `ONCHAIN_TX_LOOKUP` | Casinos ranked by observed USD volume + market share |
| POST | `/wallet/trace` | `WALLET_BALANCE_CHECK` | Native balance + casino cluster attribution |
| POST | `/anomaly/check` | `FRAUD_DETECTION` | Wash-trade / velocity / sybil screening with evidence |
| GET | `/casinos` | — | Tracked casino registry |
| GET | `/health` | — | Liveness, readiness, circuit breaker state |
| GET | `/metrics` | — | Uptime, latency percentiles, error rate, cache hit rate |

Every response carries the three fields the YAML's `signal_mapping` declares —
`confidence`, `verdict`, `reasoning` — plus `data_source`.

### Example

```bash
curl -X POST http://localhost:8787/casino/stats \
  -H 'Content-Type: application/json' \
  -d '{"slug":"stake","hours":24}'
```

```json
{
  "slug": "stake",
  "deposits_usd": 2672237.51,
  "withdrawals_usd": 772038.14,
  "net_flow_usd": 1900199.37,
  "unique_depositors": 308,
  "transaction_count": 408,
  "confidence": 0.463,
  "verdict": "healthy",
  "reasoning": "Observed 408 transfers across 2 labeled Stake.com wallets over 24h…",
  "data_source": "demo"
}
```

---

## Design decisions that protect the score

**Determinism.** Python's builtin `hash()` is randomized per process via
`PYTHONHASHSEED`, so anything seeded with it returns a different answer after
every restart. Tier A intents are graded on exact match, so we seed with
`blake2b` (`stable_seed` in [onchain.py](packages/miner/app/onchain.py)) and
quantize the query window to the cache TTL. Two identical requests produce
byte-identical scored payloads; only serve-time metadata moves.

**Both transfer directions.** Alchemy's `getAssetTransfers` filters on a single
address field per call. Querying only `toAddress` silently reports every
withdrawal as zero. We issue both directions concurrently and merge.

**Honest provenance.** Without `ALCHEMY_KEY` the miner cannot observe chain
state. Rather than inventing plausible numbers it labels output
`data_source: "demo"` and halves its confidence. `STRICT_MODE=true` refuses
synthetic answers outright. Serving fabricated figures to the network gets them
graded against real ground truth — the fastest way to lose.

**Never 5xx.** Middleware converts any unhandled exception into a 200 carrying
`verdict: "unavailable"` and `confidence: 0.0`. From the node's perspective a
throw is a failed answer; a low-confidence honest answer is strictly better.

**Bounded latency.** Token prices resolve in one batched call per request
instead of one per transfer, wallet aggregation runs under `asyncio.gather`, and
a semaphore caps upstream concurrency so a burst can't trip Alchemy's own rate
limiter. A circuit breaker opens after 5 consecutive upstream failures.

---

## Repository layout

```
telegraph/
├── packages/
│   └── miner/              # ◀ THE SUBMISSION — FastAPI Telegraph miner
│       ├── app/
│       │   ├── main.py       # endpoints, middleware, never-5xx contract
│       │   ├── onchain.py    # RPC adapters, determinism, circuit breaker
│       │   ├── analytics.py  # aggregation + fraud detection
│       │   ├── prices.py     # batched USD normalization
│       │   ├── wallets.py    # labeled casino cluster registry
│       │   └── metrics.py    # uptime / latency / error observability
│       └── tests/            # 15 tests
├── config/
│   └── miner.yaml          # ◀ Telegraph registration manifest
├── apps/
│   └── web/                # Product client — directory, market, investigations
├── packages/
│   ├── shared/             # TS types + Telegraph client
│   └── scorer/             # Rust→WASM scorer (not submitted; kept for reference)
```

---

## Quick start

**Prerequisites:** Python 3.11+, Node.js 20+ (the `@x402/*` packages need
WebCrypto, which Node 18 doesn't expose), pnpm.

```bash
# 1. Miner
cd packages/miner
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8787
# → http://localhost:8787/docs
```

Runs with no API keys — you get the labeled demo feed. Add `ALCHEMY_KEY` and
`COINGECKO_KEY` to `.env` for live chain data.

```bash
# 2. Tests
.venv/bin/python -m pytest tests/ -q --asyncio-mode=auto

# 3. Demo dashboard (optional, from repo root)
pnpm install
pnpm --filter web dev
# → http://localhost:3000
```

---

## Registering on Telegraph

1. Deploy the miner to a public HTTPS URL. **Already live on Railway:**

   ```
   https://degenminer-production.up.railway.app
   ```

   To redeploy after changes:

   ```bash
   cd packages/miner
   railway up
   ```

   To serve live chain data instead of the labeled demo feed:

   ```bash
   railway variables --set ALCHEMY_KEY=... --set COINGECKO_KEY=... --set STRICT_MODE=true
   ```

2. `base_url` in [`config/miner.yaml`](config/miner.yaml) already points at the
   deployed URL.

3. Go to [integrate.telegraphprotocol.com](https://integrate.telegraphprotocol.com),
   paste the YAML, and let it sandbox-test every declared endpoint against your
   live service.

4. On pass it registers the miner on-chain (gas only — no bond).

Registration is immutable. Validate before you submit.

---

## Proving performance

`GET /metrics` is the evidence for the 75% performance component:

```json
{
  "uptime_seconds": 604800.0,
  "total_requests": 14203,
  "error_rate": 0.0,
  "latency_p50_ms": 0.9,
  "latency_p95_ms": 783.33,
  "cache_hit_rate": 0.57,
  "upstream_failure_rate": 0.0
}
```

Capture it alongside your Canonical Score ranking at submission time.

---

## License

MIT
