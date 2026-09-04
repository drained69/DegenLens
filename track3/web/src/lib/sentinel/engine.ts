import type {
  AnomalyReport,
  Casino,
  CasinoRegistry,
  CasinoStats,
  TransactionLookup,
} from '@degenlens/shared';
import { telegraph, telegraphMinerId, telegraphPaymentConfigured } from '@/lib/telegraph';
import {
  detectFindings,
  detectWalletFindings,
  maxSeverity,
  toSnapshot,
} from './detect';
import { sentinelStore } from './store';
import type {
  EscalationStep,
  Finding,
  FraudScreen,
  Receipt,
  ScanRecord,
  ScanTrigger,
  SentinelAlert,
  TxEvidence,
  WalletWatchRow,
} from './types';

/**
 * Sentinel — DegenLens' autonomous watch agent (Telegraph Track 3 layer).
 *
 * Scan phases, each recorded as receipts:
 *
 *  1. Discover (local):   operator registry + per-operator flow stats from the
 *                         co-located DegenMiner.
 *  2. Watch (PAID):       rotating hot-wallet balances through the engine via
 *                         the miner's declared `/wallet/balance` endpoint —
 *                         every check is a paid WALLET_BALANCE_CHECK request.
 *  3. Detect:             bankrun-shaped rules over observed flow and balance
 *                         deltas. Findings carry measurements, not verdicts.
 *  4. Enrich (PAID):      for alerting operators, a FRAUD_DETECTION screen on
 *                         the moved wallet, then ONCHAIN_TX_LOOKUP on the tx
 *                         hashes the screen cites as evidence.
 *  5. Escalate (PAID):    compose OTHER miners through the engine's auto-router
 *                         — news, community, price, chained sentiment, fact
 *                         check.
 *  6. Report:             alerts land on /sentinel with the full trail and are
 *                         delivered to Telegram when configured.
 */

const CALL_TIMEOUT_MS = 45_000;
const WATCH_ROLES = new Set(['hot', 'treasury']);

export interface SentinelConfig {
  enabled: boolean;
  intervalMinutes: number;
  windowHours: number;
  floorUsd: number;
  cooldownMinutes: number;
  maxOperators: number;
  maxWallets: number;
  balanceFloor: number;
  maxFraudScreens: number;
  maxTxLookups: number;
  maxEscalations: number;
  escalate: 'auto' | 'always' | 'never';
}

function num(name: string, fallback: number): number {
  const raw = Number(process.env[name]);
  return Number.isFinite(raw) && raw >= 0 ? raw : fallback;
}

export function sentinelConfig(): SentinelConfig {
  const escalateRaw = process.env.SENTINEL_ESCALATE;
  return {
    enabled: process.env.SENTINEL_ENABLED !== 'false',
    intervalMinutes: num('SENTINEL_INTERVAL_MINUTES', 30),
    windowHours: num('SENTINEL_WINDOW_HOURS', 24),
    floorUsd: num('SENTINEL_MIN_USD', 5_000),
    cooldownMinutes: num('SENTINEL_COOLDOWN_MINUTES', 120),
    maxOperators: Math.max(1, num('SENTINEL_MAX_OPERATORS', 6)),
    maxWallets: Math.max(1, num('SENTINEL_MAX_WALLETS', 8)),
    balanceFloor: num('SENTINEL_BALANCE_FLOOR', 5),
    maxFraudScreens: Math.max(0, num('SENTINEL_MAX_FRAUD_SCREENS', 1)),
    maxTxLookups: Math.max(0, num('SENTINEL_MAX_TX_LOOKUPS', 2)),
    maxEscalations: Math.max(0, num('SENTINEL_MAX_ESCALATIONS', 2)),
    escalate:
      escalateRaw === 'always' || escalateRaw === 'never' ? escalateRaw : 'auto',
  };
}

// ── Paid-call plumbing ───────────────────────────────────────────────────────

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`timed out after ${ms}ms`)), ms),
    ),
  ]);
}

