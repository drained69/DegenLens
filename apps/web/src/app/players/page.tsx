import Link from "next/link";
import { formatCount, formatUsd, truncateAddress } from "@degenlens/shared";
import { telegraph } from "@/lib/telegraph";
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
  entity_class: string;
  classification_reasons: string[];
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
  methodology: string;
  data_source?: string;
  coverage_complete?: boolean;
}

const WINDOWS = [168, 720] as const;

export default async function PlayersPage({
  searchParams,
}: {
  searchParams?: { hours?: string };
}) {
  const hours = WINDOWS.includes(Number(searchParams?.hours) as never)
    ? Number(searchParams?.hours)
    : 168;

  let lb: Leaderboard | undefined;
  try {
    const res = await telegraph.askDirect<Leaderboard>(
      "local",
      `/players/leaderboard?hours=${hours}&limit=15`,
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
          <nav className="flex border border-ink-700" aria-label="Window">
            {WINDOWS.map((h) => (
              <Link
                key={h}
                href={`/players?hours=${h}`}
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
        }
      />

      <ProvenanceNotice source={lb?.data_source} />

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
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
