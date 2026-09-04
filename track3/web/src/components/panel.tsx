import { ReactNode } from 'react';

interface PanelProps {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Panel({ title, subtitle, actions, children, className }: PanelProps) {
  return (
    <section
      className={`surface ${className ?? ''}`}
    >
      {(title || actions) && (
        <div className="flex items-start justify-between gap-4 border-b border-ink-700/80 px-5 py-4">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-tight text-white">{title}</h2>}
            {subtitle && <p className="mt-1 text-xs leading-5 text-slate-500">{subtitle}</p>}
          </div>
          {actions}
        </div>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

interface StatProps {
  label: string;
  value: string;
  delta?: string;
  positive?: boolean;
}

export function Stat({ label, value, delta, positive }: StatProps) {
  return (
    <div className="stat-card">
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">{label}</div>
      <div className="mt-2 font-mono text-2xl font-semibold tracking-tight text-white">{value}</div>
      {delta && (
        <div className={`mt-1.5 font-mono text-[11px] ${positive ? 'text-neon-green' : 'text-neon-red'}`}>
          {delta}
        </div>
      )}
    </div>
  );
}

export function SignalBadge({ hash }: { hash?: string }) {
  if (!hash) return null;
  return (
    <span className="inline-flex items-center gap-1.5 border border-neon-cyan/30 bg-neon-cyan/5 px-2 py-1 font-mono text-[10px] text-neon-cyan">
      <span className="live-dot bg-neon-cyan" />
      signal · {hash.slice(0, 10)}…{hash.slice(-6)}
    </span>
  );
}