function extractAnswer(result: unknown): string | undefined {
  if (result == null) return undefined;
  if (typeof result === 'string') return result.slice(0, 600) || undefined;
  if (Array.isArray(result)) {
    const parts = result
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') {
          const r = item as Record<string, unknown>;
          const title = r.title ?? r.headline ?? r.text ?? r.answer;
          return typeof title === 'string' ? title : undefined;
        }
        return undefined;
      })
      .filter((x): x is string => Boolean(x))
      .slice(0, 6);
    return parts.length ? parts.join(' | ').slice(0, 600) : undefined;
  }
  if (typeof result === 'object') {
    const r = result as Record<string, unknown>;
    // OpenAI-shaped chat miners.
    const choices = r.choices;
    if (Array.isArray(choices) && choices.length > 0) {
      const msg = (choices[0] as Record<string, unknown>)?.message as
        | Record<string, unknown>
        | undefined;
      const content = msg?.content ?? (choices[0] as Record<string, unknown>)?.text;
      if (typeof content === 'string' && content.trim()) return content.slice(0, 600);
    }
    // Search-shaped miners (Tavily etc.): answer may be null with a results list.
    const results = r.results;
    if (Array.isArray(results) && results.length > 0) {
      const parts = results
        .slice(0, 4)
        .map((item) => {
          if (item && typeof item === 'object') {
            const rr = item as Record<string, unknown>;
            const title = typeof rr.title === 'string' ? rr.title : '';
            const content =
              typeof rr.content === 'string' ? rr.content.slice(0, 200) : '';
            return [title, content].filter(Boolean).join(' — ');
          }
          return undefined;
        })
        .filter((x): x is string => Boolean(x));
      if (parts.length) return parts.join(' | ').slice(0, 600);
    }
    // Common structured miner shapes.
    for (const key of ['answer', 'ai_response', 'text', 'summary', 'content', 'result']) {
      const v = r[key];
      if (typeof v === 'string' && v.trim()) return v.slice(0, 600);
    }
    if (typeof r.price_usd === 'number') {
      return `price_usd: ${r.price_usd}`;
    }
    return JSON.stringify(result).slice(0, 400);
  }
  return String(result).slice(0, 400);
}

interface BalanceResult {
  address?: string;
  chain?: string;
  block_number?: number | null;
  native_symbol?: string | null;
  native_balance?: number | null;
  balance_native?: number | null;
  balance_status?: string | null;
  verdict?: string;
  confidence?: number;
  reasoning?: string;
  data_source?: string;
}

function isTransientError(error: string): boolean {
  return (
    error.includes('timed out') ||
    error.includes('500') ||
    error.includes('502') ||
    error.includes('503') ||
    error.includes('routing failed') ||
    error.includes('fetch failed')
  );
}

async function engineCall<T>(
  purpose: Receipt['purpose'],
  endpoint: string,
  payload: Record<string, unknown>,
  method: 'GET' | 'POST',
  intent: string,
): Promise<{ ok: true; result: T } | { ok: false; error: string }> {
  const mode: Receipt['mode'] =
    String(telegraphMinerId) === 'local' ? 'local' : 'engine-direct';
  // The engine intermittently stalls or 5xxs under load; one retry with
  // backoff recovers most transient failures without masking real ones.
  const maxAttempts = 2;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const ts = new Date().toISOString();
    try {
      const res = await withTimeout(
        telegraph.askDirect<T>(telegraphMinerId, endpoint, payload, method),
        CALL_TIMEOUT_MS,
      );
      await sentinelStore.pushReceipt({
        ts,
        purpose,
        mode,
        endpoint,
        intent,
        miner_id: String(res.miner_id),
        miner_name: res.miner_name,
        cost_usd: res.cost_usd ?? 0,
        duration_ms: res.duration_ms,
        signal_hash: res.signal_hash,
        ok: true,
      });
      return { ok: true, result: res.result };
    } catch (err) {
      const error = err instanceof Error ? err.message : String(err);
      if (attempt < maxAttempts && isTransientError(error)) {
        await new Promise((r) => setTimeout(r, 1500 * attempt));
        continue;
      }
      await sentinelStore.pushReceipt({
        ts,
        purpose,
        mode,
        endpoint,
        intent,
        miner_id: String(telegraphMinerId),
        cost_usd: 0,
        ok: false,
        error,
      });
      return { ok: false, error };
    }
  }
  // Unreachable — the loop always returns.
  return { ok: false, error: 'exhausted retries' };
}

