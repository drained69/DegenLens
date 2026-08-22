"""Evidence-first domain primitives for gambling flow intelligence.

This module is deliberately independent of HTTP and upstream providers. It can
be reused by an indexed provider, an RPC adapter, or a future persisted sync
job without changing classification semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DataState(StrEnum):
    OBSERVED = "OBSERVED"
    CALCULATED = "CALCULATED"
    ESTIMATED = "ESTIMATED"
    INFERRED = "INFERRED"
    UNVERIFIED = "UNVERIFIED"


class TransferClassification(StrEnum):
    CUSTOMER_DEPOSIT = "CUSTOMER_DEPOSIT"
    CUSTOMER_WITHDRAWAL = "CUSTOMER_WITHDRAWAL"
    INTERNAL_TREASURY_TRANSFER = "INTERNAL_TREASURY_TRANSFER"
    OPERATIONAL_TRANSFER = "OPERATIONAL_TRANSFER"
    EXCHANGE_FLOW = "EXCHANGE_FLOW"
    CONTRACT_INTERACTION = "CONTRACT_INTERACTION"
    UNKNOWN = "UNKNOWN"
    OTHER = "OTHER"


@dataclass(frozen=True)
class SourceRecord:
    name: str
    kind: str
    reference: str
    discovered_at: str


@dataclass(frozen=True)
class Entity:
    id: str
    entity_type: str
    name: str
    aliases: tuple[str, ...] = ()
    confidence: float = 0.0
    sources: tuple[SourceRecord, ...] = ()


@dataclass(frozen=True)
class WalletEntity:
    address: str
    chain: str
    entity_id: str | None
    entity_type: str | None
    role: str
    confidence: float
    source: SourceRecord
    first_seen: str | None = None
    last_seen: str | None = None


@dataclass(frozen=True)
class NormalizedTransfer:
    tx_hash: str
    chain: str
    block_number: int | None
    timestamp: str
    token: str
    token_address: str | None
    raw_amount: float
    token_price: float | None
    usd_value: float | None
    from_address: str
    to_address: str
    source: str
    raw_source: dict[str, Any] = field(default_factory=dict)
    observed_at: str | None = None


@dataclass(frozen=True)
class ClassifiedTransfer:
    transfer: NormalizedTransfer
    classification: TransferClassification
    state: DataState
    confidence: float
    reasoning: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Evidence:
    claim: str
    classification: DataState
    sources: tuple[str, ...]
    transactions: tuple[str, ...]
    wallets: tuple[str, ...]
    methodology: str
    confidence: float
    coverage: float
    timestamp: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "classification": self.classification.value,
            "sources": list(self.sources),
            "transactions": list(self.transactions),
            "wallets": list(self.wallets),
            "methodology": self.methodology,
            "confidence": self.confidence,
            "coverage": self.coverage,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class FlowAggregate:
    observed_inflow_usd: float
    observed_outflow_usd: float
    attributed_customer_inflow_usd: float
    attributed_customer_outflow_usd: float
    internal_transfers_usd: float
    unknown_flow_usd: float
    transaction_count: int
    unique_depositors: int
    unique_withdrawers: int
    coverage: float
    confidence: float
    duplicate_count: int
    classifications: tuple[ClassifiedTransfer, ...]

    @property
    def net_observed_flow_usd(self) -> float:
        return self.observed_inflow_usd - self.observed_outflow_usd

    @property
    def net_customer_flow_usd(self) -> float:
        return self.attributed_customer_inflow_usd - self.attributed_customer_outflow_usd


def normalize_transfer(transfer: Any, price: float | None = None) -> NormalizedTransfer:
    """Convert the provider-neutral miner Transfer into the internal schema."""
    return NormalizedTransfer(
        tx_hash=transfer.tx_hash,
        chain=transfer.chain,
        block_number=getattr(transfer, "block_number", None),
        timestamp=transfer.timestamp.isoformat(),
        token=transfer.token_symbol,
        token_address=getattr(transfer, "token_address", None),
        raw_amount=transfer.amount,
        token_price=price,
        usd_value=transfer.amount * price if price and price > 0 else None,
        from_address=transfer.from_addr.lower(),
        to_address=transfer.to_addr.lower(),
        source=getattr(transfer, "source", "unknown"),
        raw_source=getattr(transfer, "raw_source", {}) or {},
        observed_at=None,
    )


def classify_transfer(
    transfer: NormalizedTransfer,
    casino_wallets: set[str],
    exchange_wallets: set[str] | None = None,
) -> ClassifiedTransfer:
    """Classify one transfer using only explicit registry relationships."""
    sender = transfer.from_address.lower()
    receiver = transfer.to_address.lower()
    exchange_wallets = exchange_wallets or set()
    if sender in casino_wallets and receiver in casino_wallets:
        classification = TransferClassification.INTERNAL_TREASURY_TRANSFER
        reasoning = ("Sender and receiver are in the same verified casino wallet cluster",)
        confidence = 0.98
    elif receiver in casino_wallets:
        classification = TransferClassification.CUSTOMER_DEPOSIT
        reasoning = ("Destination is a registered casino wallet", "Source is outside the casino cluster")
        confidence = 0.80
    elif sender in casino_wallets:
        classification = TransferClassification.CUSTOMER_WITHDRAWAL
        reasoning = ("Source is a registered casino wallet", "Destination is outside the casino cluster")
        confidence = 0.80
    elif sender in exchange_wallets or receiver in exchange_wallets:
        classification = TransferClassification.EXCHANGE_FLOW
        reasoning = ("One endpoint is a registered exchange wallet",)
        confidence = 0.75
    else:
        classification = TransferClassification.UNKNOWN
        reasoning = ("Transfer has no registered casino or exchange endpoint",)
        confidence = 0.25
    return ClassifiedTransfer(
        transfer=transfer,
        classification=classification,
        state=DataState.INFERRED,
        confidence=confidence,
        reasoning=reasoning,
        evidence=(transfer.tx_hash, transfer.from_address, transfer.to_address),
    )


def aggregate_flows(
    transfers: list[Any],
    prices: dict[str, float],
    casino_wallets: set[str],
    coverage: float,
    source: str,
    exchange_wallets: set[str] | None = None,
) -> FlowAggregate:
    """Deduplicate and calculate observed, attributed, internal, and unknown flow."""
    seen: set[tuple[str, str, str, str, float]] = set()
    classified: list[ClassifiedTransfer] = []
    duplicate_count = 0
    for item in transfers:
        key = (item.tx_hash, item.chain, item.from_addr.lower(), item.to_addr.lower(), item.amount)
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        normalized = normalize_transfer(item, prices.get(item.token_symbol, 0.0))
        classified.append(classify_transfer(normalized, casino_wallets, exchange_wallets))

    def value(row: ClassifiedTransfer) -> float:
        return row.transfer.usd_value or 0.0

    inbound = [r for r in classified if r.transfer.to_address in casino_wallets]
    outbound = [r for r in classified if r.transfer.from_address in casino_wallets]
    internal = [r for r in classified if r.classification == TransferClassification.INTERNAL_TREASURY_TRANSFER]
    unknown = [r for r in classified if r.classification == TransferClassification.UNKNOWN]
    deposits = [r for r in inbound if r.classification == TransferClassification.CUSTOMER_DEPOSIT]
    withdrawals = [r for r in outbound if r.classification == TransferClassification.CUSTOMER_WITHDRAWAL]
    return FlowAggregate(
        observed_inflow_usd=sum(value(r) for r in inbound),
        observed_outflow_usd=sum(value(r) for r in outbound),
        attributed_customer_inflow_usd=sum(value(r) for r in deposits),
        attributed_customer_outflow_usd=sum(value(r) for r in withdrawals),
        internal_transfers_usd=sum(value(r) for r in internal),
        unknown_flow_usd=sum(value(r) for r in unknown),
        transaction_count=len(classified),
        unique_depositors=len({r.transfer.from_address for r in deposits}),
        unique_withdrawers=len({r.transfer.to_address for r in withdrawals}),
        coverage=max(0.0, min(1.0, coverage)),
        confidence=min((r.confidence for r in classified), default=0.0) if source == "live" else 0.0,
        duplicate_count=duplicate_count,
        classifications=tuple(classified),
    )
