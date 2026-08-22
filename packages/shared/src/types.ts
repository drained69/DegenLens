import { z } from 'zod';

/** Casino registered in the DegenMiner catalog. */
export const CasinoSchema = z.object({
  slug: z.string(),
  name: z.string(),
  website: z.string().url(),
  licensed_in: z.string().nullable(),
  established: z.number().int().nullable(),
  wallet_count: z.number().int(),
  attribution_status: z.enum(['attributed', 'unobserved']).optional(),
  chains: z.array(z.string()).default([]),
  queried_chains: z.array(z.string()).optional(),
  wallets: z.array(z.object({
    address: z.string(),
    chain: z.string(),
    role: z.enum(['deposit', 'hot', 'cold', 'treasury']),
    confidence: z.number(),
    evidence_status: z.enum(['verified', 'curated', 'unverified_seed']),
    evidence: z.array(z.string()),
    source: z.string().optional(),
    discovered_at: z.string().optional(),
    last_reviewed: z.string(),
  })).optional(),
});
export type Casino = z.infer<typeof CasinoSchema>;

/** Casino stats for a lookback window. */
export const CasinoStatsSchema = z.object({
  slug: z.string(),
  name: z.string(),
  window_hours: z.number().int(),
  deposits_usd: z.number(),
  withdrawals_usd: z.number(),
  observed_inbound_usd: z.number().optional(),
  observed_outbound_usd: z.number().optional(),
  attributed_customer_inflow_usd: z.number().optional(),
  attributed_customer_outflow_usd: z.number().optional(),
  internal_transfers_usd: z.number().optional(),
  unknown_flow_usd: z.number().optional(),
  net_observed_flow_usd: z.number().optional(),
  net_customer_flow_usd: z.number().optional(),
  net_flow_usd: z.number(),
  unique_depositors: z.number().int(),
  transaction_count: z.number().int(),
  wallet_count: z.number().int(),
  chains: z.array(z.string()),
  chains_claimed: z.array(z.string()).optional(),
  chains_queried: z.array(z.string()).optional(),
  by_chain: z.array(z.object({
    chain: z.string(),
    inbound_usd: z.number(),
    outbound_usd: z.number(),
    net_usd: z.number(),
    transfers: z.number().int(),
    share_of_observed_inbound_pct: z.number(),
    data_source: z.enum(['live', 'demo', 'unavailable']).optional(),
    coverage_complete: z.boolean().optional(),
     status: z.enum(['observed', 'queried_zero', 'unavailable', 'not_registered']).optional(),
  })).optional(),
  website: z.string().url().optional(),
  licensed_in: z.string().nullable().optional(),
  established: z.number().int().nullable().optional(),
  indexed_chains: z.array(z.string()).optional(),
  coverage_complete: z.boolean().optional(),
  coverage: z.number().optional(),
  classification: z.string().optional(),
  duplicate_count: z.number().int().optional(),
  evidence: z.record(z.unknown()).optional(),
  confidence: z.number(),
  verdict: z.string(),
  reasoning: z.string(),
  data_source: z.enum(['live', 'demo', 'unavailable']).optional(),
  timestamp: z.string(),
  served_at: z.string().optional(),
});
export type CasinoStats = z.infer<typeof CasinoStatsSchema>;

export const CasinoRankRowSchema = z.object({
  rank: z.number().int(),
  slug: z.string(),
  name: z.string(),
  deposits_usd: z.number(),
  withdrawals_usd: z.number(),
  net_flow_usd: z.number(),
  market_share_pct: z.number(),
  tracked_flow_share_pct: z.number().optional(),
  unique_depositors: z.number().int(),
  transaction_count: z.number().int().optional(),
  confidence: z.number(),
  data_source: z.enum(['live', 'demo', 'unavailable']).optional(),
  coverage_complete: z.boolean().optional(),
});
export type CasinoRankRow = z.infer<typeof CasinoRankRowSchema>;

export const CasinoRankingSchema = z.object({
  window_hours: z.number().int(),
  count: z.number().int(),
  ranking: z.array(CasinoRankRowSchema),
  confidence: z.number(),
  verdict: z.string(),
  reasoning: z.string(),
  data_source: z.enum(['live', 'demo', 'unavailable']).optional(),
  timestamp: z.string(),
  served_at: z.string().optional(),
});
export type CasinoRanking = z.infer<typeof CasinoRankingSchema>;

export const WalletAssociationSchema = z.object({
  casino_slug: z.string(),
  casino_name: z.string(),
  interactions_30d: z.number().int(),
  cluster_confidence: z.number(),
});
export type WalletAssociation = z.infer<typeof WalletAssociationSchema>;

