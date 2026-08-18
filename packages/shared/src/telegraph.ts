/**
 * Telegraph Protocol HTTP client.
 *
 * Wraps the Engine API (`/engine/v1/ask`, `/engine/v1/ask/{minerId}`) and the discovery
 * endpoint (`/api/miners`). Payment is delegated to an x402-aware fetch — the caller
 * supplies one (typically from `@x402/fetch`), which handles the 402 challenge and retry
 * transparently.
 *
 * In dev / no-wallet mode, the client falls back to calling a local DegenMiner directly
 * so the UI still renders without a Base Sepolia wallet.
 */

import type {
  TelegraphEngineResponse,
  TelegraphMiner,
} from './types';

export type FetchLike = typeof fetch;

export interface TelegraphClientOptions {
  /** Telegraph node base URL, e.g. `https://devnode.telegraphprotocol.com` */
  nodeUrl: string;
  /** x402-wrapped fetch. Falls back to global fetch (which will 402). */
  paidFetch?: FetchLike;
  /** Direct fallback URL to a local DegenMiner if paid fetch is unavailable. */
  localMinerUrl?: string;
}

export class TelegraphClient {
  private nodeUrl: string;
  private paidFetch: FetchLike;
  private localMinerUrl?: string;

  constructor(opts: TelegraphClientOptions) {
    this.nodeUrl = opts.nodeUrl.replace(/\/$/, '');
    this.paidFetch = opts.paidFetch ?? fetch;
    this.localMinerUrl = opts.localMinerUrl?.replace(/\/$/, '');
  }

  /** Fetch the miner catalog. This endpoint is unpaid. */
  async listMiners(intent?: string): Promise<TelegraphMiner[]> {
    const url = new URL(`${this.nodeUrl}/api/miners`);
    if (intent) url.searchParams.set('intent', intent);
    const res = await fetch(url.toString());
    if (!res.ok) throw new TelegraphError(`listMiners failed: ${res.status}`);
    return (await res.json()) as TelegraphMiner[];
  }

  /** Read the canonical intent set. Unpaid. */
  async listIntents(): Promise<string[]> {
    const res = await fetch(`${this.nodeUrl}/engine/v1/intents`);
    if (!res.ok) throw new TelegraphError(`listIntents failed: ${res.status}`);
    const body = (await res.json()) as { intents?: string[] } | string[];
    return Array.isArray(body) ? body : body.intents ?? [];
  }

  /** Miners serving a specific intent — useful before spending. Unpaid. */
  async minersForIntent(intent: string): Promise<TelegraphMiner[]> {
    const res = await fetch(`${this.nodeUrl}/engine/v1/intents/${intent}/miners`);
    if (!res.ok) throw new TelegraphError(`minersForIntent failed: ${res.status}`);
    const body = (await res.json()) as { miners?: TelegraphMiner[] } | TelegraphMiner[];
    return Array.isArray(body) ? body : body.miners ?? [];
  }

  /** Look up a signal by hash for verification. Unpaid.
   *  Response includes the request/response payload — re-hash it to verify. */
  async getSignal(hash: string): Promise<unknown> {
    const res = await fetch(`${this.nodeUrl}/engine/v1/signal/${hash}`);
    if (!res.ok) throw new TelegraphError(`getSignal failed: ${res.status}`);
    return res.json();
  }

  /** Auto-routed ask. Engine picks the best miner. */
  async ask<T = unknown>(query: string, context?: Record<string, unknown>): Promise<TelegraphEngineResponse<T>> {
    const res = await this.paidFetch(`${this.nodeUrl}/engine/v1/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, context }),
    });
    return this._handle<T>(res);
  }

  /** Direct ask by miner ID. Skips routing. */
  async askDirect<T = unknown>(
    minerId: string | number,
    endpoint: string,
    payload: unknown,
    method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST'
  ): Promise<TelegraphEngineResponse<T>> {
    // Local fallback for hackathon dev without a funded wallet.
    if (this.localMinerUrl && String(minerId) === 'local') {
      const localRes = await fetch(`${this.localMinerUrl}${endpoint}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: method === 'GET' ? undefined : JSON.stringify(payload ?? {}),
      });
      if (!localRes.ok) {
        const text = await localRes.text();
        throw new TelegraphError(`Local miner ${localRes.status}: ${text}`, localRes.status);
      }
      const result = (await localRes.json()) as T;
      return {
        miner_id: 'local',
        miner_name: 'degenminer-local',
        endpoint,
        result,
        cost_usd: 0,
        duration_ms: 0,
        timestamp: new Date().toISOString(),
      };
    }

    const res = await this.paidFetch(`${this.nodeUrl}/engine/v1/ask/${minerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ method, endpoint, payload }),
    });
    return this._handle<T>(res);
  }

  private async _handle<T>(res: Response): Promise<TelegraphEngineResponse<T>> {
    if (res.status === 402) {
      throw new TelegraphError(
        'Payment required — install @x402/fetch and supply a wrapped fetch. See docs.telegraphprotocol.com/docs/using/x402-inference',
        402
      );
    }
    if (!res.ok) {
      const text = await res.text();
      throw new TelegraphError(`Telegraph ${res.status}: ${text}`, res.status);
    }
    return (await res.json()) as TelegraphEngineResponse<T>;
  }
}

export class TelegraphError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'TelegraphError';
    this.status = status;
  }
}
