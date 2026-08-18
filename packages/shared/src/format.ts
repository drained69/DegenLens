/** Compact USD formatter — 1.4M, 82.3K, $412. */
export function formatUsd(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

/** Truncate a wallet address to the standard `0x1234…abcd` form. */
export function truncateAddress(addr: string, chars = 4): string {
  if (!addr || addr.length < chars * 2 + 3) return addr;
  return `${addr.slice(0, chars + 2)}…${addr.slice(-chars)}`;
}

export function formatPct(n: number): string {
  return `${n.toFixed(2)}%`;
}

export function formatCount(n: number): string {
  return new Intl.NumberFormat('en-US').format(n);
}

/** Convert a signal hash into a Basescan-shaped truncation for display. */
export function displayHash(hash?: string): string {
  if (!hash) return '—';
  return `${hash.slice(0, 10)}…${hash.slice(-8)}`;
}
