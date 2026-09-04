import type {
  AnomalyReport,
  CasinoRegistry,
  CasinoStats,
  TransactionLookup,
} from '@degenlens/shared';
import { telegraph, telegraphMinerId } from '@/lib/telegraph';
import type {
  InvestigationReport,
  InvestigationStep,
  InvestigationVerdict,
  SubjectIdentification,
  VerdictTier,
} from './types';

/**
 * The investigation agent — DegenLens' flagship Track 3 experience.
 *
 * Give it a casino name, a wallet address, or a transaction hash. It plans a
 * multi-phase investigation, pays for what it needs, and reports with a full
 * receipt trail:
 *
 *   1. Identify (local)  — resolve the subject against the operator registry.
 *   2. On-chain facts (paid, our miner) — flow stats, balances, fraud screens,
 *      transaction lookups.
 *   3. Network intelligence (paid, other miners via the engine router) —
 *      news, community complaints, sentiment.
 *   4. Synthesize (paid, a chat miner) — a written assessment, grounded in the
 *      evidence collected above.
 *
 * The verdict tier itself is deterministic local rules, never the chat
 * miner's opinion — an LLM writes prose, it does not decide.
 */

const CALL_TIMEOUT_MS = 45_000;

// ── Subject identification ──────────────────────────────────────────────────

const TX_RE = /^0x[a-fA-F0-9]{64}$/;
const ADDR_RE = /^0x[a-fA-F0-9]{40}$/;

async function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`timed out after ${ms}ms`)), ms),
    ),
  ]);
}

async function localCall<T>(
  endpoint: string,
  payload: Record<string, unknown>,
  method: 'GET' | 'POST' = 'POST',
): Promise<T | null> {
  try {
    const res = await withTimeout(
      telegraph.askDirect<T>('local', endpoint, payload, method),
      CALL_TIMEOUT_MS,
    );
    return res.result;
  } catch {
    return null;
  }
}

export async function identifySubject(
  raw: string,
): Promise<SubjectIdentification> {
  const value = raw.trim();
  if (!value) return { type: 'unknown', value };

  if (TX_RE.test(value)) return { type: 'tx', value };
  if (ADDR_RE.test(value)) {
    // A wallet may belong to a catalogued operator — check before deciding
    // what network intelligence applies.
    const trace = await localCall<{
      labeled_casino?: string | null;
      labeled_casino_name?: string | null;
    }>('/wallet/balance', { address: value, chain: 'ethereum' }, 'POST');
    if (trace?.labeled_casino) {
      return {
        type: 'wallet',
        value,
        display: `${value.slice(0, 10)}… (${trace.labeled_casino_name ?? trace.labeled_casino} wallet)`,
        operator_slug: trace.labeled_casino,
        operator_name: trace.labeled_casino_name ?? trace.labeled_casino,
        screen_address: value,
        screen_chain: 'ethereum',
      };
    }
    return { type: 'wallet', value, screen_address: value, screen_chain: 'ethereum' };
  }

  // Operator: match against the catalog by slug or name.
  const registry = await localCall<CasinoRegistry>('/casinos', {}, 'GET');
  const q = value.toLowerCase();
  const match =
    (registry?.casinos ?? []).find(
      (c) => c.slug.toLowerCase() === q || c.name.toLowerCase() === q,
    ) ??
    (registry?.casinos ?? []).find((c) =>
      `${c.name} ${c.slug}`.toLowerCase().includes(q),
    );
  if (!match) {
    return {
      type: 'unknown',
      value,
      note: 'Not a transaction hash, wallet address, or catalogued operator.',
    };
  }
  const hot =
    (match.wallets ?? []).find((w) => w.role === 'hot') ??
    (match.wallets ?? [])[0];
  return {
    type: 'operator',
    value: match.slug,
    display: match.name,
    operator_slug: match.slug,
    operator_name: match.name,
    screen_address: hot?.address,
    screen_chain: hot?.chain ?? 'ethereum',
  };
}

// ── Paid-call plumbing with receipt steps ───────────────────────────────────

interface StepSink {
  (step: InvestigationStep): void;
}

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

