"""Aggregation and anomaly detection over on-chain transfer data.

This is the layer that turns raw transfers into the intelligence the network
actually pays for. Every public function returns provenance (`data_source`)
alongside its numbers so the API can never present synthetic or stale figures
as observed chain state.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field

from .onchain import Transfer, TransferSet, get_transfers, native_balance
from .prices import resolve_prices
from .wallets import CASINOS, Casino, all_casinos, get_casino


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
    # False when a lookback window could not be fully paged upstream. Totals are
    # then lower bounds on observed flow, not complete measurements.
    coverage_complete: bool = True

    @property
    def observed_inbound_usd(self) -> float:
        return self.deposits_usd

    @property
    def observed_outbound_usd(self) -> float:
        return self.withdrawals_usd


async def _aggregate_casino(casino: Casino, hours: int) -> CasinoStats:
    # Fetch every wallet's transfers concurrently rather than serially.
    sets: list[TransferSet] = await asyncio.gather(
        *(get_transfers(w.address, w.chain, hours) for w in casino.wallets)
    )

    # Resolve every distinct token symbol in ONE upstream call.
    symbols = {t.token_symbol for s in sets for t in s.transfers}
    prices = await resolve_prices(symbols)

    deposits_usd = 0.0
    withdrawals_usd = 0.0
    unique_depositors: set[str] = set()
    tx_count = 0

    for wallet, tset in zip(casino.wallets, sets):
        addr = wallet.address.lower()
        tx_count += len(tset.transfers)
        for t in tset.transfers:
            price = prices.get(t.token_symbol, 0.0)
            if price <= 0:
                continue  # unknown asset — excluded rather than guessed
            usd = t.amount * price
            if t.to_addr == addr:
                deposits_usd += usd
                unique_depositors.add(t.from_addr)
            elif t.from_addr == addr:
                withdrawals_usd += usd

    confidence = (
        sum(w.confidence for w in casino.wallets) / len(casino.wallets)
        if casino.wallets
        else 0.0
    )
    source = _merge_source([s.data_source for s in sets])
    coverage_complete = all(s.complete for s in sets)
    # Provenance discounts confidence — synthetic data is never high-confidence.
    if source == "demo":
        confidence *= 0.5
    # A truncated window yields a lower bound, not a measurement. Say so in the
    # confidence rather than presenting a partial total at full confidence.
    if not coverage_complete:
        confidence *= 0.6
    elif source == "unavailable":
        confidence = 0.0

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
        chains=sorted({w.chain for w in casino.wallets}),
        coverage_complete=coverage_complete,
    )


async def casino_stats(slug: str, hours: int = 24) -> CasinoStats | None:
    casino = get_casino(slug)
    if not casino:
        return None
    return await _aggregate_casino(casino, hours)


async def rank_casinos(hours: int = 168) -> tuple[list[dict], str]:
    """Ranked casinos plus merged provenance."""
    stats = await asyncio.gather(*(_aggregate_casino(c, hours) for c in all_casinos()))
    # Deterministic ordering: volume desc, then slug asc to break ties stably.
    ordered = sorted(stats, key=lambda s: (-s.deposits_usd, s.slug))
    total = sum(s.deposits_usd for s in ordered) or 1.0
    rows = [
        {
            "rank": i + 1,
            "slug": s.slug,
            "name": s.name,
            "deposits_usd": s.deposits_usd,
            "withdrawals_usd": s.withdrawals_usd,
            "net_flow_usd": s.net_flow_usd,
            "tracked_flow_share_pct": round(s.deposits_usd / total * 100, 2),
            "market_share_pct": round(s.deposits_usd / total * 100, 2),
            "unique_depositors": s.unique_depositors,
            "transaction_count": s.transaction_count,
            "confidence": s.confidence,
        }
        for i, s in enumerate(ordered)
    ]
    return rows, _merge_source([s.data_source for s in ordered])


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
