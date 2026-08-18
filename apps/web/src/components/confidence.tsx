export function ConfidenceBadge({ value, status }: { value: number; status?: string }) {
  const band = value >= 0.9 ? "confirmed" : value >= 0.75 ? "high" : value >= 0.55 ? "probable" : value >= 0.3 ? "possible" : "insufficient";
  const tone = value >= 0.75
    ? "text-neon-green border-neon-green/35 bg-neon-green/5"
    : value >= 0.55
      ? "text-neon-amber border-neon-amber/35 bg-neon-amber/5"
      : "text-slate-400 border-ink-600 bg-ink-800/40";
  return (
    <span className={`inline-flex border px-2 py-1 font-mono text-[10px] uppercase tracking-wider ${tone}`}>
      {status ?? band} / {Math.round(value * 100)}%
    </span>
  );
}

export function EvidenceClass({ kind }: { kind: "observed" | "calculated" | "inferred" | "model-generated" }) {
  const tone = kind === "observed"
    ? "text-neon-green"
    : kind === "calculated"
      ? "text-neon-cyan"
      : kind === "inferred"
        ? "text-neon-amber"
        : "text-slate-400";
  return (
    <span className={`inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider ${tone}`}>
      <span className={`h-1 w-1 ${
        kind === "observed" ? "bg-neon-green" : kind === "calculated" ? "bg-neon-cyan" : kind === "inferred" ? "bg-neon-amber" : "bg-slate-500"
      }`} />
      {kind}
    </span>
  );
}
