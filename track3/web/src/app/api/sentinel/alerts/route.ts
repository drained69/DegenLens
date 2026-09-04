import { NextResponse } from 'next/server';
import { startSentinel } from '@/lib/sentinel/engine';
import { sentinelStore } from '@/lib/sentinel/store';

export const dynamic = 'force-dynamic';

/** Recent alerts and the paid-call receipt log (network-usage evidence). */
export async function GET() {
  startSentinel(); // Ensures the loop is armed even if instrumentation missed.
  const state = await sentinelStore.state();
  return NextResponse.json({
    alerts: state.alerts.slice(0, 50),
    receipts: state.receipts.slice(0, 60),
  });
}
