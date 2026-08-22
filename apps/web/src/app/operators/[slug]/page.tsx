import { notFound } from 'next/navigation';
import { telegraph, telegraphMinerId } from '@/lib/telegraph';
import { Panel, SignalBadge, Stat } from '@/components/panel';
import { formatCount, formatUsd } from '@degenlens/shared';
import type { CasinoStats } from '@degenlens/shared';
import type { CasinoRegistry } from '@degenlens/shared';
import { ConfidenceBadge, EvidenceClass } from '@/components/confidence';
import { ShareBar } from '@/components/flow-chart';

export const dynamic = 'force-dynamic';

async function getStats(slug: string, hours: number) {
  try {
    return await telegraph.askDirect<CasinoStats>(
      telegraphMinerId,
      '/casino/stats',
      { slug, hours },
      'POST',
    );
  } catch {
    return null;
  }
}

async function get<T>(endpoint: string) {
  try { return await telegraph.askDirect<T>(telegraphMinerId, endpoint, {}, 'GET'); }
  catch { return null; }
}

async function getRegistry() {
  return get<CasinoRegistry>('/casinos');
}

export default async function CasinoDetailPage({ params }: { params: { slug: string } }) {
  const { slug } = params;
  // One canonical multi-chain read keeps the detail page responsive after the
  // registry expands to many public operator wallets. Secondary analyses have
  // their own expensive chain reads and are loaded only on dedicated views.
  const [week, registry] = await Promise.all([getStats(slug, 168), getRegistry()]);
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
          <div className="grid gap-3 sm:grid-cols-3"><Stat label="Website" value={operator.website?.replace(/^https?:\/\//, '') ?? 'Not recorded'} /><Stat label="License" value={operator.licensed_in ?? 'Not recorded'} /><Stat label="Established" value={operator.established ? String(operator.established) : 'Not recorded'} /></div>
        </Panel>
      </div>
    );
  }
  const s = week.result;
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
        <Stat label="7d Observed Inbound" value={formatUsd(s.observed_inbound_usd ?? s.deposits_usd)} />
        <Stat label="7d Observed Outbound" value={formatUsd(s.observed_outbound_usd ?? s.withdrawals_usd)} />
        <Stat
          label="7d Net Flow"
          value={formatUsd(s.net_flow_usd)}
          delta={s.net_flow_usd >= 0 ? 'inflow' : 'outflow'}
          positive={s.net_flow_usd >= 0}
        />
      </div>

      <Panel title="Public operator data" subtitle="Public identity facts, separate from wallet attribution and chain observations">
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat label="Website" value={operator.website?.replace(/^https?:\/\//, '') ?? 'Not recorded'} />
          <Stat label="License" value={operator.licensed_in ?? 'Not recorded'} />
          <Stat label="Established" value={operator.established ? String(operator.established) : 'Not recorded'} />
        </div>
      </Panel>

      <Panel
        title="Flow by chain"
        subtitle="Only source-backed wallet and network pairs are queried; unregistered networks are not zero-flow claims"
        actions={<EvidenceClass kind="calculated" />}
      >
        {!s.by_chain?.length ? (
          <p className="text-sm text-slate-500">
            No priced transfers on any indexed chain in this window. Queried{' '}
            {(s.chains_queried ?? operator.queried_chains ?? []).join(', ') || 'no networks'}.
          </p>
        ) : (
          <>
            <ShareBar
              rows={s.by_chain.map((row) => ({
                label: row.chain,
                pct: row.share_of_observed_inbound_pct,
                usd: row.inbound_usd,
                status: row.status,
              }))}
            />
            <ul className="mt-4 divide-y divide-ink-800">
               {s.by_chain.map((row) => (
                <li key={row.chain} className="flex items-center justify-between py-2 text-sm">
                  <span className="font-mono text-xs uppercase text-slate-300">
                    {row.chain}{' '}
                      <span className={row.status === 'unavailable' ? 'text-neon-amber' : row.status === 'observed' ? 'text-neon-green' : 'text-slate-500'}>
                      / {row.status === 'queried_zero' ? 'queried zero' : row.status === 'not_registered' ? 'not registered' : row.status ?? 'observed'}
                    </span>
                  </span>
                  <span className="font-mono text-xs text-slate-400">
                    {row.status === 'unavailable' ? 'read unavailable' : row.status === 'not_registered' ? 'no source-backed wallet claim' : <>in {formatUsd(row.inbound_usd)} · out {formatUsd(row.outbound_usd)} · {formatCount(row.transfers)} tx</>}
                  </span>
                </li>
              ))}
            </ul>
            {(s.chains_queried ?? []).some((chain) => !(s.chains ?? []).includes(chain)) && (
              <p className="mt-3 text-xs leading-5 text-slate-500">
                Also queried, no priced flow this window:{' '}
                {(s.chains_queried ?? [])
                  .filter((chain) => !(s.chains ?? []).includes(chain))
                  .join(', ')}
                .
              </p>
            )}
            {s.coverage_complete === false && (
              <p className="mt-3 border-l-2 border-neon-amber bg-ink-900 p-3 text-xs leading-5 text-neon-amber">
                Partial multi-chain coverage. One or more indexed chain reads failed;
                totals are lower bounds for the registered wallet claims and must not be
                treated as complete operator flow.
              </p>
            )}
          </>
        )}
      </Panel>

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
      <Panel title="Attribution claims" subtitle="Registry evidence is separate from observed chain activity" actions={<EvidenceClass kind="inferred" />}>
        {!operator?.wallets?.length ? <p className="text-sm text-slate-500">No address claims are available.</p> : <div className="divide-y divide-ink-700">{operator.wallets.map((wallet) => <div key={wallet.address} className="grid gap-3 py-4 first:pt-0 last:pb-0 sm:grid-cols-[1fr_auto]"><div><div className="break-all font-mono text-xs text-white">{wallet.address}</div><div className="mt-1 text-xs text-slate-500">{wallet.chain} seed / {wallet.role} / reviewed {wallet.last_reviewed}</div><p className="mt-2 text-xs text-slate-500">Queried on {(s.chains_queried ?? operator.queried_chains ?? [wallet.chain]).join(', ')}. {wallet.evidence.length ? `${wallet.evidence.length} evidence source(s)` : 'No supporting source attached. Treat as an investigative seed, not a verified ownership claim.'}</p></div><ConfidenceBadge value={wallet.confidence} status={wallet.evidence_status} /></div>)}</div>}
      </Panel>
      <div className="border-l-2 border-neon-amber bg-ink-900 p-4 text-xs leading-5 text-slate-400">Observed inbound and outbound transfers can include treasury movements, exchange funding, internal sweeps, or user activity. DegenLens does not infer solvency or gambling revenue from these values alone.</div>
    </div>
  );
}
