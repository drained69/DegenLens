'use client';

import { useEffect, useState } from 'react';
import useSWR from 'swr';
import type { AnomalyReport, WalletTrace } from '@degenlens/shared';
import { formatCount } from '@degenlens/shared';
import { Panel, Stat } from '@/components/panel';
import { DataSourceBadge, ProvenanceNotice } from '@/components/data-source';
import { PageHeader } from '@/components/page-header';

async function fetcher(url: string) {
  const response = await fetch(url);
  if (!response.ok) throw new Error('The wallet service could not complete this request.');
  return response.json();
}
const addressPattern = /^0x[a-fA-F0-9]{40}$/;

export default function WalletPage() {
  const [address, setAddress] = useState('');
  const [target, setTarget] = useState<string>('');
  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get('address');
    if (initial) {
      setAddress(initial);
      if (addressPattern.test(initial)) setTarget(initial);
    }
  }, []);
  const trace = useSWR<WalletTrace>(
    target ? `/api/wallet/trace?address=${encodeURIComponent(target)}` : null,
    fetcher,
  );
  const anomaly = useSWR<AnomalyReport>(
    target ? `/api/wallet/anomaly?address=${encodeURIComponent(target)}` : null,
    fetcher,
  );
  const validAddress = addressPattern.test(address.trim());

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Wallet / counterparty trace"
        title="Wallet explorer"
        description="Paste an address. Inspect operator exposure, activity, and anomaly signals."
      />

      <form
        onSubmit={(e) => {
          e.preventDefault();
          setTarget(address.trim());
        }}
        className="surface-highlight flex flex-col gap-3 p-4 sm:flex-row"
      >
        <label htmlFor="wallet-address" className="sr-only">Wallet address</label>
        <input
          id="wallet-address"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder="0x wallet address"
          aria-invalid={address.length > 0 && !validAddress}
          aria-describedby={address.length > 0 && !validAddress ? 'wallet-address-error' : undefined}
          className={`min-w-0 flex-1 border bg-ink-950 px-4 py-3 font-mono text-sm text-white placeholder:text-slate-600 focus:outline-none ${address.length > 0 && !validAddress ? 'border-neon-amber/60' : 'border-ink-700 focus:border-neon-cyan'}`}
        />
        <button
          type="submit"
          disabled={!validAddress}
          className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          Trace →
        </button>
      </form>

      {address.length > 0 && !validAddress && (
           <p id="wallet-address-error" className="-mt-4 font-mono text-[10px] uppercase tracking-[0.1em] text-neon-amber">
          Enter a valid 40-character hexadecimal wallet address.
        </p>
      )}

      {!target && (
        <div className="grid gap-px border border-ink-700 bg-ink-700 sm:grid-cols-3">
          <QuickStart label="Known operator wallet" value="Paste any 0x address" />
          <QuickStart label="Associations" value="30-day counterparty graph" />
          <QuickStart label="Risk screen" value="Deterministic anomaly signals" />
        </div>
      )}

      {target && trace.data && <ProvenanceNotice source={trace.data.data_source} />}

      {target && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel
            title="Associations"
            subtitle="Casinos this wallet has interacted with (30d)"
            actions={<DataSourceBadge source={trace.data?.data_source} />}
          >
            {trace.isLoading && <p className="text-sm text-slate-500">Tracing wallet associations…</p>}
            {trace.error && <p className="text-sm text-neon-red">{trace.error.message}</p>}
            {trace.data && (
              <>
                <div className="mb-4 grid grid-cols-2 gap-3">
                  <Stat
                    label={trace.data.labeled_casino ? 'Labeled wallet' : 'Top association'}
                    value={
                      // A directly labeled cluster address is a stronger claim than
                      // an association inferred from counterparties — prefer it.
                      trace.data.labeled_casino_name ??
                      trace.data.casino_name ??
                      'unlabeled'
                    }
                    delta={
                      trace.data.labeled_casino
                        ? `known ${trace.data.labeled_casino} cluster · ${(trace.data.confidence * 100).toFixed(0)}% confidence`
                        : undefined
                    }
                    positive={Boolean(trace.data.labeled_casino)}
                  />
                  <Stat
                    label="Native balance"
                    value={trace.data.balance_native.toFixed(4)}
                  />
                </div>
                {trace.data.associations.length === 0 ? (
                  <p className="text-sm text-slate-500">
                    {trace.data.labeled_casino
                      ? 'This address is a known operator wallet. No outbound interactions with other tracked casinos in the last 30 days.'
                      : 'No interactions with tracked casino clusters in the last 30 days.'}
                  </p>
                ) : (
                  <ul className="space-y-2 text-sm">
                    {trace.data.associations.map((a) => (
                      <li
                        key={a.casino_slug}
                        className="flex items-center justify-between rounded border border-ink-700 px-3 py-2"
                      >
                        <span className="text-white">{a.casino_name}</span>
                        <div className="text-right">
                          <div className="font-mono text-slate-300">
                            {formatCount(a.interactions_30d)} tx
                          </div>
                          <div className="text-xs text-slate-500">
                            confidence {(a.cluster_confidence * 100).toFixed(0)}%
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </Panel>

          <Panel
            title="Anomaly Signals"
            subtitle="Deterministic patterns requiring review, not fraud conclusions"
            actions={<DataSourceBadge source={anomaly.data?.data_source} />}
          >
            {anomaly.isLoading && <p className="text-sm text-slate-500">Screening deterministic anomaly signals…</p>}
            {anomaly.error && <p className="text-sm text-neon-red">{anomaly.error.message}</p>}
            {anomaly.data && (
              <>
                <div className="mb-4 grid grid-cols-2 gap-3">
                  <Stat
                    label="Risk"
                    value={anomaly.data.risk_tier ?? anomaly.data.verdict}
                    positive={!anomaly.data.is_suspicious}
                  />
                  <Stat
                    label="Score"
                    value={
                      ((anomaly.data.risk_score ?? anomaly.data.score ?? 0) * 100).toFixed(0) + '%'
                    }
                  />
                </div>
                {anomaly.data.signals.length === 0 ? (
                  <p className="text-sm text-slate-500">no anomalies detected</p>
                ) : (
                  <ul className="space-y-1 text-xs font-mono text-slate-300">
                    {anomaly.data.signals.map((s, i) => (
                      <li key={i} className="rounded border border-ink-700 px-2 py-1">
                        {s}
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-3 text-xs italic text-slate-500">{anomaly.data.reasoning}</p>
              </>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}

function QuickStart({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-ink-900 p-4">
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">{label}</div>
      <div className="mt-2 text-sm text-white">{value}</div>
    </div>
  );
}
