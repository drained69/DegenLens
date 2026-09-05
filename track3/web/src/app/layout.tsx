import type { Metadata } from 'next';
import { IBM_Plex_Mono, Instrument_Sans, Instrument_Serif } from 'next/font/google';
import './globals.css';
import { Nav } from '@/components/nav';
import { AppReveal } from '@/components/app-reveal';
import { BootSplash } from '@/components/boot-splash';

// Instrument Sans carries the headings: a tight grotesque that holds its
// shape at display sizes, paired with Instrument Serif's italic for the
// second line of a headline. The two are designed together, which is what
// makes the pairing read as one voice rather than two fonts.
const sans = Instrument_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-sans',
  display: 'swap',
});

const serif = Instrument_Serif({
  subsets: ['latin'],
  weight: ['400'],
  style: ['italic', 'normal'],
  variable: '--font-serif',
  display: 'swap',
});

const mono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'DegenLens | Telegraph on-chain intelligence miner',
  description:
    'A Telegraph miner providing evidence-backed transaction, wallet, operator-flow, attribution, and anomaly intelligence for observable on-chain gambling activity.',
  icons: {
    icon: '/degenlens-logo.svg',
  },
};

const CRITICAL_BOOT_CSS = `
html,body{background:#f1ece3;color:#16150f;margin:0}
#degen-boot{position:fixed;inset:0;z-index:100;display:grid;place-items:center;overflow:hidden;background:#f1ece3;transition:opacity .38s ease,visibility .38s ease}
#degen-boot.app-loader--leaving{visibility:hidden;opacity:0;pointer-events:none}
.app-loader__grid{position:absolute;inset:0;background-image:linear-gradient(rgba(84, 74, 55,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(84, 74, 55,.05) 1px,transparent 1px);background-position:center;background-size:64px 64px;-webkit-mask-image:radial-gradient(circle at center,#000 0,transparent 68%);mask-image:radial-gradient(circle at center,#000 0,transparent 68%)}
.app-loader__content{position:relative;display:flex;flex-direction:column;align-items:center;font-family:ui-sans-serif,system-ui,sans-serif}
.app-loader__mark{display:block;color:#16150f;width:84px;height:56px}
.app-loader__wordmark{margin-top:20px;color:#16150f;font-size:26px;font-weight:600;letter-spacing:-.02em}
.app-loader__wordmark span{color:#6d5ae0}
.app-loader__status{margin-top:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:#6f6a5b}
.app-loader__meter{margin-top:32px;width:256px;max-width:70vw}
.app-loader__meter-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:#6f6a5b}
.app-loader__meter-row .text-neon-green,#degen-boot-pct{color:#6d5ae0}
.app-loader__track{height:4px;overflow:hidden;background:#e2dbcd}
.app-loader__bar{height:100%;background:#6d5ae0;box-shadow:0 0 12px rgba(109, 90, 224,.3)}
html:not(.app-ready) .app-shell{visibility:hidden}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${sans.variable} ${serif.variable} ${mono.variable}`}>
      <head>
        <style dangerouslySetInnerHTML={{ __html: CRITICAL_BOOT_CSS }} />
      </head>
      <body className={`${sans.className} min-h-screen bg-ink-950 text-slate-200 antialiased`}>
        <BootSplash />
        <noscript>
          <style>{`#degen-boot{display:none!important}html .app-shell{visibility:visible!important}`}</style>
        </noscript>
        <AppReveal>
          <div className="app-shell grid-lines min-h-screen lg:grid lg:grid-cols-[232px_minmax(0,1fr)]">
            <Nav />
            <div className="min-w-0">
              <main className="mx-auto w-full max-w-[1680px] px-4 py-5 sm:px-6 sm:py-6 lg:px-7 lg:py-7">
                {children}
              </main>
              <footer className="border-t border-ink-700/70 bg-ink-950/80 px-4 py-3 sm:px-6 lg:px-7">
                <div className="mx-auto flex max-w-[1680px] flex-wrap items-center justify-between gap-2 font-mono text-[9px] uppercase tracking-[0.12em] text-slate-600">
                  <span>Observe / Analyze / Investigate / Verify</span>
                  <span>Intelligence served by DegenMiner through <a className="text-slate-400 hover:text-neon-cyan" href="https://telegraphprotocol.com" target="_blank" rel="noreferrer">Telegraph Protocol</a></span>
                </div>
              </footer>
            </div>
          </div>
        </AppReveal>
      </body>
    </html>
  );
}
