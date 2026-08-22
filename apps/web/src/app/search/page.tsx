import Link from "next/link";
import { telegraph, telegraphMinerId } from "@/lib/telegraph";
import type { CasinoRegistry } from "@degenlens/shared";
import { ConfidenceBadge, EvidenceClass } from "@/components/confidence";
import { PageHeader } from "@/components/page-header";

export const dynamic = "force-dynamic";

const addressPattern = /^0x[a-fA-F0-9]{40}$/;
const txPattern = /^0x[a-fA-F0-9]{64}$/;

async function registry() {
  try {
    return (await telegraph.askDirect<CasinoRegistry>(telegraphMinerId, "/casinos", {}, "GET")).result;
  } catch {
    return null;
  }
}

export default async function SearchPage({ searchParams }: { searchParams?: { q?: string } }) {
  const query = searchParams?.q?.trim() ?? "";
  const catalog = await registry();
  const operators = query && catalog
    ? catalog.casinos.filter((operator) => `${operator.name} ${operator.slug}`.toLowerCase().includes(query.toLowerCase()))
    : [];

  return (
    <div className="space-y-7">
      <PageHeader
        eyebrow="Investigation"
        title="Search the intelligence graph"
        description="Resolve an operator, wallet, or transaction into evidence-backed investigation paths."
      />
      <form action="/search" className="flex border border-ink-600 bg-ink-900/80">
        <input autoFocus name="q" defaultValue={query} placeholder="Stake, 0x address, or transaction hash" className="min-w-0 flex-1 bg-transparent px-4 py-4 font-mono text-sm text-white placeholder:text-slate-600 focus:outline-none" />
        <button className="border-l border-ink-600 px-5 font-mono text-xs uppercase tracking-[0.12em] text-neon-cyan hover:bg-ink-800">Investigate</button>
      </form>

      {!query && <EmptySearch />}
      {query && txPattern.test(query) && <div className="border border-ink-700 bg-ink-900 p-5 text-sm text-slate-400">Transaction hashes need a chain before they can be resolved. Open the transaction from the transfer feed, where chain context is included.</div>}
      {query && addressPattern.test(query) && <Result href={`/wallet?address=${query}`} label="Wallet or contract" title={query} detail="Trace balance, operator exposure, and anomaly signals." kind="calculated" />}
      {operators.map((operator) => (
        <Result key={operator.slug} href={`/operators/${operator.slug}`} label="Operator" title={operator.name} detail={`${operator.wallet_count} registry claims across ${operator.chains.join(", ") || "no indexed chains"}.`} kind="inferred" confidence={operator.wallets?.[0]?.confidence} status={operator.wallets?.[0]?.evidence_status} />
      ))}
      {query && !txPattern.test(query) && !addressPattern.test(query) && operators.length === 0 && (
        <div className="border border-ink-700 bg-ink-900 p-6 text-sm text-slate-400">No entity matched <span className="font-mono text-white">{query}</span>. Natural-language analytics will be routed through Ask as the query planner expands.</div>
      )}
    </div>
  );
}

function Result({ href, label, title, detail, kind, confidence, status }: { href: string; label: string; title: string; detail: string; kind: "observed" | "calculated" | "inferred"; confidence?: number; status?: string }) {
  return <Link href={href} className="block border border-ink-700 bg-ink-900 p-5 transition hover:border-neon-cyan">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><div className="font-mono text-[10px] uppercase text-slate-500">{label}</div><div className="mt-1 break-all font-mono text-base text-white">{title}</div></div><div className="flex items-center gap-3"><EvidenceClass kind={kind} />{confidence !== undefined && <ConfidenceBadge value={confidence} status={status} />}</div></div>
    <p className="mt-3 text-sm text-slate-400">{detail}</p>
  </Link>;
}

function EmptySearch() {
  return <div className="grid gap-px bg-ink-700 sm:grid-cols-3"><Example title="Operator" value="Stake" /><Example title="Wallet" value="Paste a complete 0x address" /><Example title="Transaction" value="Open from the transfer feed" /></div>;
}

function Example({ title, value }: { title: string; value: string }) {
  return <div className="bg-ink-900 p-5"><div className="text-[10px] uppercase text-slate-500">{title}</div><div className="mt-2 font-mono text-sm text-white">{value}</div></div>;
}
