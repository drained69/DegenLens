import type { FlowPoint } from "@degenlens/shared";
import { formatUsd } from "@degenlens/shared";

/**
 * Inbound/outbound flow over time, drawn as paired bars.
 *
 * Deliberately axis-light: the point is the shape of the flow and the ratio
 * between directions, not precise reading off a gridline. Exact figures live in
 * the table beneath it.
 */
export function FlowChart({ series }: { series: FlowPoint[] }) {
  if (!series.length) {
    return (
      <p className="text-sm text-slate-500">No observed transfers in this window.</p>
    );
  }

  const peak = Math.max(
    ...series.map((p) => Math.max(p.inbound_usd, p.outbound_usd)),
    1,
  );

  return (
    <div>
      <div className="flex items-end gap-px" style={{ height: 140 }}>
        {series.map((p) => {
          const inH = Math.max((p.inbound_usd / peak) * 100, p.inbound_usd > 0 ? 1 : 0);
          const outH = Math.max((p.outbound_usd / peak) * 100, p.outbound_usd > 0 ? 1 : 0);
          const label = `${p.t.slice(5, 16).replace("T", " ")} · in ${formatUsd(
            p.inbound_usd,
          )} · out ${formatUsd(p.outbound_usd)} · ${p.transfers} transfers`;
          return (
            <div
              key={p.t}
              title={label}
              className="group relative flex flex-1 items-end justify-center gap-[1px]"
              style={{ height: "100%" }}
            >
              <div
                className="w-1/2 bg-neon-green/70 transition group-hover:bg-neon-green"
                style={{ height: `${inH}%` }}
              />
              <div
                className="w-1/2 bg-neon-red/60 transition group-hover:bg-neon-red"
                style={{ height: `${outH}%` }}
              />
            </div>
          );
        })}
      </div>

      <div className="mt-3 flex items-center justify-between border-t border-ink-700 pt-2 font-mono text-[10px] uppercase text-slate-500">
        <span>{series[0].t.slice(5, 16).replace("T", " ")}</span>
        <span className="flex items-center gap-4">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 bg-neon-green/70" /> inbound
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 bg-neon-red/60" /> outbound
          </span>
          <span>peak {formatUsd(peak)}</span>
        </span>
        <span>{series[series.length - 1].t.slice(5, 16).replace("T", " ")}</span>
      </div>
    </div>
  );
}

/** Horizontal share bar used by the network and asset breakdowns. */
export function ShareBar({
  rows,
}: {
  rows: {
    label: string;
    pct: number;
    usd: number;
    accent?: string;
    status?: "observed" | "queried_zero" | "unavailable" | "not_registered";
  }[];
}) {
  return (
    <div className="space-y-3">
      {rows.map((r) => (
        <div key={r.label}>
          <div className="flex items-baseline justify-between text-sm">
            <span className="font-medium text-white">{r.label}</span>
            <span className="font-mono text-xs text-slate-400">
              {r.status === "unavailable"
                ? <span className="text-neon-amber">N/A · read unavailable</span>
                : r.status === "not_registered"
                  ? <span className="text-slate-500">Not registered by source</span>
                : r.status === "queried_zero"
                  ? <span className="text-slate-500">No observed flow for registered wallets</span>
                  : <>{formatUsd(r.usd)}{" "}<span className="text-slate-600">· {r.pct.toFixed(1)}%</span></>}
            </span>
          </div>
          <div className="mt-1.5 h-1.5 w-full bg-ink-800">
            <div
              className={r.accent ?? "bg-neon-cyan"}
              style={{ height: "100%", width: `${r.status === "unavailable" ? 0 : Math.min(r.pct, 100)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
