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
  reasoning?: string;
  error?: string;
}

const EXAMPLES = [
  'Which casino has the highest deposit growth this week?',
  'Compare fairness and volume for stake and rollbit.',
  'Is 0x974caa59e49682cda0ad2bbe82983419a2ecc400 a labeled casino wallet?',
  'What is Telegraph Protocol and how does it work?',
];

export default function AskPage() {
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<AnswerPayload | null>(null);

  async function submit(question: string) {
    setLoading(true);
    setAnswer(null);
    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: question }),
      });
      setAnswer((await res.json()) as AnswerPayload);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Natural language / routed intents"
        title="Ask DegenLens"
        description="Natural-language queries routed through Telegraph. Every answer arrives with a signal hash you can verify."
      />

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) submit(q.trim());
        }}
        className="flex gap-2"
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask anything about crypto casinos, wallets, or Telegraph itself…"
          className="flex-1 rounded-lg border border-ink-700 bg-ink-900 px-4 py-3 text-sm text-white placeholder:text-slate-600 focus:border-neon-cyan focus:outline-none"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-neon-cyan/20 px-6 py-3 text-sm font-semibold text-neon-cyan hover:bg-neon-cyan/30 disabled:opacity-50"
        >
          {loading ? '…' : 'Ask'}
        </button>
      </form>

      <div className="flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => {
              setQ(ex);
              submit(ex);
            }}
            className="rounded-full border border-ink-700 bg-ink-800/50 px-3 py-1 text-xs text-slate-400 hover:border-neon-cyan hover:text-white"
          >
            {ex}
          </button>
        ))}
      </div>

      {answer && (
        <Panel
          title="Answer"
          subtitle={answer.intent ? `intent: ${answer.intent}` : undefined}
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
