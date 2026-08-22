import { NextResponse } from 'next/server';
import { telegraph, telegraphMinerId } from '@/lib/telegraph';
import type { AnomalyReport } from '@degenlens/shared';

export async function GET(req: Request) {
  const url = new URL(req.url);
  const address = url.searchParams.get('address');
  if (!address) return NextResponse.json({ error: 'address required' }, { status: 400 });
  const chain = url.searchParams.get('chain') ?? 'ethereum';
  const hours = parseInt(url.searchParams.get('hours') ?? '24', 10);
  try {
    const res = await telegraph.askDirect<AnomalyReport>(
      telegraphMinerId,
      '/anomaly/check',
      { address, chain, hours },
      'POST',
    );
    return NextResponse.json(res.result);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
