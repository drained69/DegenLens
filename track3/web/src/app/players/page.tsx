import Link from "next/link";
import { formatCount, formatUsd, truncateAddress } from "@degenlens/shared";
import { telegraph, telegraphMinerId } from "@/lib/telegraph";
import { Panel, Stat } from "@/components/panel";
import { EvidenceClass } from "@/components/confidence";
import { DataSourceBadge, ProvenanceNotice } from "@/components/data-source";
import { PageHeader } from "@/components/page-header";

export const dynamic = "force-dynamic";

interface LeaderRow {
  address: string;
  sent_to_operators_usd: number;
  received_from_operators_usd: number;
  net_position_usd: number;
  gross_flow_usd: number;
  transfers: number;
  operators_touched: number;
  operators: string[];
  chains: string[];
  entity_class: string;
  classification_reasons: string[];
}

interface CasinoBoard {
  slug: string;
  name: string;
  addresses_observed: number;
  individual_candidates: number;
  chains_attributed: string[];
  chains_observed: string[];
  by_settlement_volume: LeaderRow[];
  net_received: LeaderRow[];
  net_sent: LeaderRow[];
  data_source?: string;
}

interface Leaderboard {
  window_hours: number;
  addresses_observed: number;
  class_counts: Record<string, number>;
  individual_candidates: number;
  infrastructure_excluded: number;
  one_directional_excluded: number;
  largest_one_directional: LeaderRow[];
  net_positive: LeaderRow[];
  net_negative: LeaderRow[];
  by_volume: LeaderRow[];
  by_casino: CasinoBoard[];
  chains_attributed: string[];
  chains_observed: string[];
  methodology: string;
  data_source?: string;
  coverage_complete?: boolean;
  casino?: string | null;
}

const WINDOWS = [168, 720] as const;

