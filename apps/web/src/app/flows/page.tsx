import Link from "next/link";
import type { LargeTransfers } from "@degenlens/shared";
import { formatUsd, truncateAddress } from "@degenlens/shared";
import { telegraph } from "@/lib/telegraph";
import { Panel } from "@/components/panel";
import { EvidenceClass } from "@/components/confidence";
import { DataSourceBadge, ProvenanceNotice } from "@/components/data-source";
import { PageHeader } from "@/components/page-header";

export const dynamic = "force-dynamic";

const WINDOWS = [24, 168, 720] as const;
const THRESHOLDS = [50_000, 100_000, 250_000, 1_000_000] as const;

const EXPLORER: Record<string, string> = {
  ethereum: "https://etherscan.io/tx/",
  base: "https://basescan.org/tx/",
  polygon: "https://polygonscan.com/tx/",
  arbitrum: "https://arbiscan.io/tx/",
  optimism: "https://optimistic.etherscan.io/tx/",
  bsc: "https://bscscan.com/tx/",
  avalanche: "https://snowtrace.io/tx/",
};

export default async function FlowsPage({
  searchParams,
}: {
  searchParams?: { hours?: string; min?: string };
}) {
  const hours = WINDOWS.includes(Number(searchParams?.hours) as never)
    ? Number(searchParams?.hours)
    : 24;
  const minUsd = THRESHOLDS.includes(Number(searchParams?.min) as never)
    ? Number(searchParams?.min)
    : 100_000;

  let feed: LargeTransfers | undefined;
  try {
    const res = await telegraph.askDirect<LargeTransfers>(
      "local",
      `/market/large-transfers?hours=${hours}&min_usd=${minUsd}&limit=100`,
      {},
      "GET",
    );
    feed = res.result;
  } catch {
    feed = undefined;
  }

  const rows = feed?.transfers ?? [];
  const inbound = rows.filter((r) => r.direction === "inbound");
  const outbound = rows.filter((r) => r.direction === "outbound");

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Transfer feed / individually verifiable"
        title="Large transfers."
        subtitle="Each row is one transaction."
        description="Transfers above a USD threshold touching an attributed operator cluster. This is the most directly checkable output the miner produces — every row links to a block explorer so you can confirm it without trusting us."
      />

      <div className="-mt-4 flex flex-wrap gap-4">
          <div>
            <div className="mb-1.5 font-mono text-[10px] uppercase text-slate-500">
              Window
            </div>
            <div className="flex border border-ink-700">
              {WINDOWS.map((h) => (
                <Link
                  key={h}
                  href={`/flows?hours=${h}&min=${minUsd}`}
                  className={`border-r border-ink-700 px-3 py-1.5 font-mono text-xs uppercase last:border-r-0 ${
                    h === hours
                      ? "bg-white text-ink-950"
                      : "text-slate-400 hover:bg-ink-800 hover:text-white"
                  }`}
                >
                  {h === 24 ? "24h" : h === 168 ? "7d" : "30d"}
                </Link>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-1.5 font-mono text-[10px] uppercase text-slate-500">
              Minimum size
            </div>
            <div className="flex border border-ink-700">
              {THRESHOLDS.map((m) => (
                <Link
                  key={m}
                  href={`/flows?hours=${hours}&min=${m}`}
                  className={`border-r border-ink-700 px-3 py-1.5 font-mono text-xs uppercase last:border-r-0 ${
                    m === minUsd
                      ? "bg-white text-ink-950"
                      : "text-slate-400 hover:bg-ink-800 hover:text-white"
                  }`}
                >
                  ${m >= 1_000_000 ? `${m / 1_000_000}M` : `${m / 1000}k`}
                </Link>
              ))}
            </div>
          </div>
      </div>

      <ProvenanceNotice source={feed?.data_source} />

      <div className="grid gap-3 sm:grid-cols-3">
        <Summary label="Transfers above threshold" value={String(feed?.count ?? 0)} />
        <Summary
          label="Inbound value"
          value={formatUsd(inbound.reduce((s, r) => s + r.usd_value, 0))}
          tone="in"
        />
        <Summary
          label="Outbound value"
          value={formatUsd(outbound.reduce((s, r) => s + r.usd_value, 0))}
          tone="out"
        />
      </div>

      <Panel
        title="Feed"
        subtitle={`Transfers ≥ ${formatUsd(minUsd)} in the last ${
          hours === 24 ? "24 hours" : hours === 168 ? "7 days" : "30 days"
        }`}
        actions={<DataSourceBadge source={feed?.data_source} />}
      >
        {!rows.length ? (
          <p className="text-sm text-slate-500">
            No transfers above this threshold in the window. Lower the minimum or
            widen the window.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-ink-700 text-left font-mono text-[10px] uppercase text-slate-500">
                  <th className="py-2 pr-4">Value</th>
                  <th className="py-2 pr-4">Dir</th>
                  <th className="py-2 pr-4">Operator</th>
                  <th className="py-2 pr-4">Asset</th>
                  <th className="py-2 pr-4">Counterparty</th>
                  <th className="py-2 pr-4">Chain</th>
                  <th className="py-2 pr-4">When</th>
                  <th className="py-2 pr-4">Tx</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={`${r.tx_hash}-${r.direction}-${r.usd_value}`}
                    className="border-b border-ink-800/60 hover:bg-ink-800/40"
                  >
                    <td className="py-2.5 pr-4 font-mono text-white">
                      {formatUsd(r.usd_value)}
                    </td>
                    <td className="py-2.5 pr-4">
                      <span
                        className={`font-mono text-[10px] uppercase ${
                          r.direction === "inbound"
                            ? "text-neon-green"
                            : "text-neon-red"
                        }`}
                      >
                        {r.direction === "inbound" ? "in" : "out"}
                      </span>
                    </td>
                    <td className="py-2.5 pr-4">
                      <Link
                        href={`/operators/${r.operator_slug}`}
                        className="text-white hover:text-neon-cyan"
                      >
                        {r.operator_name}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-xs text-slate-300">
                      {r.token}
                    </td>
                    <td className="py-2.5 pr-4">
                      <Link
                        href={`/wallet?address=${r.counterparty}`}
                        className="font-mono text-xs text-slate-400 hover:text-neon-cyan"
                      >
                        {truncateAddress(r.counterparty)}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-xs text-slate-500">
                      {r.chain}
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-xs text-slate-500">
                      {r.timestamp.slice(5, 16).replace("T", " ")}
                    </td>
                    <td className="py-2.5 pr-4">
                      <a
                        href={`${EXPLORER[r.chain] ?? EXPLORER.ethereum}${r.tx_hash}`}
                        target="_blank"
                        rel="noreferrer"
                        className="font-mono text-xs text-neon-cyan hover:underline"
                      >
                        verify ↗
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="mt-4 border-t border-ink-700 pt-3">
              <EvidenceClass kind="observed" />
              <p className="mt-2 text-xs leading-5 text-slate-500">
                Each row is a chain transaction. The operator association is a
                separate attribution claim — the transfer is observed, whose
                wallet it touched is a label. Direction describes the transfer,
                not its purpose: an inbound transfer is not proof of a player
                deposit.
              </p>
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}

function Summary({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "in" | "out";
}) {
  const color =
    tone === "in" ? "text-neon-green" : tone === "out" ? "text-neon-red" : "text-white";
  return (
    <div className="border border-ink-700 bg-ink-800/50 p-4">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 font-mono text-2xl font-semibold ${color}`}>{value}</div>
    </div>
  );
}
