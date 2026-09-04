import type { CasinoStats } from '@degenlens/shared';

export type Severity = 'medium' | 'high';

export type ScanTrigger = 'boot' | 'schedule' | 'manual';

/** One detected risk condition on an operator's observed flow or balances. */
export interface Finding {
  rule: string;
  severity: Severity;
  measurement: string;
  evidence: string[];
}

/** One paid call the agent made through the Telegraph Engine. */
export interface Receipt {
  ts: string;
  purpose:
    | 'discovery'
    | 'stats'
    | 'watch'
    | 'fraud'
    | 'txlookup'
    | 'escalation';
  mode: 'engine-direct' | 'engine-routed' | 'local';
  endpoint?: string;
  query?: string;
  intent?: string;
  miner_id: string;
  miner_name?: string;
  cost_usd: number;
  duration_ms?: number;
  signal_hash?: string;
  ok: boolean;
  error?: string;
}

/** One escalation step: a question the agent sent to another miner on the network. */
export interface EscalationStep {
  step: string;
  query: string;
  intent?: string;
  miner_id?: string;
  miner_name?: string;
  answer?: string;
  cost_usd: number;
  duration_ms?: number;
  signal_hash?: string;
  ok: boolean;
  error?: string;
}

/** Rolling summary of an operator's last observed stats, for delta detection. */
export interface StatsSnapshot {
  ts: string;
  deposits_usd: number;
  withdrawals_usd: number;
  net_flow_usd: number;
  unique_depositors: number;
  transaction_count: number;
  verdict: string;
  confidence: number;
}

/** Last observed native balance of a watched wallet, for drain detection. */
export interface WalletSnapshot {
  ts: string;
  native_balance: number;
  symbol: string;
  block_number?: number | null;
}

/** One watched-operator wallet row attached to an alert. */
export interface WalletWatchRow {
  operator_slug: string;
  operator_name: string;
  address: string;
  chain: string;
  role: string;
  balance?: number | null;
  symbol?: string | null;
  previous?: number | null;
  drop_pct?: number;
  ok: boolean;
  note?: string;
}

/** Paid FRAUD_DETECTION screen attached to an alert. */
export interface FraudScreen {
  address: string;
  chain: string;
  ok: boolean;
  risk_tier?: string;
  risk_score?: number;
  reasoning?: string;
  signal_count?: number;
  error?: string;
}

/** Paid ONCHAIN_TX_LOOKUP evidence attached to an alert. */
export interface TxEvidence {
  tx_hash: string;
  chain: string;
  ok: boolean;
  status?: string;
  from_address?: string;
  to_address?: string;
  value_native?: number;
  reasoning?: string;
  error?: string;
}

export interface SentinelAlert {
  id: string;
  ts: string;
  operator_slug: string;
  operator_name: string;
  severity: Severity;
  title: string;
  findings: Finding[];
  stats?: CasinoStats;
  previous?: StatsSnapshot;
  wallet_watch: WalletWatchRow[];
  fraud_screens: FraudScreen[];
  tx_lookups: TxEvidence[];
  escalation: EscalationStep[];
  signal_hashes: string[];
  data_source?: string;
  telegram_delivered?: boolean;
}

export interface ScanRecord {
  id: string;
  started_at: string;
  duration_ms: number;
  trigger: ScanTrigger;
  operators_scanned: number;
  wallets_watched: number;
  alerts_fired: number;
  escalations: number;
  paid_calls: number;
  spend_usd: number;
  errors: string[];
}

export interface SentinelTotals {
  scans: number;
  paid_calls: number;
  spend_usd: number;
  alerts_fired: number;
  escalations: number;
}

export interface SentinelState {
  version: 1;
  started_at: string;
  snapshots: Record<string, StatsSnapshot>;
  /** `${chain}:${address}` -> last observed balance. */
  wallet_snapshots: Record<string, WalletSnapshot>;
  /** Rotation cursor over the flattened watch list. */
  watch_cursor: number;
  /** Cooldown bookkeeping: `${slug}:${rule}` -> last alert timestamp. */
  last_alert_at: Record<string, string>;
  alerts: SentinelAlert[];
  scans: ScanRecord[];
  receipts: Receipt[];
  totals: SentinelTotals;
}
