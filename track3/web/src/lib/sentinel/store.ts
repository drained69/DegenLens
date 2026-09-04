import { promises as fs } from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import type {
  ScanRecord,
  SentinelAlert,
  SentinelState,
  SentinelTotals,
} from './types';

const MAX_ALERTS = 100;
const MAX_SCANS = 50;
const MAX_RECEIPTS = 400;

const STATE_FILE =
  process.env.SENTINEL_STATE_PATH ??
  path.join(process.cwd(), '.sentinel-state.json');

function freshState(): SentinelState {
  return {
    version: 1,
    started_at: new Date().toISOString(),
    snapshots: {},
    wallet_snapshots: {},
    watch_cursor: 0,
    last_alert_at: {},
    alerts: [],
    scans: [],
    receipts: [],
    totals: { scans: 0, paid_calls: 0, spend_usd: 0, alerts_fired: 0, escalations: 0 },
  };
}

export type ScanPhase =
  | 'discover'
  | 'watch'
  | 'detect'
  | 'enrich'
  | 'escalate'
  | 'report'
  | 'idle';

interface SentinelRuntime {
  state: SentinelState;
  loaded: boolean;
  running: boolean;
  /** Live phase of the in-progress scan, for the UI pipeline. */
  phase: ScanPhase;
  /** Operator the current phase is working on, when applicable. */
  phaseSubject?: string;
  lastScan?: ScanRecord;
  timer?: ReturnType<typeof setInterval>;
  bootTimer?: ReturnType<typeof setTimeout>;
}

// Module state is duplicated across Next.js route bundles and dev hot reloads,
// so the singleton lives on globalThis.
const g = globalThis as unknown as { __degenlens_sentinel?: SentinelRuntime };

function runtime(): SentinelRuntime {
  if (!g.__degenlens_sentinel) {
    g.__degenlens_sentinel = {
      state: freshState(),
      loaded: false,
      running: false,
      phase: 'idle',
    };
  }
  return g.__degenlens_sentinel;
}

async function loadFromDisk(): Promise<void> {
  const rt = runtime();
  if (rt.loaded) return;
  rt.loaded = true;
  try {
    const raw = await fs.readFile(STATE_FILE, 'utf8');
    const parsed = JSON.parse(raw) as SentinelState;
    if (parsed?.version === 1) {
      rt.state = {
        ...freshState(),
        ...parsed,
        totals: { ...freshState().totals, ...parsed.totals },
      };
    }
  } catch {
    // Missing or corrupt state file — start fresh rather than crash the agent.
  }
}

async function persist(): Promise<void> {
  try {
    await fs.writeFile(STATE_FILE, JSON.stringify(runtime().state), 'utf8');
  } catch {
    // Ephemeral or read-only filesystem — in-memory state remains authoritative.
  }
}

export const sentinelStore = {
  async state(): Promise<SentinelState> {
    await loadFromDisk();
    return runtime().state;
  },

  runtime(): SentinelRuntime {
    return runtime();
  },

  isRunning(): boolean {
    return runtime().running;
  },

  setRunning(value: boolean): void {
    runtime().running = value;
    if (!value) {
      runtime().phase = 'idle';
      runtime().phaseSubject = undefined;
    }
  },

  setPhase(phase: ScanPhase, subject?: string): void {
    const rt = runtime();
    rt.phase = phase;
    rt.phaseSubject = subject;
  },

  phase(): { phase: ScanPhase; subject?: string } {
    const rt = runtime();
    return { phase: rt.phase, subject: rt.phaseSubject };
  },

  setLastScan(scan: ScanRecord): void {
    runtime().lastScan = scan;
  },

  lastScan(): ScanRecord | undefined {
    return runtime().lastScan;
  },

  /** Append an alert (bounded) and stamp the cooldown key. */
  async pushAlert(alert: SentinelAlert, cooldownKeys: string[]): Promise<void> {
    await loadFromDisk();
    const s = runtime().state;
    s.alerts.unshift(alert);
    if (s.alerts.length > MAX_ALERTS) s.alerts.length = MAX_ALERTS;
    s.totals.alerts_fired += 1;
    const now = alert.ts;
    for (const key of cooldownKeys) s.last_alert_at[key] = now;
    await persist();
  },

  async pushScan(scan: ScanRecord): Promise<void> {
    await loadFromDisk();
    const s = runtime().state;
    s.scans.unshift(scan);
    if (s.scans.length > MAX_SCANS) s.scans.length = MAX_SCANS;
    s.totals.scans += 1;
    s.totals.escalations += scan.escalations;
    runtime().lastScan = scan;
    await persist();
  },

  async pushReceipt(receipt: SentinelState['receipts'][number]): Promise<void> {
    await loadFromDisk();
    const s = runtime().state;
    s.receipts.unshift(receipt);
    if (s.receipts.length > MAX_RECEIPTS) s.receipts.length = MAX_RECEIPTS;
    // Only engine traffic counts as paid calls — local co-located miner calls
    // are free and would otherwise inflate the network-usage evidence.
    if (receipt.ok && receipt.mode !== 'local') {
      s.totals.paid_calls += 1;
      s.totals.spend_usd += receipt.cost_usd ?? 0;
    }
  },

  async flushReceipts(): Promise<void> {
    await persist();
  },

  async putSnapshot(slug: string, snapshot: SentinelState['snapshots'][string]): Promise<void> {
    await loadFromDisk();
    runtime().state.snapshots[slug] = snapshot;
  },

  async putWalletSnapshot(
    key: string,
    snapshot: SentinelState['wallet_snapshots'][string],
  ): Promise<void> {
    await loadFromDisk();
    runtime().state.wallet_snapshots[key] = snapshot;
  },

  walletSnapshotFor(key: string): SentinelState['wallet_snapshots'][string] | undefined {
    return runtime().state.wallet_snapshots?.[key];
  },

  watchCursor(): number {
    return runtime().state.watch_cursor ?? 0;
  },

  async setWatchCursor(cursor: number): Promise<void> {
    await loadFromDisk();
    runtime().state.watch_cursor = cursor;
  },

  async persist(): Promise<void> {
    await persist();
  },

  snapshotFor(slug: string): SentinelState['snapshots'][string] | undefined {
    return runtime().state.snapshots[slug];
  },

  lastAlertAt(key: string): string | undefined {
    return runtime().state.last_alert_at[key];
  },

  totals(): SentinelTotals {
    return runtime().state.totals;
  },

  /** In-memory receipts, newest first (pushReceipt unshifts). */
  receipts(): SentinelState['receipts'] {
    return runtime().state.receipts;
  },

  newId(): string {
    return randomUUID();
  },
};
