"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { BrandLockup } from "@/components/logo";

const links = [
  { href: "/", label: "Intelligence" },
  { href: "/market", label: "Market" },
  { href: "/operators", label: "Directory" },
  { href: "/flows", label: "Flows" },
  { href: "/players", label: "Players" },
  { href: "/wallet", label: "Wallets" },
  { href: "/search", label: "Investigate" },
  { href: "/ask", label: "Ask" },
  { href: "/integration", label: "Telegraph" },
];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Nav() {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 border-b border-ink-700/80 bg-ink-950/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-[1440px] items-center gap-4 px-4 py-3 sm:px-6 lg:px-8">
        <Link
          href="/"
          aria-label="DegenLens home"
          className="shrink-0 focus-visible:outline-none"
          onClick={() => setOpen(false)}
        >
          <BrandLockup />
        </Link>

        <form
          action="/search"
          className="hidden min-w-0 flex-1 items-stretch border border-ink-700 bg-ink-900/80 sm:flex lg:max-w-[420px]"
        >
          <label htmlFor="global-search" className="sr-only">
            Search entities
          </label>
          <input
            id="global-search"
            name="q"
            placeholder="Operator, 0x address, tx hash"
            className="min-w-0 flex-1 bg-transparent px-3 py-2 font-mono text-xs text-white placeholder:text-slate-600 focus:outline-none"
          />
          <button
            type="submit"
            className="border-l border-ink-700 px-3 font-mono text-[10px] uppercase tracking-wider text-neon-cyan transition hover:bg-ink-800"
          >
            Search
          </button>
        </form>

        <div className="ml-auto flex shrink-0 items-center gap-3">
          <div className="hidden items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500 lg:flex">
            <span className="live-dot bg-neon-green" />
            <span className="text-neon-green">index live</span>
          </div>
          <button
            type="button"
            className="border border-neon-cyan/50 bg-neon-cyan/10 px-3 py-2 font-mono text-[11px] uppercase tracking-[0.14em] text-neon-cyan lg:hidden"
            aria-expanded={open}
            aria-controls="mobile-nav"
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "Close" : "Menu"}
          </button>
        </div>
      </div>

      <nav
        aria-label="Primary"
        className="hidden border-t border-ink-800/80 lg:block"
      >
        <div className="mx-auto flex max-w-[1440px] items-center gap-0.5 overflow-x-auto px-4 sm:px-6 lg:px-8">
          {links.map((l) => {
            const active = isActive(pathname, l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={`relative whitespace-nowrap px-3 py-2.5 font-mono text-[11px] uppercase tracking-[0.12em] transition ${
                  active
                    ? "text-white"
                    : "text-slate-500 hover:text-white"
                }`}
              >
                {l.label}
                {active ? (
                  <span className="absolute inset-x-2 bottom-0 h-px bg-neon-green shadow-[0_0_8px_rgba(74,222,128,0.7)]" />
                ) : null}
              </Link>
            );
          })}
        </div>
      </nav>

      {open ? (
        <div
          id="mobile-nav"
          className="border-t border-ink-700 bg-ink-950 lg:hidden"
        >
          <form action="/search" className="flex border-b border-ink-700 sm:hidden">
            <label htmlFor="mobile-search" className="sr-only">
              Search entities
            </label>
            <input
              id="mobile-search"
              name="q"
              placeholder="Operator, 0x, tx hash"
              className="min-w-0 flex-1 bg-transparent px-4 py-3 font-mono text-xs text-white placeholder:text-slate-600 focus:outline-none"
            />
            <button
              type="submit"
              className="border-l border-ink-700 px-4 font-mono text-[10px] uppercase text-neon-cyan"
            >
              Search
            </button>
          </form>
          <nav aria-label="Mobile" className="grid">
            {links.map((l) => {
              const active = isActive(pathname, l.href);
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  onClick={() => setOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={`border-b border-ink-800 px-4 py-3 font-mono text-xs uppercase tracking-[0.14em] ${
                    active ? "bg-ink-900 text-white" : "text-slate-400"
                  }`}
                >
                  {l.label}
                </Link>
              );
            })}
          </nav>
        </div>
      ) : null}
    </header>
  );
}
