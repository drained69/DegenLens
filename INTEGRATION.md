# Registering DegenMiner on Telegraph

Everything needed to submit at
[integrate.telegraphprotocol.com](https://integrate.telegraphprotocol.com).

**Registration writes a public YAML commitment to the registry contract.** A
bad registration can be replaced with `updateMiner`, but that creates a new
registration ID and intent ID, so existing targeted integrations must migrate.
Run the preflight before registering or updating.

---

## 1. Preflight

```bash
packages/miner/.venv/bin/python scripts/preflight.py
```

This validates the manifest schema, the Base Sepolia on-chain intent registry,
the live endpoint contract, and the reliability contract. It also checks the
console validator separately so a Telegraph backend registry mismatch is visible
before you attempt upload.

It must print **Ready to submit** with zero blocking issues. Warnings are
usually latency — an endpoint slower than ~10s risks a node timeout, and a
timeout counts as a failed answer against the Canonical Score.

The preflight also checks the console's remote `/api/validate` endpoint. If it
reports that the three intents below are non-canonical while the on-chain checks
return `true`, the console backend is out of sync with the Base Sepolia registry.
That is a Telegraph infrastructure issue and cannot be corrected by editing the
miner YAML. Confirm the split with:

```bash
packages/miner/.venv/bin/python scripts/preflight.py --skip-live
```

Do not replace these intents with `WEATHER_CHECK`, `CHAT_COMPLETION`, or another
unrelated intent merely to bypass the stale validator. That would misrepresent
the miner's capability and reduce routing quality. Use the manual registration
flow below until Telegraph synchronizes the console validator. The repository
preflight reports this as a warning because the on-chain contract check is
authoritative; the console compatibility warning is not a YAML or miner API
failure. The browser upload itself remains unavailable until Telegraph fixes its
validator backend, so use the manual registration flow below.

## 2. Submission artifact

The single file you paste is [`config/miner.yaml`](config/miner.yaml).

| Field | Value | Why |
|---|---|---|
| `base_url` | `https://degenlensv1.up.railway.app` | The combined production endpoint serving both the DegenLens website and miner API. |
| hosted YAML | `https://degenlensv1.up.railway.app/miner.yaml` | Stable HTTPS copy of the exact manifest bytes used for the SHA-256 commitment. |
| `auth.type` | `none` | The API is public. Declaring this explicitly stops the node injecting auth headers. |
| `on_chain` | *omitted* | This miner serves pure HTTP inference and does not publish into ERC-8183 jobs. Note a *partial* block is invalid — `on_chain.transform` is mandatory once the block exists — so it is all or nothing. Floor price is set in the registration transaction instead. |
| `semantics.signal_mapping` | `confidence` / `verdict` / `reasoning` | Every endpoint returns all three. |

The manifest's three intents were checked directly against the Base Sepolia
registry contract. The contract is authoritative for registration; the node
catalog is only operational metadata. Do not rely on historical miner-count
tables because counts change as miners activate or leave.

## 3. Flow at integrate.telegraphprotocol.com

1. **Connect API → Miner → Continue**.
2. **Import & Upload** — paste the complete `config/miner.yaml`. The console
   validates the closed YAML schema, tests every endpoint, and pins the exact
   file bytes to IPFS.
3. **Register** — connect the wallet that will own the slug and submit on Base
   Sepolia. You need Base Sepolia ETH for gas and a non-zero EVM fee address;
   the current minimum floor is `0.01 USDC` (`10000` base units). There is no
   miner bond.

For manual Foundry registration, the deployment now hosts the exact YAML bytes
at `https://degenlensv1.up.railway.app/miner.yaml`. Verify the hosted hash before
registration and use that URL in the transaction:

```bash
export DIAMOND=0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8
export RPC=https://base-sepolia.g.alchemy.com/v2/<ALCHEMY_KEY>
export YAML_URL=https://degenlensv1.up.railway.app/miner.yaml
export YAML_HASH=0x<sha256-of-exact-yaml-bytes>
export FEE_ADDRESS=0x<fee-recipient>
export MINER_PRIVATE_KEY=0x<testnet-only-key>

cast send "$DIAMOND" \
  "registerMiner(string,bytes32,address,uint256,string[])" \
  "$YAML_URL" "$YAML_HASH" "$FEE_ADDRESS" 10000 \
  '["ONCHAIN_TX_LOOKUP","WALLET_BALANCE_CHECK","FRAUD_DETECTION"]' \
  --rpc-url "$RPC" --private-key "$MINER_PRIVATE_KEY"
```

The manual flow is the correct workaround when the console says
`non-canonical intent` for an intent that the contract accepts. The contract
check is authoritative and the transaction uses the same Diamond address as
the console.

The current console validator can be reproduced without a browser:

```bash
YAML=$(python3 -c 'import json; print(json.dumps(open("config/miner.yaml").read()))')
curl -s -X POST https://integrate.telegraphprotocol.com/api/validate \
  -H 'Content-Type: application/json' \
  --data "{\"yaml\":$YAML,\"api_key\":\"\"}"
```

At present, this endpoint incorrectly returns `non-canonical intent` for the
three intents even though the Base Sepolia contract returns `true`. Save the
response when contacting Telegraph support or retrying after the console is
fixed. Do not spend gas until the YAML is hosted and its hash is final.

Verify canonical intents before sending:

```bash
curl -s https://devnode.telegraphprotocol.com/engine/v1/intents
```

For the authoritative on-chain check, use a Base Sepolia RPC and the registry
contract:

```bash
export RPC=https://base-sepolia-rpc.publicnode.com
export DIAMOND=0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8
cast call "$DIAMOND" 'isCanonicalIntent(string)(bool)' ONCHAIN_TX_LOOKUP --rpc-url "$RPC"
cast call "$DIAMOND" 'isCanonicalIntent(string)(bool)' WALLET_BALANCE_CHECK --rpc-url "$RPC"
cast call "$DIAMOND" 'isCanonicalIntent(string)(bool)' FRAUD_DETECTION --rpc-url "$RPC"
```

Each command must return `true`. If the integration console reports these
exact intents as non-canonical while the commands return `true`, the console
has a stale node/registry cache or is connected to the wrong network. Refresh
the console, reconnect the wallet on Base Sepolia, and retry. Do not replace
these intents with unrelated names: doing so would make the registration
semantically incorrect and can cause the on-chain transaction to revert.

On macOS, verify the hosted bytes match the committed file:

```bash
shasum -a 256 config/miner.yaml | awk '{print "0x"$1}'
curl -s "$YAML_URL" | shasum -a 256
```

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

## 4a. Real x402-to-settlement test

Run this only after the miner is active in `GET /api/miners` and the production
URL passes preflight. The script checks the active numeric miner, the declared
endpoint, and the payer's Base Sepolia USDC balance before it sends a paid
request. It prints the `PAYMENT-RESPONSE`, `signal_hash`, and signal lookup so
the call can be independently audited.

```bash
export TELEGRAPH_NODE_URL=https://devnode.telegraphprotocol.com
export TELEGRAPH_MINER_ID=<active numeric miner id>
export EVM_PRIVATE_KEY=0x<testnet-only-key>
export BASE_SEPOLIA_RPC_URL=https://base-sepolia.g.alchemy.com/v2/<ALCHEMY_KEY>
pnpm e2e:x402
```

The wallet must hold at least `0.01` Base Sepolia USDC and enough Base Sepolia
ETH for any wallet-side operation required by the x402 SDK. The node's 402
challenge is authoritative for the amount and `payTo`; the script never
hardcodes either value. Never use a production-funded key for this test.

Adjacent intents like `TVL_LOOKUP` and `CRYPTO_PRICE` are deliberately **not**
declared. Those queries are mostly about DeFi protocols and spot prices rather
than gambling; answering them badly would depress the Canonical Score across
every intent this miner serves.

## 5. After registering

Read `registrationId` from the `MinerRegistered` receipt and inspect activation
directly. Rejected registrations are absent from the loaded catalog:

```bash
curl -s "https://devnode.telegraphprotocol.com/api/miners/<registrationId>" \
  | jq '.miner | {activation_status, rejection_reason, retrying, fetch_attempts}'
curl -s https://devnode.telegraphprotocol.com/api/miners \
  | jq '.[] | select(.slug == "degenlens-onchain")'
```

After status is `active`, install any upstream API key through the node wallet
challenge flow. Never put provider keys in this YAML. Set `TELEGRAPH_MINER_ID`
to the active numeric ID in Railway and redeploy; production uses no local
fallback when this variable is missing and rejects non-numeric values.

The 75% performance component is measured, so keep the service up and let the
app drive real traffic. `GET /metrics` is the evidence:

```bash
curl https://degenlensv1.up.railway.app/metrics
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
