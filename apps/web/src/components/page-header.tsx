import type { ReactNode } from "react";

export function Eyebrow({
  children,
  tone = "cyan",
}: {
  children: ReactNode;
  tone?: "cyan" | "green" | "amber" | "slate";
}) {
  const color =
    tone === "green"
      ? "text-neon-green"
      : tone === "amber"
        ? "text-neon-amber"
        : tone === "slate"
          ? "text-slate-500"
          : "text-neon-cyan";
  return (
    <div className={`font-mono text-[10px] uppercase tracking-[0.16em] ${color}`}>
      {children}
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  description,
  actions,
}: {
  eyebrow: string;
  title: ReactNode;
  subtitle?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="border-b border-ink-700/80 pb-8">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <Eyebrow>{eyebrow}</Eyebrow>
          <h1 className="mt-3 text-[2rem] font-semibold leading-[1.15] tracking-tight text-white sm:text-[2.5rem]">
            {title}
            {subtitle ? <span className="mt-1 block text-slate-500">{subtitle}</span> : null}
          </h1>
          {description ? (
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </div>
    </header>
  );
}
