"use client";

import { useEffect, type ReactNode } from "react";

const LOAD_DURATION_MS = 1600;
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
 * Plays the first-paint boot overlay on every full page load, then reveals
 * the app. The overlay itself lives in the server layout so the DL logo is
 * in the initial HTML and cannot be skipped or flashed past.
 */
export function AppReveal({ children }: { children: ReactNode }) {
  useEffect(() => {
    const boot = document.getElementById("degen-boot");
    if (!boot || boot.classList.contains("app-loader--leaving")) {
      document.documentElement.classList.add("app-ready");
      return;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const duration = reduceMotion ? 180 : LOAD_DURATION_MS;
    const startedAt = performance.now();
    let frame = 0;
    let fadeTimer = 0;
    let finished = false;

    const finish = () => {
      if (finished) return;
      finished = true;
      setProgress(100);
      fadeTimer = window.setTimeout(dismissBoot, reduceMotion ? 20 : FADE_DURATION_MS);
    };

    const tick = (now: number) => {
      const ratio = Math.min((now - startedAt) / duration, 1);
      setProgress(Math.round((1 - Math.pow(1 - ratio, 3)) * 100));
      if (ratio < 1) {
        frame = requestAnimationFrame(tick);
        return;
      }
      finish();
    };

    frame = requestAnimationFrame(tick);
    const failsafe = window.setTimeout(finish, duration + 1200);

    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(fadeTimer);
      window.clearTimeout(failsafe);
    };
  }, []);

  return <>{children}</>;
}
