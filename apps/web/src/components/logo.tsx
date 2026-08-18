interface BrandMarkProps {
  className?: string;
  title?: string;
  /** Show the small green accent slot. Defaults to true. */
  accent?: boolean;
}

/**
 * DegenLens monogram — a custom-cut DL with a green aperture slot in the D's
 * counter. Bold sans-serif letterforms, single accent color, sharp corners.
 * No eyes, no reticles, no gradients, no color quadrants.
 *
 * The aperture reads as "the observation slit of a lens" without literally
 * drawing an eye — a subtle nod to the product name.
 */
export function BrandMark({
  className = "h-8 w-8",
  title = "DegenLens",
  accent = true,
}: BrandMarkProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 48 32"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={title}
      shapeRendering="geometricPrecision"
    >
      {/*
        The "D" — built from four rectangles so corners stay sharp at every
        size. Stem on the left, top and bottom bars, right bar with a subtle
        inward chamfer for the counter opening.
      */}
      <path d="M0 0h6v32H0z" />
      <path d="M6 0h13v6H6z" />
      <path d="M6 26h13v6H6z" />
      <path d="M19 0l6 6v20l-6 6V0z" />

      {/* The aperture — the only accent. A horizontal slot through the D's counter. */}
      {accent && <rect x="8" y="14" width="9" height="4" fill="#4ADE80" />}

      {/*
        The "L" — a bold stem on the right with a wider foot. Note the foot
        overhangs so the DL reads as one linked lockup, not two letters
        floating apart.
      */}
      <path d="M30 0h6v26h12v6H30V0z" />
    </svg>
  );
}

/**
 * Wordmark — clean lowercase with the "lens" portion tinted. No decorative
 * flourish, just typography. Matches the terminal aesthetic.
 */
export function BrandWordmark({ className = "" }: { className?: string }) {
  return (
    <span
      className={`whitespace-nowrap font-semibold tracking-tight text-white ${className}`}
    >
      degen<span className="text-neon-green">lens</span>
    </span>
  );
}

/**
 * Compact lockup for the nav bar — monogram + wordmark, sharp corners.
 */
export function BrandLockup({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <BrandMark className="h-6 w-9 shrink-0 text-white" />
      <BrandWordmark className="text-[17px]" />
    </span>
  );
}