/** Local, unpaid call to the co-located miner (undeclared convenience endpoints). */
async function localCall<T>(
  purpose: Receipt['purpose'],
  endpoint: string,
  payload: Record<string, unknown>,
  method: 'GET' | 'POST',
  intent: string,
): Promise<{ ok: true; result: T } | { ok: false; error: string }> {
  try {
    const res = await withTimeout(
      telegraph.askDirect<T>('local', endpoint, payload, method),
      CALL_TIMEOUT_MS,
    );
    await sentinelStore.pushReceipt({
      ts: new Date().toISOString(),
      purpose,
      mode: 'local',
      endpoint,
      intent,
      miner_id: 'local',
      cost_usd: 0,
      ok: true,
    });
    return { ok: true, result: res.result };
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    return { ok: false, error };
  }
}

async function routedAsk(
  purpose: Receipt['purpose'],
  query: string,
): Promise<EscalationStep> {
  const step: EscalationStep = { step: '', query, cost_usd: 0, ok: false };
  // The auto-router intermittently 500s or times out under load; one retry
  // with backoff recovers most transient failures without masking real ones.
  const maxAttempts = 2;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const res = await withTimeout(
        telegraph.ask<unknown>(query),
        CALL_TIMEOUT_MS,
      );
      step.ok = true;
      step.answer = extractAnswer(res.result);
      step.intent = res.intent;
      step.miner_id = String(res.miner_id);
      step.miner_name = res.miner_name;
      step.cost_usd = res.cost_usd ?? 0;
      step.duration_ms = res.duration_ms;
      step.signal_hash = res.signal_hash;
      break;
    } catch (err) {
      step.error = err instanceof Error ? err.message : String(err);
      if (attempt < maxAttempts && isTransientError(step.error)) {
        await new Promise((r) => setTimeout(r, 1500 * attempt));
        continue;
      }
    }
  }
  await sentinelStore.pushReceipt({
    ts: new Date().toISOString(),
    purpose,
    mode: 'engine-routed',
    query,
    intent: step.intent,
    miner_id: step.miner_id ?? 'router',
    miner_name: step.miner_name,
    cost_usd: step.cost_usd,
    duration_ms: step.duration_ms,
    signal_hash: step.signal_hash,
    ok: step.ok,
    error: step.error,
  });
  return step;
}

// ── Watch list ───────────────────────────────────────────────────────────────

interface WatchEntry {
  operator: Casino;
  address: string;
  chain: string;
  role: string;
}

function buildWatchList(casinos: Casino[]): WatchEntry[] {
  const list: WatchEntry[] = [];
  for (const casino of casinos) {
    for (const w of casino.wallets ?? []) {
      if (WATCH_ROLES.has(w.role)) {
        list.push({ operator: casino, address: w.address, chain: w.chain, role: w.role });
      }
    }
  }
  // Stable order so rotation covers the whole list over successive scans.
  list.sort((a, b) =>
    `${a.operator.slug}:${a.address}`.localeCompare(`${b.operator.slug}:${b.address}`),
  );
  return list;
}

// ── Enrichment: fraud screens and tx lookups (paid) ─────────────────────────

async function fraudScreen(
  target: Pick<WalletWatchRow, 'address' | 'chain'>,
): Promise<FraudScreen> {
  const query =
    `How likely is ${target.address} on ${target.chain} to be showing fraudulent ` +
    `or anomalous transaction activity in the last 24 hours?`;
  const res = await engineCall<AnomalyReport & { signals?: string[] }>(
    'fraud',
    '/anomaly/check',
    { query, address: target.address, chain: target.chain, hours: 24 },
    'POST',
    'FRAUD_DETECTION',
  );
  if (!res.ok) {
    return { address: target.address, chain: target.chain, ok: false, error: res.error };
  }
  return {
    address: target.address,
    chain: target.chain,
    ok: true,
    risk_tier: res.result.risk_tier ?? res.result.verdict,
    risk_score: res.result.risk_score ?? res.result.score,
    reasoning: res.result.reasoning,
    signals: res.result.signals ?? [],
    signal_count: res.result.signal_count,
  };
}

