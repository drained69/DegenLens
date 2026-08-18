import { Panel } from "@/components/panel";
import { telegraphNodeUrl } from "@/lib/telegraph";

export const revalidate = 300;

/** Intents DegenMiner declares in config/miner.yaml. */
const DECLARED = ["ONCHAIN_TX_LOOKUP", "WALLET_BALANCE_CHECK", "FRAUD_DETECTION"] as const;

/** An intent needs this many miners before it is eligible for global prizes. */
const ELIGIBILITY_MINERS = 3;

interface IntentRow {
  intent_name: string;
  miner_count: number;
  description?: string;
  canonical?: boolean;
}

async function getIntents(): Promise<IntentRow[] | null> {
  try {
    const res = await fetch(`${telegraphNodeUrl}/engine/v1/intents`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { intents?: IntentRow[] };
    return body.intents ?? null;
  } catch {
    return null;
  }
}

async function getMinerHealth() {
  const base = process.env.LOCAL_MINER_URL ?? "http://localhost:8787";
  try {
    const [health, metrics] = await Promise.all([
      fetch(`${base}/health`, { cache: "no-store" }).then((r) => r.json()),
      fetch(`${base}/metrics`, { cache: "no-store" }).then((r) => r.json()),
    ]);
    return { health, metrics };
  } catch {
    return null;
  }
}

export default async function IntegrationPage() {
  const [intents, miner] = await Promise.all([getIntents(), getMinerHealth()]);

  const declaredRows = DECLARED.map((name) => {
    const row = intents?.find((i) => i.intent_name === name);
    const before = row?.miner_count ?? null;
    // If we are not yet registered, our entry would add one.
    const projected = before === null ? null : before + 1;
    return { name, before, projected, description: row?.description };
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight text-white">
          Telegraph Integration
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Live network state, declared intents, and registration readiness for DegenMiner.
        </p>
      </div>

      <Panel
        title="Node connectivity"
        subtitle="Discovery endpoints are unpaid — inference is gated by x402"
      >
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <Row label="Engine node" value={telegraphNodeUrl} mono />
          <Row
            label="Canonical intents"
            value={intents ? `${intents.length} discovered` : "unreachable"}
            tone={intents ? "ok" : "bad"}
          />
          <Row
            label="Local miner"
            value={
              miner
                ? `${miner.health.status} · ${miner.health.data_mode} mode`
                : "not running"
            }
            tone={miner ? "ok" : "bad"}
          />
          <Row
            label="Circuit breaker"
            value={
              miner
                ? miner.health.circuit_breaker.open
                  ? "OPEN — upstream failing"
                  : "closed"
                : "—"
            }
            tone={miner && !miner.health.circuit_breaker.open ? "ok" : "warn"}
          />
        </dl>
        {!miner && (
          <p className="mt-4 border border-ink-700 bg-ink-800/60 px-4 py-3 text-xs text-slate-400">
            Start the miner with{" "}
            <code className="bg-ink-900 px-1.5 py-0.5 font-mono text-neon-cyan">
              pnpm miner:dev
            </code>{" "}
            to populate this panel.
          </p>
        )}
      </Panel>

      <Panel
        title="Declared intents"
        subtitle={`An intent needs ${ELIGIBILITY_MINERS}+ miners and 100+ application requests to be prize-eligible`}
      >
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-ink-700 text-left text-[10px] uppercase tracking-wider text-slate-500">
                <th className="py-2 pr-4">Intent</th>
                <th className="py-2 pr-4 text-right">Miners now</th>
                <th className="py-2 pr-4 text-right">With us</th>
                <th className="py-2 pr-4">Eligibility</th>
              </tr>
            </thead>
            <tbody>
              {declaredRows.map((r) => {
                const eligible = r.projected !== null && r.projected >= ELIGIBILITY_MINERS;
                return (
                  <tr key={r.name} className="border-b border-ink-800/60 align-top">
                    <td className="py-3 pr-4 font-mono text-xs text-neon-cyan">{r.name}</td>
                    <td className="py-3 pr-4 text-right font-mono text-slate-300">
                      {r.before ?? "—"}
                    </td>
                    <td className="py-3 pr-4 text-right font-mono text-white">
                      {r.projected ?? "—"}
                    </td>
                    <td className="py-3 pr-4">
                      {r.projected === null ? (
                        <span className="text-slate-500">unknown</span>
                      ) : eligible ? (
                        <span className="text-neon-green">threshold met</span>
                      ) : (
                        <span className="text-neon-amber">
                          needs {ELIGIBILITY_MINERS - r.projected} more miner(s)
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="mt-4 text-xs leading-5 text-slate-500">
          Adjacent intents like <code className="font-mono">TVL_LOOKUP</code> and{" "}
          <code className="font-mono">CRYPTO_PRICE</code> are deliberately not declared.
          Those queries are mostly about DeFi protocols and spot prices rather than
          casinos, and answering them poorly would depress the Canonical Score across
          every intent this miner serves.
        </p>
      </Panel>

      {miner && (
        <Panel
          title="Miner performance"
          subtitle="Evidence for the 75% performance component of the Miner Track score"
        >
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Metric label="Uptime" value={`${Math.round(miner.metrics.uptime_seconds)}s`} />
            <Metric label="Requests" value={String(miner.metrics.total_requests)} />
            <Metric
              label="Error rate"
              value={`${(miner.metrics.error_rate * 100).toFixed(1)}%`}
              tone={miner.metrics.error_rate === 0 ? "ok" : "bad"}
            />
            <Metric label="p50" value={`${miner.metrics.latency_p50_ms}ms`} />
            <Metric label="p95" value={`${miner.metrics.latency_p95_ms}ms`} />
            <Metric
              label="Cache hits"
              value={`${(miner.metrics.cache_hit_rate * 100).toFixed(0)}%`}
            />
          </div>
        </Panel>
      )}

      <Panel title="Registration checklist" subtitle="integrate.telegraphprotocol.com">
        <ol className="space-y-3 text-sm text-slate-300">
          <Step n={1} done>
            <strong className="text-white">Write the YAML.</strong> Declared intents,
            endpoints with exact paths and methods, input/output schemas, and{" "}
            <code className="font-mono text-neon-cyan">auth.type: none</code> for a public
            API. Lives at <code className="font-mono">config/miner.yaml</code>.
          </Step>
          <Step n={2} done>
            <strong className="text-white">Omit the on_chain block.</strong> This miner
            serves pure inference over HTTP and does not publish into ERC-8183 jobs, so the
            mapping is not required. The floor price is set in the registration transaction.
          </Step>
          <Step n={3}>
            <strong className="text-white">Deploy to a public HTTPS URL</strong> and set it
            as <code className="font-mono">base_url</code> — the production API endpoint
            Telegraph routes to, not the project website.
          </Step>
          <Step n={4}>
            <strong className="text-white">Import &amp; Upload.</strong> Paste the YAML into
            the developer console; it parses the values and pins to IPFS via Pinata.
          </Step>
          <Step n={5}>
            <strong className="text-white">Register.</strong> Submit the IPFS hash to the
            registry contract on Base Sepolia. Gas only, no bond — but registration is
            immutable, so validate first.
          </Step>
        </ol>
      </Panel>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
  tone,
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "ok" | "warn" | "bad";
}) {
  const color =
    tone === "ok"
      ? "text-neon-green"
      : tone === "bad"
        ? "text-neon-red"
        : tone === "warn"
          ? "text-neon-amber"
          : "text-white";
  return (
    <div className="border border-ink-700 bg-ink-800/50 px-4 py-3">
      <dt className="text-[10px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className={`mt-1 break-all ${mono ? "font-mono text-xs" : ""} ${color}`}>
        {value}
      </dd>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "ok" | "bad";
}) {
  return (
    <div className="border border-ink-700 bg-ink-800/50 p-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div
        className={`mt-1 font-mono text-lg font-semibold ${
          tone === "ok" ? "text-neon-green" : tone === "bad" ? "text-neon-red" : "text-white"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function Step({
  n,
  done,
  children,
}: {
  n: number;
  done?: boolean;
  children: React.ReactNode;
}) {
  return (
    <li className="flex gap-3">
      <span
        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center border font-mono text-[10px] ${
          done
            ? "border-neon-green/50 text-neon-green"
            : "border-ink-600 text-slate-500"
        }`}
      >
        {done ? "✓" : n}
      </span>
      <span className="leading-6">{children}</span>
    </li>
  );
}
