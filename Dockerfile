FROM node:20-slim AS build

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip && rm -rf /var/lib/apt/lists/* \
  && corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/shared/package.json packages/shared/package.json

RUN pnpm install --frozen-lockfile

COPY apps/web apps/web
COPY packages/shared packages/shared
COPY config/miner.yaml config/miner.yaml
COPY config/miner.yaml apps/web/config/miner.yaml
COPY packages/miner/requirements.txt packages/miner/requirements.txt
COPY packages/miner/app packages/miner/app

RUN pip3 install --break-system-packages --no-cache-dir -r packages/miner/requirements.txt \
  && pnpm --filter web build

ENV NODE_ENV=production \
    PORT=8080 \
    LOCAL_MINER_URL=http://127.0.0.1:8787 \
    STRICT_MODE=true \
    MAX_UPSTREAM_CONCURRENCY=4 \
    REQUEST_TIMEOUT_S=20

EXPOSE 8080

CMD ["sh", "-c", "python3 -m uvicorn app.main:app --app-dir packages/miner --host 127.0.0.1 --port 8787 & exec pnpm --filter web start --hostname 0.0.0.0 --port ${PORT:-8080}"]
