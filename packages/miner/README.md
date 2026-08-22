# DegenMiner

**Telegraph Miner Track entry — on-chain gambling intelligence.**

DegenMiner is the supply layer for crypto gambling data on Telegraph. It observes
labeled casino wallet clusters across EVM chains and answers three canonical
intents: deposit/withdrawal flow, wallet attribution, and fraud screening.

---

## Intent strategy

The Miner Track scores 75% on normalized Canonical Score against the best miner
in each intent — but an intent only becomes prize-eligible once it has **≥3
active miners** and **≥100 requests** from Track 3 applications. That creates a
sweet spot: enough competition to qualify, few enough rivals to win.

Live network counts at time of writing:

| Intent | Miners before us | With DegenMiner | Position |
|---|---|---|---|
| `ONCHAIN_TX_LOOKUP` | 2 | **3** | Hits eligibility exactly; 2 rivals to beat |
| `FRAUD_DETECTION` | 2 | **3** | Hits eligibility exactly; 2 rivals to beat |
| `WALLET_BALANCE_CHECK` | 0 | **1** | Uncontested, but not yet prize-eligible |

Re-check before registering — the set moves:

```bash
curl https://devnode.telegraphprotocol.com/engine/v1/intents
```

We deliberately do **not** declare adjacent intents like `TVL_LOOKUP` or
`CRYPTO_PRICE`. Those queries are mostly about DeFi protocols and spot prices,
not casinos; answering them badly would drag the Canonical Score down. A miner
is scored on how well it answers, not how much it claims to cover.

---

## What it serves

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

---

## Design decisions that protect the score

**Determinism.** Python's builtin `hash()` is randomized per process via
`PYTHONHASHSEED`, so anything seeded with it returns different answers after a
restart. Tier A intents are graded on exact match, so we seed with `blake2b`
instead (`stable_seed` in [onchain.py](app/onchain.py)) and quantize the query
window to the cache TTL. Two identical requests produce byte-identical scored
payloads; only the `served_at` metadata field moves.

**Both transfer directions.** Alchemy's `getAssetTransfers` filters by a single
address field per call. Querying only `toAddress` silently reports every
withdrawal as zero. We issue both directions concurrently and merge.

**Honest provenance.** Without `ALCHEMY_KEY` the miner cannot observe chain
state. Rather than inventing plausible numbers, it labels output
`data_source: "demo"` and halves its confidence. Set `STRICT_MODE=true` to
refuse synthetic answers outright. Serving fabricated figures to the network
gets them graded against real ground truth — the fastest way to lose.

**Never 5xx.** A middleware converts any unhandled exception into a 200 with
`verdict: "unavailable"` and `confidence: 0.0`. From the node's perspective a
throw is a failed answer; a low-confidence honest answer is strictly better.

**Bounded latency.** Token prices resolve in one batched call per request
instead of one per transfer, wallet aggregation runs under `asyncio.gather`, and
a semaphore caps upstream concurrency so a burst cannot trip Alchemy's own rate
limiter. A circuit breaker opens after 5 consecutive upstream failures.

---

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8787
```

Open http://localhost:8787/docs. Works with no API keys — you get the labeled
demo feed.

## Test

```bash
.venv/bin/python -m pytest tests/ -q --asyncio-mode=auto
```

15 tests covering determinism, both-direction correctness, provenance honesty,
signal-mapping contract compliance, and never-5xx behavior.

## Deploy

The registered combined Railway deployment is:
**https://degenlensv1.up.railway.app**

```bash
railway up                      # redeploy
railway logs                    # runtime logs
railway variables --set ALCHEMY_KEY=...   # switch to live chain data
```

Deployment notes worth keeping:

- Railway executes `startCommand` **without a shell**, so `$PORT` is not
  expanded there. Port binding is handled by the Dockerfile `CMD`, which wraps
  the command in `sh -c`. Do not add a `startCommand` referencing `$PORT`.
- Runs a **single** uvicorn worker on purpose: the metrics ring buffer, response
  cache, and circuit breaker are per-process state, so multiple workers would
  report split metrics and duplicate caches.

## Register on Telegraph

1. Deploy to a public HTTPS URL (done — see above).
2. `base_url` in [`config/miner.yaml`](../../config/miner.yaml) already points at it.
3. Go to [integrate.telegraphprotocol.com](https://integrate.telegraphprotocol.com),
   paste the YAML, and let it sandbox-test every declared endpoint.
4. On pass, it registers the miner on-chain (gas only, no bond).

Registration is immutable — validate before you submit.

---

## Proving performance at submission

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

Screenshot this alongside your Canonical Score ranking.
