import Link from "next/link";
import { telegraph, telegraphMinerId } from "@/lib/telegraph";
import { DataSourceBadge } from "@/components/data-source";
import { PageHeader } from "@/components/page-header";
import { formatCount, formatUsd } from "@degenlens/shared";
import type { CasinoRanking, CasinoRegistry, CasinoStats, CoverageReport } from "@degenlens/shared";

export const dynamic = "force-dynamic";

const SUPPORTED_CHAINS = ["ethereum", "base", "polygon", "arbitrum", "optimism", "bsc", "avalanche"];

async function direct<T>(endpoint: string) {
  try {
    return await telegraph.askDirect<T>(telegraphMinerId, endpoint, {}, "GET");
  } catch {
    return null;
  }
}

async function stats(slug: string) {
  try {
    return await telegraph.askDirect<CasinoStats>(
      telegraphMinerId,
      "/casino/stats",
      { slug, hours: 168 },
      "POST",
    );
  } catch {
    return null;
  }
}

export default async function OperatorsPage({
  searchParams,
}: {
  searchParams?: { q?: string; sort?: string };
}) {
  const [rankingRes, registryRes, coverageRes] = await Promise.all([
    direct<CasinoRanking>("/casino/ranking?hours=168"),
    direct<CasinoRegistry>("/casinos"),
    direct<CoverageReport>("/coverage"),
  ]);
  const ranking = rankingRes?.result;
  const registry = registryRes?.result;
  const coverage = coverageRes?.result;
  const query = searchParams?.q?.trim().toLowerCase() ?? "";
  const sort = searchParams?.sort ?? "activity";
  const unavailableRows = ranking?.ranking.filter((row) => row.data_source === "unavailable") ?? [];
  const recovered = await Promise.all(unavailableRows.map((row) => stats(row.slug)));
  const recoveredBySlug = new Map(
    unavailableRows.map((row, index) => [row.slug, recovered[index]?.result]),
  );
  const bySlug = new Map(
    ranking?.ranking.map((row) => {
      const current = recoveredBySlug.get(row.slug);
      return [
        row.slug,
        current && current.data_source !== "unavailable"
          ? {
              ...row,
              deposits_usd: current.deposits_usd,
              withdrawals_usd: current.withdrawals_usd,
              net_flow_usd: current.net_flow_usd,
              unique_depositors: current.unique_depositors,
              transaction_count: current.transaction_count,
              confidence: current.confidence,
              data_source: current.data_source,
              coverage_complete: current.coverage_complete,
            }
          : row,
      ] as const;
    }) ?? [],
  );
  const scopedOperators = (registry?.casinos ?? [])
    .filter((operator) => operator.wallet_count > 0)
    .filter((operator) => !query || `${operator.name} ${operator.slug}`.toLowerCase().includes(query))
    .sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "established") return (a.established ?? 9999) - (b.established ?? 9999);
      const aRow = bySlug.get(a.slug);
      const bRow = bySlug.get(b.slug);
      if (aRow?.data_source === "unavailable" && bRow?.data_source !== "unavailable") return 1;
      if (bRow?.data_source === "unavailable" && aRow?.data_source !== "unavailable") return -1;
      return (bRow?.deposits_usd ?? -1) - (aRow?.deposits_usd ?? -1);
    });
  const watchlist = scopedOperators.slice(0, 4);

  return (
    <div className="terminal-page-space">
      <PageHeader
        eyebrow="Operator terminal / attribution-aware"
        title="Operator directory"
        description="Review attributed operators and the wallet activity observed across indexed chains."
        actions={<DataSourceBadge source={ranking?.data_source} />}
      />

      <section className="market-strip" aria-label="Operator coverage summary">
        <Metric label="Watchlist" value={String(watchlist.length)} />
        <Metric label="In scope" value={String(registry?.attributed_count ?? 0)} />
        <Metric label="Out of scope" value={String(registry?.unattributed_count ?? 0)} />
        <Metric label="Claims" value={String(coverage?.wallet_clusters ?? 0)} />
        <Metric label="Indexed chains" value={String(SUPPORTED_CHAINS.length)} />
        <Metric label="Window" value="7 days" />
      </section>

      <section className="terminal-panel">
        <div className="terminal-panel-header">
          <div><div className="terminal-section-title">Operator watchlist</div><div className="terminal-meta mt-1">Ranked by observed inbound flow / trailing 7 days</div></div>
          <span className="terminal-meta">{watchlist.length} shown</span>
        </div>
        <div className="divide-y divide-[#eeeaf2]">
          {watchlist.map((operator, index) => {
            const row = bySlug.get(operator.slug);
            return (
              <Link
                key={operator.slug}
                href={`/operators/${operator.slug}`}
                className="grid min-h-[88px] grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 bg-[#fff] px-4 py-4 text-[#211e2b] transition hover:bg-[#f7f4fd]"
              >
                <span className="font-mono text-[10px] text-[#aaa4b1]">{String(index + 1).padStart(2, "0")}</span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-base font-semibold text-[#211e2b]">{operator.name}</h2>
                    <Status status={operator.attribution_status} />
                  </div>
                  <div className="mt-1 font-mono text-[9px] uppercase text-[#817b89]">
                    {operator.wallet_count} claims · {(operator.chains ?? []).join(", ") || "No indexed chains"}
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-mono text-sm font-semibold text-[#211e2b]">{row?.data_source === "unavailable" ? "Unavailable" : row ? formatUsd(row.deposits_usd) : "Unobserved"}</div>
                  <div className="mt-1 hidden font-mono text-[9px] uppercase text-[#817b89] sm:block">{row?.data_source === "unavailable" ? "Chain read failed" : row ? `${formatCount(row.unique_depositors)} counterparties` : "No flow data"}</div>
                </div>
              </Link>
            );
          })}
          {!watchlist.length && <div className="p-5 text-sm text-slate-500">No attributed operators are available for the watchlist.</div>}
        </div>
      </section>

      <form action="/operators" className="grid gap-2 border border-[#e3dfeb] bg-white p-3 sm:grid-cols-[1fr_auto_auto]">
        <label className="sr-only" htmlFor="operator-query">Search operators</label><input id="operator-query" name="q" defaultValue={searchParams?.q} placeholder="Search operators" className="min-w-0 border-0 bg-[#f4f1f7] px-3 py-2 text-sm text-[#211e2b] focus:outline-none" />
        <select name="sort" defaultValue={sort} className="border-0 bg-[#f4f1f7] px-3 py-2 font-mono text-xs uppercase text-slate-500"><option value="activity">Sort: activity</option><option value="name">Sort: name</option><option value="established">Sort: established</option></select>
        <button className="btn-primary px-4 py-2">Filter</button>
      </form>
      <div className="terminal-meta">{scopedOperators.length} operators match the current filters</div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="market-metric"><div className="terminal-meta">{label}</div><div className="mt-1.5 font-mono text-lg font-semibold text-white">{value}</div></div>; }
function Status({ status }: { status?: string }) { return <span className={`font-mono text-[9px] uppercase ${status === "attributed" ? "text-neon-green" : "text-slate-500"}`}>{status === "attributed" ? "attributed" : "unobserved"}</span>; }
