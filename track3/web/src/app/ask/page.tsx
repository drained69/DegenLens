'use client';

import { useState } from 'react';
import { Panel } from '@/components/panel';
import { PageHeader } from '@/components/page-header';

interface AnswerPayload {
  answer: string;
  miner_id?: string;
  miner_name?: string;
  intent?: string;
  cost_usd?: number;
  duration_ms?: number;
  signal_hash?: string;
  routed_via?: string;
  data_source?: string;
  reasoning?: string;
  error?: string;
}

const EXAMPLES = [
  'Which attributed operator has the highest observed inbound flow this week?',
  'Which large transfers touched attributed operator clusters today?',
  'Is 0x974caa59e49682cda0ad2bbe82983419a2ecc400 an attributed operator wallet?',
  'Screen this wallet for explainable transaction anomalies.',
];

export default function AskPage() {
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<AnswerPayload | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);

  async function submit(question: string) {
    setLoading(true);
    setAnswer(null);
    setRequestError(null);
    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: question }),
      });
      const payload = (await res.json()) as AnswerPayload;
      if (!res.ok) throw new Error(payload.error ?? 'The request could not be completed.');
      setAnswer(payload);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : 'The request could not be completed.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Natural language / routed intents"
        title="Ask the intelligence network"
        description="Send a plain-language request through the Telegraph Engine. The response identifies the selected miner, classified intent, cost, latency, provenance, and verifiable signal hash."
      />

      <section className="surface-highlight p-4 sm:p-5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <label htmlFor="ask-query" className="font-mono text-[10px] uppercase tracking-[0.14em] text-neon-cyan">Query workspace</label>
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">{q.length}/500</span>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (q.trim()) submit(q.trim());
          }}
          className="flex flex-col gap-3 sm:flex-row"
        >
          <input
            id="ask-query"
            value={q}
            maxLength={500}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. Which operator had the highest observed inbound flow this week?"
            className="min-w-0 flex-1 border border-ink-700 bg-ink-950 px-4 py-3 text-sm text-white placeholder:text-slate-600 focus:border-neon-cyan focus:outline-none"
          />
          <button
            type="submit"
            disabled={loading || !q.trim()}
            className="btn-primary min-w-[110px] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {loading ? 'Resolving…' : 'Ask →'}
          </button>
        </form>
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 font-mono text-[10px] uppercase tracking-[0.1em] text-slate-500">
          <span><span className="text-neon-green">●</span> natural language</span>
          <span><span className="text-neon-cyan">●</span> routed intent</span>
          <span><span className="text-neon-amber">●</span> provenance included</span>
        </div>
      </section>

      <div className="flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            disabled={loading}
            onClick={() => {
              setQ(ex);
              submit(ex);
            }}
            className="border border-ink-700 bg-ink-800/50 px-3 py-1 text-xs text-slate-400 hover:border-neon-cyan hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {ex}
          </button>
        ))}
      </div>

      {loading && (
        <div className="surface p-5" role="status">
          <div className="flex items-center gap-3 text-sm text-slate-300"><span className="live-dot bg-neon-cyan" /> Resolving your query against the intelligence graph…</div>
          <div className="mt-4 h-1 overflow-hidden bg-ink-700"><div className="h-full w-1/3 animate-pulse bg-neon-cyan" /></div>
        </div>
      )}

      {requestError && (
        <div className="border border-neon-red/40 bg-neon-red/5 p-4 text-sm text-neon-red" role="alert">
          <div className="font-mono text-[10px] uppercase tracking-[0.14em]">Request failed</div>
          <p className="mt-1 text-slate-300">{requestError}</p>
        </div>
      )}

      {answer && (
        <Panel
          title="Answer"
          subtitle={answer.intent ? `intent: ${answer.intent}` : 'resolved response'}
          actions={answer.routed_via ? <span className="font-mono text-[10px] uppercase text-neon-green">{answer.routed_via}</span> : undefined}
        >
          {answer.error ? (
            <p className="text-sm text-neon-red">{answer.error}</p>
          ) : (
            <>
              <p className="whitespace-pre-wrap text-sm text-slate-200">{answer.answer}</p>
              {answer.reasoning && (
                <p className="mt-3 text-xs italic text-slate-500">
                  Router: {answer.reasoning}
                </p>
              )}
              <div className="mt-4 flex flex-wrap gap-3 border-t border-ink-800 pt-3 text-xs text-slate-500">
                {answer.miner_name && (
                  <span>
                    miner:{' '}
                    <span className="font-mono text-white">
                      {answer.miner_name} #{answer.miner_id}
                    </span>
                  </span>
                )}
                {answer.cost_usd !== undefined && (
                  <span>
                    cost: <span className="font-mono text-white">${answer.cost_usd.toFixed(3)}</span>
                  </span>
                )}
                {answer.duration_ms !== undefined && (
                  <span>
                    took: <span className="font-mono text-white">{answer.duration_ms}ms</span>
                  </span>
                )}
                {answer.data_source && (
                  <span>
                    source: <span className="font-mono text-white">{answer.data_source}</span>
                  </span>
                )}
                {answer.signal_hash && (
                  <span className="break-all">
                    signal:{' '}
                    <span className="font-mono text-neon-cyan">
                      {answer.signal_hash.slice(0, 14)}…
                    </span>
                  </span>
                )}
              </div>
            </>
          )}
        </Panel>
      )}
    </div>
  );
}
