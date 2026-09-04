import { NextResponse } from 'next/server';
import { runScan, sentinelConfig, startSentinel } from '@/lib/sentinel/engine';
import { sentinelStore } from '@/lib/sentinel/store';

export const dynamic = 'force-dynamic';

/**
 * Run a Sentinel scan immediately (manual trigger or external cron).
 * Concurrency is rejected with 409 rather than queueing.
 */
export async function POST() {
  startSentinel(); // Belt and suspenders: a manual run also arms the schedule.
  try {
    const scan = await runScan('manual');
    return NextResponse.json({ scan });
  } catch (err) {
    if (sentinelStore.isRunning() || String(err).includes('already running')) {
      return NextResponse.json({ error: 'A scan is already running.' }, { status: 409 });
    }
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}

export async function GET() {
  return NextResponse.json({
    error: 'POST to this endpoint to trigger a scan.',
    config: sentinelConfig(),
  });
}
