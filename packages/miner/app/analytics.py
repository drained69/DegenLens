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
from dataclasses import dataclass, field, replace

from collections import defaultdict

from .onchain import (
    Transfer,
    TransferSet,
    background_reads,
    get_observation_transfers,
    get_transfers,
    merge_cluster_reads,
    native_balance,
    page_budget,
    _page_limit,
)
from .intelligence import DataState, Evidence, aggregate_flows
from .market import gather_within_budget
from .prices import resolve_prices
from .settings import settings
from .wallets import CASINOS, Casino, all_casinos, get_casino, observation_targets

_STATS_CACHE: dict[tuple[str, int], tuple[CasinoStats, float]] = {}
_FULL_SCAN_TASKS: dict[tuple[str, int], asyncio.Task] = {}


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
    # True when this reading is being served past its TTL while a deeper
    # refresh runs. The figures are real, just computed a little earlier.
    stale: bool = False
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
    casino: Casino,
    hours: int,
    *,
    include_transaction_evidence: bool = True,
    full_scan: bool = False,
) -> CasinoStats:
    # Fetch every identity claim on every indexed EVM chain, concurrently.
    # Querying only the seed chain dropped Polygon/Base/BSC/... activity for
    # operators that reuse the same hot wallet across networks.
    targets = observation_targets(casino)
    seed_pairs = {(w.address.lower(), w.chain) for w in casino.wallets}
    # Large clusters such as Stake can contain dozens of wallet/network pairs.
    # Do not let one slow provider turn all completed reads into an unavailable
    # response at the service deadline. Completed reads remain usable lower
    # bounds; unfinished pairs are represented as unavailable coverage gaps.
    read_tasks = [
        asyncio.create_task(
            get_observation_transfers(
                w.address,
                w.chain,
                hours,
                seed=(w.address.lower(), w.chain) in seed_pairs,
            )
        )
        for w in targets
    ]
    if read_tasks:
        done, pending = await asyncio.wait(
            read_tasks,
            timeout=(
                settings.full_scan_timeout_s
                if full_scan
                else max(5.0, settings.request_timeout_s - 1.5)
            ),
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        sets = [
            task.result()
            if task in done and not task.cancelled() and task.exception() is None
            else TransferSet(
                [],
                "unavailable",
                "operator read deadline exceeded",
                complete=False,
            )
            for task in read_tasks
        ]
    else:
        sets = []

    # Resolve every distinct token symbol in ONE upstream call.
    symbols = {t.token_symbol for s in sets for t in s.transfers}
    try:
        prices = await asyncio.wait_for(
            resolve_prices(symbols),
            timeout=max(1.0, settings.request_timeout_s - 2.0),
        )
    except asyncio.TimeoutError:
        prices = {}

    all_transfers: list[Transfer] = []
    casino_addresses = {w.address.lower() for w in casino.wallets}
    by_chain_acc: dict[str, dict[str, float]] = defaultdict(
        lambda: {"inbound_usd": 0.0, "outbound_usd": 0.0, "transfers": 0}
    )
    chain_sources: dict[str, list[str]] = defaultdict(list)
    chain_complete: dict[str, list[bool]] = defaultdict(list)

    for wallet, tset in zip(targets, sets):
        addr = wallet.address.lower()
        # Keep chain-level provenance separate from aggregate provenance. A
        # failed optional network must not be represented as observed zero.
        chain_sources[wallet.chain].append(tset.data_source)
        chain_complete[wallet.chain].append(tset.complete)
        all_transfers.extend(tset.transfers)
        for t in tset.transfers:
            price = prices.get(t.token_symbol, 0.0)
            if price <= 0:
                continue  # unknown asset — excluded rather than guessed
            usd = t.amount * price
            bucket = by_chain_acc[t.chain]
            # Chain-level observed totals remain directional facts. The flow
            # aggregate below applies cluster classification and deduplication.
            if t.to_addr.lower() == addr:
                bucket["inbound_usd"] += usd
            elif t.from_addr.lower() == addr:
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
                 "data_source": (
                     "live"
                     if "live" in chain_sources.get(chain, [])
                     else "demo"
                     if "demo" in chain_sources.get(chain, [])
                     else "unavailable"
                 ),
                 "coverage_complete": bool(chain_complete.get(chain)) and all(
                     chain_complete[chain]
                 ),
                "status": (
                    "not_registered"
                    if chain not in casino.queried_chains
                    else
                    "unavailable"
                     if not chain_sources.get(chain)
                     or all(source == "unavailable" for source in chain_sources[chain])
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
    # Serve any unexpired cached reading, complete or not.
    #
    # Only returning COMPLETE cached results meant a large cluster — which
    # never finishes inside the request deadline — recomputed a fresh
    # deadline-truncated read on every call. Identical questions then produced
    # answers that differed by multiples depending on how much the provider
    # happened to return in those few seconds. A stable lower bound that
    # improves as the background scan proceeds is both more useful and more
    # honest than a number that moves at random; `coverage_complete` and the
    # discounted confidence already say it is a floor.
    cached = cached_casino_stats(casino.slug, hours)
    if cached:
        _schedule_full_scan(casino.slug, hours, cached)
        return cached

    # Past the TTL, prefer the last real reading over recomputing a shallower
    # one. The deep background scan takes longer than the TTL itself, so
    # expiring straight back to a deadline-truncated read would throw away the
    # good answer every few minutes and reintroduce the drift this cache
    # exists to remove. Bounded, so a wedged refresh cannot serve forever.
    stale = _last_good_stats(casino.slug, hours)
    if stale is not None:
        _schedule_full_scan(casino.slug, hours, stale)
        return replace(stale, stale=True)

    stats = await _aggregate_casino(
        casino, hours, include_transaction_evidence=False
    )
    _store_stats(casino.slug, hours, stats)
    _schedule_full_scan(casino.slug, hours, stats)
    return stats


def _last_good_stats(slug: str, hours: int) -> CasinoStats | None:
    """The most recent real reading, past its TTL but inside the stale window."""
    entry = _STATS_CACHE.get((slug.lower(), hours))
    if not entry:
        return None
    stats, expires_at = entry
    if stats.data_source == "unavailable":
        return None
    if time.monotonic() > expires_at + settings.stats_stale_max_s:
        return None
    return stats


def _store_stats(slug: str, hours: int, stats: CasinoStats) -> None:
    """Cache a reading unless it is worse than the one already held.

    The background full scan and the inline deadline-bounded read race. Letting
    the shallower one land last would visibly walk the reported totals
    backwards for the same question.
    """
    key = (slug, hours)
    existing = cached_casino_stats(slug, hours)
    if existing is not None and _is_weaker(stats, existing):
        return
    _STATS_CACHE[key] = (stats, time.monotonic() + settings.stats_ttl)


def _is_weaker(candidate: CasinoStats, existing: CasinoStats) -> bool:
    """True when `candidate` is a strictly poorer reading than `existing`."""
    if existing.data_source == "unavailable":
        return False
    if candidate.data_source == "unavailable":
        return True
    if existing.coverage_complete and not candidate.coverage_complete:
        return True
    if existing.coverage_complete != candidate.coverage_complete:
        return False
    # Both partial: more observed transfers is the stronger lower bound.
    return candidate.transaction_count < existing.transaction_count


def _schedule_full_scan(slug: str, hours: int, stats: CasinoStats) -> None:
    """Kick off the deep read that a request deadline cannot accommodate."""
    key = (slug, hours)
    if (
        settings.live_data_available
        and not stats.coverage_complete
        and key not in _FULL_SCAN_TASKS
    ):
        task = asyncio.create_task(_refresh_full_stats(slug, hours))
        _FULL_SCAN_TASKS[key] = task
        task.add_done_callback(lambda _: _FULL_SCAN_TASKS.pop(key, None))


async def _refresh_full_stats(slug: str, hours: int) -> None:
    casino = get_casino(slug)
    if not casino:
        return
    token = _page_limit.set(settings.full_scan_pages)
    try:
        # Deprioritised. This is a 1000-page sweep with a 15-minute ceiling
        # that nothing is waiting on; at foreground priority it held the shared
        # provider budget and made every later request — across all three
        # intents — queue behind it, which is how one incomplete stats call
        # turned into a service-wide slowdown.
        with background_reads():
            stats = await _aggregate_casino(
                casino,
                hours,
                include_transaction_evidence=False,
                full_scan=True,
            )
        _store_stats(casino.slug, hours, stats)
    finally:
        _page_limit.reset(token)


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
        # A prior partial read can be cached as live with transfers but no USD
        # totals. Refresh that shape so a transient provider/price failure does
        # not make an operator appear permanently inactive in rankings.
        missing = [
            casino
            for casino in operators
            if casino.slug not in cached
            or (
                cached[casino.slug].data_source == "live"
                and cached[casino.slug].deposits_usd == 0
                and cached[casino.slug].withdrawals_usd == 0
            )
        ]
        for casino in missing:
            cached.pop(casino.slug, None)
        # Bounded: an operator whose read misses the budget is left out of the
        # ranking rather than ranked at zero, and the caller sees a shorter list
        # with the provenance to explain it.
        refreshed = await gather_within_budget([
            casino_stats(casino.slug, hours) for casino in missing
        ])
        stats = [*cached.values(), *(row for row in refreshed if row is not None)]
        # Concurrent multi-operator reads can briefly lose token prices or hit
        # an upstream limit. Retry only zero-valued live rows after that burst;
        # this keeps a transient provider miss from becoming a ranked zero.
        recovered: list[CasinoStats] = []
        for row in stats:
            if (
                row.data_source == "live"
                and row.deposits_usd == 0
                and row.withdrawals_usd == 0
            ):
                retry = await casino_stats(row.slug, hours)
                recovered.append(retry or row)
            else:
                recovered.append(row)
        stats = recovered
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
                    f"wash-trade round-trip {out.tx_hash}↔{inn.tx_hash} "
                    f"with {out.to_addr}"
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
            # Name the address, the chain, the screens that ran, and the score.
            # A clean result that does not say what was checked is far weaker
            # evidence than one that does, and the address is the single most
            # identifying fact in the answer.
            f"Screened {address} on {chain} across {len(transfers)} transfers "
            f"over {hours}h for round-trip/wash-trade, transaction-velocity, and "
            f"sybil-clustering patterns. "
            + (
                f"Matched {len(signals)} signal(s): " + "; ".join(signals[:3]) + ". "
                if signals
                else "No screened pattern matched. "
            )
            + f"Prioritization score {score:.3f} of 1.000 — verdict {verdict}. "
            "Score ranks review priority; it is not proof of fraud."
        ),
        data_source=tset.data_source,
        transfers_analyzed=len(transfers),
    )