function evidenceTxHashes(screen: FraudScreen): string[] {
  // Wash-trade round-trip signals cite tx hashes; mine them for paid lookups.
  const text = [screen.reasoning ?? '', ...(screen.signals ?? [])].join(' ');
  const hashes = text.match(/0x[a-fA-F0-9]{64}/g) ?? [];
  return [...new Set(hashes)].slice(0, 3);
}

/** Largest recent observed transfers for an operator, from the free local feed. */
async function largestTransferHashes(
  slug: string,
  windowHours: number,
): Promise<{ txHash: string; chain: string }[]> {
  const res = await localCall<{
    transfers?: { tx_hash: string; chain: string; operator_slug: string; usd_value: number }[];
    pending_first_read?: boolean;
    data_source?: string;
  }>(
    'txlookup',
    `/market/large-transfers?hours=${windowHours}&min_usd=25000&limit=50`,
    {},
    'GET',
    'ONCHAIN_TX_LOOKUP',
  );
  if (!res.ok) return [];
  const rows = (res.result.transfers ?? [])
    .filter((t) => t.operator_slug === slug)
    .sort((a, b) => b.usd_value - a.usd_value)
    .slice(0, 2);
  return rows.map((t) => ({ txHash: t.tx_hash, chain: t.chain }));
}

async function txLookup(txHash: string, chain: string): Promise<TxEvidence> {
  const query = `Did transaction ${txHash} succeed on ${chain}, and what did it move?`;
  const res = await engineCall<TransactionLookup>(
    'txlookup',
    '/transaction/lookup',
    { query, tx_hash: txHash, chain },
    'POST',
    'ONCHAIN_TX_LOOKUP',
  );
  if (!res.ok) {
    return { tx_hash: txHash, chain, ok: false, error: res.error };
  }
  return {
    tx_hash: txHash,
    chain,
    ok: true,
    status: res.result.status,
    from_address: res.result.from_address,
    to_address: res.result.to_address ?? undefined,
    value_native: res.result.value_native,
    reasoning: res.result.reasoning,
  };
}

// ── Escalation: compose other miners on the network ─────────────────────────

async function escalate(alert: SentinelAlert): Promise<EscalationStep[]> {
  const name = alert.operator_name;
  const steps: EscalationStep[] = [];

  const news = await routedAsk(
    'escalation',
    `Search recent news articles about ${name}, a crypto casino, covering withdrawal delays, insolvency, license problems, or payout freezes from the past 30 days.`,
  );
  news.step = 'news_search';
  steps.push(news);

  const community = await routedAsk(
    'escalation',
    `Search the web for recent player complaints about ${name} crypto casino: withdrawal problems, delayed payouts, frozen accounts, or exit scam reports on Reddit and X.`,
  );
  community.step = 'community_search';
  steps.push(community);

  const price = await routedAsk(
    'escalation',
    'What is the current price of Ethereum (ETH) in USD?',
  );
  price.step = 'price_context';
  steps.push(price);

  // Sentiment is chained off the other miners' output — a real multi-miner workflow.
  const chatter = [news.answer, community.answer].filter(Boolean).join(' ');
  if (chatter.length > 40) {
    const sentiment = await routedAsk(
      'escalation',
      `Analyze the sentiment of the following text about ${name} casino and state whether it is positive, negative, or neutral, with one sentence of justification: "${chatter.slice(0, 500)}"`,
    );
    sentiment.step = 'sentiment';
    steps.push(sentiment);
  }

  const factCheck = await routedAsk(
    'escalation',
    `Fact check this claim: "${name} crypto casino is withholding player withdrawals or is insolvent." Is there evidence supporting or refuting it?`,
  );
  factCheck.step = 'fact_check';
  steps.push(factCheck);

  return steps;
}

