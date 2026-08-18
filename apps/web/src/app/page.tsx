import type { ReactNode } from "react";
import Link from "next/link";
import type { CasinoRanking, CasinoRegistry } from "@degenlens/shared";
import { formatCount, formatUsd } from "@degenlens/shared";
import { telegraph } from "@/lib/telegraph";
import { Panel, Stat } from "@/components/panel";
import { ConfidenceBadge, EvidenceClass } from "@/components/confidence";
import { DataSourceBadge, ProvenanceNotice } from "@/components/data-source";
import { PageHeader } from "@/components/page-header";

export const dynamic = "force-dynamic";

async function direct<T>(endpoint: string) {
  try {
    return await telegraph.askDirect<T>("local", endpoint, {}, "GET");
  } catch {
    return null;
  }
}

export default async function IntelligencePage() {
  const [dayResponse, weekResponse, registryResponse] = await Promise.all([
    direct<CasinoRanking>("/casino/ranking?hours=24"),
    direct<CasinoRanking>("/casino/ranking?hours=168"),
    direct<CasinoRegistry>("/casinos"),
  ]);
  const day = dayResponse?.result;
  const week = weekResponse?.result;
  const registry = registryResponse?.result;
  const leader = day?.ranking[0];
  const claims = registry?.casinos.flatMap((operator) => operator.wallets ?? []) ?? [];

  return (
    <div className="space-y-10">
      <PageHeader
        eyebrow="DegenLens intelligence graph / live terminal"
        title="Investigate on-chain gambling activity."
        subtitle="Trace every conclusion to evidence."
        description="Search operators, wallets, and transactions. DegenLens separates observed chain facts, calculated metrics, and attribution claims instead of flattening them into one score."
        actions={
          <Link href="/search" className="btn-primary">
            Start investigation
          </Link>
        }
      />

      <ProvenanceNotice source={day?.data_source} />

      <section aria-labelledby="snapshot">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-neon-cyan">
              Current scope
            </div>
            <h2 id="snapshot" className="mt-1 text-xl font-semibold tracking-tight text-white">
              Observed network snapshot
            </h2>
          </div>
          <DataSourceBadge source={day?.data_source} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Tracked operators" value={String(registry?.count ?? 0)} />
          <Stat label="Attribution claims" value={String(claims.length)} />
          <Stat label="24h inbound leader" value={leader?.name ?? "unavailable"} />
          <Stat
            label="Observed transfers"
            value={formatCount(
              day?.ranking.reduce((sum, row) => sum + (row.transaction_count ?? 0), 0) ?? 0,
            )}
          />
        </div>
      </section>

      <div className="grid gap-5 lg:grid-cols-[1.25fr_.75fr]">
        <Panel
          title="Intelligence feed"
          subtitle="Measured changes and investigative leads, not accusations"
        >
          {!day || !week ? (
            <p className="text-sm text-slate-500">The miner is unavailable.</p>
          ) : (
            <div className="divide-y divide-ink-700/80">
              {day.ranking.map((row) => {
                const weekly = week.ranking.find((candidate) => candidate.slug === row.slug);
                const baseline = (weekly?.deposits_usd ?? 0) / 7;
                const change = baseline > 0 ? ((row.deposits_usd - baseline) / baseline) * 100 : 0;
                const elevated = Math.abs(change) >= 40;
                return (
                  <article key={row.slug} className="py-5 first:pt-0 last:pb-0">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div
                          className={`font-mono text-[10px] uppercase tracking-[0.14em] ${
                            elevated ? "text-neon-amber" : "text-neon-green"
                          }`}
                        >
                          {elevated ? "elevated" : "informational"} / flow change
                        </div>
                        <h3 className="mt-1.5 text-base font-medium tracking-tight text-white">
                          {row.name} inbound flow is {change >= 0 ? "above" : "below"} its 7-day
                          daily average
                        </h3>
                      </div>
                      <span
                        className={`font-mono text-sm ${
                          change >= 0 ? "text-neon-green" : "text-neon-red"
                        }`}
                      >
                        {change >= 0 ? "+" : ""}
                        {change.toFixed(1)}%
                      </span>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-slate-400">
                      Observed {formatUsd(row.deposits_usd)} inbound and{" "}
                      {formatUsd(row.withdrawals_usd)} outbound across attributed wallets in 24
                      hours.
                    </p>
                    <div className="mt-3 flex items-center justify-between">
                      <EvidenceClass kind="calculated" />
                      <Link
                        href={`/operators/${row.slug}`}
                        className="font-mono text-[10px] uppercase tracking-[0.12em] text-neon-cyan hover:text-white"
                      >
                        Investigate →
                      </Link>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </Panel>

        <Panel
          title="Evidence posture"
          subtitle="Confidence applies to claims, not the whole platform"
        >
          <div className="space-y-3">
            <EvidenceRow
              title="Chain transactions"
              text="RPC facts are never synthesized by transaction lookup."
              badge={<EvidenceClass kind="observed" />}
            />
            <EvidenceRow
              title="Flow metrics"
              text="Directional transfers do not prove wagers, deposits, or withdrawals."
              badge={<EvidenceClass kind="calculated" />}
            />
            <EvidenceRow
              title="Operator labels"
              text="Seed claims remain explicitly unverified until source evidence is attached."
              badge={
                <ConfidenceBadge
                  value={Math.max(...claims.map((claim) => claim.confidence), 0)}
                  status={claims[0]?.evidence_status}
                />
              }
            />
          </div>
        </Panel>
      </div>

      <section aria-labelledby="how-it-works">
        <div className="mb-6 max-w-2xl">
          <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-neon-green">
            From question to evidence
          </div>
          <h2 id="how-it-works" className="mt-1 text-xl font-semibold tracking-tight text-white">
            How DegenLens works
          </h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            Each investigation moves through the same auditable path. The interface keeps
            direct observations, derived metrics, and attribution claims separate so you can
            judge the evidence behind the answer.
          </p>
        </div>
        <ol className="grid gap-px bg-ink-700 sm:grid-cols-2 lg:grid-cols-4">
          <ProcessStep
            number="01"
            title="Submit a query"
            text="Search an operator, wallet, or transaction, or ask through Telegraph using a supported intent."
          />
          <ProcessStep
            number="02"
            title="Resolve chain data"
            text="DegenMiner retrieves transaction and balance facts, then calculates flows and anomaly signals."
          />
          <ProcessStep
            number="03"
            title="Classify evidence"
            text="Every result identifies what was observed, what was calculated, and what remains an attribution claim."
          />
          <ProcessStep
            number="04"
            title="Inspect the result"
            text="Follow wallets, counterparties, and source records to verify the conclusion for yourself."
          />
        </ol>
      </section>

      <section className="grid gap-px bg-ink-700 sm:grid-cols-3">
        <Capability
          intent="ONCHAIN_TX_LOOKUP"
          title="Verify a transaction"
          text="Resolve canonical chain facts, then layer operator attribution separately."
          href="/search"
        />
        <Capability
          intent="WALLET_BALANCE_CHECK"
          title="Trace a wallet"
          text="Inspect balances, counterparties, and operator exposure."
          href="/wallet"
        />
        <Capability
          intent="FRAUD_DETECTION"
          title="Review anomalies"
          text="Surface deterministic patterns with evidence and cautious language."
          href="/wallet"
        />
      </section>
    </div>
  );
}

function EvidenceRow({
  title,
  text,
  badge,
}: {
  title: string;
  text: string;
  badge: ReactNode;
}) {
  return (
    <div className="border border-ink-700/80 bg-ink-800/30 p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-white">{title}</span>
        {badge}
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-500">{text}</p>
    </div>
  );
}

function ProcessStep({ number, title, text }: { number: string; title: string; text: string }) {
  return (
    <li className="bg-ink-950 p-5">
      <div className="flex items-center gap-3">
        <span className="font-mono text-xs text-neon-cyan">{number}</span>
        <span className="h-px flex-1 bg-ink-600" />
      </div>
      <h3 className="mt-5 text-base font-medium tracking-tight text-white">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-slate-400">{text}</p>
    </li>
  );
}

function Capability({
  intent,
  title,
  text,
  href,
}: {
  intent: string;
  title: string;
  text: string;
  href: string;
}) {
  return (
    <Link href={href} className="bg-ink-900 p-5 transition hover:bg-ink-800">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-neon-cyan">
        {intent}
      </div>
      <h2 className="mt-2 text-base font-medium tracking-tight text-white">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-slate-400">{text}</p>
    </Link>
  );
}
