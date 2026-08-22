import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#f5f3ee',
          900: '#ffffff',
          800: '#f1edff',
          700: '#e3dfeb',
          600: '#cec8db',
        },
        neon: {
          green: '#6f55d9',
          red: '#df5d67',
          amber: '#bd792f',
          cyan: '#7a5ee7',
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
