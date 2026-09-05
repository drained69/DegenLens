import Link from "next/link";
import type { CasinoRanking, CasinoRegistry, CasinoStats } from "@degenlens/shared";
import { formatCount, formatUsd } from "@degenlens/shared";
import { catalogDirect } from "@/lib/catalog";
import { startSentinel } from "@/lib/sentinel/engine";
import { EvidenceClass } from "@/components/confidence";
import { DataSourceBadge } from "@/components/data-source";

export const dynamic = "force-dynamic";

async function direct<T>(endpoint: string) {
  return catalogDirect<T>(endpoint);
}

async function stats(slug: string, hours: number) {
  return catalogDirect<CasinoStats>("/casino/stats", { slug, hours }, "POST");
}

export default async function IntelligencePage() {
  // Any site visit arms the Sentinel agent's scheduled loop (idempotent).
  startSentinel();
  // Keep the landing page cheap and reliable. Ranking is an explicit operator
  // workflow; triggering two full registry scans during every homepage render
  // starves the miner when several visitors arrive together.
  const [dayResponse, registryResponse] = await Promise.all([
    direct<CasinoRanking>("/casino/ranking?hours=24"),
    direct<CasinoRegistry>("/casinos"),
  ]);
  const day = dayResponse;
  const registry = registryResponse;
  const rankingRows = day?.ranking ?? [];
  // Ranking may contain a stale zero placeholder after a partial chain read.
  // Recover those operators from their canonical stats endpoint before showing
  // the market strip or activity table.
  const fallbackOperators = rankingRows.length
    ? rankingRows
    : (registry?.casinos ?? []).filter((operator) => operator.wallet_count > 0).map((operator, index) => ({
        rank: index + 1,
        slug: operator.slug,
        name: operator.name,
        deposits_usd: 0,
        withdrawals_usd: 0,
        net_flow_usd: 0,
        market_share_pct: 0,
        unique_depositors: 0,
        transaction_count: 0,
        confidence: 0,
        data_source: "unavailable" as const,
        coverage_complete: false,
      }));
  const zeroRows = fallbackOperators.filter(
    (row) => row.deposits_usd === 0 && row.withdrawals_usd === 0,
  );
  const recovered = await Promise.all(zeroRows.map((row) => stats(row.slug, 24)));
  const recoveredBySlug = new Map(
    zeroRows.map((row, index) => [row.slug, recovered[index]]),
  );
  const rows = fallbackOperators.map((row) => {
    const current = recoveredBySlug.get(row.slug);
    return current && current.data_source !== "unavailable"
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
      : row;
  });
  const claims = registry?.casinos.flatMap((operator) => operator.wallets ?? []) ?? [];
  const inbound = rows.reduce((sum, row) => sum + row.deposits_usd, 0);
  const outbound = rows.reduce((sum, row) => sum + row.withdrawals_usd, 0);
  const transfers = rows.reduce((sum, row) => sum + (row.transaction_count ?? 0), 0);
  // A ranking can contain measured operators while one optional operator read
  // is unavailable. Use the rows and keep the endpoint provenance visible.
  const flowAvailable = rows.some((row) => row.data_source !== "unavailable");
  const registryAvailable = Boolean(registryResponse);

  return (
    <div className="terminal-page-space">
      <section className="terminal-command-zone">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="terminal-kicker">DegenMiner / Telegraph intelligence supply</div>
            <h1 className="mt-2 text-[2rem] font-semibold leading-[1.05] tracking-[-0.02em] text-ink-1000 sm:text-[2.75rem]">
              Evidence for the
              <span className="mt-0.5 block font-serif font-normal italic text-slate-400">
                on-chain gambling economy.
              </span>
            </h1>
          </div>
          <div className="flex items-center gap-3 font-mono text-[9px] uppercase tracking-[.12em] text-slate-500">
            <span className="flex items-center gap-2"><span className="live-dot bg-neon-green" /> feed live</span>
            <span className="hidden text-slate-700 sm:inline">/</span>
            <span className="hidden sm:inline">scope: attributed flows</span>
          </div>
        </div>
        <form action="/search" className="terminal-global-search mt-6">
          <span className="terminal-search-symbol" aria-hidden="true">⌕</span>
          <label htmlFor="terminal-search" className="sr-only">Search the intelligence graph</label>
          <input id="terminal-search" name="q" placeholder="Search operator, wallet, transaction, infrastructure rail, or evidence..." />
          <span className="hidden border-l border-ink-700 pl-3 font-mono text-[9px] uppercase tracking-[.12em] text-slate-600 sm:inline">Enter to investigate</span>
          <button type="submit" className="terminal-search-submit">Search</button>
        </form>
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 font-mono text-[9px] uppercase tracking-[.1em] text-slate-600">
          <span>Try: <Link href="/search?q=Stake" className="text-slate-400 hover:text-neon-cyan">Stake</Link></span>
          <span>or paste a complete wallet address</span>
        </div>
      </section>

      <section className="market-strip" aria-label="Gambling market snapshot">
        <MarketMetric label="24h observed inbound" value={flowAvailable ? formatUsd(inbound) : "unavailable"} accent="text-neon-cyan" />
        <MarketMetric label="24h observed outbound" value={flowAvailable ? formatUsd(outbound) : "unavailable"} accent="text-ink-1000" />
        <MarketMetric label="Net direction" value={flowAvailable ? formatUsd(inbound - outbound) : "unavailable"} accent={inbound >= outbound ? "text-neon-green" : "text-neon-red"} />
        <MarketMetric label="Operators catalogued" value={registryAvailable ? String(registry?.attributed_count ?? 0) : "unavailable"} />
        <MarketMetric label="Wallet claims" value={registryAvailable ? formatCount(claims.length) : "unavailable"} />
        <MarketMetric label="Transfers observed" value={flowAvailable ? formatCount(transfers) : "unavailable"} />
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(280px,.75fr)]">
        <section className="terminal-panel min-w-0">
          <div className="terminal-panel-header">
            <div><div className="terminal-section-title">Intelligence stream</div><div className="terminal-meta mt-1">Live signals / ordered by materiality</div></div>
            <Link href="/flows" className="terminal-action-link">Open activity ↗</Link>
          </div>
          <div className="divide-y divide-ink-700/70">
            {!rows.length ? <div className="p-5 text-sm text-slate-500">No indexed signals are available.</div> : rows.slice(0, 6).map((row, index) => {
              const severity = index === 0 ? "confirmed" : "context";
              return (
                <Link href={`/operators/${row.slug}`} key={row.slug} className="signal-row block">
                  <div className="signal-row-index">{String(index + 1).padStart(2, "0")}</div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`signal-severity signal-severity-${severity}`}>{severity}</span>
                      <span className="terminal-meta">flow change / {row.name}</span>
                    </div>
                    <h2 className="mt-2 text-sm font-medium text-slate-100">{row.name} observed flow snapshot</h2>
                    <p className="mt-1.5 text-xs leading-5 text-slate-500">Observed {formatUsd(row.deposits_usd)} inbound and {formatUsd(row.withdrawals_usd)} outbound in the last 24 hours.</p>
                    <div className="mt-3 flex flex-wrap items-center gap-3"><EvidenceClass kind="calculated" /><span className="terminal-meta">confidence {(row.confidence * 100).toFixed(0)}%</span><span className="terminal-meta">operator/{row.slug}</span></div>
                  </div>
                  <div className={`signal-delta ${row.net_flow_usd >= 0 ? "text-neon-green" : "text-neon-red"}`}>{formatUsd(row.net_flow_usd)}</div>
                </Link>
              );
            })}
          </div>
        </section>

        <aside className="space-y-4">
          <EvidenceRail confidence={day?.confidence ?? 0} source={day?.data_source} claims={claims.length} />
          <section className="terminal-panel p-4">
            <div className="terminal-kicker text-neon-green">Analyst posture</div>
            <p className="mt-3 text-sm leading-6 text-slate-300">Observed transfers describe movement, not purpose. Gambling P/L and revenue remain unverified unless a source exposes the wager event.</p>
            <div className="mt-4 border-t border-ink-700 pt-3 font-mono text-[9px] uppercase tracking-[.1em] text-slate-500">Coverage gaps are rendered as gaps</div>
          </section>
        </aside>
      </div>

      <section className="terminal-panel">
        <div className="terminal-panel-header"><div><div className="terminal-section-title">Operator activity matrix</div><div className="terminal-meta mt-1">Observed flow / trailing 24 hours</div></div><div className="flex items-center gap-3"><DataSourceBadge source={day?.data_source} /><Link href="/operators" className="terminal-action-link">Directory ↗</Link></div></div>
        <div className="overflow-x-auto">
          <table className="terminal-table min-w-[680px]"><thead><tr><th>Rank</th><th>Entity</th><th>Inbound</th><th>Outbound</th><th>Net direction</th><th>Counterparties</th><th>State</th></tr></thead><tbody>{rows.slice(0, 8).map((row) => <tr key={row.slug}><td className="font-mono text-slate-600">{String(row.rank).padStart(2, "0")}</td><td><Link href={`/operators/${row.slug}`} className="font-medium text-slate-200 hover:text-neon-cyan">{row.name}</Link><div className="terminal-meta mt-1">operator/{row.slug}</div></td><td className="font-mono text-ink-1000">{formatUsd(row.deposits_usd)}</td><td className="font-mono text-slate-300">{formatUsd(row.withdrawals_usd)}</td><td className={`font-mono ${row.net_flow_usd >= 0 ? "text-neon-green" : "text-neon-red"}`}>{formatUsd(row.net_flow_usd)}</td><td className="font-mono text-slate-400">{formatCount(row.unique_depositors)}</td><td><EvidenceClass kind="observed" /></td></tr>)}</tbody></table>
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-3">
        <RouteTile href="/investigate" code="AGENT / 09" title="Run an investigation" text="One click: the agent pays our miner and the network for a verdict on any casino, wallet, or transaction." />
        <RouteTile href="/sentinel" code="AUTOMATE / 10" title="Watch with Sentinel" text="An autonomous agent scanning operator flow and hot-wallet balances on a schedule." />
        <RouteTile href="/network" code="NETWORK / 11" title="See the network" text="Where the agents' money goes: intents consumed, miners paid, and what came back — all live." />
      </section>
    </div>
  );
}

