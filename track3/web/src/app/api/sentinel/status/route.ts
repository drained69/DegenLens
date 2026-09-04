import { NextResponse } from 'next/server';
import { sentinelStatus, startSentinel } from '@/lib/sentinel/engine';

export const dynamic = 'force-dynamic';

/** Agent status: scheduler state, config, last/next scan, network totals. */
export async function GET() {
  startSentinel(); // Ensures the loop is armed even if instrumentation missed.
  return NextResponse.json(sentinelStatus());
}
