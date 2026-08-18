'use client';

import { useEffect, useState } from 'react';
import useSWR from 'swr';
import type { AnomalyReport, WalletTrace } from '@degenlens/shared';
import { formatCount } from '@degenlens/shared';
import { Panel, Stat } from '@/components/panel';
import { DataSourceBadge, ProvenanceNotice } from '@/components/data-source';
import { PageHeader } from '@/components/page-header';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function WalletPage() {
  const [address, setAddress] = useState('');
  const [target, setTarget] = useState<string>('');
  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get('address');
    if (initial) { setAddress(initial); setTarget(initial); }
  }, []);
  const trace = useSWR<WalletTrace>(
    target ? `/api/wallet/trace?address=${encodeURIComponent(target)}` : null,
    fetcher,
  );
  const anomaly = useSWR<AnomalyReport>(
    target ? `/api/wallet/anomaly?address=${encodeURIComponent(target)}` : null,
    fetcher,
  );

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
        className="flex gap-2"
      >
        <input
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder="0x..."
          className="flex-1 border border-ink-700 bg-ink-900 px-4 py-2 font-mono text-sm text-white placeholder:text-slate-600 focus:border-neon-cyan focus:outline-none"
        />
        <button
          type="submit"
          className="border border-neon-cyan bg-neon-cyan/10 px-4 py-2 text-sm font-semibold text-neon-cyan hover:bg-neon-cyan/20"
        >
          Trace
        </button>
      </form>

      {target && trace.data && <ProvenanceNotice source={trace.data.data_source} />}

      {target && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel
            title="Associations"
            subtitle="Casinos this wallet has interacted with (30d)"
            actions={<DataSourceBadge source={trace.data?.data_source} />}
          >
            {trace.isLoading && <p className="text-sm text-slate-500">tracing…</p>}
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
            {anomaly.isLoading && <p className="text-sm text-slate-500">scanning…</p>}
            {anomaly.data && (
              <>
                <div className="mb-4 grid grid-cols-2 gap-3">
                  <Stat
                    label="Verdict"
                    value={anomaly.data.verdict}
                    positive={anomaly.data.verdict === 'normal'}
                  />
                  <Stat label="Score" value={(anomaly.data.score * 100).toFixed(0) + '%'} />
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
