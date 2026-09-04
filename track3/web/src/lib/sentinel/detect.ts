import type { CasinoStats } from '@degenlens/shared';
import type { Finding, Severity, StatsSnapshot, WalletWatchRow } from './types';

/**
 * Bankrun-style detection over observed operator flow and watched balances.
 *
 * Every rule is evidence-aware: it reports a measurement and the underlying
 * numbers, never a claim about solvency. Directional flow is an observation,
 * not proof of insolvency — the alert copy keeps that distinction.
 */

const OUTFLOW_DOMINANCE_RATIO = 1.5;
const DEPOSITOR_EXIT_DROP = 0.5;
const CONFIDENCE_FLOOR = 0.3;

export const DRAIN_DROP_HIGH = 0.5;
export const DRAIN_DROP_MEDIUM = 0.25;

export function toSnapshot(stats: CasinoStats): StatsSnapshot {
  return {
    ts: stats.served_at ?? stats.timestamp,
    deposits_usd: stats.deposits_usd,
    withdrawals_usd: stats.withdrawals_usd,
    net_flow_usd: stats.net_flow_usd,
    unique_depositors: stats.unique_depositors,
    transaction_count: stats.transaction_count,
    verdict: stats.verdict,
    confidence: stats.confidence,
  };
}

export function detectFindings(
  stats: CasinoStats,
  prev: StatsSnapshot | undefined,
  floorUsd: number,
): Finding[] {
  const findings: Finding[] = [];
  const material = stats.withdrawals_usd >= floorUsd;

  // Withdrawals materially exceed deposits over the window.
  if (
    material &&
    stats.deposits_usd > 0 &&
    stats.withdrawals_usd / stats.deposits_usd >= OUTFLOW_DOMINANCE_RATIO
  ) {
    findings.push({
      rule: 'outflow_dominance',
      severity: 'high',
      measurement: `withdrawals $${fmt(stats.withdrawals_usd)} vs deposits $${fmt(stats.deposits_usd)} (${(stats.withdrawals_usd / stats.deposits_usd).toFixed(2)}x over ${stats.window_hours}h)`,
      evidence: [
        `observed withdrawals_usd = ${stats.withdrawals_usd}`,
        `observed deposits_usd = ${stats.deposits_usd}`,
        `window_hours = ${stats.window_hours}`,
      ],
    });
  }

  // Net flow is negative on material volume.
  if (material && stats.net_flow_usd < 0) {
    findings.push({
      rule: 'net_outflow',
      severity: 'medium',
      measurement: `net observed flow $${fmt(stats.net_flow_usd)} over ${stats.window_hours}h`,
      evidence: [
        `net_flow_usd = ${stats.net_flow_usd}`,
        `withdrawals_usd = ${stats.withdrawals_usd}`,
      ],
    });
  }

  if (prev) {
    // Net flow flipped from positive to negative between scans.
    if (prev.net_flow_usd > 0 && stats.net_flow_usd < 0 && material) {
      findings.push({
        rule: 'flow_flip',
        severity: 'high',
        measurement: `net flow flipped from $${fmt(prev.net_flow_usd)} (seen ${shortAgo(prev.ts)}) to $${fmt(stats.net_flow_usd)}`,
        evidence: [
          `previous net_flow_usd = ${prev.net_flow_usd}`,
          `current net_flow_usd = ${stats.net_flow_usd}`,
        ],
      });
    }

    // Depositors halved while withdrawals stayed material — player exodus shape.
    if (
      prev.unique_depositors > 0 &&
      stats.unique_depositors <= Math.max(1, prev.unique_depositors * DEPOSITOR_EXIT_DROP) &&
      material
    ) {
      findings.push({
        rule: 'depositor_exit',
        severity: 'medium',
        measurement: `unique depositors fell from ${prev.unique_depositors} to ${stats.unique_depositors} while withdrawals held at $${fmt(stats.withdrawals_usd)}`,
        evidence: [
          `previous unique_depositors = ${prev.unique_depositors}`,
          `current unique_depositors = ${stats.unique_depositors}`,
          `withdrawals_usd = ${stats.withdrawals_usd}`,
        ],
      });
    }

    // Verdict changed away from healthy.
    if (prev.verdict !== stats.verdict && stats.verdict !== 'healthy') {
      findings.push({
        rule: 'verdict_change',
        severity: 'medium',
        measurement: `miner verdict moved from "${prev.verdict}" to "${stats.verdict}"`,
        evidence: [
          `previous verdict = ${prev.verdict}`,
          `current verdict = ${stats.verdict}`,
          `current confidence = ${stats.confidence}`,
        ],
      });
    }
  }

  // The miner itself reports low confidence on live data — surface honesty, not alarm.
  if (stats.confidence < CONFIDENCE_FLOOR && stats.data_source === 'live') {
    findings.push({
      rule: 'low_confidence_observation',
      severity: 'medium',
      measurement: `live observation reported at confidence ${stats.confidence.toFixed(2)} (below ${CONFIDENCE_FLOOR})`,
      evidence: [
        `confidence = ${stats.confidence}`,
        `data_source = live`,
        `coverage_complete = ${stats.coverage_complete ?? 'unknown'}`,
      ],
    });
  }

  return findings;
}

/**
 * Drain detection over paid wallet-balance watches.
 * A drop is only a finding when the prior balance was material — dust moving
 * is operations, not a bankrun.
 */
export function detectWalletFindings(
  rows: WalletWatchRow[],
  floorNative: number,
): Finding[] {
  const findings: Finding[] = [];
  for (const row of rows) {
    if (!row.ok || row.previous == null || row.balance == null) continue;
    const prev = row.previous;
    const curr = row.balance;
    if (prev < floorNative) continue;

    const dropPct = prev > 0 ? (prev - curr) / prev : 0;
    const where = `${row.address.slice(0, 10)}… (${row.role}, ${row.chain})`;
    const measured = `balance ${fmtNative(prev, row.symbol)} → ${fmtNative(curr, row.symbol)} (${(dropPct * 100).toFixed(1)}% drop) at ${where}`;

    if (dropPct >= DRAIN_DROP_HIGH) {
      findings.push({
        rule: 'wallet_drain',
        severity: 'high',
        measurement: measured,
        evidence: [
          `previous native_balance = ${prev} ${row.symbol ?? ''}`,
          `current native_balance = ${curr} ${row.symbol ?? ''}`,
          `watched via paid WALLET_BALANCE_CHECK on ${row.chain}`,
        ],
      });
    } else if (dropPct >= DRAIN_DROP_MEDIUM) {
      findings.push({
        rule: 'balance_decline',
        severity: 'medium',
        measurement: measured,
        evidence: [
          `previous native_balance = ${prev} ${row.symbol ?? ''}`,
          `current native_balance = ${curr} ${row.symbol ?? ''}`,
        ],
      });
    }
  }
  return findings;
}

function fmtNative(n: number, symbol?: string | null): string {
  const s = symbol ?? '';
  return `${n.toFixed(n >= 100 ? 0 : 2)}${s ? ' ' + s : ''}`;
}

export function maxSeverity(findings: Finding[]): Severity | null {
  if (findings.some((f) => f.severity === 'high')) return 'high';
  if (findings.length > 0) return 'medium';
  return null;
}

function fmt(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(1)}K`;
  return `${sign}${abs.toFixed(0)}`;
}

function shortAgo(iso: string): string {
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 0) return 'earlier';
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `${Math.max(1, minutes)}m ago`;
  return `${Math.round(minutes / 60)}h ago`;
}