// ── Telegram delivery (optional) ────────────────────────────────────────────

async function deliverTelegram(alert: SentinelAlert): Promise<boolean> {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chatId) return false;

  const lines = [
    `[${alert.severity.toUpperCase()}] DegenLens Sentinel — ${alert.operator_name}`,
    '',
    ...alert.findings.map((f) => `• ${f.rule}: ${f.measurement}`),
    '',
    `Observed evidence, not a solvency verdict.`,
    `https://degenlensv1.up.railway.app/sentinel`,
  ];
  try {
    const res = await withTimeout(
      fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: chatId,
          text: lines.join('\n'),
          disable_web_page_preview: true,
        }),
      }),
      10_000,
    );
    return res.ok;
  } catch {
    return false;
  }
}

// ── The scan loop ────────────────────────────────────────────────────────────

export async function runScan(trigger: ScanTrigger): Promise<ScanRecord> {
  if (sentinelStore.isRunning()) {
    throw new Error('A scan is already running.');
  }
  sentinelStore.setRunning(true);

  const cfg = sentinelConfig();
  const startedAt = new Date().toISOString();
  const startedMs = Date.now();
  // Force the on-disk state to load before baselines are captured, or the
  // per-scan deltas would measure against an empty in-memory state.
  const stateBefore = await sentinelStore.state();
  const totalsStart = { ...stateBefore.totals };
  const errors: string[] = [];
  let operatorsScanned = 0;
  let walletsWatched = 0;
  let alertsFired = 0;
  let escalations = 0;

  try {
    // 1. Discovery — the operator registry and flow stats from the co-located
    //    miner. This is a DIRECT call, so it is free regardless of whether
    //    the endpoint is declared -- and /casinos IS declared now, because
    //    the engine refuses undeclared paths outright and real callers were
    //    getting `endpoint "/casinos" is not declared` back.
    sentinelStore.setPhase('discover', 'operator registry');
    const registry = await localCall<CasinoRegistry>(
      'discovery',
      '/casinos',
      {},
      'GET',
      'ONCHAIN_TX_LOOKUP',
    );
    if (!registry.ok) {
      errors.push(`discovery failed: ${registry.error}`);
      await sentinelStore.flushReceipts();
      const scan = finishScan(
        trigger, startedAt, startedMs, totalsStart, 0, 0, 0, 0, errors,
      );
      await sentinelStore.pushScan(scan);
      return scan;
    }

    const attributed = (registry.result.casinos ?? []).filter(
      (c) => c.attribution_status === 'attributed',
    );
    const operators = attributed.slice(0, cfg.maxOperators);
    const watchList = buildWatchList(attributed);

    // 2. Paid watch — rotate through hot wallets, engine-routed balance checks.
    const state = await sentinelStore.state();
    const cursor = state.watch_cursor ?? 0;
    const slice: WatchEntry[] =
      watchList.length <= cfg.maxWallets
        ? watchList
        : Array.from(
            { length: cfg.maxWallets },
            (_, i) => watchList[(cursor + i) % watchList.length],
          );
    const nextCursor = (cursor + slice.length) % Math.max(1, watchList.length);
    await sentinelStore.setWatchCursor(nextCursor);

    const watchRows: WalletWatchRow[] = [];
    for (const entry of slice) {
      sentinelStore.setPhase('watch', `${entry.operator.name} ${entry.address.slice(0, 8)}…`);
      const row = await watchWallet(entry);
      watchRows.push(row);
      if (row.ok) walletsWatched += 1;
    }

    // Flow snapshots per operator (free, local) for the flow-side rules.
    sentinelStore.setPhase('detect', 'flow + balance rules');
    const statsBySlug = new Map<string, CasinoStats>();
    const prevBySlug = new Map<string, ReturnType<typeof sentinelStore.snapshotFor>>();
    for (const casino of operators) {
      const statsRes = await localCall<CasinoStats>(
        'stats',
        '/casino/stats',
        { slug: casino.slug, hours: cfg.windowHours },
        'POST',
        'ONCHAIN_TX_LOOKUP',
      );
      if (!statsRes.ok) {
        errors.push(`${casino.slug}: stats failed: ${statsRes.error}`);
        continue;
      }
      const stats = statsRes.result;
      if (stats?.verdict === 'unavailable') {
        errors.push(`${casino.slug}: miner returned verdict "unavailable"`);
        continue;
      }
      operatorsScanned += 1;
      statsBySlug.set(casino.slug, stats);
      prevBySlug.set(casino.slug, sentinelStore.snapshotFor(casino.slug));
      await sentinelStore.putSnapshot(casino.slug, toSnapshot(stats));
    }

    // 3. Detection — combine flow rules and paid balance-drain rules.
    const pending: SentinelAlert[] = [];
    for (const casino of operators) {
      const stats = statsBySlug.get(casino.slug);
      if (!stats) continue;
      const prev = prevBySlug.get(casino.slug);
      const flowFindings = detectFindings(stats, prev, cfg.floorUsd);
      const rows = watchRows.filter((r) => r.operator_slug === casino.slug);
      const walletFindings = detectWalletFindings(rows, cfg.balanceFloor);
      const findings: Finding[] = [...flowFindings, ...walletFindings];
      const severity = maxSeverity(findings);

      if (severity) {
        const fresh = findings.filter((f) => {
          const key = `${casino.slug}:${f.rule}`;
          const last = sentinelStore.lastAlertAt(key);
          if (!last) return true;
          return Date.now() - Date.parse(last) > cfg.cooldownMinutes * 60_000;
        });

        if (fresh.length > 0) {
          pending.push({
            id: sentinelStore.newId(),
            ts: new Date().toISOString(),
            operator_slug: casino.slug,
            operator_name: stats.name || casino.name,
            severity: maxSeverity(fresh) ?? 'medium',
            title: alertTitle(fresh, stats.name || casino.name),
            findings: fresh,
            stats,
            previous: prev,
            wallet_watch: rows,
            fraud_screens: [],
            tx_lookups: [],
            escalation: [],
            signal_hashes: [],
            data_source: stats.data_source,
          });
        }
      }
    }

    // Persist alerts before enrichment so the UI sees them immediately.
    for (const alert of pending) {
      await sentinelStore.pushAlert(
        alert,
        alert.findings.map((f) => `${alert.operator_slug}:${f.rule}`),
      );
      alertsFired += 1;
    }

    // 4. Paid enrichment — fraud screens, then tx lookups on cited evidence.
    // Rotation means an alerting operator often has no watch rows this scan,
    // so fall back to its first known hot/treasury wallet from the registry.
    const walletBySlug = new Map<string, WatchEntry>();
    for (const entry of watchList) {
      if (!walletBySlug.has(entry.operator.slug)) {
        walletBySlug.set(entry.operator.slug, entry);
      }
    }
    for (const alert of pending) {
      if (cfg.maxFraudScreens <= 0) break;
      const watched =
        alert.wallet_watch.find((r) => r.drop_pct !== undefined) ??
        alert.wallet_watch.find((r) => r.ok);
      const fallback = walletBySlug.get(alert.operator_slug);
      if (!watched && !fallback) continue;
      sentinelStore.setPhase('enrich', alert.operator_name);
      const target: Pick<WalletWatchRow, 'address' | 'chain'> = watched ?? {
        address: fallback!.address,
        chain: fallback!.chain,
      };
      const screen = await fraudScreen(target);
      alert.fraud_screens.push(screen);

      if (screen.ok && cfg.maxTxLookups > 0) {
        let hashes: { txHash: string; chain: string }[] = evidenceTxHashes(screen).map(
          (txHash) => ({ txHash, chain: target.chain }),
        );
        // Fall back to the operator's largest recent observed transfer when
        // the screen cites nothing. The registry-wide feed is often cold —
        // tolerate that and skip rather than guess a target.
        if (hashes.length === 0) {
          hashes = await largestTransferHashes(alert.operator_slug, cfg.windowHours);
        }
        for (const { txHash, chain } of hashes.slice(0, cfg.maxTxLookups)) {
          alert.tx_lookups.push(await txLookup(txHash, chain));
        }
      }
      await sentinelStore.persist();
    }

    // 5. Escalation — compose other miners for the most severe alerts.
    if (cfg.escalate !== 'never' && cfg.maxEscalations > 0) {
      const toEscalate = pending
        .filter((a) => cfg.escalate === 'always' || a.severity === 'high')
        // High-severity alerts claim the escalation budget first.
        .sort((a, b) =>
          a.severity === b.severity
            ? Date.parse(b.ts) - Date.parse(a.ts)
            : a.severity === 'high'
              ? -1
              : 1,
        )
        .slice(0, cfg.maxEscalations);

      for (const alert of toEscalate) {
        try {
          sentinelStore.setPhase('escalate', alert.operator_name);
          alert.escalation = await escalate(alert);
          escalations += 1;
        } catch (err) {
          errors.push(
            `${alert.operator_slug}: escalation failed: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
        alert.telegram_delivered = await deliverTelegram(alert);
        await sentinelStore.persist();
      }
    }

    sentinelStore.setPhase('report');
    const scan = finishScan(
      trigger, startedAt, startedMs, totalsStart,
      operatorsScanned, walletsWatched, alertsFired, escalations, errors,
    );
    await sentinelStore.pushScan(scan);
    return scan;
  } finally {
    sentinelStore.setRunning(false);
  }
}

async function watchWallet(entry: WatchEntry): Promise<WalletWatchRow> {
  const key = `${entry.chain}:${entry.address}`;
  const query =
    `What is the current native balance of ${entry.address} on ${entry.chain}?`;
  const res = await engineCall<BalanceResult>(
    'watch',
    '/wallet/balance',
    { query, address: entry.address, chain: entry.chain },
    'POST',
    'WALLET_BALANCE_CHECK',
  );

  const row: WalletWatchRow = {
    operator_slug: entry.operator.slug,
    operator_name: entry.operator.name,
    address: entry.address,
    chain: entry.chain,
    role: entry.role,
    ok: false,
  };

  if (!res.ok) {
    row.note = res.error;
    return row;
  }
  const bal = res.result;
  const native =
    bal.native_balance ?? bal.balance_native ?? null;
  row.ok = true;
  row.balance = native;
  row.symbol = bal.native_symbol ?? null;

  const prev = sentinelStore.walletSnapshotFor(key);
  if (prev) {
    row.previous = prev.native_balance;
    if (prev.native_balance > 0 && native != null) {
      row.drop_pct = (prev.native_balance - native) / prev.native_balance;
    }
  }
  if (native != null && bal.balance_status !== 'unavailable') {
    await sentinelStore.putWalletSnapshot(key, {
      ts: new Date().toISOString(),
      native_balance: native,
      symbol: bal.native_symbol ?? '',
      block_number: bal.block_number ?? null,
    });
  }
  return row;
}

function finishScan(
  trigger: ScanTrigger,
  startedAt: string,
  startedMs: number,
  totalsStart: { paid_calls: number; spend_usd: number },
  operatorsScanned: number,
  walletsWatched: number,
  alertsFired: number,
  escalations: number,
  errors: string[],
): ScanRecord {
  const totals = sentinelStore.totals();
  return {
    id: sentinelStore.newId(),
    started_at: startedAt,
    duration_ms: Date.now() - startedMs,
    trigger,
    operators_scanned: operatorsScanned,
    wallets_watched: walletsWatched,
    alerts_fired: alertsFired,
    escalations,
    paid_calls: totals.paid_calls - totalsStart.paid_calls,
    spend_usd: Number((totals.spend_usd - totalsStart.spend_usd).toFixed(4)),
    errors,
  };
}

function alertTitle(findings: { rule: string }[], name: string): string {
  const rules = findings.map((f) => f.rule).join(', ');
  return `${name}: ${rules}`;
}

// ── Scheduler ────────────────────────────────────────────────────────────────

export function startSentinel(): void {
  const rt = sentinelStore.runtime();
  if (rt.timer) return;

  const cfg = sentinelConfig();
  if (!cfg.enabled || cfg.intervalMinutes <= 0) return;

  rt.timer = setInterval(() => {
    void runScan('schedule').catch(() => {
      // Scan failures are recorded inside runScan; never crash the interval.
    });
  }, cfg.intervalMinutes * 60_000);
  rt.timer.unref?.();

  // First scan shortly after boot so the agent has something to show.
  rt.bootTimer = setTimeout(() => {
    void runScan('boot').catch(() => undefined);
  }, 15_000);
  rt.bootTimer.unref?.();
}

export function stopSentinel(): void {
  const rt = sentinelStore.runtime();
  if (rt.timer) clearInterval(rt.timer);
  if (rt.bootTimer) clearTimeout(rt.bootTimer);
  rt.timer = undefined;
  rt.bootTimer = undefined;
}

export function nextScanAt(): string | null {
  const rt = sentinelStore.runtime();
  if (!rt.timer) return null;
  const last = rt.lastScan?.started_at ?? rt.state?.started_at;
  const cfg = sentinelConfig();
  if (!last) return null;
  const next = Date.parse(last) + cfg.intervalMinutes * 60_000;
  return new Date(next).toISOString();
}

/** What the recent receipts say about whether paid calls are actually landing. */
function paymentHealth(): {
  state: 'ok' | 'rejected' | 'unfunded' | 'not_configured' | 'unknown';
  paid_ok: number;
  paid_failed: number;
  last_error: string | null;
} {
  if (!telegraphPaymentConfigured) {
    return { state: 'not_configured', paid_ok: 0, paid_failed: 0, last_error: null };
  }
  const paid = sentinelStore
    .receipts()
    .filter((r) => r.mode !== 'local');
  const ok = paid.filter((r) => r.ok).length;
  const failed = paid.filter((r) => !r.ok);
  const lastError = failed.length ? (failed[0].error ?? null) : null;  // newest first
  if (!paid.length) {
    return { state: 'unknown', paid_ok: 0, paid_failed: 0, last_error: null };
  }
  if (ok > 0 && failed.length === 0) {
    return { state: 'ok', paid_ok: ok, paid_failed: 0, last_error: null };
  }
  // A 402 with a funded-looking wallet is almost always an empty USDC balance,
  // and saying so beats making the operator read the raw receipt log.
  const unfunded = Boolean(
    lastError && /payment required|balance or allowance/i.test(lastError),
  );
  return {
    state: unfunded ? 'unfunded' : 'rejected',
    paid_ok: ok,
    paid_failed: failed.length,
    last_error: lastError,
  };
}

export function sentinelStatus() {
  const cfg = sentinelConfig();
  const rt = sentinelStore.runtime();
  const phase = sentinelStore.phase();
  return {
    enabled: cfg.enabled,
    interval_minutes: cfg.intervalMinutes,
    window_hours: cfg.windowHours,
    floor_usd: cfg.floorUsd,
    cooldown_minutes: cfg.cooldownMinutes,
    max_operators: cfg.maxOperators,
    max_wallets: cfg.maxWallets,
    balance_floor: cfg.balanceFloor,
    max_escalations: cfg.maxEscalations,
    escalate: cfg.escalate,
    scheduler_running: Boolean(rt.timer),
    scan_in_progress: sentinelStore.isRunning(),
    scan_phase: phase.phase,
    scan_phase_subject: phase.subject ?? null,
    payment_configured: telegraphPaymentConfigured,
    // `payment_configured` only means a key is present. It says nothing about
    // whether payments SUCCEED, and the difference is the whole agent: with an
    // unfunded wallet every paid watch is rejected 402, `wallets_watched` and
    // `paid_calls` sit at 0 on every scan, and the status still cheerfully
    // reads "configured: true". Report what the receipts actually show.
    payment_health: paymentHealth(),
    miner_id: String(telegraphMinerId),
    last_scan: sentinelStore.lastScan() ?? null,
    next_scan_at: nextScanAt(),
    totals: sentinelStore.totals(),
  };
}
