"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { BrandLockup } from "@/components/logo";

const groups = [
  {
    label: "Intelligence",
    links: [
      { href: "/", label: "Overview", code: "01" },
      { href: "/operators", label: "Operators", code: "02" },
      { href: "/players", label: "Players", code: "03" },
      { href: "/wallet", label: "Wallets", code: "04" },
      { href: "/market", label: "Market", code: "05" },
    ],
  },
  {
    label: "Investigate",
    links: [
      { href: "/search", label: "Universal search", code: "06" },
      { href: "/flows", label: "Transaction feed", code: "07" },
      { href: "/ask", label: "Query intelligence", code: "08" },
    ],
  },
];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Nav() {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <>
      <header className="terminal-mobile-header">
        <Link href="/" aria-label="DegenLens home" onClick={() => setOpen(false)}>
          <BrandLockup />
        </Link>
        <div className="ml-auto flex items-center gap-3">
          <span className="live-dot bg-neon-green" aria-hidden="true" />
          <button
            type="button"
            className="terminal-menu-button"
            aria-expanded={open}
            aria-controls="terminal-navigation"
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "Close" : "Menu"}
          </button>
        </div>
      </header>

      <aside id="terminal-navigation" className={`terminal-sidebar ${open ? "is-open" : ""}`}>
        <div className="terminal-brand">
          <Link href="/" aria-label="DegenLens home" onClick={() => setOpen(false)}>
            <BrandLockup />
          </Link>
          <span className="terminal-edition">INTELLIGENCE OS / 01</span>
        </div>

        <form action="/search" className="terminal-quick-search">
          <label htmlFor="side-search" className="sr-only">Search intelligence</label>
          <span aria-hidden="true">⌕</span>
          <input ref={searchRef} id="side-search" name="q" placeholder="Search or paste hash" />
          <kbd className="hidden sm:inline">⌘K</kbd>
        </form>

        <nav aria-label="Primary" className="terminal-nav-groups">
          {groups.map((group) => (
            <div key={group.label} className="terminal-nav-group">
              <div className="terminal-nav-label">{group.label}</div>
              {group.links.map((link) => {
                const active = isActive(pathname, link.href);
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setOpen(false)}
                    className={`terminal-nav-link ${active ? "is-active" : ""}`}
                  >
                    <span className="terminal-nav-code">{link.code}</span>
                    <span>{link.label}</span>
                    {active ? <span className="terminal-nav-marker" /> : null}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

      </aside>
      {open ? <button className="terminal-nav-scrim" aria-label="Close navigation" onClick={() => setOpen(false)} /> : null}
    </>
  );
}