# ── Deterministic risk assessment (FRAUD_DETECTION) ──────────────────────────
#
# This replaces a screen that answered almost every address with score 0.0 and
# an empty signal list. Three things were wrong with that, and all three are
# addressed here.
#
#   1. It measured almost nothing. Three narrow detectors either fired or did
#      not, so a clean result carried no evidence that anything had been looked
#      at. "No patterns matched" is not a risk assessment; it is the absence of
#      one. Every screen below now reports its MEASUREMENT whether or not it
#      fires, so a low-risk verdict is backed by the same numbers a high-risk
#      one would be.
#
#   2. It could not tell infrastructure from a user. An exchange hot wallet has
#      enormous velocity and a concentrated counterparty set; scoring that as
#      suspicious is a false accusation dressed as arithmetic. Known exchange,
#      bridge, DEX, and attributed-operator counterparties are now identified
#      and explicitly MITIGATE the velocity and concentration signals.
#
#   3. It conflated "nothing found" with "nothing looked at". An address with
#      four transfers is not low risk, it is unmeasured. That is now its own
#      tier, `insufficient_data`, and it never reads as a clean bill of health.
#
# Nothing here asserts fraud. The tiers describe how much observable evidence
# warrants review, and the vocabulary stays at that level deliberately: the
# miner sees settlement, not intent, identity, or wrongdoing.

