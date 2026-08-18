type Source = "live" | "demo" | "unavailable" | string | undefined;

const STYLES: Record<string, { label: string; cls: string; dot: string; title: string }> = {
  live: {
    label: "live chain data",
    cls: "border-neon-green/40 text-neon-green",
    dot: "bg-neon-green",
    title: "Observed directly from chain via RPC.",
  },
  demo: {
    label: "demo data",
    cls: "border-neon-amber/40 text-neon-amber",
    dot: "bg-neon-amber",
    title:
      "Synthetic development data — no upstream RPC key configured. Deterministic, but not observed chain state.",
  },
  unavailable: {
    label: "unavailable",
    cls: "border-neon-red/40 text-neon-red",
    dot: "bg-neon-red",
    title: "Upstream data provider could not be reached. No figures are being asserted.",
  },
};

/**
 * Provenance badge.
 *
 * The miner labels every response `live` / `demo` / `unavailable` rather than
 * inventing numbers when upstream is down. Surfacing that honestly is the point
 * of the product, so it gets a first-class UI element instead of a footnote.
 */
export function DataSourceBadge({ source }: { source: Source }) {
  const style = STYLES[source ?? ""] ?? STYLES.unavailable;
  return (
    <span
      title={style.title}
      className={`inline-flex items-center gap-1.5 border px-2 py-1 font-mono text-[10px] uppercase tracking-wider ${style.cls}`}
    >
      <span className={`h-1.5 w-1.5 ${style.dot}`} />
      {style.label}
    </span>
  );
}

/** Inline warning shown when a page is rendering non-live figures. */
export function ProvenanceNotice({ source }: { source: Source }) {
  if (source === "live") return null;
  if (source === "demo") {
    return (
      <div className="border border-neon-amber/40 bg-neon-amber/5 px-4 py-3 text-xs leading-5 text-neon-amber">
        <span className="font-semibold uppercase">Demo data.</span> The miner has no
        upstream RPC key configured, so these figures are deterministic synthetic values —
        not observed chain state. Set <code className="font-mono">ALCHEMY_KEY</code> to
        serve live data.
      </div>
    );
  }
  return (
    <div className="border border-neon-red/40 bg-neon-red/5 px-4 py-3 text-xs leading-5 text-neon-red">
      <span className="font-semibold uppercase">Data unavailable.</span> The upstream
      provider could not be reached. The miner is deliberately asserting no figures rather
      than guessing.
    </div>
  );
}
