'use client';

import Link from 'next/link';
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
  payment_configured: boolean;
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

interface AlertsPayload {
  alerts: SentinelAlert[];
  receipts: Receipt[];
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

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
      <span className="font-mono text-[10px] text-neon-cyan">{tx.tx_hash.slice(0, 18)}…</span>
      <span className="ml-2 font-mono text-slate-400">{tx.status ?? 'unknown'}</span>
      {tx.value_native !== undefined && (
        <span className="ml-2 font-mono text-slate-300">{tx.value_native.toFixed(4)}</span>
      )}
      <p className="mt-0.5 text-slate-300">{tx.reasoning}</p>
    </div>
  );
}

export default function SentinelPage() {
  const status = useSWR<SentinelStatus>('/api/sentinel/status', fetcher, {
    refreshInterval: 10_000,
  });
  const alerts = useSWR<AlertsPayload>('/api/sentinel/alerts', fetcher, {
    refreshInterval: 10_000,
  });

  const s = status.data;
  const scanning = s?.scan_in_progress ?? false;

  async function runScan() {
    if (scanning) return;
    await fetch('/api/sentinel/run', { method: 'POST' }).catch(() => undefined);
    await Promise.all([status.mutate(), alerts.mutate()]);
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

  const alertList = alerts.data?.alerts ?? [];
  const receipts = alerts.data?.receipts ?? [];

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Autonomous agent / bankrun watch"
        title="Sentinel."
        subtitle="An agent that watches, detects, escalates, and reports."
        description="Sentinel scans attributed operators on a schedule through paid Telegraph calls to the DegenMiner. When observed flow turns bankrun-shaped, it escalates by composing other miners on the network — news search, community search, price, sentiment, fact check — and receipts every paid call with its intent, miner, cost, and signal hash."
        actions={
          <button
            type="button"
            onClick={runScan}
            disabled={scanning}
            className="btn-primary min-w-[150px] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {scanning ? 'Scanning…' : 'Run scan now'}
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
        <Stat
          label="Wallets watched"
          value={String(s?.last_scan?.wallets_watched ?? 0)}
          delta="last scan"
        />
        <Stat label="Paid calls" value={String(s?.totals.paid_calls ?? 0)} />
        <Stat label="Network spend" value={formatUsd(s?.totals.spend_usd ?? 0)} />
        <Stat label="Alerts fired" value={String(s?.totals.alerts_fired ?? 0)} />
        <Stat label="Escalations" value={String(s?.totals.escalations ?? 0)} />
      </div>

      {s && (
        <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-[10px] uppercase tracking-[0.1em] text-slate-500">
          <span>
            mode: <span className="text-white">{s.payment_configured ? 'x402 paid' : 'local fallback'}</span>
          </span>
          <span>
            miner: <span className="text-white">{s.miner_id}</span>
          </span>
          <span>
            window: <span className="text-white">{s.window_hours}h</span>
          </span>
          <span>
            floor: <span className="text-white">{formatUsd(s.floor_usd)}</span>
          </span>
          <span>
            wallet budget: <span className="text-white">{s.max_wallets}/scan</span>
          </span>
          <span>
            drain floor: <span className="text-white">{s.balance_floor} native</span>
          </span>
          <span>
            escalate: <span className="text-white">{s.escalate}</span>
          </span>
          <span>
            last scan: <span className="text-white">{timeAgo(s.last_scan?.started_at)}</span>
          </span>
          <span>
            next scan: <span className="text-white">{s.next_scan_at ? timeAgo(s.next_scan_at).replace(' ago', '') : '—'}</span>
          </span>
        </div>
      )}

      <Panel
        title="Alerts"
        subtitle="Observed-flow findings. Directional flow is evidence, not proof of insolvency."
      >
        {alertList.length === 0 ? (
          <div className="py-6 text-center text-sm text-slate-500">
            {scanning
              ? 'Scanning operators…'
              : 'No alerts yet. Sentinel is watching — findings land here with their escalation trail.'}
          </div>
        ) : (
          <ul className="space-y-4">
            {alertList.map((alert) => (
              <li
                key={alert.id}
                className={`border-l-2 bg-ink-800/30 p-4 ${
                  alert.severity === 'high'
                    ? 'border-neon-red'
                    : 'border-neon-amber'
                }`}
              >
                <div className="flex flex-wrap items-center gap-3">
                  <span
                    className={`font-mono text-[10px] uppercase tracking-[0.14em] ${
                      alert.severity === 'high' ? 'text-neon-red' : 'text-neon-amber'
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
                  <span className="ml-auto font-mono text-[10px] text-slate-500">
                    {timeAgo(alert.ts)}
                  </span>
                </div>

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

                {alert.wallet_watch.length > 0 && (
                  <WatchTable rows={alert.wallet_watch} />
                )}

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
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Network receipts"
        subtitle="Every paid Telegraph call the agent made — the Track 3 evidence trail."
      >
        {receipts.length === 0 ? (
          <div className="py-6 text-center text-sm text-slate-500">
            No engine calls recorded yet. Run a scan to generate paid traffic.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left">
              <thead>
                <tr className="border-b border-ink-700 font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">
                  <th className="py-2 pr-4">When</th>
                  <th className="py-2 pr-4">Purpose</th>
                  <th className="py-2 pr-4">Route</th>
                  <th className="py-2 pr-4">Intent</th>
                  <th className="py-2 pr-4">Miner</th>
                  <th className="py-2 pr-4">Cost</th>
                  <th className="py-2">Signal</th>
                </tr>
              </thead>
              <tbody>
                {receipts.map((r, i) => (
                  <tr key={`${r.ts}-${i}`} className="border-b border-ink-800 text-xs">
                    <td className="py-2 pr-4 font-mono text-slate-500">{timeAgo(r.ts)}</td>
                    <td className="py-2 pr-4 text-slate-300">{PURPOSE_LABELS[r.purpose] ?? r.purpose}</td>
                    <td className="py-2 pr-4 font-mono text-slate-500">{r.mode}</td>
                    <td className="py-2 pr-4 font-mono text-slate-300">{r.intent ?? '—'}</td>
                    <td className="py-2 pr-4 text-slate-300">{r.miner_name ?? r.miner_id}</td>
                    <td className="py-2 pr-4 font-mono text-slate-300">
                      {r.ok ? `$${(r.cost_usd ?? 0).toFixed(3)}` : '—'}
                    </td>
                    <td className="py-2 font-mono text-neon-cyan">
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
  );
}