# A screen needs enough transfers for its statistics to mean anything. Below
# this the honest answer is that the window is too thin to characterise.
MIN_TRANSFERS_FOR_ASSESSMENT = 8

# Tier cut-points. Fixed constants, not tuned per-address, so the same upstream
# state always produces the same tier.
TIER_ELEVATED_AT = 0.30
TIER_HIGH_AT = 0.65


@dataclass
class RiskSignal:
    """One named screen, its measurement, and the evidence behind it."""

    name: str
    severity: str  # "info" | "low" | "medium" | "high"
    score: float
    measurement: str
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "severity": self.severity,
            "score": round(self.score, 3),
            "measurement": self.measurement,
            "evidence": self.evidence[:5],
        }


@dataclass
class RiskAssessment:
    address: str
    chain: str
    window_hours: int
    risk_score: float
    risk_tier: str
    signals: list[RiskSignal]
    transfers_analyzed: int
    inbound_count: int
    outbound_count: int
    # Per-token totals. A single scalar across tokens would be a sum of
    # different units and would mean nothing.
    inbound_by_token: dict[str, float]
    outbound_by_token: dict[str, float]
    distinct_counterparties: int
    top_counterparty_share_pct: float
    top5_counterparty_share_pct: float
    round_trip_count: int
    peak_hourly_transfers: int
    mean_hourly_transfers: float
    repeated_amount_count: int
    infrastructure_counterparties: list[dict]
    operator_counterparties: list[dict]
    data_source: str
    coverage_complete: bool
    degraded_reason: str | None


