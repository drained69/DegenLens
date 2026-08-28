# DegenLens Web

Next.js 14 dashboard for investigating observable on-chain gambling activity.
It consumes DegenMiner locally during development and can use Telegraph x402
calls when configured with an active miner ID.

## Development

From the repository root, install dependencies once:

```bash
pnpm install
python3 -m venv packages/miner/.venv
packages/miner/.venv/bin/pip install -r packages/miner/requirements.txt
```

Start the miner and web app in separate terminals:

```bash
# Terminal 1, from the repository root
packages/miner/.venv/bin/uvicorn \
  --app-dir packages/miner app.main:app --reload --port 8787
```

```bash
# Terminal 2, from the repository root
pnpm --filter web dev
# -> http://localhost:3000
```

The miner works without provider keys and returns labeled demo data. For live
chain data, copy `packages/miner/.env.example` to `packages/miner/.env` and set
`ALCHEMY_KEY`.

## Environment

The local web defaults are:

```bash
TELEGRAPH_MINER_ID=local
LOCAL_MINER_URL=http://localhost:8787
```

For a deployed or separately hosted web app, configure the server environment:

```bash
TELEGRAPH_NODE_URL=https://devnode.telegraphprotocol.com
TELEGRAPH_MINER_ID=<active numeric miner id>
EVM_PRIVATE_KEY=0x<testnet-only-key>
```

`EVM_PRIVATE_KEY` is used only by server-side code for x402 payment retries.
Never expose it through `NEXT_PUBLIC_*`, client components, logs, or committed
files. Use a funded Base Sepolia testnet wallet for development.

With `TELEGRAPH_MINER_ID=local`, the app calls `LOCAL_MINER_URL` directly and
does not require a funded wallet. With a numeric miner ID, the server uses the
Telegraph Engine and x402 payment flow.

## Commands

Run these from the repository root:

```bash
pnpm --filter web lint
pnpm --filter web typecheck
pnpm --filter web build
```

## Routes

| Route | Purpose |
|-------|---------|
| `/` | Intelligence overview |
| `/market` | Observed flow by chain and asset |
| `/operators` | Operator directory |
| `/operators/[slug]` | Operator investigation |
| `/flows` | Large transfer feed |
| `/players` | Counterparty evaluation |
| `/wallet` | Wallet trace and anomaly check |
| `/search` | Universal investigation |
| `/ask` | Natural-language queries |
| `/integration` | Telegraph integration status |
| `/api/wallet/trace` | Proxy to DegenMiner |
| `/api/wallet/anomaly` | Proxy to DegenMiner |
| `/api/ask` | Routes natural language to a miner call |

## x402 integration

The server-side Telegraph client wraps requests with x402 payment handling when
`EVM_PRIVATE_KEY` and a numeric `TELEGRAPH_MINER_ID` are configured. Keep the
signer in server-only modules such as `src/lib/telegraph.ts`; never import it
into a client component or expose it through a `NEXT_PUBLIC_*` variable.
