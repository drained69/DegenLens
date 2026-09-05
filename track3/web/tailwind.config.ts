import type { Config } from 'tailwindcss';

/**
 * Single source of truth for the light "warm canvas" theme.
 *
 * The palette is intentionally small and semantic:
 *
 *   ink    — warm neutral surfaces AND text. The scale runs light→dark as the
 *            number DECREASES from 950 (page canvas) through 900 (card) and
 *            700/600 (borders) down to 1000? — no: 1000 is the one exception,
 *            the primary text color, kept on the same family so text and
 *            surfaces share one warm cast.
 *   slate  — REPLACED with the warm text ramp, so the standard text-slate-*
 *            utilities used across pages resolve to readable ink values
 *            instead of Tailwind's cool grays that clash with the canvas.
 *   neon   — the accent family, violet-led. Kept dark enough for text usage
 *            on the light canvas; tones are checked for contrast.
 */
const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          1000: '#16150f', // primary text
          950: '#f1ece3', // page canvas
          900: '#ffffff', // card surface
          850: '#f6f2ea', // recessed fills (inputs, segmented rows)
          800: '#efebfb', // violet-tinted fill (active states, highlights)
          700: '#e2dbcd', // default border
          600: '#c9c0ab', // strong border / disabled
        },
        slate: {
          // Warm text ramp (replaces Tailwind's slate). Pairs map to the
          // same value deliberately — 100/200 near-primary, 300/400
          // secondary, 500 muted, 600/700 faint — so slight semantic drift
          // between pages never changes the visual hierarchy.
          50: '#16150f',
          100: '#262319',
          200: '#262319',
          300: '#5d574a',
          400: '#5d574a',
          500: '#7b7566',
          600: '#948d7c',
          700: '#948d7c',
          800: '#a49d8a',
          900: '#a49d8a',
        },
        neon: {
          green: '#2f4026', // primary accent (violet) — "confirmed" evidence
          red: '#b8474f', // risk / failure
          amber: '#a8701f', // warning / elevated
          cyan: '#6d5ae0', // interactive accent
        },
      },
      fontFamily: {
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
        display: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        // The italic counterpart used for the second line of a display
        // heading. Instrument Serif ships one weight; it is a display face,
        // not a body face, so it is never applied to running text.
        serif: ['var(--font-serif)', 'ui-serif', 'Georgia', 'serif'],
      },
    },
  },
  plugins: [],
};

export default config;
