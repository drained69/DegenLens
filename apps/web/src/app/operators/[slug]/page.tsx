import { notFound } from 'next/navigation';
import { telegraph } from '@/lib/telegraph';
import { Panel, SignalBadge, Stat } from '@/components/panel';
import { formatCount, formatUsd } from '@degenlens/shared';
import type { CasinoStats } from '@degenlens/shared';
import type {
  AssetMix,
  CasinoRegistry,
  CounterpartyConcentration,
  FlowSeries,
} from '@degenlens/shared';
import { truncateAddress } from '@degenlens/shared';
import { ConfidenceBadge, EvidenceClass } from '@/components/confidence';
import { FlowChart, ShareBar } from '@/components/flow-chart';

export const revalidate = 300;

async function getStats(slug: string, hours: number) {
  try {
    return await telegraph.askDirect<CasinoStats>(
      'local',
      '/casino/stats',
      { slug, hours },
      'POST',
    );
  } catch {
    return null;
  }
}

async function get<T>(endpoint: string) {
  try { return await telegraph.askDirect<T>('local', endpoint, {}, 'GET'); }
  catch { return null; }
}

async function getRegistry() {
  return get<CasinoRegistry>('/casinos');
}

export default async function CasinoDetailPage({ params }: { params: { slug: string } }) {
  const { slug } = params;
  const [day, week, month, registry, seriesRes, partiesRes, assetsRes] = await Promise.all([
    getStats(slug, 24),
    getStats(slug, 168),
    getStats(slug, 720),
    getRegistry(),
    get<FlowSeries>(`/operator/${slug}/series?hours=168&bucket_hours=4`),
    get<CounterpartyConcentration>(`/operator/${slug}/counterparties?hours=168&top=10`),
    get<AssetMix>(`/market/assets?slug=${slug}&hours=168`),
  ]);
  const operator = registry?.result.casinos.find((candidate) => candidate.slug === slug);
  if (!operator) return notFound();
  if (!operator.wallets?.length || !week?.result || week.result.verdict === 'unknown_casino') {
    return (
      <div className="space-y-6">
        <div className="border-b border-ink-700 pb-7">
          <div className="font-mono text-[10px] uppercase text-slate-500">Catalog entry / unobserved</div>
          <h1 className="mt-2 text-3xl font-semibold text-white">{operator.name}</h1>
          <p className="mt-1 font-mono text-sm text-slate-500">/{operator.slug}</p>
        </div>
        <Panel title="No observed flow" subtitle="This is a coverage status, not a zero-activity result.">
          <p className="text-sm leading-6 text-slate-400">DegenLens has catalogued this operator but has no reviewed wallet attribution to query. Add evidence before treating this entry as a measurable activity source.</p>
        </Panel>
        <Panel title="Public metadata" subtitle="Identity facts are separate from wallet attribution.">
          <div className="grid gap-3 sm:grid-cols-3"><Stat label="Website" value={operator.website.replace(/^https?:\/\//, '')} /><Stat label="License" value={operator.licensed_in ?? 'Not recorded'} /><Stat label="Established" value={operator.established ? String(operator.established) : 'Not recorded'} /></div>
        </Panel>
      </div>
    );
  }
  const s = week.result;
  const series = seriesRes?.result;
  const parties = partiesRes?.result;
  const assets = assetsRes?.result;

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-white">{s.name}</h1>
          <p className="mt-1 font-mono text-sm text-slate-500">/{s.slug}</p>
        </div>
        <SignalBadge hash={week.signal_hash} />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="24h Observed Inbound" value={formatUsd(day?.result?.observed_inbound_usd ?? day?.result?.deposits_usd ?? 0)} />
        <Stat label="7d Observed Inbound" value={formatUsd(s.observed_inbound_usd ?? s.deposits_usd)} />
        <Stat label="30d Observed Inbound" value={formatUsd(month?.result?.observed_inbound_usd ?? month?.result?.deposits_usd ?? 0)} />
        <Stat
          label="7d Net Flow"
          value={formatUsd(s.net_flow_usd)}
          delta={s.net_flow_usd >= 0 ? 'inflow' : 'outflow'}
          positive={s.net_flow_usd >= 0}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Activity" subtitle="Aggregated across all labeled clusters">
          <ul className="space-y-3 text-sm">
            <li className="flex justify-between border-b border-ink-800 pb-2">
              <span className="text-slate-400">Transactions (7d)</span>
              <span className="font-mono text-white">{formatCount(s.transaction_count)}</span>
            </li>
            <li className="flex justify-between border-b border-ink-800 pb-2">
              <span className="text-slate-400">Unique inbound counterparties (7d)</span>
              <span className="font-mono text-white">{formatCount(s.unique_depositors)}</span>
            </li>
            <li className="flex justify-between border-b border-ink-800 pb-2">
              <span className="text-slate-400">Cluster confidence</span>
              <span className="font-mono text-white">{(s.confidence * 100).toFixed(1)}%</span>
            </li>
            <li className="flex justify-between">
              <span className="text-slate-400">Verdict</span>
              <span
                className={`font-mono ${s.verdict === 'net_inflow' ? 'text-neon-green' : 'text-neon-amber'}`}
              >
                {s.verdict}
              </span>
            </li>
          </ul>
        </Panel>

        <Panel title="Miner reasoning" subtitle="Provided in the signal payload">
          <p className="text-sm text-slate-300">{s.reasoning}</p>
          <div className="mt-4 text-xs text-slate-500">
            <div>
              Cost: <span className="font-mono text-white">${week.cost_usd.toFixed(3)}</span>
              {'  ·  '}
              Duration: <span className="font-mono text-white">{week.duration_ms}ms</span>
            </div>
            <div className="mt-1">
              Miner:{' '}
              <span className="font-mono text-white">
                {week.miner_name} #{week.miner_id}
              </span>
            </div>
          </div>
        </Panel>
      </div>
      <Panel
        title="Observed flow over time"
        subtitle="7 days in 4-hour buckets — inbound against outbound"
        actions={<EvidenceClass kind="calculated" />}
      >
        {!series?.series?.length ? (
          <p className="text-sm text-slate-500">
            {series?.error
              ? 'This operator has no reviewed wallet claim, so its flow is unobserved. That is not a statement that its flow is zero.'
              : 'No observed transfers in this window.'}
          </p>
        ) : (
          <>
            <FlowChart series={series.series} />
            {series.coverage_complete === false && (
              <p className="mt-3 text-xs leading-5 text-neon-amber">
                Partial coverage — the upstream page budget was exhausted before
                this window was fully traversed. Bars are lower bounds.
              </p>
            )}
          </>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel
          title="Counterparty concentration"
          subtitle="Who the observed flow actually moves between"
          actions={<EvidenceClass kind="calculated" />}
        >
          {!parties?.counterparties?.length ? (
            <p className="text-sm text-slate-500">No counterparties observed.</p>
          ) : (
            <>
              <div className="mb-4 border border-ink-700 bg-ink-800/50 p-4">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">
                  Top 10 share of observed flow
                </div>
                <div className="mt-1 font-mono text-3xl font-semibold text-white">
                  {(parties.top10_share_of_observed_flow_pct ?? 0).toFixed(1)}%
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  across {formatCount(parties.distinct_counterparties ?? 0)} distinct
                  addresses
                </p>
              </div>
              <ul className="space-y-1.5">
                {parties.counterparties.slice(0, 6).map((c) => (
                  <li
                    key={c.address}
                    className="flex items-center justify-between border border-ink-700 px-3 py-2"
                  >
                    <span className="font-mono text-xs text-slate-300">
                      {truncateAddress(c.address, 6)}
                    </span>
                    <span className="text-right">
                      <span className="font-mono text-xs text-white">
                        {formatUsd(c.total_usd)}
                      </span>
                      <span className="ml-2 font-mono text-[10px] text-slate-500">
                        {(c.share_of_observed_flow_pct ?? 0).toFixed(1)}%
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs leading-5 text-slate-500">
                High concentration indicates routing, bridge, or exchange activity
                rather than broad user behaviour. Read the headline totals through
                this figure.
              </p>
            </>
          )}
        </Panel>

        <Panel
          title="Asset composition"
          subtitle="What this operator's observed flow is denominated in"
          actions={<EvidenceClass kind="calculated" />}
        >
          {!assets?.assets?.length ? (
            <p className="text-sm text-slate-500">No observed flow.</p>
          ) : (
            <>
              <div className="mb-4 border border-ink-700 bg-ink-800/50 p-4">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">
                  Stablecoin share
                </div>
                <div className="mt-1 font-mono text-3xl font-semibold text-white">
                  {assets.stablecoin_share_pct.toFixed(1)}%
                </div>
              </div>
              <ShareBar
                rows={assets.assets.slice(0, 5).map((a) => ({
                  label: a.symbol,
                  pct: a.share_of_observed_inbound_pct,
                  usd: a.inbound_usd,
                  accent: a.is_stablecoin ? 'bg-neon-green' : 'bg-neon-amber',
                }))}
              />
            </>
          )}
        </Panel>
      </div>

      <Panel title="Attribution claims" subtitle="Registry evidence is separate from observed chain activity" actions={<EvidenceClass kind="inferred" />}>
        {!operator?.wallets?.length ? <p className="text-sm text-slate-500">No address claims are available.</p> : <div className="divide-y divide-ink-700">{operator.wallets.map((wallet) => <div key={wallet.address} className="grid gap-3 py-4 first:pt-0 last:pb-0 sm:grid-cols-[1fr_auto]"><div><div className="break-all font-mono text-xs text-white">{wallet.address}</div><div className="mt-1 text-xs text-slate-500">{wallet.chain} / {wallet.role} / reviewed {wallet.last_reviewed}</div><p className="mt-2 text-xs text-slate-500">{wallet.evidence.length ? `${wallet.evidence.length} evidence source(s)` : 'No supporting source attached. Treat as an investigative seed, not a verified ownership claim.'}</p></div><ConfidenceBadge value={wallet.confidence} status={wallet.evidence_status} /></div>)}</div>}
      </Panel>
      <div className="border-l-2 border-neon-amber bg-ink-900 p-4 text-xs leading-5 text-slate-400">Observed inbound and outbound transfers can include treasury movements, exchange funding, internal sweeps, or user activity. DegenLens does not infer solvency or gambling revenue from these values alone.</div>
    </div>
  );
}
