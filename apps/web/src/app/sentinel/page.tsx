'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import useSWR from 'swr';
import { formatUsd } from '@degenlens/shared';
import { PageHeader } from '@/components/page-header';
import { Panel, Stat } from '@/components/panel';
import { DataSourceBadge } from '@/components/data-source';
import type {
  FraudScreen,
  Receipt,
  ScanRecord,
  SentinelAlert,
  TxEvidence,
  WalletWatchRow,
} from '@/lib/sentinel/types';

interface SentinelStatus {
  enabled: boolean;
  interval_minutes: number;
  window_hours: number;
  floor_usd: number;
  cooldown_minutes: number;
  max_operators: number;
  max_wallets: number;
  balance_floor: number;
  max_escalations: number;
  escalate: string;
  scheduler_running: boolean;
  scan_in_progress: boolean;
  scan_phase: string;
  scan_phase_subject: string | null;
  payment_configured: boolean;
  payment_health?: {
    state: 'ok' | 'rejected' | 'unfunded' | 'not_configured' | 'unknown';
    paid_ok: number;
    paid_failed: number;
    last_error: string | null;
  };
  miner_id: string;
  last_scan: ScanRecord | null;
  next_scan_at: string | null;
  totals: {
    scans: number;
    paid_calls: number;
    spend_usd: number;
    alerts_fired: number;
    escalations: number;
  };
}

interface SentinelData {
  alerts: SentinelAlert[];
  scans: ScanRecord[];
  receipts: Receipt[];
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const PHASES = [
  { key: 'discover', label: 'Discover', detail: 'registry + flow stats (local)' },
  { key: 'watch', label: 'Watch', detail: 'paid balance checks' },
  { key: 'detect', label: 'Detect', detail: 'bankrun rules' },
  { key: 'enrich', label: 'Enrich', detail: 'fraud screen + tx lookup' },
  { key: 'escalate', label: 'Escalate', detail: 'other miners via router' },
  { key: 'report', label: 'Report', detail: 'alert + telegram + receipts' },
] as const;

const STEP_LABELS: Record<string, string> = {
  news_search: 'News search',
  community_search: 'Community search',
  price_context: 'Price context',
  sentiment: 'Sentiment',
  fact_check: 'Fact check',
};

const PURPOSE_LABELS: Record<string, string> = {
  discovery: 'discovery',
  stats: 'flow scan',
  watch: 'balance watch',
  fraud: 'fraud screen',
  txlookup: 'tx lookup',
  escalation: 'escalation',
};

const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1 };

