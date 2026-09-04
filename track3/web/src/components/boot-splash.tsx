import { BrandMark, BrandWordmark } from "@/components/logo";

/**
 * First-paint boot screen. Rendered as static HTML in the root layout so the
 * DL mark is visible before React hydrates or route data arrives.
 */
export function BootSplash() {
  return (
    <div
      id="degen-boot"
      className="app-loader"
      role="status"
      aria-live="polite"
      aria-label="Loading DegenLens"
    >
      <div className="app-loader__grid" aria-hidden="true" />
      <div className="app-loader__content">
        <BrandMark className="app-loader__mark h-14 w-[84px] text-white" />
        <BrandWordmark className="app-loader__wordmark mt-5 text-[26px]" />
        <span className="app-loader__status">Resolving on-chain signals</span>

        <div className="app-loader__meter">
          <div className="app-loader__meter-row">
            <span>Initializing index</span>
            <span id="degen-boot-pct" className="text-neon-green">
              0%
            </span>
          </div>
          <div
            className="app-loader__track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={0}
            aria-labelledby="degen-boot-pct"
          >
            <div id="degen-boot-bar" className="app-loader__bar" style={{ width: "0%" }} />
          </div>
        </div>
      </div>
    </div>
  );
}
