# DegenLens Web

Next.js 14 dashboard (Track 3). Consumes Telegraph miners for verified gambling intelligence.

## Dev

```bash
# In one terminal: start the miner
cd ../../packages/miner && uvicorn app.main:app --reload --port 8787

# In another: start the web app
pnpm --filter web dev
# → http://localhost:3000
```

By default the app calls `LOCAL_MINER_URL` (default `http://localhost:8787`) so you don't need a funded wallet to develop. To route through the real Telegraph testnet, install `@x402/fetch` and pass a wrapped fetch into the `TelegraphClient` in `src/lib/telegraph.ts`.

## Routes

| Route | Purpose |
|-------|---------|
| `/` | Intelligence overview |
| `/market` | Observed flow by chain and asset |
| `/operators` | Operator directory |
| `/operators/[slug]` | Operator investigation |
| `/flows` | Large transfer feed |
| `/players` | Counterparty evaluation |
| `/wallet` | Wallet trace + anomaly check |
| `/search` | Universal investigation |
| `/ask` | Natural-language queries |
| `/integration` | Telegraph integration status |
| `/api/wallet/trace` | Proxy to DegenMiner |
| `/api/wallet/anomaly` | Proxy to DegenMiner |
| `/api/ask` | Auto-routes natural language to the right miner call |

## Wire in real x402 payments

```typescript
// src/lib/telegraph.ts
import { wrapFetchWithPayment } from '@x402/fetch';
import { createSigner } from '@x402/evm';

const signer = createSigner(process.env.EVM_PRIVATE_KEY!);
const paidFetch = wrapFetchWithPayment(fetch, signer);

export const telegraph = new TelegraphClient({
  nodeUrl: process.env.TELEGRAPH_NODE_URL!,
  paidFetch,
});
```