function timeAgo(iso?: string | null): string {
  if (!iso) return '—';
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const minutes = Math.round(ms / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function fmtNative(n: number | null | undefined, symbol?: string | null): string {
  if (n == null || !Number.isFinite(n)) return '—';
  const s = symbol ?? '';
  return `${n.toFixed(Math.abs(n) >= 1000 ? 0 : 2)}${s ? ` ${s}` : ''}`;
}

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ── Pipeline stepper ─────────────────────────────────────────────────────────

function Pipeline({ status }: { status: SentinelStatus | undefined }) {
  const activeIndex = status?.scan_in_progress
    ? PHASES.findIndex((p) => p.key === status.scan_phase)
    : -1;

  return (
    <Panel
      title="Agent pipeline"
      subtitle="Every scan runs the same six phases. Live state updates as the scan progresses."
      actions={
        status?.scan_in_progress ? (
          <span className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-neon-cyan">
            <span className="live-dot bg-neon-cyan" />
            {status.scan_phase_subject ?? status.scan_phase}
          </span>
        ) : (
          <span className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">
            <span className="live-dot bg-neon-green" />
            armed · next {status?.next_scan_at ? timeAgo(status.next_scan_at).replace(' ago', '') : '—'}
          </span>
        )
      }
    >
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {PHASES.map((phase, i) => {
          const active = i === activeIndex;
          const done = activeIndex >= 0 && i < activeIndex;
          return (
            <li
              key={phase.key}
              className={`relative border p-3 transition-colors ${
                active
                  ? 'border-neon-cyan/60 bg-neon-cyan/5'
                  : done
                    ? 'border-neon-green/30 bg-neon-green/5'
                    : 'border-ink-700 bg-ink-800/30'
              }`}
            >
              <div className="flex items-center gap-2">
                <span
                  className={`font-mono text-[10px] ${
                    active ? 'text-neon-cyan' : done ? 'text-neon-green' : 'text-slate-600'
                  }`}
                >
                  {done ? '✓' : String(i + 1).padStart(2, '0')}
                </span>
                <span className={`text-xs font-semibold ${active ? 'text-white' : 'text-slate-300'}`}>
                  {phase.label}
                </span>
                {active && <span className="live-dot bg-neon-cyan" aria-hidden="true" />}
              </div>
              <p className="mt-1 text-[11px] leading-4 text-slate-500">{phase.detail}</p>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

// ── Alert evidence components ────────────────────────────────────────────────

function WatchTable({ rows }: { rows: WalletWatchRow[] }) {
  return (
    <div className="mt-4 overflow-x-auto border-t border-ink-700 pt-3">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">
        Wallet watch — paid WALLET_BALANCE_CHECK via the network
      </div>
      <table className="mt-2 w-full min-w-[560px] text-left">
        <thead>
          <tr className="border-b border-ink-700 font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">
            <th className="py-1.5 pr-4">Wallet</th>
            <th className="py-1.5 pr-4">Chain / role</th>
            <th className="py-1.5 pr-4">Previous</th>
            <th className="py-1.5 pr-4">Current</th>
            <th className="py-1.5">Change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const drop = r.drop_pct;
            const rising = drop !== undefined && drop < 0;
            return (
              <tr key={`${r.chain}:${r.address}`} className="border-b border-ink-800 text-xs">
                <td className="py-1.5 pr-4 font-mono text-slate-300">
                  {r.address.slice(0, 10)}…{r.address.slice(-6)}
                </td>
                <td className="py-1.5 pr-4 text-slate-400">
                  {r.chain} · {r.role}
                </td>
                <td className="py-1.5 pr-4 font-mono text-slate-400">
                  {fmtNative(r.previous, r.symbol)}
                </td>
                <td className="py-1.5 pr-4 font-mono text-white">
                  {r.ok ? fmtNative(r.balance, r.symbol) : 'unavailable'}
                </td>
                <td className="py-1.5 font-mono">
                  {drop === undefined ? (
                    <span className="text-slate-500">baseline</span>
                  ) : (
                    <span className={drop > 0.25 ? 'text-neon-red' : rising ? 'text-neon-green' : 'text-slate-400'}>
                      {(drop * 100).toFixed(1)}%
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FraudScreenRow({ screen }: { screen: FraudScreen }) {
  if (!screen.ok) {
    return (
      <p className="mt-2 text-xs text-slate-500">
        {screen.address.slice(0, 10)}… — screen unavailable: {screen.error}
      </p>
    );
  }
  const tierClass =
    screen.risk_tier === 'high_risk'
      ? 'text-neon-red'
      : screen.risk_tier === 'elevated_risk'
        ? 'text-neon-amber'
        : 'text-neon-green';
  return (
    <div className="mt-2 text-xs leading-5">
      <span className="font-mono text-[10px] uppercase tracking-wider text-neon-cyan">
        {screen.address.slice(0, 10)}…
      </span>
      <span className={`ml-2 font-mono ${tierClass}`}>
        {screen.risk_tier}
        {screen.risk_score !== undefined ? ` (${screen.risk_score.toFixed(2)})` : ''}
      </span>
      {screen.signal_count !== undefined && (
        <span className="ml-2 font-mono text-[10px] text-slate-500">
          {screen.signal_count} signals
        </span>
      )}
      <p className="mt-0.5 text-slate-300">{screen.reasoning}</p>
      {(screen.signals ?? []).slice(0, 3).map((sig, i) => (
        <p key={i} className="mt-0.5 font-mono text-[10px] text-slate-500">
          · {sig.length > 110 ? `${sig.slice(0, 110)}…` : sig}
        </p>
      ))}
    </div>
  );
}

function TxRow({ tx }: { tx: TxEvidence }) {
  if (!tx.ok) {
    return (
      <p className="mt-2 text-xs text-slate-500">
        {tx.tx_hash.slice(0, 18)}… — lookup failed: {tx.error}
      </p>
    );
  }
  return (
    <div className="mt-2 text-xs leading-5">
      <Link
        href={`/transactions/${tx.tx_hash}`}
        className="font-mono text-[10px] text-neon-cyan hover:underline"
      >
        {tx.tx_hash.slice(0, 18)}…
      </Link>
      <span className="ml-2 font-mono text-slate-400">{tx.status ?? 'unknown'}</span>
      {tx.value_native !== undefined && (
        <span className="ml-2 font-mono text-slate-300">{tx.value_native.toFixed(4)}</span>
      )}
      <p className="mt-0.5 text-slate-300">{tx.reasoning}</p>
    </div>
  );
}

function AlertCard({ alert }: { alert: SentinelAlert }) {
  const [open, setOpen] = useState(true);
  const high = alert.severity === 'high';

  return (
    <li
      className={`border-l-2 bg-ink-800/30 p-4 ${high ? 'border-neon-red' : 'border-neon-amber'}`}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={`font-mono text-[10px] uppercase tracking-[0.14em] ${
            high ? 'text-neon-red' : 'text-neon-amber'
          }`}
        >
          [{alert.severity}]
        </span>
        <Link
          href={`/operators/${alert.operator_slug}`}
          className="text-sm font-semibold text-white hover:text-neon-cyan"
        >
          {alert.operator_name}
        </Link>
        <DataSourceBadge source={alert.data_source} />
        <span className="font-mono text-[10px] text-slate-500">
          {alert.findings.length} finding{alert.findings.length === 1 ? '' : 's'}
          {alert.escalation.length > 0 && ` · ${alert.escalation.length} escalation steps`}
        </span>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="ml-auto font-mono text-[10px] uppercase tracking-wider text-slate-500 hover:text-white"
        >
          {open ? 'Collapse' : 'Expand'}
        </button>
        <span className="font-mono text-[10px] text-slate-500">{timeAgo(alert.ts)}</span>
      </div>

      {open && (
        <>
          <ul className="mt-3 space-y-2">
            {alert.findings.map((f) => (
              <li key={f.rule} className="text-xs leading-5 text-slate-300">
                <span className="font-mono text-[10px] uppercase tracking-wider text-neon-cyan">
                  {f.rule}
                </span>
                <span className="mx-2 text-slate-600">/</span>
                {f.measurement}
              </li>
            ))}
          </ul>

          {alert.wallet_watch.length > 0 && <WatchTable rows={alert.wallet_watch} />}

          {alert.fraud_screens.length > 0 && (
            <div className="mt-4 border-t border-ink-700 pt-3">
              <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">
                Fraud screens — paid FRAUD_DETECTION via the network
              </div>
              {alert.fraud_screens.map((screen, i) => (
                <FraudScreenRow key={i} screen={screen} />
              ))}
            </div>
          )}

          {alert.tx_lookups.length > 0 && (
            <div className="mt-4 border-t border-ink-700 pt-3">
              <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">
                Transaction evidence — paid ONCHAIN_TX_LOOKUP via the network
              </div>
              {alert.tx_lookups.map((tx, i) => (
                <TxRow key={i} tx={tx} />
              ))}
            </div>
          )}

          {alert.escalation.length > 0 && (
            <div className="mt-4 border-t border-ink-700 pt-3">
              <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">
                Escalation trail — other miners on the network
              </div>
              <ul className="mt-2 space-y-2">
                {alert.escalation.map((step, i) => (
                  <li key={`${alert.id}-${step.step}-${i}`} className="text-xs leading-5">
                    <span className="font-mono text-[10px] uppercase tracking-wider text-neon-green">
                      {STEP_LABELS[step.step] ?? step.step}
                    </span>
                    {step.miner_name && (
                      <span className="ml-2 font-mono text-[10px] text-slate-500">
                        via {step.miner_name}
                        {step.intent ? ` · ${step.intent}` : ''}
                        {step.cost_usd ? ` · $${step.cost_usd.toFixed(3)}` : ''}
                      </span>
                    )}
                    {step.ok ? (
                      <p className="mt-0.5 text-slate-300">{step.answer ?? '(no text returned)'}</p>
                    ) : (
                      <p className="mt-0.5 text-slate-500">unavailable — {step.error ?? 'failed'}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {alert.telegram_delivered && (
            <div className="mt-3 font-mono text-[10px] uppercase tracking-wider text-neon-green">
              Telegram alert delivered
            </div>
          )}
        </>
      )}
    </li>
  );
}

// ── Receipt analytics ────────────────────────────────────────────────────────

function receiptStats(receipts: Receipt[]) {
  const paid = receipts.filter((r) => r.mode !== 'local');
  const byIntent = new Map<string, { count: number; ok: number; spend: number }>();
  const byMiner = new Map<string, { calls: number; spend: number }>();
  for (const r of paid) {
    const key = r.intent ?? 'unknown';
    const agg = byIntent.get(key) ?? { count: 0, ok: 0, spend: 0 };
    agg.count += 1;
    if (r.ok) {
      agg.ok += 1;
      agg.spend += r.cost_usd ?? 0;
    }
    byIntent.set(key, agg);
    const minerKey = r.miner_name ?? r.miner_id;
    const mAgg = byMiner.get(minerKey) ?? { calls: 0, spend: 0 };
    mAgg.calls += 1;
    mAgg.spend += r.ok ? (r.cost_usd ?? 0) : 0;
    byMiner.set(minerKey, mAgg);
  }
  const okCount = paid.filter((r) => r.ok).length;
  return {
    paid,
    byIntent: [...byIntent.entries()].sort((a, b) => b[1].count - a[1].count),
    byMiner: [...byMiner.entries()].sort((a, b) => b[1].calls - a[1].calls),
    successRate: paid.length ? (okCount / paid.length) * 100 : 100,
    avgCost: okCount
      ? paid.filter((r) => r.ok).reduce((s, r) => s + (r.cost_usd ?? 0), 0) / okCount
      : 0,
  };
}

// ── Page ─────────────────────────────────────────────────────────────────────

type Tab = 'alerts' | 'history' | 'receipts';

export default function SentinelPage() {
  const [tab, setTab] = useState<Tab>('alerts');
  const [severityFilter, setSeverityFilter] = useState<'all' | 'high'>('all');

  const status = useSWR<SentinelStatus>('/api/sentinel/status', fetcher, {
    refreshInterval: 5_000,
  });
  const data = useSWR<SentinelData>('/api/sentinel/alerts', fetcher, {
    refreshInterval: 5_000,
  });

  const s = status.data;
  const scanning = s?.scan_in_progress ?? false;

  async function runScan() {
    if (scanning) return;
    await fetch('/api/sentinel/run', { method: 'POST' }).catch(() => undefined);
    await Promise.all([status.mutate(), data.mutate()]);
  }

  const agentState = !s
    ? '…'
    : !s.enabled
      ? 'disabled'
      : scanning
        ? 'scanning'
        : s.scheduler_running
          ? 'armed'
          : 'idle';

  const alertList = useMemo(() => {
    const all = data.data?.alerts ?? [];
    return severityFilter === 'all'
      ? all
      : all.filter((a) => a.severity === 'high');
  }, [data.data, severityFilter]);

  const scans = data.data?.scans ?? [];
  const stats = useMemo(
    () => receiptStats(data.data?.receipts ?? []),
    [data.data],
  );

  const highCount = (data.data?.alerts ?? []).filter((a) => a.severity === 'high').length;

  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: 'alerts', label: 'Alerts', count: data.data?.alerts.length },
    { key: 'history', label: 'Scan history', count: scans.length },
    { key: 'receipts', label: 'Network receipts', count: stats.paid.length },
  ];

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Autonomous agent / bankrun watch"
        title="Sentinel."
        subtitle="An agent that watches, detects, escalates, and reports."
        description="Sentinel scans attributed operators on a schedule, checking hot-wallet balances through paid Telegraph calls to the DegenMiner. When observed flow turns bankrun-shaped, it escalates by composing other miners on the network — news search, community search, price, sentiment, fact check — and receipts every paid call with its intent, miner, cost, and signal hash."
        actions={
          <button
            type="button"
            onClick={runScan}
            disabled={scanning}
            className="btn-primary min-w-[150px] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {scanning ? `Scanning — ${s?.scan_phase ?? ''}…` : 'Run scan now'}
          </button>
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="Agent state"
          value={agentState}
          delta={s?.scheduler_running ? `every ${s.interval_minutes}m` : undefined}
          positive={s?.scheduler_running}
        />
        {/* A key being present is not the same as payments landing: an
            unfunded wallet has every paid call rejected 402 while this
            tile still reads "x402 live", which is how the agent sat at
            0 paid calls without it being visible anywhere on the page. */}
        <Stat
          label="Paid calls"
          value={String(s?.totals.paid_calls ?? 0)}
          delta={
            !s?.payment_configured
              ? 'local fallback'
              : s?.payment_health?.state === 'unfunded'
                ? `unfunded — ${s.payment_health.paid_failed} rejected`
                : s?.payment_health?.state === 'rejected'
                  ? `rejected — ${s.payment_health.paid_failed} failed`
                  : s?.payment_health?.state === 'ok'
                    ? `x402 live — ${s.payment_health.paid_ok} paid`
                    : 'x402 configured'
          }
          positive={
            s?.payment_configured &&
            s?.payment_health?.state !== 'unfunded' &&
            s?.payment_health?.state !== 'rejected'
          }
        />
        <Stat label="Network spend" value={formatUsd(s?.totals.spend_usd ?? 0)} />
        <Stat
          label="Alerts fired"
          value={String(s?.totals.alerts_fired ?? 0)}
          delta={highCount > 0 ? `${highCount} high` : undefined}
        />
        <Stat label="Escalations" value={String(s?.totals.escalations ?? 0)} />
        <Stat
          label="Last scan"
          value={s?.last_scan ? timeAgo(s.last_scan.started_at).replace(' ago', '') : '—'}
          delta={s?.last_scan ? fmtDuration(s.last_scan.duration_ms) : undefined}
        />
      </div>

      <Pipeline status={s} />

      <div className="flex flex-wrap items-center gap-2 border-b border-ink-700">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            aria-current={tab === t.key ? 'page' : undefined}
            className={`-mb-px border-b-2 px-4 py-2.5 font-mono text-xs uppercase tracking-[0.1em] transition-colors ${
              tab === t.key
                ? 'border-neon-cyan text-white'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            {t.label}
            {t.count !== undefined && (
              <span className={`ml-2 ${tab === t.key ? 'text-neon-cyan' : 'text-slate-600'}`}>
                {t.count}
              </span>
            )}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2 pb-2">
          {tab === 'alerts' && (data.data?.alerts.length ?? 0) > 0 && (
            <div className="flex border border-ink-700">
              {(['all', 'high'] as const).map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setSeverityFilter(f)}
                  className={`px-3 py-1 font-mono text-[10px] uppercase tracking-wider ${
                    severityFilter === f
                      ? 'bg-white text-ink-950'
                      : 'text-slate-500 hover:text-white'
                  }`}
                >
                  {f === 'high' ? 'high only' : 'all'}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {tab === 'alerts' && (
        <Panel
          title="Alerts"
          subtitle="Observed-flow findings. Directional flow is evidence, not proof of insolvency."
        >
          {alertList.length === 0 ? (
            <div className="py-6 text-center text-sm text-slate-500">
              {scanning
                ? 'Scanning operators — findings land here as the detect phase completes…'
                : 'No alerts yet. Sentinel is watching — findings land here with their full evidence trail.'}
            </div>
          ) : (
            <ul className="space-y-4">
              {[...alertList]
                .sort(
                  (a, b) =>
                    (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) ||
                    Date.parse(b.ts) - Date.parse(a.ts),
                )
                .map((alert) => (
                  <AlertCard key={alert.id} alert={alert} />
                ))}
            </ul>
          )}
        </Panel>
      )}

      {tab === 'history' && (
        <Panel
          title="Scan history"
          subtitle="Every scan with its trigger, duration, coverage, and findings."
        >
          {scans.length === 0 ? (
            <div className="py-6 text-center text-sm text-slate-500">
              No scans recorded yet.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-left">
                <thead>
                  <tr className="border-b border-ink-700 font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">
                    <th className="py-2 pr-4">Started</th>
                    <th className="py-2 pr-4">Trigger</th>
                    <th className="py-2 pr-4">Duration</th>
                    <th className="py-2 pr-4">Operators</th>
                    <th className="py-2 pr-4">Wallets</th>
                    <th className="py-2 pr-4">Alerts</th>
                    <th className="py-2 pr-4">Paid calls</th>
                    <th className="py-2 pr-4">Spend</th>
                    <th className="py-2">Errors</th>
                  </tr>
                </thead>
                <tbody>
                  {scans.map((scan) => (
                    <tr key={scan.id} className="border-b border-ink-800 text-xs">
                      <td className="py-2 pr-4 font-mono text-slate-400">
                        {scan.started_at.slice(0, 19).replace('T', ' ')}
                      </td>
                      <td className="py-2 pr-4 font-mono text-slate-300">{scan.trigger}</td>
                      <td className="py-2 pr-4 font-mono text-slate-400">
                        {fmtDuration(scan.duration_ms)}
                      </td>
                      <td className="py-2 pr-4 font-mono text-slate-300">{scan.operators_scanned}</td>
                      <td className="py-2 pr-4 font-mono text-slate-300">{scan.wallets_watched}</td>
                      <td className="py-2 pr-4 font-mono text-white">{scan.alerts_fired}</td>
                      <td className="py-2 pr-4 font-mono text-slate-300">{scan.paid_calls}</td>
                      <td className="py-2 pr-4 font-mono text-slate-300">
                        ${scan.spend_usd.toFixed(2)}
                      </td>
                      <td className="py-2 font-mono">
                        {scan.errors.length === 0 ? (
                          <span className="text-neon-green">0</span>
                        ) : (
                          <span
                            className="text-neon-amber"
                            title={scan.errors.slice(0, 3).join('\n')}
                          >
                            {scan.errors.length}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}

      {tab === 'receipts' && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Success rate" value={`${stats.successRate.toFixed(0)}%`} positive={stats.successRate > 90} />
            <Stat label="Avg cost / call" value={`$${stats.avgCost.toFixed(3)}`} />
            <Stat label="Paid calls shown" value={String(stats.paid.length)} />
            <Stat
              label="Spend shown"
              value={formatUsd(stats.paid.filter((r) => r.ok).reduce((sum, r) => sum + (r.cost_usd ?? 0), 0))}
            />
          </div>

          {stats.byIntent.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {stats.byIntent.map(([intent, agg]) => (
                <span
                  key={intent}
                  className="border border-ink-700 bg-ink-800/50 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-400"
                >
                  <span className="text-white">{intent}</span>
                  {` · ${agg.ok}/${agg.count} ok · $${agg.spend.toFixed(2)}`}
                </span>
              ))}
            </div>
          )}

          {stats.byMiner.length > 1 && (
            <Panel
              title="Network composition"
              subtitle="Which miners on the Telegraph network the agent has paid."
            >
              <div className="flex flex-wrap gap-2">
                {stats.byMiner.map(([miner, agg]) => (
                  <span
                    key={miner}
                    className="border border-ink-700 bg-ink-800/50 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-400"
                  >
                    <span className="text-white">{miner}</span>
                    {` · ${agg.calls} call${agg.calls === 1 ? '' : 's'} · $${agg.spend.toFixed(2)}`}
                  </span>
                ))}
              </div>
            </Panel>
          )}

          <Panel
            title="Network receipts"
            subtitle="Every paid Telegraph call the agent made — the Track 3 evidence trail. Local co-located calls are omitted."
          >
            {stats.paid.length === 0 ? (
              <div className="py-6 text-center text-sm text-slate-500">
                No engine calls recorded yet. Run a scan to generate paid traffic.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[680px] text-left">
                  <thead>
                    <tr className="border-b border-ink-700 font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">
                      <th className="py-2 pr-4">When</th>
                      <th className="py-2 pr-4">Purpose</th>
                      <th className="py-2 pr-4">Route</th>
                      <th className="py-2 pr-4">Intent</th>
                      <th className="py-2 pr-4">Miner</th>
                      <th className="py-2 pr-4">Cost</th>
                      <th className="py-2 pr-4">Took</th>
                      <th className="py-2">Signal</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.paid.map((r, i) => (
                      <tr key={`${r.ts}-${i}`} className="border-b border-ink-800 text-xs">
                        <td className="py-2 pr-4 font-mono text-slate-500">{timeAgo(r.ts)}</td>
                        <td className="py-2 pr-4 text-slate-300">{PURPOSE_LABELS[r.purpose] ?? r.purpose}</td>
                        <td className="py-2 pr-4 font-mono text-slate-500">{r.mode}</td>
                        <td className="py-2 pr-4 font-mono text-slate-300">{r.intent ?? '—'}</td>
                        <td className="py-2 pr-4 text-slate-300">{r.miner_name ?? r.miner_id}</td>
                        <td className="py-2 pr-4 font-mono text-slate-300">
                          {r.ok ? `$${(r.cost_usd ?? 0).toFixed(3)}` : '—'}
                        </td>
                        <td className="py-2 pr-4 font-mono text-slate-500">
                          {r.duration_ms !== undefined ? fmtDuration(r.duration_ms) : '—'}
                        </td>
                        <td className="py-2 font-mono text-neon-cyan" title={r.signal_hash ?? r.error ?? ''}>
                          {r.signal_hash ? `${r.signal_hash.slice(0, 10)}…` : r.ok ? '—' : 'failed'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
