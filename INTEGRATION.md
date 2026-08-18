# Registering DegenMiner on Telegraph

Everything needed to submit at
[integrate.telegraphprotocol.com](https://integrate.telegraphprotocol.com).

**Registration writes to the registry contract and is immutable.** A wrong
`base_url`, a failing endpoint, or a broken response contract cannot be edited
afterwards — only re-registered as a separate miner. Run the preflight first.

---

## 1. Preflight

```bash
packages/miner/.venv/bin/python scripts/preflight.py
```

This mirrors what the sandbox validator does: manifest schema, then a live call
to every declared endpoint against `base_url`, then the reliability contract.

It must print **Ready to submit** with zero blocking issues. Warnings are
usually latency — an endpoint slower than ~10s risks a node timeout, and a
timeout counts as a failed answer against the Canonical Score.

## 2. Submission artifact

The single file you paste is [`config/miner.yaml`](config/miner.yaml).

| Field | Value | Why |
|---|---|---|
| `base_url` | `https://degenminer-production.up.railway.app` | The **production API endpoint** Telegraph routes to — not the project website. The site belongs under `docs.website`. |
| `auth.type` | `none` | The API is public. Declaring this explicitly stops the node injecting auth headers. |
| `on_chain` | *omitted* | This miner serves pure HTTP inference and does not publish into ERC-8183 jobs. Note a *partial* block is invalid — `on_chain.transform` is mandatory once the block exists — so it is all or nothing. Floor price is set in the registration transaction instead. |
| `semantics.signal_mapping` | `confidence` / `verdict` / `reasoning` | Every endpoint returns all three. |

## 3. Flow at integrate.telegraphprotocol.com

1. **Connect API → Miner → Continue**
2. **Import & Upload** — paste `config/miner.yaml`. It parses the values and
   pins to IPFS via Pinata.
3. **Register** — submit the IPFS hash to the registry contract on Base
   Sepolia. Gas only, no bond.

## 4. Declared intents

| Intent | Miners before us | Why this one |
|---|---|---|
| `ONCHAIN_TX_LOOKUP` | 2 | Registering makes 3 — exactly the eligibility threshold, with only 2 rivals |
| `FRAUD_DETECTION` | 2 | Same position |
| `WALLET_BALANCE_CHECK` | 0 | Uncontested, but stays prize-ineligible until two others join |

Re-check before submitting, the set moves:

```bash
curl https://devnode.telegraphprotocol.com/engine/v1/intents
```

Adjacent intents like `TVL_LOOKUP` and `CRYPTO_PRICE` are deliberately **not**
declared. Those queries are mostly about DeFi protocols and spot prices rather
than gambling; answering them badly would depress the Canonical Score across
every intent this miner serves.

## 5. After registering

The 75% performance component is measured, so keep the service up and let the
app drive real traffic. `GET /metrics` is the evidence:

```bash
curl https://degenminer-production.up.railway.app/metrics
```

Capture it alongside the Canonical Score ranking at submission time.

---

## Known limits — state these rather than let a judge find them

- **Attribution coverage is the binding constraint.** 5 of 55 catalogued
  operators have reviewed wallet claims, 6 clusters, one chain. Every figure is
  scoped to that, and the `/coverage` endpoint publishes it.
- **Wallet labels are `unverified_seed`.** Confidence is capped at 0.55 by the
  registry and cannot be raised by fresh chain data — only by a human attaching
  a source. `/attribution/discover` proposes candidates to close this gap, but
  never writes to the registry itself.
- **Multi-chain is wired but unused.** Seven chains are configured; no
  attributed wallet exists outside Ethereum yet.
- **30-day windows may be partial.** `coverage_complete: false` marks a window
  the page budget could not fully traverse; totals are then lower bounds.