async function paidStep(
  phase: InvestigationStep['phase'],
  step: string,
  label: string,
  intent: string,
  endpoint: string,
  payload: Record<string, unknown>,
  method: 'GET' | 'POST',
  sink: StepSink,
  summarize?: (result: Record<string, unknown>) => string | undefined,
): Promise<Record<string, unknown> | null> {
  const base: InvestigationStep = {
    id: newId(),
    phase,
    step,
    label,
    intent,
    cost_usd: 0,
    ok: false,
  };
  try {
    const res = await withTimeout(
      telegraph.askDirect<Record<string, unknown>>(
        telegraphMinerId,
        endpoint,
        payload,
        method,
      ),
      CALL_TIMEOUT_MS,
    );
    const s: InvestigationStep = {
      ...base,
      ok: true,
      miner_id: String(res.miner_id),
      miner_name: res.miner_name,
      cost_usd: res.cost_usd ?? 0,
      duration_ms: res.duration_ms,
      signal_hash: res.signal_hash,
      summary: summarize?.(res.result ?? {}),
    };
    sink(s);
    return res.result ?? {};
  } catch (err) {
    sink({
      ...base,
      error: err instanceof Error ? err.message : String(err),
    });
    return null;
  }
}

function extractAnswer(result: unknown): string | undefined {
  if (result == null) return undefined;
  if (typeof result === 'string') return result.slice(0, 900) || undefined;
  if (typeof result !== 'object') return String(result).slice(0, 400);
  const r = result as Record<string, unknown>;
  // OpenAI-shaped chat miners.
  const choices = r.choices;
  if (Array.isArray(choices) && choices.length > 0) {
    const msg = (choices[0] as Record<string, unknown>)?.message as
      | Record<string, unknown>
      | undefined;
    const content = msg?.content ?? (choices[0] as Record<string, unknown>)?.text;
    if (typeof content === 'string' && content.trim()) return content.slice(0, 900);
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
    if (parts.length) return parts.join(' | ').slice(0, 700);
  }
  for (const key of ['answer', 'ai_response', 'text', 'summary', 'content']) {
    const v = r[key];
    if (typeof v === 'string' && v.trim()) return v.slice(0, 900);
  }
  return JSON.stringify(result).slice(0, 400);
}

function isTransientError(error: string): boolean {
  return (
    error.includes('timed out') ||
    error.includes('500') ||
    error.includes('502') ||
    error.includes('503') ||
    error.includes('routing failed') ||
    error.includes('upstream error')
  );
}

/** An upstream 4xx from a miner the router should never have chosen.
 *
 * The auto-router classifies with an LLM and there is no documented way to pin
 * an intent, so a prompt carrying numeric evidence gets read as something else
 * entirely: the synthesis prompt, which embeds "withdrawals 43136108 are at
 * least 1.5x deposits 27602306", was routed to a currency-pair miner and came
 * back `{"error":"invalid_pair"}`. Retrying the SAME text just lands on the
 * same wrong miner, so these get one attempt with a plain-language fallback
 * that carries no figures for the classifier to trip over. */
function isMisroutedError(error: string): boolean {
  return (
    /upstream error 4\d\d/.test(error) ||
    /invalid_pair|not declared|405|Method Not Allowed/i.test(error)
  );
}

async function routedStep(
  phase: InvestigationStep['phase'],
  step: string,
  label: string,
  query: string,
  sink: StepSink,
  fallbackQuery?: string,
): Promise<string | undefined> {
  const base: InvestigationStep = {
    id: newId(),
    phase,
    step,
    label,
    cost_usd: 0,
    ok: false,
  };
  // The auto-router intermittently 5xxs or times out under load; one retry
  // with backoff recovers most transient failures without masking real ones.
  const maxAttempts = fallbackQuery ? 3 : 2;
  let text = query;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const res = await withTimeout(
        telegraph.ask<unknown>(text),
        CALL_TIMEOUT_MS,
      );
      const answer = extractAnswer(res.result);
      sink({
        ...base,
        ok: true,
        intent: res.intent,
        miner_id: String(res.miner_id),
        miner_name: res.miner_name,
        cost_usd: res.cost_usd ?? 0,
        duration_ms: res.duration_ms,
        signal_hash: res.signal_hash,
        summary: answer?.slice(0, 220),
      });
      return answer;
    } catch (err) {
      const error = err instanceof Error ? err.message : String(err);
      if (attempt < maxAttempts && isTransientError(error)) {
        await new Promise((r) => setTimeout(r, 1500 * attempt));
        continue;
      }
      // Wrong miner rather than a flaky one: re-ask without the figures.
      if (attempt < maxAttempts && fallbackQuery && isMisroutedError(error)) {
        text = fallbackQuery;
        continue;
      }
      sink({ ...base, error });
      return undefined;
    }
  }
  return undefined;
}

