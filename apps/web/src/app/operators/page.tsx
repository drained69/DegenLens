import Link from 'next/link';
import { telegraph } from '@/lib/telegraph';
import { Panel } from '@/components/panel';
import { PageHeader } from '@/components/page-header';
import { formatCount, formatPct, formatUsd } from '@degenlens/shared';
import type { CasinoRanking, CasinoRegistry } from '@degenlens/shared';

export const dynamic = 'force-dynamic';

async function getData() {
  try {
    const [ranking, registry] = await Promise.all([
      telegraph.askDirect<CasinoRanking>('local', '/casino/ranking?hours=168', {}, 'GET'),
      telegraph.askDirect<CasinoRegistry>('local', '/casinos', {}, 'GET'),
    ]);
    return { ranking: ranking.result, registry: registry.result };
  } catch {
    return null;
  }
}

export default async function CasinosPage({
  searchParams,
}: {
  searchParams?: { q?: string; coverage?: string; sort?: string };
}) {
  const data = await getData();
  const query = searchParams?.q?.trim().toLowerCase() ?? '';
  const coverage = searchParams?.coverage ?? 'all';
  const sort = searchParams?.sort ?? 'activity';
  const ranking = data?.ranking;
  const bySlug = new Map(ranking?.ranking.map((row) => [row.slug, row]) ?? []);
  const operators = (data?.registry.casinos ?? [])
    .filter((operator) => !query || `${operator.name} ${operator.slug}`.toLowerCase().includes(query))
    .filter((operator) => coverage === 'all' || operator.attribution_status === coverage)
    .sort((a, b) => {
      if (sort === 'name') return a.name.localeCompare(b.name);
      if (sort === 'established') return (a.established ?? 9999) - (b.established ?? 9999);
      return (bySlug.get(b.slug)?.deposits_usd ?? -1) - (bySlug.get(a.slug)?.deposits_usd ?? -1);
    });
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Directory / global operator catalog"
        title="Operator directory"
        description="Browse the full catalog, then separate observed flow from operators that still need wallet attribution."
      />
      <form action="/operators" className="grid gap-2 border border-ink-700 bg-ink-900 p-3 sm:grid-cols-[1fr_auto_auto_auto]">
        <label className="sr-only" htmlFor="operator-query">Search operators</label>
        <input id="operator-query" name="q" defaultValue={searchParams?.q} placeholder="Search operator catalog" className="min-w-0 border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:outline-none" />
        <select name="coverage" defaultValue={coverage} className="border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-xs uppercase text-slate-300">
          <option value="all">All coverage</option>
          <option value="attributed">Observed coverage</option>
          <option value="unobserved">Needs attribution</option>
        </select>
        <select name="sort" defaultValue={sort} className="border border-ink-700 bg-ink-950 px-3 py-2 font-mono text-xs uppercase text-slate-300">
          <option value="activity">Sort: activity</option>
          <option value="name">Sort: name</option>
          <option value="established">Sort: established</option>
        </select>
        <button className="border border-neon-cyan px-4 py-2 font-mono text-xs uppercase text-neon-cyan hover:bg-neon-cyan/10">Filter</button>
      </form>
      <Panel title={`${operators.length} operators in catalog`} subtitle={ranking?.reasoning ?? 'awaiting miner…'}>
        {!data && <p className="text-sm text-slate-500">The catalog is unavailable.</p>}
        {data && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {operators.map((operator) => {
              const r = bySlug.get(operator.slug);
              const attributed = operator.attribution_status === 'attributed';
              return (
              <Link
                key={operator.slug}
                href={`/operators/${operator.slug}`}
                className="hover-lift block border border-ink-700 bg-ink-800/40 p-4"
              >
                <div className="flex items-center justify-between">
                  <div className="font-semibold text-white">{operator.name}</div>
                  <div className={`font-mono text-[10px] uppercase ${attributed ? 'text-neon-green' : 'text-slate-500'}`}>{attributed ? 'observed' : 'unobserved'}</div>
                </div>
                <div className="mt-2 font-mono text-2xl text-white">{r ? formatUsd(r.deposits_usd) : 'No flow data'}</div>
                <div className="mt-1 flex items-center justify-between text-xs text-slate-400">
                  <span>{r ? `${formatPct(r.tracked_flow_share_pct ?? r.market_share_pct)} observed share` : `${operator.chains.length ? operator.chains.join(', ') : 'No indexed chains'}`}</span>
                  <span>{r ? `${formatCount(r.unique_depositors)} counterparties` : 'Attribution pending'}</span>
                </div>
              </Link>
              );
            })}
          </div>
        )}
      </Panel>
    </div>
  );
}