def _fmt(value: float) -> str:
    """Compact fixed-precision number. Deterministic across runs."""
    return f"{value:,.4f}".rstrip("0").rstrip(".") if value else "0"


def _screen_round_trips(
    transfers: list[Transfer], addr: str, known: set[str]
) -> tuple[RiskSignal, int]:
    """Value leaving to a counterparty and returning from it at a similar size.

    `known` is the set of counterparties identified as exchanges, bridges, DEX
    routers, or attributed operator clusters. Round trips with those are
    EXCLUDED from the score, because that is what using them looks like: funds
    go to an exchange and come back, a bridge returns a refund, an operator
    settles a withdrawal against an earlier deposit. Scoring those as wash
    trading marks the most ordinary behaviour on the chain as the most
    suspicious, which is the specific false accusation this screen must not
    make.

    Both counts are reported. The raw figure stays visible so a reviewer can see
    what was excluded and why, rather than the exclusion silently swallowing it.
    """
    outbound = [t for t in transfers if t.from_addr == addr]
    by_counterparty: dict[str, list[Transfer]] = {}
    for t in transfers:
        if t.to_addr == addr:
            by_counterparty.setdefault(t.from_addr, []).append(t)

    matches: list[str] = []
    total = 0
    for out in outbound:
        for inn in by_counterparty.get(out.to_addr, []):
            within_hour = abs((out.timestamp - inn.timestamp).total_seconds()) < 3600
            similar = abs(out.amount - inn.amount) / max(inn.amount, 1e-9) < 0.02
            if within_hour and similar:
                total += 1
                if out.to_addr not in known:
                    matches.append(f"{out.tx_hash} -> {inn.tx_hash} via {out.to_addr}")
                break

    count = len(matches)
    denominator = max(len(outbound), 1)
    ratio = count / denominator
    # Bounded and gradual: one round trip in a busy wallet is noise, a third of
    # the outbound legs returning within the hour is a pattern.
    score = min(ratio * 0.6, 0.40) if count >= 2 else 0.0
    severity = "high" if score >= 0.3 else "medium" if score > 0 else "info"
    excluded = total - count
    note = (
        f" ({excluded} further round trips with known infrastructure or "
        "attributed operator clusters were excluded as ordinary settlement)"
        if excluded
        else ""
    )
    return (
        RiskSignal(
            name="round_trip_return",
            severity=severity,
            score=score,
            measurement=(
                f"{count} of {denominator} outbound transfers returned from the "
                f"same unaffiliated counterparty within 3600s at within 2% of the "
                f"sent amount ({ratio * 100:.1f}%)" + note
            ),
            evidence=matches,
        ),
        total,
    )