export default async function PlayersPage({
  searchParams,
}: {
    searchParams?: { hours?: string; player?: string; casino?: string };
}) {
  const hours = WINDOWS.includes(Number(searchParams?.hours) as never)
    ? Number(searchParams?.hours)
    : 168;
  const casino = searchParams?.casino || "stake";

  let lb: Leaderboard | undefined;
  try {
    const res = await telegraph.askDirect<Leaderboard>(
      telegraphMinerId,
      `/players/leaderboard?hours=${hours}&limit=15&casino=${encodeURIComponent(casino)}`,
      {},
      "GET",
    );
    lb = res.result;
  } catch {
    lb = undefined;
  }

  const counts = lb?.class_counts ?? {};
  const observed = lb?.addresses_observed ?? 0;

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Counterparty analysis / settlement-derived"
        title="Player evaluation."
        subtitle="Settlement direction, not profit and loss."
        description={
          <>
            Every address that transacted with an attributed operator cluster,
            classified by behaviour. Net position is what an address received
            from operators minus what it sent them — a verifiable sum over
            transactions, but <em>not</em> gambling winnings.
          </>
        }
        actions={
          <div className="flex flex-wrap gap-2">
          <nav className="flex border border-ink-700" aria-label="Operator">
            {['stake', 'rollbit', 'bcgame', 'shuffle'].map((slug) => (
              <Link key={slug} href={`/players?hours=${hours}&casino=${slug}`} aria-current={slug === casino ? 'page' : undefined} className={`border-r border-ink-700 px-3 py-2 font-mono text-xs uppercase last:border-r-0 ${slug === casino ? 'bg-white text-ink-950' : 'text-slate-400 hover:bg-ink-800 hover:text-white'}`}>{slug}</Link>
            ))}
          </nav>
          <nav className="flex border border-ink-700" aria-label="Window">
            {WINDOWS.map((h) => (
              <Link
                key={h}
                href={`/players?hours=${h}&casino=${casino}`}
                aria-current={h === hours ? "page" : undefined}
                className={`border-r border-ink-700 px-4 py-2 font-mono text-xs uppercase last:border-r-0 ${
                  h === hours
                    ? "bg-white text-ink-950"
                    : "text-slate-400 hover:bg-ink-800 hover:text-white"
                }`}
              >
                {h === 168 ? "7d" : "30d"}
              </Link>
            ))}
          </nav>
          </div>
        }
      />

      <ProvenanceNotice source={lb?.data_source} />

      <Panel
        title="Analyze a player"
        subtitle="Wallet analysis is live; usernames require a verified operator identity link"
        actions={<EvidenceClass kind="inferred" />}
      >
        <form action="/wallet" className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <label className="sr-only" htmlFor="player-identity">Player username or wallet</label>
          <input
            id="player-identity"
            name="address"
            required
            placeholder="0x wallet address"
            className="border border-ink-700 bg-ink-950 px-4 py-3 font-mono text-sm text-white outline-none placeholder:text-slate-600 focus:border-neon-cyan"
          />
          <button className="border border-neon-cyan px-5 py-3 font-mono text-xs uppercase text-neon-cyan hover:bg-neon-cyan hover:text-ink-950">
            Analyze wallet
          </button>
        </form>
        <p className="mt-3 text-xs leading-5 text-slate-500">
          Casino usernames are off-chain account identifiers and cannot be resolved from
          Alchemy. Username analysis will only be enabled for operators that provide a
          verified public username-to-wallet or bet-history feed; guessing that link would
          attribute another person&apos;s activity.
        </p>
      </Panel>

      <div className="border-l-2 border-neon-amber bg-ink-900 p-4 text-xs leading-5 text-slate-400">
        <span className="font-semibold uppercase text-neon-amber">
          Why this is not a winners board.
        </span>{" "}
        On-chain settlement only shows a result when an address both sends to and
        receives from an operator inside the window. Most do not: players often
        deposit from one address and withdraw to another, and balances held inside
        a casino are invisible entirely. Non-wager flows — affiliate payouts,
        rakeback, bonuses, staff payments — move in the same direction as winnings
        and cannot be told apart. Addresses without a round trip are reported
        separately rather than ranked.
      </div>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Addresses observed" value={formatCount(observed)} />
        <Stat
          label="Bidirectional candidates"
          value={formatCount(counts.individual_candidate ?? 0)}
          delta={
            observed
              ? `${(((counts.individual_candidate ?? 0) / observed) * 100).toFixed(1)}% of observed`
              : undefined
          }
          positive
        />
        <Stat
          label="One-directional"
          value={formatCount(counts.one_directional ?? 0)}
        />
        <Stat
          label="Infrastructure"
          value={formatCount(counts.infrastructure ?? 0)}
        />
      </section>

      {!lb ? (
        <Panel title="Unavailable">
          <p className="text-sm text-slate-500">The miner is unreachable.</p>
        </Panel>
      ) : (
        <>
          <div className="grid gap-5 lg:grid-cols-2">
            <Board
              title="Net positive position"
              subtitle="Received more from operators than sent, with a round trip present"
              rows={lb.net_positive}
              tone="pos"
              source={lb.data_source}
            />
            <Board
              title="Net negative position"
              subtitle="Sent more to operators than received, with a round trip present"
              rows={lb.net_negative}
              tone="neg"
              source={lb.data_source}
            />
          </div>

       <Panel
            title={`${casino} player board`}
            subtitle="Operator-scoped settlement activity. Username identity is shown only when supplied by a verified operator feed."
            actions={<DataSourceBadge source={lb.data_source} />}
          >
            <div className="mb-4 flex flex-wrap gap-2 font-mono text-[10px] uppercase text-slate-500">
              <span>Observed chains:</span>
              {(lb.chains_observed ?? []).length ? lb.chains_observed.map((chain) => (
                <span key={chain} className="border border-ink-700 px-2 py-1 text-slate-300">{chain}</span>
              )) : <span>none in this window</span>}
              {(lb.chains_attributed ?? []).length > 0 && (
                <span className="text-slate-600">· seed claims: {lb.chains_attributed.join(', ')}</span>
              )}
            </div>
            <div className="space-y-6">
              {(lb.by_casino ?? []).filter((board) => board.slug === casino).map((casino) => (
                <section key={casino.slug} className="border border-ink-700">
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 bg-ink-950 px-4 py-3">
                    <div>
                       <Link href={`/operators/${casino.slug}`} className="font-semibold text-white hover:text-neon-cyan">{casino.name}</Link>
                      <div className="mt-1 font-mono text-[10px] uppercase text-slate-500">
                        {casino.addresses_observed} counterparties / {casino.individual_candidates} bidirectional candidates
                      </div>
                    </div>
                    <span className="font-mono text-[10px] uppercase text-slate-500">
                      {(casino.chains_observed ?? []).join(" / ") || "no observed chains"}
                    </span>
                  </div>
                  <div className="grid gap-px bg-ink-700 lg:grid-cols-3">
                    <CasinoList title="Highest settlement volume" metric="volume" rows={casino.by_settlement_volume} />
                    <CasinoList title="Net received" metric="net" rows={casino.net_received} tone="pos" />
                    <CasinoList title="Net sent" metric="net" rows={casino.net_sent} tone="neg" />
                  </div>
                </section>
              ))}
            </div>
            <p className="mt-4 border-t border-ink-700 pt-4 text-xs leading-5 text-slate-500">
              These are blockchain settlement rankings, not username, wager, profit, or loss rankings.
              True amount wagered and casino P&amp;L require an operator bet-history feed because
              individual wagers settle inside the casino&apos;s private ledger.
            </p>
          </Panel>

          <Panel
            title="Largest one-directional flows"
            subtitle="Single-leg movements — no round trip, so no settled result to rank"
            actions={<EvidenceClass kind="observed" />}
          >
            {!lb.largest_one_directional?.length ? (
              <p className="text-sm text-slate-500">None observed.</p>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b border-ink-700 text-left font-mono text-[10px] uppercase text-slate-500">
                        <th className="py-2 pr-4">Address</th>
                        <th className="py-2 pr-4 text-right">Gross</th>
                        <th className="py-2 pr-4">Direction</th>
                        <th className="py-2 pr-4 text-right">Transfers</th>
                        <th className="py-2 pr-4">Operators</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lb.largest_one_directional.slice(0, 10).map((r) => (
                        <tr
                          key={r.address}
                          className="border-b border-ink-800/60 hover:bg-ink-800/40"
                        >
                          <td className="py-2.5 pr-4">
                            <Link
                              href={`/wallet?address=${r.address}`}
                              className="font-mono text-xs text-slate-300 hover:text-neon-cyan"
                            >
                              {truncateAddress(r.address, 6)}
                            </Link>
                          </td>
                          <td className="py-2.5 pr-4 text-right font-mono text-white">
                            {formatUsd(r.gross_flow_usd)}
                          </td>
                          <td className="py-2.5 pr-4">
                            <span
                              className={`font-mono text-[10px] uppercase ${
                                r.received_from_operators_usd >
                                r.sent_to_operators_usd
                                  ? "text-neon-green"
                                  : "text-neon-red"
                              }`}
                            >
                              {r.received_from_operators_usd >
                              r.sent_to_operators_usd
                                ? "received only"
                                : "sent only"}
                            </span>
                          </td>
                          <td className="py-2.5 pr-4 text-right font-mono text-xs text-slate-400">
                            {r.transfers}
                          </td>
                          <td className="py-2.5 pr-4 font-mono text-xs text-slate-500">
                            {r.operators.join(", ")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-4 border-t border-ink-700 pt-3 text-xs leading-5 text-slate-500">
                  A single-leg movement is a transfer, not a result. These are
                  consistent with treasury movement, an OTC leg, a bridge hop, or
                  simply a player whose other leg falls outside this window or
                  uses a different address.
                </p>
              </>
            )}
          </Panel>

          <Panel title="Methodology" subtitle="Read every figure above through this">
            <p className="text-sm leading-6 text-slate-300">{lb.methodology}</p>
          </Panel>
        </>
      )}
    </div>
  );
}

function CasinoList({
  title,
  rows,
  metric,
  tone,
}: {
  title: string;
  rows: LeaderRow[];
  metric: "volume" | "net";
  tone?: "pos" | "neg";
}) {
  return (
    <div className="bg-ink-900 p-4">
      <h3 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{title}</h3>
      {!rows?.length ? <p className="mt-3 text-xs text-slate-600">No observed counterparties.</p> : (
        <ol className="mt-3 space-y-2">
          {rows.slice(0, 5).map((row, index) => (
            <li key={row.address} className="flex items-center justify-between gap-3 text-xs">
              <Link href={`/wallet?address=${row.address}`} className="font-mono text-slate-300 hover:text-neon-cyan">
                <span className="mr-2 text-slate-600">{String(index + 1).padStart(2, "0")}</span>
                <span title="Username unavailable from blockchain data">{truncateAddress(row.address, 5)}</span>
              </Link>
              <span className={`font-mono ${tone === "pos" ? "text-neon-green" : tone === "neg" ? "text-neon-red" : "text-white"}`}>
                {formatUsd(metric === "volume" ? row.gross_flow_usd : row.net_position_usd)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Board({
  title,
  subtitle,
  rows,
  tone,
  source,
}: {
  title: string;
  subtitle: string;
  rows: LeaderRow[];
  tone: "pos" | "neg";
  source?: string;
}) {
  return (
    <Panel
      title={title}
      subtitle={subtitle}
      actions={<DataSourceBadge source={source} />}
    >
      {!rows?.length ? (
        <p className="text-sm text-slate-500">
          No addresses with a round trip in this window. That is the normal
          result — see the note above.
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.slice(0, 8).map((r) => (
            <li
              key={r.address}
              className="border border-ink-700 px-3 py-2.5"
            >
              <div className="flex items-center justify-between gap-3">
                <Link
                  href={`/wallet?address=${r.address}`}
                  className="font-mono text-xs text-slate-300 hover:text-neon-cyan"
                >
                  {truncateAddress(r.address, 6)}
                </Link>
                <span
                  className={`font-mono text-sm ${
                    tone === "pos" ? "text-neon-green" : "text-neon-red"
                  }`}
                >
                  {formatUsd(r.net_position_usd)}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between font-mono text-[10px] text-slate-500">
                <span>
                  out {formatUsd(r.sent_to_operators_usd)} · in{" "}
                  {formatUsd(r.received_from_operators_usd)}
                </span>
                <span>
                  {r.transfers} tx · {r.operators.join(", ")}
                  {r.chains?.length ? ` · ${r.chains.join(", ")}` : ""}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