// ── Verdict rules (deterministic, local) ────────────────────────────────────

function flowPoints(stats: CasinoStats | null): { points: number; reasons: string[] } {
  if (!stats) return { points: 0, reasons: [] };
  const points: number[] = [];
  const reasons: string[] = [];
  const material = stats.withdrawals_usd >= 5000;
  if (material && stats.deposits_usd > 0 && stats.withdrawals_usd / stats.deposits_usd >= 1.5) {
    points.push(2);
    reasons.push(
      `withdrawals $${Math.round(stats.withdrawals_usd).toLocaleString()} are ≥1.5× deposits $${Math.round(stats.deposits_usd).toLocaleString()} over ${stats.window_hours}h`,
    );
  } else if (stats.net_flow_usd < 0 && material) {
    points.push(1);
    reasons.push(
      `net observed flow is negative ($${Math.round(stats.net_flow_usd).toLocaleString()} over ${stats.window_hours}h)`,
    );
  } else {
    reasons.push(
      `observed flow does not show stress: $${Math.round(stats.deposits_usd).toLocaleString()} in / $${Math.round(stats.withdrawals_usd).toLocaleString()} out over ${stats.window_hours}h`,
    );
  }
  return { points: points.reduce((a, b) => a + b, 0), reasons };
}

function fraudPoints(screen: AnomalyReport | null): { points: number; reasons: string[] } {
  if (!screen) return { points: 0, reasons: [] };
  const tier = screen.risk_tier ?? screen.verdict;
  const reasons = [
    `fraud screen: ${tier}${screen.risk_score !== undefined ? ` (score ${screen.risk_score.toFixed(2)}, ${screen.signal_count ?? 0} signals)` : ''}`,
  ];
  if (tier === 'high_risk') return { points: 2, reasons };
  if (tier === 'elevated_risk' || tier === 'suspicious') return { points: 1, reasons };
  return { points: 0, reasons };
}

function sentimentPoints(sentiment: string | undefined): { points: number; reasons: string[] } {
  if (!sentiment) return { points: 0, reasons: [] };
  const s = sentiment.toLowerCase();
  if (s.includes('negative')) {
    return { points: 1, reasons: [`network sentiment: negative — ${sentiment.slice(0, 140)}`] };
  }
  if (s.includes('positive')) {
    return { points: 0, reasons: [`network sentiment: positive — ${sentiment.slice(0, 140)}`] };
  }
  return { points: 0, reasons: [`network sentiment: ${sentiment.slice(0, 140)}`] };
}

function tierFor(points: number): VerdictTier {
  if (points >= 4) return 'avoid';
  if (points >= 2) return 'elevated';
  if (points >= 1) return 'watch';
  return 'healthy';
}

const TIER_COPY: Record<VerdictTier, string> = {
  healthy: 'No bankrun-shaped conditions observed in the evidence collected.',
  watch: 'One warning sign observed — worth monitoring, not proof of trouble.',
  elevated: 'Multiple warning signs observed. Treat with caution.',
  avoid: 'Strong observed stress signals. The evidence warrants avoiding exposure.',
  unknown: 'Not enough evidence was collectable to form a view.',
};

/**
 * The router falls back to verification-shaped miners when the chat leader is
 * rate-limited, and those answer with detection junk. The step receipt keeps
 * exactly what the network said; this guard only stops non-prose from being
 * promoted to the assessment panel.
 */