export const WalletTraceSchema = z.object({
  address: z.string(),
  chain: z.string(),
  labeled_casino: z.string().nullable(),
  top_association: z.string().nullable(),
  casino_name: z.string().nullable(),
  confidence: z.number(),
  balance_native: z.number(),
  associations: z.array(WalletAssociationSchema),
  verdict: z.string(),
  reasoning: z.string(),
  labeled_casino_name: z.string().nullable().optional(),
  association_count: z.number().int().optional(),
  data_source: z.enum(['live', 'demo', 'unavailable']).optional(),
  timestamp: z.string(),
  served_at: z.string().optional(),
  classification: z.enum(['observed', 'calculated']).optional(),
  attribution: z.object({
    role: z.string(),
    evidence_status: z.string(),
    evidence: z.array(z.string()),
    last_reviewed: z.string(),
  }).nullable().optional(),
});
export type WalletTrace = z.infer<typeof WalletTraceSchema>;

export const AnomalyReportSchema = z.object({
  address: z.string(),
  chain: z.string(),
  verdict: z.enum(['normal', 'suspicious', 'critical', 'unavailable']),
  score: z.number(),
  signals: z.array(z.string()),
  signal_count: z.number().int(),
  is_suspicious: z.boolean().optional(),
  transfers_analyzed: z.number().int().optional(),
  window_hours: z.number().int().optional(),
  confidence: z.number(),
  reasoning: z.string(),
  data_source: z.enum(['live', 'demo', 'unavailable']).optional(),
  timestamp: z.string(),
  served_at: z.string().optional(),
});
export type AnomalyReport = z.infer<typeof AnomalyReportSchema>;

export const TransactionAssociationSchema = z.object({
  direction: z.enum(['from', 'to']),
  operator_slug: z.string(),
  operator_name: z.string(),
  address: z.string(),
  role: z.string(),
  confidence: z.number(),
  evidence_status: z.string(),
  evidence: z.array(z.string()),
});

export const TransactionLookupSchema = z.object({
  tx_hash: z.string(),
  chain: z.string(),
  status: z.string().optional(),
  block_number: z.number().int().nullable().optional(),
  block_hash: z.string().nullable().optional(),
  from_address: z.string().optional(),
  to_address: z.string().nullable().optional(),
  value_wei: z.string().optional(),
  value_native: z.number().optional(),
  gas: z.number().int().optional(),
  gas_price_wei: z.string().optional(),
  input: z.string().optional(),
  classification: z.string().optional(),
  associations: z.array(TransactionAssociationSchema).default([]),
  confidence: z.number(),
  verdict: z.string(),
  reasoning: z.string(),
  data_source: z.enum(['live', 'demo', 'unavailable']),
  method: z.string(),
  evidence: z.array(z.object({
    type: z.string(),
    chain: z.string(),
    tx_hash: z.string(),
  })),
  timestamp: z.string(),
  served_at: z.string().optional(),
});
export type TransactionLookup = z.infer<typeof TransactionLookupSchema>;

export const CasinoRegistrySchema = z.object({
  count: z.number().int(),
  attributed_count: z.number().int().optional(),
  unattributed_count: z.number().int().optional(),
  confidence: z.number(),
  verdict: z.string(),
  reasoning: z.string(),
  data_source: z.literal('registry'),
  casinos: z.array(CasinoSchema),
  timestamp: z.string(),
  served_at: z.string().optional(),
});
export type CasinoRegistry = z.infer<typeof CasinoRegistrySchema>;

/** Telegraph Engine `/ask` response envelope. */
export const TelegraphEngineResponseSchema = z.object({
  miner_id: z.string(),
  miner_name: z.string().optional(),
  endpoint: z.string().optional(),
  result: z.unknown(),
  cost_usd: z.number(),
  duration_ms: z.number(),
  timestamp: z.string().optional(),
  reasoning: z.string().optional(),
  intent: z.string().optional(),
  signal_hash: z.string().optional(),
  warnings: z.array(z.string()).optional(),
});
export type TelegraphEngineResponse<T = unknown> = Omit<
  z.infer<typeof TelegraphEngineResponseSchema>,
  'result'
> & { result: T };

/** Miner listing from `/api/miners`. */
export const TelegraphMinerSchema = z.object({
  id: z.union([z.string(), z.number()]),
  slug: z.string(),
  name: z.string(),
  description: z.string().optional(),
  base_url: z.string().optional(),
  supported_intents: z.array(z.string()).optional(),
  activation_status: z.string().optional(),
  min_price_usdc: z.union([z.string(), z.number()]).optional(),
});
export type TelegraphMiner = z.infer<typeof TelegraphMinerSchema>;

// ── Market analysis ──────────────────────────────────────────────────────────

export const ChainFlowSchema = z.object({
  chain: z.string(),
  inbound_usd: z.number(),
  outbound_usd: z.number(),
  net_usd: z.number(),
  transfers: z.number().int(),
  share_of_observed_inbound_pct: z.number(),
});
export type ChainFlow = z.infer<typeof ChainFlowSchema>;

