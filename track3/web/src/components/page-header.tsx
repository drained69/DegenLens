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
    <header className="relative border-b border-ink-700/80 pb-8">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl">
          <Eyebrow>{eyebrow}</Eyebrow>
          {/* Two-line display heading: a tight grotesque states the subject,
              an italic serif carries the qualifier under it. The pair is what
              gives the page its voice, so the serif is reserved for exactly
              this slot and never used for running text. */}
          <h1 className="mt-3 max-w-4xl text-[2.15rem] font-semibold leading-[1.05] tracking-[-0.02em] text-ink-1000 sm:text-[3rem]">
            {title}
            {subtitle ? (
              <span className="mt-0.5 block font-serif text-[2.15rem] font-normal italic leading-[1.05] tracking-[-0.01em] text-slate-400 sm:text-[3rem]">
                {subtitle}
              </span>
            ) : null}
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