function usableSynthesis(answer: string | undefined): string | undefined {
  if (!answer) return undefined;
  const a = answer.trim();
  if (a.length < 40) return undefined;
  if (/injection pattern/i.test(a)) return undefined;
  if (a.startsWith('{')) return undefined;
  if (/^(regarding|queried)\b/i.test(a)) return undefined;
  return a;
}

// ── The investigation ────────────────────────────────────────────────────────

export async function runInvestigation(
  rawSubject: string,
  sink: StepSink,
): Promise<InvestigationReport> {
  const startedMs = Date.now();
  const steps: InvestigationStep[] = [];
  const onStep: StepSink = (step) => {
    steps.push(step);
    sink(step);
  };

  // 1. Identify (local, free).
  const subject = await identifySubject(rawSubject);
  const identifyLabel =
    subject.type === 'operator'
      ? `Resolved operator: ${subject.display ?? subject.value}`
      : subject.type === 'wallet'
        ? `Resolved wallet: ${subject.display ?? subject.value}`
        : subject.type === 'tx'
          ? 'Resolved transaction hash'
          : 'Subject not recognized';
  onStep({
    id: newId(),
    phase: 'identify',
    step: 'identify',
    label: identifyLabel,
    intent: 'local',
    cost_usd: 0,
    ok: subject.type !== 'unknown',
    summary: subject.note,
  });

  if (subject.type === 'unknown') {
    return finish(rawSubject, subject, steps, startedMs, {
      tier: 'unknown',
      headline: TIER_COPY.unknown,
      reasoning: [subject.note ?? 'Subject could not be resolved.'],
    });
  }

  const operatorName = subject.operator_name;

  // 2. On-chain facts (paid, our miner).
  let stats: CasinoStats | null = null;
  let screen: AnomalyReport | null = null;

  if (subject.type === 'operator') {
    const statsRes = await paidStep(
      'onchain',
      'flow',
      'Operator flow (paid ONCHAIN_TX_LOOKUP via DegenMiner)',
      'ONCHAIN_TX_LOOKUP',
      '/casino/stats',
      { slug: subject.operator_slug, hours: 168 },
      'POST',
      onStep,
      (r) => {
        const dep = Number(r.deposits_usd ?? 0);
        const wd = Number(r.withdrawals_usd ?? 0);
        return `7d observed: $${Math.round(dep).toLocaleString()} in / $${Math.round(wd).toLocaleString()} out · ${Number(r.unique_depositors ?? 0).toLocaleString()} depositors · source ${r.data_source ?? '-'}`;
      },
    );
    if (statsRes) stats = statsRes as unknown as CasinoStats;

    if (subject.screen_address) {
      const screenRes = await paidStep(
        'onchain',
        'fraud',
        'Hot-wallet fraud screen (paid FRAUD_DETECTION via DegenMiner)',
        'FRAUD_DETECTION',
        '/anomaly/check',
        {
          query: `How likely is ${subject.screen_address} on ${subject.screen_chain} to be showing fraudulent or anomalous activity in the last 24 hours?`,
          address: subject.screen_address,
          chain: subject.screen_chain,
          hours: 24,
        },
        'POST',
        onStep,
        (r) => {
          const tier = r.risk_tier ?? r.verdict ?? '-';
          return `${tier} · ${String(r.reasoning ?? '').slice(0, 160)}`;
        },
      );
      if (screenRes) screen = screenRes as unknown as AnomalyReport;
    }
  } else if (subject.type === 'wallet') {
    const traceRes = await paidStep(
      'onchain',
      'balance',
      'Wallet balance + attribution (paid WALLET_BALANCE_CHECK via DegenMiner)',
      'WALLET_BALANCE_CHECK',
      '/wallet/balance',
      { address: subject.value, chain: 'ethereum' },
      'POST',
      onStep,
      (r) => {
        const bal = r.native_balance ?? r.balance_native;
        const labeled = r.labeled_casino_name ?? r.labeled_casino;
        return typeof bal === 'number'
          ? `${bal.toFixed(3)} ETH${labeled ? ` · labeled ${labeled}` : ' · unattributed'} · ${r.balance_status ?? '-'}`
          : String(r.reasoning ?? '').slice(0, 180);
      },
    );
    if (traceRes && (traceRes as Record<string, unknown>).labeled_casino && !operatorName) {
      // handled during identify; nothing further
    }

    const screenRes = await paidStep(
      'onchain',
      'fraud',
      'Wallet fraud screen (paid FRAUD_DETECTION via DegenMiner)',
      'FRAUD_DETECTION',
      '/anomaly/check',
      {
        query: `How likely is ${subject.value} on ethereum to be showing fraudulent or anomalous activity in the last 24 hours?`,
        address: subject.value,
        chain: 'ethereum',
        hours: 24,
      },
      'POST',
      onStep,
      (r) => `${r.risk_tier ?? r.verdict ?? '-'} · ${String(r.reasoning ?? '').slice(0, 160)}`,
    );
    if (screenRes) screen = screenRes as unknown as AnomalyReport;
  } else {
    // Transaction: one paid lookup, then screen the sender.
    const txRes = await paidStep(
      'onchain',
      'tx',
      'Transaction lookup (paid ONCHAIN_TX_LOOKUP via DegenMiner)',
      'ONCHAIN_TX_LOOKUP',
      '/transaction/lookup',
      {
        query: `Did transaction ${subject.value} succeed on ethereum, and what did it move?`,
        tx_hash: subject.value,
        chain: 'ethereum',
      },
      'POST',
      onStep,
      (r) => `${r.status ?? '-'} · ${String(r.reasoning ?? '').slice(0, 180)}`,
    );
    if (txRes && typeof txRes.from_address === 'string' && screen === null) {
      const screenRes = await paidStep(
        'onchain',
        'fraud',
        'Sender fraud screen (paid FRAUD_DETECTION via DegenMiner)',
        'FRAUD_DETECTION',
        '/anomaly/check',
        {
          query: `How likely is ${txRes.from_address} on ethereum to be showing fraudulent or anomalous activity in the last 24 hours?`,
          address: txRes.from_address,
          chain: 'ethereum',
          hours: 24,
        },
        'POST',
        onStep,
        (r) => `${r.risk_tier ?? r.verdict ?? '-'} · ${String(r.reasoning ?? '').slice(0, 160)}`,
      );
      if (screenRes) screen = screenRes as unknown as AnomalyReport;
    }
  }

  // 3. Network intelligence (paid, other miners via the router).
  // Sequential on purpose: two concurrent paid asks from the same wallet sign
  // the same x402 nonce, and the engine rejects the replay with a payment
  // error. Paid calls must be serialized end-to-end.
  const networkSubject = operatorName ?? (subject.type === 'tx' ? undefined : subject.display ?? subject.value);
  let news: string | undefined;
  let community: string | undefined;
  let sentiment: string | undefined;

  if (networkSubject) {
    news = await routedStep(
      'network',
      'news',
      'News search (paid, other miner via router)',
      `Search recent news articles about ${networkSubject}, a crypto casino, covering withdrawal delays, insolvency, license problems, or payout freezes from the past 30 days.`,
      onStep,
    );
    community = await routedStep(
      'network',
      'community',
      'Community complaints (paid, other miner via router)',
      `Search the web for recent player complaints about ${networkSubject} crypto casino: withdrawal problems, delayed payouts, frozen accounts, or exit scam reports on Reddit and X.`,
      onStep,
    );
    const chatter = [news, community]
      .filter(Boolean)
      .join(' ')
      // Router classifier safety: strip dollar signs and glyphs from quoted
      // web content before sending it back through the router.
      .replace(/\$/g, '')
      .replace(/[≥≤]/g, '');
    if (chatter.length > 40) {
      sentiment = await routedStep(
        'network',
        'sentiment',
        'Sentiment analysis (paid, other miner via router)',
        `Analyze the sentiment of the following text about ${networkSubject} and state whether it is positive, negative, or neutral with one sentence of justification: "${chatter.slice(0, 500)}"`,
        onStep,
        // A shorter, unquoted fallback: the 500-character embedded blob is
        // what makes this look like something other than sentiment analysis.
        `Is the general sentiment about the crypto casino ${networkSubject} positive, negative, or neutral? Answer in one sentence.`,
      );
    }
  }
  await routedStep(
    'network',
    'price',
    'ETH price context (paid, other miner via router)',
    'What is the current price of Ethereum (ETH) in USD?',
    onStep,
  );

  // 4. Deterministic verdict + paid synthesis.
  const flow = flowPoints(subject.type === 'operator' ? stats : null);
  const fraud = fraudPoints(screen);
  const sent = sentimentPoints(sentiment);
  const points = flow.points + fraud.points + sent.points;
  const tier = tierFor(points);
  const reasoning = [
    ...flow.reasons,
    ...fraud.reasons,
    ...sent.reasons,
  ];

  let synthesis: string | undefined;
  let synthesisMiner: string | undefined;
  if (reasoning.length > 0 && networkSubject) {
    // The router's classifier chokes on dollar signs and math glyphs in
    // queries — express the evidence qualitatively so the request reliably
    // classifies as CHAT_COMPLETION.
    const qualitative = reasoning
      .join('; ')
      .replace(/\$/g, '')
      .replace(/≥/g, 'at least ')
      .replace(/×/g, 'x')
      .replace(/,/g, '');
    const synth = await routedStep(
      'synthesize',
      'synthesis',
      'Written assessment (paid CHAT_COMPLETION miner via router)',
      // Question-form phrasing routes to CHAT_COMPLETION reliably; long
      // imperative prompts with embedded evidence drift into verification
      // intents.
      `Should players be concerned about ${networkSubject} casino based on these observed results: ${qualitative}? Answer in three sentences, do not invent facts, and state plainly whether this evidence supports a claim that the operator is insolvent.`,
      onStep,
      // Figure-free fallback. The evidence string is what mis-routes this:
      // embedded amounts and ratios read as a price or currency-pair request,
      // and the router sent it to a pair miner that answered "invalid_pair".
      `Write three sentences of plain-language commentary about whether players should be concerned about the crypto casino ${networkSubject}. Do not invent facts and do not claim the operator is insolvent.`,
    );
    const usable = usableSynthesis(synth);
    if (usable) {
      synthesis = usable;
      const step = steps.find((s) => s.step === 'synthesis');
      synthesisMiner = step?.miner_name;
    }
  }

  return finish(rawSubject, subject, steps, startedMs, {
    tier,
    headline: TIER_COPY[tier],
    reasoning,
    synthesis,
    synthesis_miner: synthesisMiner,
  });
}

