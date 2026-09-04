import { telegraph, telegraphMinerId } from "@/lib/telegraph";
import type { TransactionLookup } from "@degenlens/shared";
import { truncateAddress } from "@degenlens/shared";
import { Panel, Stat } from "@/components/panel";
import { ConfidenceBadge, EvidenceClass } from "@/components/confidence";
import { DataSourceBadge, ProvenanceNotice } from "@/components/data-source";

export const dynamic = "force-dynamic";

async function lookup(hash: string, chain: string) {
  try {
    return await telegraph.askDirect<TransactionLookup>(telegraphMinerId, "/transaction/lookup", { tx_hash: hash, chain });
  } catch {
    return null;
  }
}

export default async function TransactionPage({ params, searchParams }: { params: { hash: string }; searchParams?: { chain?: string } }) {
  const chain = searchParams?.chain ?? "ethereum";
  const response = await lookup(params.hash, chain);
  const tx = response?.result;
  return <div className="space-y-6">
    <header className="border-b border-ink-700 pb-6"><div className="font-mono text-[10px] uppercase text-neon-cyan">Transaction investigation / {chain}</div><h1 className="mt-2 break-all font-mono text-xl text-white sm:text-2xl">{params.hash}</h1></header>
    {!tx ? <div className="border border-neon-red/50 bg-ink-900 p-6 text-slate-400">The miner could not execute this lookup.</div> : <>
      <ProvenanceNotice source={tx.data_source} />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Stat label="Status" value={tx.verdict} /><Stat label="Block" value={tx.block_number?.toString() ?? "pending"} /><Stat label="Native value" value={(tx.value_native ?? 0).toFixed(6)} /><Stat label="Classification" value={tx.classification ?? "unavailable"} /></div>
      <div className="grid gap-5 lg:grid-cols-[1.25fr_.75fr]">
        <Panel title="Observed RPC facts" subtitle="Directly returned by the selected chain RPC" actions={<EvidenceClass kind="observed" />}><dl className="space-y-3 text-sm"><Row label="From" value={tx.from_address ?? "unavailable"} /><Row label="To" value={tx.to_address ?? "contract creation"} /><Row label="Value (wei)" value={tx.value_wei ?? "unavailable"} /><Row label="Gas limit" value={tx.gas?.toString() ?? "unavailable"} /><Row label="Method input" value={tx.input ? `${tx.input.slice(0, 34)}${tx.input.length > 34 ? "..." : ""}` : "unavailable"} /></dl></Panel>
        <Panel title="Entity attribution" subtitle="Registry claims are separate from transaction facts" actions={<ConfidenceBadge value={tx.confidence} />}>
          {tx.associations.length === 0 ? <p className="text-sm text-slate-500">Neither endpoint matches the current operator registry.</p> : <ul className="space-y-3">{tx.associations.map((association) => <li key={`${association.direction}-${association.address}`} className="border border-ink-700 p-3"><div className="flex justify-between gap-3"><div><div className="text-sm font-medium text-white">{association.operator_name}</div><div className="mt-1 font-mono text-xs text-slate-500">{association.direction} / {association.role} / {truncateAddress(association.address)}</div></div><ConfidenceBadge value={association.confidence} status={association.evidence_status} /></div></li>)}</ul>}
        </Panel>
      </div>
      <Panel title="Method and evidence" subtitle="What supports this answer" actions={<DataSourceBadge source={tx.data_source} />}><p className="text-sm leading-6 text-slate-300">{tx.reasoning}</p><div className="mt-4 border-l-2 border-neon-cyan pl-4 font-mono text-xs text-slate-400">Method: {tx.method}<br />Evidence: transaction {tx.evidence[0]?.tx_hash ?? "unavailable"}</div></Panel>
    </>}
  </div>;
}

function Row({ label, value }: { label: string; value: string }) { return <div className="grid gap-1 border-b border-ink-800 pb-3 sm:grid-cols-[120px_1fr]"><dt className="text-slate-500">{label}</dt><dd className="break-all font-mono text-xs text-white">{value}</dd></div>; }