export const NetworkDistributionSchema = z.object({
  window_hours: z.number().int(),
  chains: z.array(ChainFlowSchema),
  chains_observed: z.number().int(),
  total_inbound_usd: z.number(),
  data_source: z.string().optional(),
  coverage_complete: z.boolean().optional(),
  confidence: z.number().optional(),
  verdict: z.string().optional(),
  reasoning: z.string().optional(),
});
export type NetworkDistribution = z.infer<typeof NetworkDistributionSchema>;

export const AssetRowSchema = z.object({
  symbol: z.string(),
  inbound_usd: z.number(),
  outbound_usd: z.number(),
  transfers: z.number().int(),
  share_of_observed_inbound_pct: z.number(),
  is_stablecoin: z.boolean(),
});
export type AssetRow = z.infer<typeof AssetRowSchema>;

export const AssetMixSchema = z.object({
  slug: z.string().nullable().optional(),
  window_hours: z.number().int(),
  assets: z.array(AssetRowSchema),
  distinct_assets: z.number().int(),
  stablecoin_share_pct: z.number(),
  data_source: z.string().optional(),
  coverage_complete: z.boolean().optional(),
  confidence: z.number().optional(),
  reasoning: z.string().optional(),
});
export type AssetMix = z.infer<typeof AssetMixSchema>;

export const LargeTransferSchema = z.object({
  tx_hash: z.string(),
  chain: z.string(),
  operator_slug: z.string(),
  operator_name: z.string(),
  direction: z.enum(['inbound', 'outbound']),
  counterparty: z.string(),
  token: z.string(),
  amount: z.number(),
  usd_value: z.number(),
  timestamp: z.string(),
});
export type LargeTransfer = z.infer<typeof LargeTransferSchema>;

export const LargeTransfersSchema = z.object({
  window_hours: z.number().int(),
  min_usd: z.number(),
  count: z.number().int(),
  transfers: z.array(LargeTransferSchema),
  data_source: z.string().optional(),
  coverage_complete: z.boolean().optional(),
  confidence: z.number().optional(),
  reasoning: z.string().optional(),
});
export type LargeTransfers = z.infer<typeof LargeTransfersSchema>;

export const FlowPointSchema = z.object({
  t: z.string(),
  inbound_usd: z.number(),
  outbound_usd: z.number(),
  net_usd: z.number(),
  transfers: z.number().int(),
});
export type FlowPoint = z.infer<typeof FlowPointSchema>;

export const FlowSeriesSchema = z.object({
  slug: z.string(),
  name: z.string().optional(),
  window_hours: z.number().int().optional(),
  bucket_hours: z.number().int().optional(),
  points: z.number().int().optional(),
  series: z.array(FlowPointSchema),
  error: z.string().optional(),
  data_source: z.string().optional(),
  coverage_complete: z.boolean().optional(),
  confidence: z.number().optional(),
  verdict: z.string().optional(),
  reasoning: z.string().optional(),
});
export type FlowSeries = z.infer<typeof FlowSeriesSchema>;

export const CounterpartySchema = z.object({
  address: z.string(),
  inbound_usd: z.number(),
  outbound_usd: z.number(),
  total_usd: z.number(),
  transfers: z.number().int(),
  share_of_observed_flow_pct: z.number().optional(),
});
export type Counterparty = z.infer<typeof CounterpartySchema>;

export const CounterpartyConcentrationSchema = z.object({
  slug: z.string(),
  name: z.string().optional(),
  window_hours: z.number().int().optional(),
  distinct_counterparties: z.number().int().optional(),
  top10_share_of_observed_flow_pct: z.number().optional(),
  counterparties: z.array(CounterpartySchema),
  error: z.string().optional(),
  data_source: z.string().optional(),
  confidence: z.number().optional(),
  reasoning: z.string().optional(),
});
export type CounterpartyConcentration = z.infer<typeof CounterpartyConcentrationSchema>;

export const CoverageReportSchema = z.object({
  operators_catalogued: z.number().int(),
  operators_attributed: z.number().int(),
  operators_unattributed: z.number().int(),
  wallet_clusters: z.number().int(),
  chains_covered: z.array(z.string()),
  chains_claimed: z.array(z.string()).optional(),
  attributed: z.array(
    z.object({
      slug: z.string(),
      name: z.string(),
      wallets: z.number().int(),
      chains: z.array(z.string()),
      chains_queried: z.array(z.string()).optional(),
      evidence_status: z.string().nullable(),
    }),
  ),
  unattributed: z.array(
    z.object({
      slug: z.string(),
      name: z.string(),
      attribution_status: z.string(),
    }),
  ),
  caveat: z.string(),
  confidence: z.number().optional(),
  reasoning: z.string().optional(),
});
export type CoverageReport = z.infer<typeof CoverageReportSchema>;
