import { NextResponse } from 'next/server';
import { telegraph } from '@/lib/telegraph';
import type { WalletTrace } from '@degenlens/shared';

export async function GET(req: Request) {
  const address = new URL(req.url).searchParams.get('address');
  if (!address) return NextResponse.json({ error: 'address required' }, { status: 400 });
  const chain = new URL(req.url).searchParams.get('chain') ?? 'ethereum';
  try {
    const res = await telegraph.askDirect<WalletTrace>(
      'local',
      '/wallet/trace',
      { address, chain },
      'POST',
    );
    return NextResponse.json(res.result);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
