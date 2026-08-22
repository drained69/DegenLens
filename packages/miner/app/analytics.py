"""Aggregation and anomaly detection over on-chain transfer data.

This is the layer that turns raw transfers into the intelligence the network
actually pays for. Every public function returns provenance (`data_source`)
alongside its numbers so the API can never present synthetic or stale figures
as observed chain state.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field

from collections import defaultdict

from .onchain import (
    Transfer,
    TransferSet,
    get_observation_transfers,
    get_transfers,
    merge_cluster_reads,
    native_balance,
)
from .intelligence import DataState, Evidence, aggregate_flows
from .prices import resolve_prices
from .settings import settings
from .wallets import CASINOS, Casino, all_casinos, get_casino, observation_targets

_STATS_CACHE: dict[tuple[str, int], tuple[CasinoStats, float]] = {}


def _merge_source(sources: list[str]) -> str:
    """Worst-case provenance wins: unavailable > demo > live."""
    if not sources:
        return "unavailable"
    for level in ("unavailable", "demo"):
        if level in sources:
            return level
    return "live"


@dataclass
class CasinoStats:
    slug: str
    name: str
    window_hours: int
    deposits_usd: float
    withdrawals_usd: float
    net_flow_usd: float
    unique_depositors: int
    transaction_count: int
    confidence: float
    data_source: str
    wallet_count: int
    chains: list[str] = field(default_factory=list)
    chains_claimed: list[str] = field(default_factory=list)
    chains_queried: list[str] = field(default_factory=list)
    by_chain: list[dict] = field(default_factory=list)
    # False when a lookback window could not be fully paged upstream. Totals are
    # then lower bounds on observed flow, not complete measurements.
    coverage_complete: bool = True
    coverage: float = 0.0
    evidence: dict = field(default_factory=dict)
    observed_inflow: float = 0.0
    observed_outflow: float = 0.0
    internal_transfers_usd: float = 0.0
    unknown_flow_usd: float = 0.0
    unique_withdrawers: int = 0
    duplicate_count: int = 0

    @property
    def observed_inbound_usd(self) -> float:
        return self.observed_inflow

    @property
    def observed_outbound_usd(self) -> float:
        return self.observed_outflow


async def _aggregate_casino(
    casino: Casino, hours: int, *, include_transaction_evidence: bool = True
) -> CasinoStats:
    # Fetch every identity claim on every indexed EVM chain, concurrently.
    # Querying only the seed chain dropped Polygon/Base/BSC/... activity for
    # operators that reuse the same hot wallet across networks.
    targets = observation_targets(casino)
    seed_pairs = {(w.address.lower(), w.chain) for w in casino.wallets}
    sets: list[TransferSet] = await asyncio.gather(
        *(
            get_observation_transfers(
                w.address,
                w.chain,
                hours,
                seed=(w.address.lower(), w.chain) in seed_pairs,
            )
            for w in targets
        )
    ) if targets else []

    # Resolve every distinct token symbol in ONE upstream call.
    symbols = {t.token_symbol for s in sets for t in s.transfers}
    prices = await resolve_prices(symbols)

    all_transfers: list[Transfer] = []
    casino_addresses = {w.address.lower() for w in casino.wallets}
    by_chain_acc: dict[str, dict[str, float]] = defaultdict(
        lambda: {"inbound_usd": 0.0, "outbound_usd": 0.0, "transfers": 0}
    )
    chain_sources: dict[str, str] = {}
    chain_complete: dict[str, bool] = {}

    for wallet, tset in zip(targets, sets):
        addr = wallet.address.lower()
        # Keep chain-level provenance separate from aggregate provenance. A
        # failed optional network must not be represented as observed zero.
        chain_sources[wallet.chain] = tset.data_source
        chain_complete[wallet.chain] = tset.complete
        all_transfers.extend(tset.transfers)
        for t in tset.transfers:
            price = prices.get(t.token_symbol, 0.0)
            if price <= 0:
                continue  # unknown asset — excluded rather than guessed
            usd = t.amount * price
            bucket = by_chain_acc[t.chain]
            # Chain-level observed totals remain directional facts. The flow
            # aggregate below applies cluster classification and deduplication.
            if t.to_addr == addr:
                bucket["inbound_usd"] += usd
            elif t.from_addr == addr:
                bucket["outbound_usd"] += usd
            bucket["transfers"] += 1

    source, coverage_complete = merge_cluster_reads(sets)
    flow = aggregate_flows(
        all_transfers,
        prices,
        casino_addresses,
        coverage=sum(1 for s in sets if s.data_source != "unavailable") / max(len(sets), 1),
        source=source,
    )
    deposits_usd = flow.attributed_customer_inflow_usd
    withdrawals_usd = flow.attributed_customer_outflow_usd
    unique_depositors = {r.transfer.from_address for r in flow.classifications if r.classification.value == "CUSTOMER_DEPOSIT"}
    tx_count = flow.transaction_count

    confidence = (
        sum(w.confidence for w in casino.wallets) / len(casino.wallets)
        if casino.wallets
        else 0.0
    )
    # Provenance discounts confidence — synthetic data is never high-confidence.
    if source == "unavailable":
        confidence = 0.0
    elif source == "demo":
        confidence *= 0.5
    # A truncated window yields a lower bound, not a measurement. Say so in the
    # confidence rather than presenting a partial total at full confidence.
    if source != "unavailable" and not coverage_complete:
        confidence *= 0.6

    total_in = sum(b["inbound_usd"] for b in by_chain_acc.values()) or 1.0
    by_chain = sorted(
        (
            {
                "chain": chain,
                "inbound_usd": round(b["inbound_usd"], 2),
                "outbound_usd": round(b["outbound_usd"], 2),
                "net_usd": round(b["inbound_usd"] - b["outbound_usd"], 2),
                "transfers": int(b["transfers"]),
                "share_of_observed_inbound_pct": round(
                    b["inbound_usd"] / total_in * 100, 2
                ),
                "data_source": chain_sources.get(chain, "unavailable"),
                "coverage_complete": chain_complete.get(chain, False),
                "status": (
                    "not_registered"
                    if chain not in casino.queried_chains
                    else
                    "unavailable"
                    if chain_sources.get(chain) == "unavailable"
                    else "observed"
                    if b["transfers"]
                    else "queried_zero"
                ),
            }
            for chain in casino.queried_chains
            for b in [by_chain_acc[chain]]
        ),
        key=lambda row: -row["inbound_usd"],
    )

    rounded_inbound = round(deposits_usd, 2)
    rounded_outbound = round(withdrawals_usd, 2)
    return CasinoStats(
        slug=casino.slug,
        name=casino.name,
        window_hours=hours,
        deposits_usd=rounded_inbound,
        withdrawals_usd=rounded_outbound,
        net_flow_usd=round(rounded_inbound - rounded_outbound, 2),
        unique_depositors=len(unique_depositors),
        transaction_count=tx_count,
        confidence=round(confidence, 3),
        data_source=source,
        wallet_count=len(casino.wallets),
        chains=[row["chain"] for row in by_chain if row["transfers"]],
        chains_claimed=casino.chains,
        chains_queried=casino.queried_chains,
        by_chain=by_chain,
        coverage_complete=coverage_complete,
        coverage=round(flow.coverage, 3),
        observed_inflow=round(flow.observed_inflow_usd, 2),
        observed_outflow=round(flow.observed_outflow_usd, 2),
        internal_transfers_usd=round(flow.internal_transfers_usd, 2),
        unknown_flow_usd=round(flow.unknown_flow_usd, 2),
        unique_withdrawers=flow.unique_withdrawers,
        duplicate_count=flow.duplicate_count,
        evidence=Evidence(
            claim=f"{casino.name} attributed customer flow over {hours} hours",
            classification=DataState.CALCULATED,
            sources=tuple(sorted({s.data_source for s in sets})),
            transactions=(
                tuple(r.transfer.tx_hash for r in flow.classifications)
                if include_transaction_evidence
                else ()
            ),
            wallets=tuple(sorted(casino_addresses)),
            methodology="Sum deduplicated transfers classified against the registered casino wallet cluster; internal cluster transfers excluded from customer flow.",
            confidence=round(confidence, 3),
            coverage=round(flow.coverage, 3),
            timestamp=max((t.timestamp.isoformat() for t in all_transfers), default=""),
        ).as_dict(),
    )


async def casino_stats(slug: str, hours: int = 24) -> CasinoStats | None:
    casino = get_casino(slug)
    if not casino:
        return None
    stats = await _aggregate_casino(
        casino, hours, include_transaction_evidence=False
    )
    _STATS_CACHE[(casino.slug, hours)] = (
        stats, time.monotonic() + settings.stats_ttl
    )
    return stats


def cached_casino_stats(slug: str, hours: int) -> CasinoStats | None:
    cached = _STATS_CACHE.get((slug.lower(), hours))
    if not cached or cached[1] <= time.monotonic():
        return None
    return cached[0]


async def rank_casinos(hours: int = 168) -> tuple[list[dict], str]:
    """Ranked casinos plus merged provenance."""
    now = time.monotonic()
    if settings.live_data_available:
        operators = all_casinos()
        cached = {
            casino.slug: entry[0]
            for casino in operators
            if (entry := _STATS_CACHE.get((casino.slug, hours))) and entry[1] > now
        }
        missing = [casino for casino in operators if casino.slug not in cached]
        refreshed = await asyncio.gather(*(
            casino_stats(casino.slug, hours) for casino in missing
        ))
        stats = [*cached.values(), *(row for row in refreshed if row is not None)]
    else:
        stats = await asyncio.gather(*(
            _aggregate_casino(casino, hours, include_transaction_evidence=False)
            for casino in all_casinos()
        ))
    unavailable = [casino for casino in all_casinos() if not any(s.slug == casino.slug for s in stats)]
    # Unavailable reads are unknown, not zero. Keep them after measured rows so
    # a provider failure cannot silently rank an operator by a fabricated zero.
    ordered = sorted(
        stats,
        key=lambda s: (s.data_source == "unavailable", -s.deposits_usd, s.slug),
    )
    measured = [s for s in ordered if s.data_source != "unavailable"]
    total = sum(s.deposits_usd for s in measured) or 1.0
    rows = [
        {
            "rank": i + 1,
            "slug": s.slug,
            "name": s.name,
            "deposits_usd": s.deposits_usd,
            "withdrawals_usd": s.withdrawals_usd,
            "net_flow_usd": s.net_flow_usd,
            "tracked_flow_share_pct": (
                round(s.deposits_usd / total * 100, 2)
                if s.data_source != "unavailable"
                else 0.0
            ),
            "market_share_pct": (
                round(s.deposits_usd / total * 100, 2)
                if s.data_source != "unavailable"
                else 0.0
            ),
            "unique_depositors": s.unique_depositors,
            "transaction_count": s.transaction_count,
            "confidence": s.confidence,
            "data_source": s.data_source,
            "coverage_complete": s.coverage_complete,
        }
        for i, s in enumerate(ordered)
    ]
    rows.extend(
        {
            "rank": len(rows) + 1,
            "slug": casino.slug,
            "name": casino.name,
            "deposits_usd": 0.0,
            "withdrawals_usd": 0.0,
            "net_flow_usd": 0.0,
            "tracked_flow_share_pct": 0.0,
            "market_share_pct": 0.0,
            "unique_depositors": 0,
            "transaction_count": 0,
            "confidence": 0.0,
            "data_source": "unavailable",
            "coverage_complete": False,
        }
        for i, casino in enumerate(unavailable)
    )
    return rows, _merge_source([s.data_source for s in ordered]) if ordered else "unavailable"


# ── Fraud detection ──────────────────────────────────────────────────────────


@dataclass
class AnomalyReport:
    address: str
    chain: str
    verdict: str  # "normal" | "suspicious" | "critical"
    score: float  # 0..1, higher = more suspicious
    signals: list[str]
    reasoning: str
    data_source: str
    transfers_analyzed: int


def _detect_wash_trading(transfers: list[Transfer], addr: str) -> tuple[float, list[str]]:
    """Round-trip flows returning to the same counterparty at a similar size."""
    outbound = [t for t in transfers if t.from_addr == addr]
    inbound = [t for t in transfers if t.to_addr == addr]
    signals: list[str] = []
    score = 0.0

    # Index inbound by counterparty so this stays near-linear instead of O(n²).
    by_counterparty: dict[str, list[Transfer]] = {}
    for t in inbound:
        by_counterparty.setdefault(t.from_addr, []).append(t)

    for out in outbound:
        for inn in by_counterparty.get(out.to_addr, []):
            within_hour = abs((out.timestamp - inn.timestamp).total_seconds()) < 3600
            similar_size = abs(out.amount - inn.amount) / max(inn.amount, 1e-9) < 0.02
            if within_hour and similar_size:
                signals.append(
                    f"wash-trade round-trip {out.tx_hash[:10]}↔{inn.tx_hash[:10]} "
                    f"with {out.to_addr[:10]}"
                )
                score += 0.15
                break  # one signal per outbound leg
    return score, signals


def _detect_velocity_spike(transfers: list[Transfer]) -> tuple[float, list[str]]:
    """Transaction rate more than 3σ above the rolling hourly mean."""
    per_hour: dict[int, int] = {}
    for t in transfers:
        bucket = int(t.timestamp.timestamp() // 3600)
        per_hour[bucket] = per_hour.get(bucket, 0) + 1
    counts = list(per_hour.values())
    if len(counts) < 4:
        return 0.0, []
    mean = statistics.mean(counts)
    stdev = statistics.stdev(counts)
    if stdev == 0:
        return 0.0, []
    peak = max(counts)
    if peak > mean + 3 * stdev:
        return 0.3, [f"velocity spike: peak {peak}/h vs mean {mean:.1f}/h (σ={stdev:.1f})"]
    return 0.0, []


def _detect_sybil(transfers: list[Transfer], addr: str) -> tuple[float, list[str]]:
    """Many small stablecoin deposits from a wide set of distinct senders."""
    inbound = [t for t in transfers if t.to_addr == addr]
    if not inbound:
        return 0.0, []
    small = [t for t in inbound if t.amount < 10 and t.token_symbol in {"USDT", "USDC", "DAI"}]
    senders = {t.from_addr for t in small}
    if len(senders) > 50 and len(small) / len(inbound) > 0.4:
        return 0.35, [
            f"sybil pattern: {len(senders)} distinct senders under $10 "
            f"({len(small)}/{len(inbound)} of inbound)"
        ]
    return 0.0, []


async def anomaly_check(
    address: str, chain: str = "ethereum", hours: int = 24
) -> AnomalyReport:
    address = address.lower()
    tset = await get_transfers(address, chain, hours)
    transfers = tset.transfers

    if not transfers:
        reason = tset.degraded_reason or "no activity in window"
        return AnomalyReport(
            address=address,
            chain=chain,
            verdict="unavailable" if tset.data_source == "unavailable" else "normal",
            score=0.0,
            signals=[],
            reasoning=(
                f"Analysis unavailable ({reason})."
                if tset.data_source == "unavailable"
                else f"No transfers observed in the last {hours}h ({reason})."
            ),
            data_source=tset.data_source,
            transfers_analyzed=0,
        )

    score = 0.0
    signals: list[str] = []
    for detector in (_detect_wash_trading, _detect_sybil):
        s, sig = detector(transfers, address)
        score += s
        signals.extend(sig)
    s, sig = _detect_velocity_spike(transfers)
    score += s
    signals.extend(sig)

    score = round(min(score, 1.0), 3)
    verdict = "normal" if score < 0.25 else ("suspicious" if score < 0.7 else "critical")

    return AnomalyReport(
        address=address,
        chain=chain,
        verdict=verdict,
        score=score,
        signals=signals[:20],  # bound response size
        reasoning=(
            f"Analyzed {len(transfers)} transfers over {hours}h. "
            + ("; ".join(signals[:3]) if signals else "No anomaly patterns matched.")
        ),
        data_source=tset.data_source,
        transfers_analyzed=len(transfers),
    )


# ── Wallet tracing ───────────────────────────────────────────────────────────


@dataclass
class WalletTrace:
    address: str
    chain: str
    casino_slug: str | None
    casino_name: str | None
    confidence: float
    balance_native: float
    associations: list[dict]
    data_source: str


async def wallet_trace(address: str, chain: str = "ethereum") -> WalletTrace:
    address = address.lower()

    # Fetch the subject's transfers ONCE, then match against known clusters —
    # the previous shape re-fetched inside a nested per-wallet loop.
    tset, (balance, balance_source) = await asyncio.gather(
        get_transfers(address, chain, 24 * 30),
        native_balance(address, chain),
    )

    counterparties: dict[str, int] = {}
    for t in tset.transfers:
        other = t.from_addr if t.to_addr == address else t.to_addr
        counterparties[other] = counterparties.get(other, 0) + 1

    associations: list[dict] = []
    for casino in CASINOS.values():
        hits = 0
        best_confidence = 0.0
        for wallet in casino.wallets:
            wa = wallet.address.lower()
            if wa in counterparties:
                hits += counterparties[wa]
                best_confidence = max(best_confidence, wallet.confidence)
        if hits:
            associations.append(
                {
                    "casino_slug": casino.slug,
                    "casino_name": casino.name,
                    "interactions_30d": hits,
                    "cluster_confidence": best_confidence,
                }
            )

    associations.sort(key=lambda a: (-a["interactions_30d"], a["casino_slug"]))
    top = associations[0] if associations else None

    return WalletTrace(
        address=address,
        chain=chain,
        casino_slug=top["casino_slug"] if top else None,
        casino_name=top["casino_name"] if top else None,
        confidence=top["cluster_confidence"] if top else 0.0,
        balance_native=round(balance, 6),
        associations=associations,
        data_source=_merge_source([tset.data_source, balance_source]),
    )
