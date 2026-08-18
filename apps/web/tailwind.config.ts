import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#05070d',
          900: '#0a0e18',
          800: '#0f1524',
          700: '#1a2236',
          600: '#252f47',
        },
        neon: {
          green: '#4ade80',
          red: '#f87171',
          amber: '#fbbf24',
          cyan: '#22d3ee',
        },
      },
      fontFamily: {
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
        display: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
};

export default config;
