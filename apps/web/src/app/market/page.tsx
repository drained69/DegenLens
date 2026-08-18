import Link from "next/link";
import type {
  AssetMix,
  CoverageReport,
  NetworkDistribution,
} from "@degenlens/shared";
import { formatCount, formatUsd } from "@degenlens/shared";
import { telegraph } from "@/lib/telegraph";
import { Panel, Stat } from "@/components/panel";
import { EvidenceClass } from "@/components/confidence";
import { DataSourceBadge, ProvenanceNotice } from "@/components/data-source";
import { ShareBar } from "@/components/flow-chart";
import { PageHeader } from "@/components/page-header";

export const dynamic = "force-dynamic";

async function direct<T>(endpoint: string) {
  try {
    return await telegraph.askDirect<T>("local", endpoint, {}, "GET");
  } catch {
    return null;
  }
}

const WINDOWS = [
  { hours: 24, label: "24h" },
  { hours: 168, label: "7d" },
  { hours: 720, label: "30d" },
] as const;

export default async function MarketPage({
  searchParams,
}: {
  searchParams?: { hours?: string };
}) {
  const requested = Number(searchParams?.hours);
  const hours = WINDOWS.some((w) => w.hours === requested) ? requested : 24;

  const [networksRes, assetsRes, coverageRes] = await Promise.all([
    direct<NetworkDistribution>(`/market/networks?hours=${hours}`),
    direct<AssetMix>(`/market/assets?hours=${hours}`),
    direct<CoverageReport>("/coverage"),
  ]);

  const networks = networksRes?.result;
  const assets = assetsRes?.result;
  const coverage = coverageRes?.result;

  const totalIn = networks?.total_inbound_usd ?? 0;
  const totalOut =
    networks?.chains.reduce((s, c) => s + c.outbound_usd, 0) ?? 0;
  const transfers = networks?.chains.reduce((s, c) => s + c.transfers, 0) ?? 0;

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Aggregate view / observed flow only"
        title="Market composition."
        subtitle="Scoped to what we can actually see."
        description={
          <>
            Chain and asset breakdowns across attributed operator clusters.
            Every share below is a share of <em>observed</em> flow — not market
            share. Operators without a reviewed wallet claim are unobserved,
            and contribute nothing rather than a zero.
          </>
        }
        actions={
          <nav className="flex border border-ink-700" aria-label="Window">
            {WINDOWS.map((w) => (
              <Link
                key={w.hours}
                href={`/market?hours=${w.hours}`}
                aria-current={w.hours === hours ? "page" : undefined}
                className={`border-r border-ink-700 px-4 py-2 font-mono text-xs uppercase last:border-r-0 ${
                  w.hours === hours
                    ? "bg-white text-ink-950"
                    : "text-slate-400 hover:bg-ink-800 hover:text-white"
                }`}
              >
                {w.label}
              </Link>
            ))}
          </nav>
        }
      />

      <ProvenanceNotice source={networks?.data_source} />

      {networks && !networks.coverage_complete && (
        <div className="border border-neon-amber/40 bg-neon-amber/5 px-4 py-3 text-xs leading-5 text-neon-amber">
          <span className="font-semibold uppercase">Partial coverage.</span> The
          upstream page budget was exhausted before this window was fully
          traversed. Totals below are lower bounds on observed flow, not complete
          measurements.
        </div>
      )}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label={`Observed inbound · ${hours}h`} value={formatUsd(totalIn)} />
        <Stat label={`Observed outbound · ${hours}h`} value={formatUsd(totalOut)} />
        <Stat
          label="Net direction"
          value={formatUsd(totalIn - totalOut)}
          delta={totalIn - totalOut >= 0 ? "net inbound" : "net outbound"}
          positive={totalIn - totalOut >= 0}
        />
        <Stat label="Transfers observed" value={formatCount(transfers)} />
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel
          title="Chain distribution"
          subtitle="Share of observed inbound flow by network"
          actions={<DataSourceBadge source={networks?.data_source} />}
        >
          {!networks?.chains.length ? (
            <p className="text-sm text-slate-500">
              No observed flow. The miner may be unavailable.
            </p>
          ) : (
            <>
              <ShareBar
                rows={networks.chains.map((c) => ({
                  label: c.chain,
                  pct: c.share_of_observed_inbound_pct,
                  usd: c.inbound_usd,
                }))}
              />
              <div className="mt-4 border-t border-ink-700 pt-3">
                <EvidenceClass kind="calculated" />
                <p className="mt-2 text-xs leading-5 text-slate-500">
                  Covers {networks.chains_observed} chain(s) where attributed
                  clusters exist. Chains with no attributed wallet are absent,
                  not zero.
                </p>
              </div>
            </>
          )}
        </Panel>

        <Panel
          title="Asset composition"
          subtitle="What the observed flow is denominated in"
          actions={<DataSourceBadge source={assets?.data_source} />}
        >
          {!assets?.assets.length ? (
            <p className="text-sm text-slate-500">No observed flow.</p>
          ) : (
            <>
              <div className="mb-4 border border-ink-700 bg-ink-800/50 p-4">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">
                  Stablecoin share of observed inbound
                </div>
                <div className="mt-1 font-mono text-3xl font-semibold text-white">
                  {assets.stablecoin_share_pct.toFixed(1)}%
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                  across {assets.distinct_assets} distinct assets
                </p>
              </div>
              <ShareBar
                rows={assets.assets.slice(0, 6).map((a) => ({
                  label: `${a.symbol}${a.is_stablecoin ? " ·  stable" : ""}`,
                  pct: a.share_of_observed_inbound_pct,
                  usd: a.inbound_usd,
                  accent: a.is_stablecoin ? "bg-neon-green" : "bg-neon-amber",
                }))}
              />
            </>
          )}
        </Panel>
      </div>

      <Panel
        title="Coverage"
        subtitle="What this miner can and cannot see — read every figure above through this"
      >
        {!coverage ? (
          <p className="text-sm text-slate-500">Coverage report unavailable.</p>
        ) : (
          <>
            <div className="mb-5 grid gap-3 sm:grid-cols-4">
              <Stat
                label="Operators catalogued"
                value={String(coverage.operators_catalogued)}
              />
              <Stat
                label="With wallet claims"
                value={String(coverage.operators_attributed)}
                positive
              />
              <Stat
                label="Unobserved"
                value={String(coverage.operators_unattributed)}
              />
              <Stat
                label="Wallet clusters"
                value={String(coverage.wallet_clusters)}
              />
            </div>

            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 font-mono text-[10px] uppercase text-neon-green">
                  Attributed — produce flow figures
                </h3>
                <ul className="space-y-1.5">
                  {coverage.attributed.map((o) => (
                    <li
                      key={o.slug}
                      className="flex items-center justify-between border border-ink-700 px-3 py-2 text-sm"
                    >
                      <Link
                        href={`/operators/${o.slug}`}
                        className="text-white hover:text-neon-cyan"
                      >
                        {o.name}
                      </Link>
                      <span className="font-mono text-[10px] uppercase text-slate-500">
                        {o.wallets} cluster{o.wallets === 1 ? "" : "s"} ·{" "}
                        {o.evidence_status}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <h3 className="mb-2 font-mono text-[10px] uppercase text-slate-500">
                  Catalogued — no reviewed wallet claim
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {coverage.unattributed.map((o) => (
                    <span
                      key={o.slug}
                      title={o.attribution_status}
                      className="border border-ink-700 px-2 py-1 text-xs text-slate-500"
                    >
                      {o.name}
                    </span>
                  ))}
                </div>
                <p className="mt-3 text-xs leading-5 text-slate-500">
                  These operators are unobserved. Their absence from the figures
                  above is a coverage gap, not evidence of inactivity.
                </p>
              </div>
            </div>

            <p className="mt-5 border-t border-ink-700 pt-3 text-xs leading-5 text-slate-500">
              {coverage.caveat}
            </p>
          </>
        )}
      </Panel>
    </div>
  );
}
