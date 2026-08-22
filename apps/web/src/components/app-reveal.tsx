"use client";

import { useEffect, type ReactNode } from "react";

const FADE_DURATION_MS = 380;

function setProgress(value: number) {
  const bar = document.getElementById("degen-boot-bar");
  const pct = document.getElementById("degen-boot-pct");
  const track = document.querySelector<HTMLElement>("#degen-boot [role='progressbar']");
  const boot = document.getElementById("degen-boot");
  if (bar) bar.style.width = `${value}%`;
  if (pct) pct.textContent = `${value}%`;
  if (track) track.setAttribute("aria-valuenow", String(value));
  if (boot) boot.setAttribute("aria-label", `Loading DegenLens, ${value}%`);
}

function dismissBoot() {
  const boot = document.getElementById("degen-boot");
  boot?.classList.add("app-loader--leaving");
  document.documentElement.classList.add("app-ready");
}

/**
 * Reveals the app after hydration. The overlay is only a first-paint fallback;
 * it must never sit above an interactive route while data is loading.
 */
export function AppReveal({ children }: { children: ReactNode }) {
  useEffect(() => {
    const boot = document.getElementById("degen-boot");
    if (!boot || boot.classList.contains("app-loader--leaving")) {
      document.documentElement.classList.add("app-ready");
      return;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let fadeTimer = 0;
    let finished = false;

    const finish = () => {
      if (finished) return;
      finished = true;
      setProgress(100);
      fadeTimer = window.setTimeout(dismissBoot, reduceMotion ? 20 : FADE_DURATION_MS);
    };

    finish();

    return () => {
      window.clearTimeout(fadeTimer);
    };
  }, []);

  return <>{children}</>;
}