function MarketMetric({ label, value, accent = "text-ink-1000" }: { label: string; value: string; accent?: string }) {
  return <div className="market-metric"><div className="terminal-meta">{label}</div><div className={`mt-1.5 font-mono text-lg font-semibold ${accent}`}>{value}</div></div>;
}

function EvidenceRail({ confidence, source, claims }: { confidence: number; source?: string; claims: number }) {
  return <section className="terminal-panel"><div className="terminal-panel-header"><div><div className="terminal-section-title">Evidence posture</div><div className="terminal-meta mt-1">How to read this terminal</div></div><span className="signal-severity signal-severity-confirmed">auditable</span></div><div className="space-y-3 p-4"><EvidenceLine label="Observed" text="Direct chain or source fact" tone="text-neon-green" /><EvidenceLine label="Calculated" text="Derived from observed records" tone="text-neon-cyan" /><EvidenceLine label="Inferred" text="Attribution requiring review" tone="text-neon-amber" /><div className="border-t border-ink-700 pt-3"><div className="flex items-end justify-between"><span className="terminal-meta">Signal confidence</span><span className="font-mono text-xl text-ink-1000">{(confidence * 100).toFixed(0)}%</span></div><div className="confidence-track mt-2"><span style={{ width: `${Math.max(0, Math.min(confidence * 100, 100))}%` }} /></div><div className="mt-3 flex justify-between terminal-meta"><span>{claims} registry claims</span><span>{source ?? "unavailable"}</span></div></div><Link href="/integration" className="telegraph-stamp"><span className="live-dot bg-neon-cyan" /> Telegraph verified <span className="ml-auto">inspect ↗</span></Link></div></section>;
}

function EvidenceLine({ label, text, tone }: { label: string; text: string; tone: string }) {
  return <div className="flex items-start gap-3"><span className={`mt-1 h-1.5 w-1.5 shrink-0 ${tone.replace("text-", "bg-")}`} /><div><div className={`font-mono text-[10px] uppercase tracking-[.1em] ${tone}`}>{label}</div><div className="mt-0.5 text-xs text-slate-500">{text}</div></div></div>;
}

function RouteTile({ href, code, title, text }: { href: string; code: string; title: string; text: string }) {
  return <Link href={href} className="route-tile"><div className="terminal-kicker">{code}</div><div className="mt-2 text-sm font-medium text-ink-1000">{title}<span className="ml-2 text-neon-cyan">↗</span></div><p className="mt-1.5 text-xs leading-5 text-slate-500">{text}</p></Link>;
}
