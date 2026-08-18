import type { Metadata } from 'next';
import { IBM_Plex_Mono, IBM_Plex_Sans } from 'next/font/google';
import './globals.css';
import { Nav } from '@/components/nav';
import { AppReveal } from '@/components/app-reveal';
import { BootSplash } from '@/components/boot-splash';

const sans = IBM_Plex_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-sans',
  display: 'swap',
});

const mono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'DegenLens | On-chain gambling intelligence',
  description:
    'Evidence-backed intelligence for investigating on-chain gambling operators, wallets, transactions, and risk signals.',
  icons: {
    icon: '/degenlens-logo.svg',
  },
};

const CRITICAL_BOOT_CSS = `
html,body{background:#05070d;color:#e5e7eb;margin:0}
#degen-boot{position:fixed;inset:0;z-index:100;display:grid;place-items:center;overflow:hidden;background:#05070d;transition:opacity .38s ease,visibility .38s ease}
#degen-boot.app-loader--leaving{visibility:hidden;opacity:0;pointer-events:none}
.app-loader__grid{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-position:center;background-size:64px 64px;-webkit-mask-image:radial-gradient(circle at center,#000 0,transparent 68%);mask-image:radial-gradient(circle at center,#000 0,transparent 68%)}
.app-loader__content{position:relative;display:flex;flex-direction:column;align-items:center;font-family:ui-sans-serif,system-ui,sans-serif}
.app-loader__mark{display:block;color:#fff;width:84px;height:56px}
.app-loader__wordmark{margin-top:20px;color:#fff;font-size:26px;font-weight:600;letter-spacing:-.02em}
.app-loader__wordmark span{color:#4ade80}
.app-loader__status{margin-top:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:#64748b}
.app-loader__meter{margin-top:32px;width:256px;max-width:70vw}
.app-loader__meter-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:#64748b}
.app-loader__meter-row .text-neon-green,#degen-boot-pct{color:#4ade80}
.app-loader__track{height:4px;overflow:hidden;background:#1a2236}
.app-loader__bar{height:100%;background:#4ade80;box-shadow:0 0 12px rgba(74,222,128,.65)}
html:not(.app-ready) .app-shell{visibility:hidden}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${sans.variable} ${mono.variable}`}>
      <head>
        <style dangerouslySetInnerHTML={{ __html: CRITICAL_BOOT_CSS }} />
      </head>
      <body className={`${sans.className} min-h-screen bg-ink-950 text-slate-200 antialiased`}>
        <BootSplash />
        <noscript>
          <style>{`#degen-boot{display:none!important}html .app-shell{visibility:visible!important}`}</style>
        </noscript>
        <script
          dangerouslySetInnerHTML={{
            __html:
              "setTimeout(function(){document.documentElement.classList.add('app-ready');var e=document.getElementById('degen-boot');if(e)e.classList.add('app-loader--leaving');},5000);",
          }}
        />
        <AppReveal>
          <div className="app-shell grid-lines flex min-h-screen flex-col">
            <Nav />
            <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-7 sm:px-6 sm:py-9 lg:px-8 lg:py-10">
              {children}
            </main>
            <footer className="border-t border-ink-700/80 bg-ink-950/90">
              <div className="mx-auto flex max-w-[1440px] flex-col gap-3 px-4 py-6 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
                <span className="font-mono text-[10px] uppercase tracking-[0.14em]">
                  Observed facts / calculated metrics / explicit inference
                </span>
                <span>
                  Intelligence served by DegenMiner through{' '}
                  <a
                    className="text-neon-cyan hover:text-white"
                    href="https://telegraphprotocol.com"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Telegraph Protocol
                  </a>
                </span>
              </div>
            </footer>
          </div>
        </AppReveal>
      </body>
    </html>
  );
}
