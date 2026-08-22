import { NextResponse } from 'next/server';
import { telegraph } from '@/lib/telegraph';

/**
 * Auto-routed ask through Telegraph.
 *
 * Without a funded x402 wallet the Telegraph call 402s, so development falls
 * back to the local DegenMiner. Malformed input is
 * answered with a structured 400 rather than being allowed to throw.
 */
export async function POST(req: Request) {
  let query: string | undefined;
  try {
    const body = (await req.json()) as { query?: unknown };
    if (typeof body?.query === 'string') query = body.query.trim();
  } catch {
    return NextResponse.json(
      { error: 'Request body must be JSON of the form {"query": "..."}' },
      { status: 400 },
    );
  }

  if (!query) {
    return NextResponse.json(
      { error: 'A non-empty "query" string is required.' },
      { status: 400 },
    );
  }

  // Preferred path: let the Telegraph Engine route to the best miner.
  try {
    const res = await telegraph.ask<Record<string, unknown>>(query);
    const result = res.result as { answer?: string; text?: string } | undefined;
    return NextResponse.json({
      answer: result?.answer ?? result?.text ?? JSON.stringify(res.result),
      miner_id: res.miner_id,
      miner_name: res.miner_name,
      intent: res.intent,
      cost_usd: res.cost_usd,
      duration_ms: res.duration_ms,
      signal_hash: res.signal_hash,
      reasoning: res.reasoning,
      routed_via: 'telegraph',
    });
  } catch (engineErr) {
    // Never turn a paid routing failure into an unrelated successful answer.
    if (process.env.NODE_ENV !== 'development') {
      return NextResponse.json(
        { error: 'Telegraph routing failed.', detail: String(engineErr) },
        { status: 502 },
      );
    }

    // Development-only fallback keeps local demos usable without masking
    // production payment, routing, or miner configuration failures.
    try {
      const r = await telegraph.askDirect<{
        reasoning: string;
        data_source?: string;
      }>('local', '/casino/ranking?hours=168', {}, 'GET');
      return NextResponse.json({
        answer:
          `${r.result.reasoning}\n\n` +
          `(Answered by the local miner because Telegraph routing was unavailable. ` +
          `Configure EVM_PRIVATE_KEY with Base Sepolia USDC to enable x402.)`,
        miner_id: 'local',
        miner_name: 'degenminer-local',
        intent: 'ONCHAIN_TX_LOOKUP',
        cost_usd: 0,
        duration_ms: r.duration_ms,
        data_source: r.result.data_source,
        routed_via: 'local-fallback',
      });
    } catch {
      return NextResponse.json(
        {
          error:
            'Telegraph is unreachable and the local miner is not running. ' +
            'Start it with `pnpm miner:dev`.',
          detail: String(engineErr),
        },
        { status: 502 },
      );
    }
  }
}