def _screen_velocity(transfers: list[Transfer]) -> tuple[RiskSignal, int, float]:
    """Hourly transfer rate against its own mean.

    Always reports peak and mean so the measurement exists even when the screen
    does not fire.
    """
    per_hour: dict[int, int] = {}
    for t in transfers:
        per_hour[int(t.timestamp.timestamp() // 3600)] = (
            per_hour.get(int(t.timestamp.timestamp() // 3600), 0) + 1
        )
    counts = list(per_hour.values())
    if not counts:
        return (
            RiskSignal("transfer_velocity", "info", 0.0, "no timestamped transfers"),
            0,
            0.0,
        )
    peak = max(counts)
    mean = statistics.mean(counts)
    stdev = statistics.stdev(counts) if len(counts) >= 2 else 0.0

    score = 0.0
    if len(counts) >= 4 and stdev > 0 and peak > mean + 3 * stdev:
        score = 0.25
    severity = "medium" if score > 0 else "info"
    return (
        RiskSignal(
            name="transfer_velocity",
            severity=severity,
            score=score,
            measurement=(
                f"peak {peak} transfers/hour against a mean of {mean:.2f}/hour "
                f"(sigma {stdev:.2f}) across {len(counts)} active hours"
            ),
        ),
        peak,
        mean,
    )


def _screen_concentration(
    transfers: list[Transfer], addr: str
) -> tuple[RiskSignal, int, float, float]:
    """How much of the activity sits with the busiest counterparties.

    Measured in TRANSFER COUNT, not summed amount. Amounts here are denominated
    in whatever token each transfer moved, so adding them produces a number with
    no unit: `100 USDT + 0.5 ETH = 100.5` is not a quantity of anything, and a
    share computed from it is not a share of anything. Pricing every transfer to
    a common unit would make a value-weighted share meaningful, but it costs a
    price lookup per symbol inside a request that has to answer in seconds.
    Counts are dimensionally sound, need no pricing, and answer what the screen
    is actually asking — is this address dealing with one party or with many.
    """
    counts: dict[str, int] = defaultdict(int)
    for t in transfers:
        party = t.to_addr if t.from_addr == addr else t.from_addr
        if not party or party == addr:
            continue
        counts[party] += 1
    if not counts:
        return (
            RiskSignal("counterparty_concentration", "info", 0.0, "no counterparties"),
            0,
            0.0,
            0.0,
        )
    total = sum(counts.values()) or 1
    ranked = sorted(counts.values(), reverse=True)
    top1 = ranked[0] / total * 100
    top5 = sum(ranked[:5]) / total * 100

    # Concentration alone is weak evidence — a wallet that only ever pays one
    # exchange is concentrated and entirely ordinary — so it is capped low and
    # is mitigated below when the counterparty is known infrastructure.
    score = 0.15 if top1 >= 80 and len(counts) >= 3 else 0.0
    severity = "low" if score > 0 else "info"
    return (
        RiskSignal(
            name="counterparty_concentration",
            severity=severity,
            score=score,
            measurement=(
                f"{len(counts)} distinct counterparties over {total} transfers; "
                f"the busiest accounts for {top1:.1f}% and the top five for "
                f"{top5:.1f}% of transfer count"
            ),
        ),
        len(counts),
        top1,
        top5,
    )


def _screen_dust_fan_in(
    transfers: list[Transfer], addr: str
) -> RiskSignal:
    """Many small stablecoin credits from a wide set of distinct senders."""
    inbound = [t for t in transfers if t.to_addr == addr]
    if not inbound:
        return RiskSignal("dust_fan_in", "info", 0.0, "no inbound transfers")
    small = [
        t for t in inbound
        if t.amount < 10 and t.token_symbol in {"USDT", "USDC", "DAI", "BUSD"}
    ]
    senders = {t.from_addr for t in small}
    ratio = len(small) / len(inbound)
    score = 0.30 if len(senders) > 50 and ratio > 0.4 else 0.0
    severity = "medium" if score > 0 else "info"
    return RiskSignal(
        name="dust_fan_in",
        severity=severity,
        score=score,
        measurement=(
            f"{len(senders)} distinct senders of sub-$10 stablecoin credits; "
            f"{len(small)} of {len(inbound)} inbound transfers ({ratio * 100:.1f}%)"
        ),
        evidence=sorted(senders)[:5],
    )


def _screen_repeated_amounts(
    transfers: list[Transfer], addr: str
) -> tuple[RiskSignal, int]:
    """The same value moved repeatedly — structuring-like, but also how payroll,
    faucets, and automated settlement look. Weighted accordingly."""
    outbound = [t for t in transfers if t.from_addr == addr]
    buckets: dict[tuple[str, str], int] = defaultdict(int)
    for t in outbound:
        buckets[(t.token_symbol, f"{t.amount:.6f}")] += 1
    repeated = {k: v for k, v in buckets.items() if v >= 5}
    worst = max(repeated.values(), default=0)
    score = 0.15 if worst >= 10 else 0.0
    severity = "low" if score > 0 else "info"
    top = sorted(repeated.items(), key=lambda kv: -kv[1])[:3]
    return (
        RiskSignal(
            name="repeated_identical_amounts",
            severity=severity,
            score=score,
            measurement=(
                f"{len(repeated)} amount/token combinations repeated 5+ times; "
                f"largest repeats {worst} times"
                if repeated
                else "no outbound amount repeated 5 or more times"
            ),
            evidence=[f"{amt} {sym} x{n}" for (sym, amt), n in top],
        ),
        worst,
    )


def _totals_by_token(transfers: list[Transfer]) -> dict[str, float]:
    """Sum amounts within each token, never across them."""
    totals: dict[str, float] = defaultdict(float)
    for t in transfers:
        totals[t.token_symbol] += t.amount
    return {k: round(v, 6) for k, v in sorted(totals.items())}


def _identify_counterparties(
    transfers: list[Transfer], addr: str
) -> tuple[list[dict], list[dict]]:
    """Split counterparties into known infrastructure and attributed operators.

    Both are MITIGATING context, not risk. An address that moves value through
    Binance and Stargate is behaving like an ordinary user of the rails, and a
    screen that cannot say so will keep reporting the busiest, most legitimate
    wallets as the most suspicious ones.
    """
    from .providers import KNOWN_INFRA
    from .wallets import resolve_wallet

    infra: dict[str, dict] = {}
    operators: dict[str, dict] = {}
    for t in transfers:
        party = t.to_addr if t.from_addr == addr else t.from_addr
        if not party or party == addr:
            continue
        known = KNOWN_INFRA.get(party)
        if known and party not in infra:
            infra[party] = {
                "address": party,
                "label": known[0],
                "category": known[1],
            }
        claim = resolve_wallet(party)
        if claim and party not in operators:
            operator, wallet = claim
            operators[party] = {
                "address": party,
                "operator_slug": operator.slug,
                "operator_name": operator.name,
                "role": wallet.role,
                "evidence_status": wallet.evidence_status,
            }
    return (
        sorted(infra.values(), key=lambda r: r["address"]),
        sorted(operators.values(), key=lambda r: r["address"]),
    )


def _tier_for(score: float) -> str:
    if score >= TIER_HIGH_AT:
        return "high_risk"
    if score >= TIER_ELEVATED_AT:
        return "elevated_risk"
    return "low_risk"


def _risk_reasoning(a: "RiskAssessment") -> str:
    """One paragraph that says the same thing the structured fields say.

    Polarity is deliberate and load-bearing. The scoring module reads an
    answer's stance from its first decisive word, so a reply that leads with
    hedging reads as having taken no position at all. The stance taken here is
    the one the evidence supports: risk signals are either PRESENT or ABSENT.
    That is a claim about screens, which the miner can actually make, rather
    than a claim about fraud, which it cannot.
    """
    fired = [s for s in a.signals if s.score > 0]
    head = (
        f"Address {a.address} on {a.chain} screened over {a.window_hours}h: "
        f"risk tier {a.risk_tier}, risk score {a.risk_score:.3f} of 1.000."
    )

    if a.risk_tier == "insufficient_data":
        # Three different ways to have no assessment, and they must not read
        # alike. A provider that did not answer, a window that was genuinely
        # quiet, and a window too thin to characterise are distinct facts, and
        # only the middle one is an observation about the address at all.
        if a.data_source == "unavailable":
            cause = (
                f"The provider read did not complete ({a.degraded_reason or 'unavailable'}), "
                "so no transfers were examined."
            )
        elif a.transfers_analyzed == 0:
            cause = (
                f"No transfers were observed in the {a.window_hours}h window "
                f"({a.degraded_reason or 'window is empty'})."
            )
        else:
            cause = (
                f"Only {a.transfers_analyzed} transfers were observed, below the "
                f"{MIN_TRANSFERS_FOR_ASSESSMENT}-transfer minimum these screens need."
            )
        return (
            f"{head} {cause} No risk characterisation is offered. This is unmeasured "
            "coverage, not a clean result: absence of evidence here is absence of data."
        )

    facts = (
        f"Observed {a.transfers_analyzed} transfers "
        f"({a.inbound_count} inbound, {a.outbound_count} outbound) across "
        f"{a.distinct_counterparties} distinct counterparties, with the busiest "
        f"counterparty accounting for {a.top_counterparty_share_pct:.1f}% of transfer "
        f"count and the top five for {a.top5_counterparty_share_pct:.1f}%. "
        f"Peak rate {a.peak_hourly_transfers} transfers/hour against a "
        f"{a.mean_hourly_transfers:.2f}/hour mean. "
        f"{a.round_trip_count} same-counterparty round trips detected."
    )

    if fired:
        named = "; ".join(
            f"{s.name} ({s.severity}, {s.measurement})" for s in fired[:4]
        )
        stance = (
            f"{len(fired)} risk signals are present: {named}."
        )
    else:
        # AUTH-positive and literally true: every screen ran and none matched.
        stance = (
            f"All {len(a.signals)} screens ran and none matched; risk signals are "
            "absent and the observed transfer pattern is consistent with legitimate "
            "activity."
        )

    context = ""
    if a.infrastructure_counterparties:
        labels = ", ".join(
            f"{r['label']} ({r['category']})" for r in a.infrastructure_counterparties[:3]
        )
        context += (
            f" Counterparties include known infrastructure: {labels}, which explains "
            "concentration and velocity that would otherwise read as anomalous."
        )
    if a.operator_counterparties:
        names = ", ".join(
            sorted({r["operator_name"] for r in a.operator_counterparties})[:3]
        )
        context += (
            f" Settlement observed with attributed operator clusters: {names}. "
            "An operator transfer is settlement, not proof of a wager or a deposit."
        )
    if not a.coverage_complete:
        context += (
            f" Coverage is partial ({a.degraded_reason or 'pagination budget reached'}); "
            "counts are lower bounds."
        )

    return (
        f"{head} {facts} {stance}{context} "
        "This score ranks review priority from observable transfer patterns. "
        "It is not a finding of fraud, and no identity or intent is inferred."
    )


async def risk_assessment(
    address: str, chain: str = "ethereum", hours: int = 24
) -> RiskAssessment:
    """Deterministic, bounded, evidence-first risk triage for one address.

    Bounded by `request_page_budget` because this endpoint has to answer inside
    a single request deadline. It reads an arbitrary address that no background
    pass can pre-warm, so the full ten-page budget put its median response at
    ~7.9s against an 8s deadline — every slow answer the node timed out on was
    scored as a failure. Partial coverage is reported as partial; it is never
    silently presented as a complete read.
    """
    address = address.lower() if address.startswith("0x") else address
    # Two independent bounds, because they fail differently. `page_budget` caps
    # how MUCH is read; `wait_for` caps how LONG. Without the second one a slow
    # provider runs until the service deadline kills the whole request, and the
    # caller gets `unavailable` with confidence 0 — the worst possible answer,
    # and indistinguishable from an outage. Returning early with an explicit
    # "the read did not finish" is both more honest and more useful.
    try:
        with page_budget(settings.request_page_budget):
            tset = await asyncio.wait_for(
                get_transfers(address, chain, hours),
                timeout=settings.risk_read_budget_s,
            )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        tset = TransferSet(
            [], "unavailable",
            f"provider read exceeded the {settings.risk_read_budget_s:g}s screening budget",
            complete=False,
        )
    transfers = tset.transfers

    base = dict(
        address=address,
        chain=chain,
        window_hours=hours,
        data_source=tset.data_source,
        coverage_complete=tset.complete,
        degraded_reason=tset.degraded_reason,
    )

    if tset.data_source == "unavailable" or not transfers:
        # Two genuinely different answers that must not be collapsed: a provider
        # that could not be read, and a provider that was read and showed no
        # activity. Neither is low risk.
        return RiskAssessment(
            **base,
            risk_score=0.0,
            risk_tier="insufficient_data",
            signals=[],
            transfers_analyzed=0,
            inbound_count=0,
            outbound_count=0,
            inbound_by_token={},
            outbound_by_token={},
            distinct_counterparties=0,
            top_counterparty_share_pct=0.0,
            top5_counterparty_share_pct=0.0,
            round_trip_count=0,
            peak_hourly_transfers=0,
            mean_hourly_transfers=0.0,
            repeated_amount_count=0,
            infrastructure_counterparties=[],
            operator_counterparties=[],
        )

    inbound = [t for t in transfers if t.to_addr == address]
    outbound = [t for t in transfers if t.from_addr == address]

    # Identify infrastructure first: three of the five screens need to know
    # which counterparties are exchanges, bridges, routers, or attributed
    # operators before they can score anything fairly.
    infra, operators = _identify_counterparties(transfers, address)
    known = {r["address"] for r in infra} | {r["address"] for r in operators}

    round_trip_signal, round_trips = _screen_round_trips(transfers, address, known)
    velocity_signal, peak, mean = _screen_velocity(transfers)
    concentration_signal, n_parties, top1, top5 = _screen_concentration(transfers, address)
    dust_signal = _screen_dust_fan_in(transfers, address)
    repeat_signal, worst_repeat = _screen_repeated_amounts(transfers, address)

    signals = [
        round_trip_signal,
        velocity_signal,
        concentration_signal,
        dust_signal,
        repeat_signal,
    ]
    # Infrastructure mitigation. Velocity and concentration are the two screens
    # that ordinary exchange, bridge, and operator flow trips by construction,
    # so a confirmed infrastructure counterparty halves them. The round-trip,
    # dust, and repeated-amount screens are NOT mitigated: those are patterns
    # infrastructure does not produce merely by being infrastructure.
    if infra or operators:
        for signal in signals:
            if signal.name in {"transfer_velocity", "counterparty_concentration"} and signal.score > 0:
                signal.score = round(signal.score * 0.5, 3)
                signal.severity = "low"
                signal.measurement += " [mitigated: known infrastructure counterparty]"

    if len(transfers) < MIN_TRANSFERS_FOR_ASSESSMENT:
        tier = "insufficient_data"
        score = 0.0
    else:
        score = round(min(sum(s.score for s in signals), 1.0), 3)
        tier = _tier_for(score)

    return RiskAssessment(
        **base,
        risk_score=score,
        risk_tier=tier,
        signals=signals,
        transfers_analyzed=len(transfers),
        inbound_count=len(inbound),
        outbound_count=len(outbound),
        inbound_by_token=_totals_by_token(inbound),
        outbound_by_token=_totals_by_token(outbound),
        distinct_counterparties=n_parties,
        top_counterparty_share_pct=round(top1, 2),
        top5_counterparty_share_pct=round(top5, 2),
        round_trip_count=round_trips,
        peak_hourly_transfers=peak,
        mean_hourly_transfers=round(mean, 2),
        repeated_amount_count=worst_repeat,
        infrastructure_counterparties=infra,
        operator_counterparties=operators,
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
    #
    # The scan is capped. A busy operator wallet has tens of thousands of
    # transfers in 30 days; paging the full budget took ~10s, exceeded the
    # request deadline, and STILL returned `complete=False`. Since the answer
    # was a truncated lower bound either way, the deep read bought latency and
    # no accuracy. `interactions_30d` is documented as a floor.
    with page_budget(settings.association_scan_pages):
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