function finish(
  rawSubject: string,
  subject: SubjectIdentification,
  steps: InvestigationStep[],
  startedMs: number,
  verdict: InvestigationVerdict,
): InvestigationReport {
  const paid = steps.filter((s) => s.ok && (s.cost_usd ?? 0) > 0);
  return {
    id: newId(),
    ts: new Date().toISOString(),
    subject: {
      type: subject.type,
      value: subject.type === 'unknown' ? rawSubject : subject.value,
      display: subject.display,
    },
    duration_ms: Date.now() - startedMs,
    paid_calls: paid.length,
    cost_usd: Number(paid.reduce((s, r) => s + (r.cost_usd ?? 0), 0).toFixed(4)),
    steps,
    verdict,
  };
}

// ── Recent investigations (in-memory) ────────────────────────────────────────

interface HistoryEntry {
  id: string;
  ts: string;
  subject: string;
  tier: VerdictTier;
  paid_calls: number;
  cost_usd: number;
}

const g = globalThis as unknown as {
  __degenlens_investigations?: InvestigationReport[];
};

function history(): InvestigationReport[] {
  if (!g.__degenlens_investigations) g.__degenlens_investigations = [];
  return g.__degenlens_investigations;
}

export function recordInvestigation(report: InvestigationReport): void {
  const h = history();
  h.unshift(report);
  if (h.length > 24) h.length = 24;
}

export function getInvestigation(id: string): InvestigationReport | null {
  return history().find((r) => r.id === id) ?? null;
}

export function recentInvestigations(): HistoryEntry[] {
  return history().map((r) => ({
    id: r.id,
    ts: r.ts,
    subject: r.subject.display ?? r.subject.value,
    tier: r.verdict.tier,
    paid_calls: r.paid_calls,
    cost_usd: r.cost_usd,
  }));
}
