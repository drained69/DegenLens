"""Wallet attribution discovery.

The binding constraint on this miner is coverage: 6 clusters on one chain
against a market of dozens of operators running many wallets each. The gap is
not capability — every analytic already works — it is *labels*.

Labels cannot be invented. A wrong label silently corrupts every figure derived
from it, and registration is immutable. So this module does not assign labels.
It proposes *candidates* with the on-chain evidence behind each one, ranked by
strength, for a human to confirm or reject.

The heuristics come from how operators actually run treasuries:

  1. SIBLING SWEEPS — hot wallets sweep to cold storage and refill from it. An
     address exchanging significant value bidirectionally with a known cluster,
     while showing hub behaviour of its own, is likely another wallet of the
     same operator.
  2. SHARED COUNTERPARTIES — two wallets serving the same operator see the same
     users. High Jaccard overlap on counterparty sets is hard to produce by
     coincidence at scale.
  3. HUB SHAPE — operator wallets face many distinct addresses with modest
     average transfer sizes, which distinguishes them from whales and OTC desks.

None of these is proof. Each is reported with the numbers that produced it so a
reviewer can check the claim rather than trust the score.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

from .market import cached_aggregate, gather_within_budget
from .onchain import get_observation_transfers, get_transfers
from .prices import resolve_prices
from .wallets import CONFIDENCE_CEILING, Casino, attributed_operators, get_casino, observation_targets

# A candidate must clear all of these before it is worth a reviewer's time.
MIN_SHARED_VALUE_USD = 25_000
MIN_INTERACTIONS = 5
MIN_HUB_COUNTERPARTIES = 25

# Hard ceiling on how many candidates get individually profiled per call.
# Latency is scored: an answer that times out is worth nothing.
MAX_PROFILE_FANOUT = 12


@dataclass
class Candidate:
    address: str
    chain: str
    proposed_operator: str
    proposed_operator_name: str

    # Evidence
    value_with_cluster_usd: float = 0.0
    interactions_with_cluster: int = 0
    bidirectional_with_cluster: bool = False
    own_counterparties: int = 0
    own_transfers: int = 0
    shared_counterparty_overlap: float = 0.0
    avg_transfer_usd: float = 0.0

    signals: list[str] = field(default_factory=list)
    strength: float = 0.0
    recommended_status: str = "unverified_seed"

    def as_dict(self) -> dict:
        return {
            "address": self.address,
            "chain": self.chain,
            "proposed_operator": self.proposed_operator,
            "proposed_operator_name": self.proposed_operator_name,
            "evidence": {
                "value_with_cluster_usd": round(self.value_with_cluster_usd, 2),
                "interactions_with_cluster": self.interactions_with_cluster,
                "bidirectional_with_cluster": self.bidirectional_with_cluster,
                "own_counterparties": self.own_counterparties,
                "own_transfers": self.own_transfers,
                "shared_counterparty_overlap_pct": round(
                    self.shared_counterparty_overlap * 100, 2
                ),
                "avg_transfer_usd": round(self.avg_transfer_usd, 2),
            },
            "signals": self.signals,
            "strength": round(self.strength, 3),
            "recommended_status": self.recommended_status,
            "review_required": True,
            "note": (
                "Candidate only. On-chain behaviour is consistent with an operator "
                "wallet but does not prove ownership. Confirm against operator "
                "disclosure or a block-explorer label before adding to the registry."
            ),
        }


def _score(c: Candidate) -> tuple[float, list[str]]:
    """Combine evidence into a reviewer-priority score.

    Deliberately conservative: the output is a queue ordering, not a confidence
    in ownership. Nothing here can promote a candidate past `curated` — only a
    human checking a source can do that.
    """
    signals: list[str] = []
    score = 0.0

    if c.bidirectional_with_cluster:
        score += 0.30
        signals.append(
            f"bidirectional flow with the known cluster "
            f"(${c.value_with_cluster_usd:,.0f} over {c.interactions_with_cluster} transfers) "
            f"— consistent with treasury sweeps between an operator's own wallets"
        )
    else:
        score += 0.10
        signals.append(
            f"one-directional flow with the known cluster "
            f"(${c.value_with_cluster_usd:,.0f}) — weaker: also consistent with a "
            f"large user or an exchange"
        )

    if c.own_counterparties >= MIN_HUB_COUNTERPARTIES:
        score += 0.25
        signals.append(
            f"faces {c.own_counterparties} distinct counterparties of its own — "
            f"hub shape, not an individual"
        )

    if c.shared_counterparty_overlap >= 0.15:
        score += 0.30
        signals.append(
            f"{c.shared_counterparty_overlap * 100:.0f}% counterparty overlap with the "
            f"known cluster — the same users reach both, which is hard to produce by "
            f"coincidence"
        )
    elif c.shared_counterparty_overlap >= 0.05:
        score += 0.12
        signals.append(
            f"{c.shared_counterparty_overlap * 100:.0f}% counterparty overlap with the "
            f"known cluster — weak but present"
        )

    # Operator wallets move a lot in many modest transfers. A very high average
    # points at OTC or bridge activity instead.
    if 0 < c.avg_transfer_usd < 100_000 and c.own_transfers >= 50:
        score += 0.15
        signals.append(
            f"{c.own_transfers} transfers averaging ${c.avg_transfer_usd:,.0f} — "
            f"retail-facing cadence rather than OTC settlement"
        )

    return min(score, 1.0), signals


async def _profile(address: str, chain: str, hours: int) -> tuple[set[str], int, float]:
    """Counterparty set, transfer count, and average USD size for an address."""
    tset = await get_transfers(address, chain, hours)
    prices = await resolve_prices({t.token_symbol for t in tset.transfers})

    parties: set[str] = set()
    total = 0.0
    counted = 0
    for t in tset.transfers:
        other = t.to_addr if t.from_addr == address else t.from_addr
        parties.add(other)
        usd = t.amount * prices.get(t.token_symbol, 0.0)
        if usd > 0:
            total += usd
            counted += 1
    return parties, len(tset.transfers), (total / counted if counted else 0.0)


async def discover_for_operator(
    slug: str, hours: int = 168, max_candidates: int = 10
) -> dict:
    """Propose sibling wallet candidates, served from the aggregate cache.

    Discovery reads the whole cluster and then profiles each shortlisted
    counterparty — one upstream round trip apiece. That does not fit a request
    deadline cold, so it uses the same serve-cached / rebuild-behind pattern as
    the market aggregates.
    """
    return await cached_aggregate(
        ("discover", slug, hours, max_candidates),
        lambda: _build_discover_for_operator(slug, hours, max_candidates),
    )


async def _build_discover_for_operator(
    slug: str, hours: int = 168, max_candidates: int = 10
) -> dict:
    casino: Casino | None = get_casino(slug)
    if not casino or not casino.is_attributed:
        return {
            "slug": slug,
            "error": "operator has no reviewed wallet claim to expand from",
            "candidates": [],
        }

    known = {w.address.lower() for w in casino.wallets}
    every_known = {
        w.address.lower() for op in attributed_operators() for w in op.wallets
    }

    # Aggregate flow between the known cluster and everything it touches.
    agg: dict[str, dict] = defaultdict(
        lambda: {"in": 0.0, "out": 0.0, "n": 0, "chain": "ethereum"}
    )
    cluster_counterparties: set[str] = set()

    targets = observation_targets(casino)
    seed_pairs = {(w.address.lower(), w.chain) for w in casino.wallets}
    cluster_sets = await gather_within_budget([
        get_observation_transfers(
            wallet.address,
            wallet.chain,
            hours,
            seed=(wallet.address.lower(), wallet.chain) in seed_pairs,
        )
        for wallet in targets
    ])
    clusters_read = sum(1 for tset in cluster_sets if tset is not None)
    # One price lookup for every symbol across the whole cluster. Resolving per
    # wallet issued a serial upstream call per wallet, which sat outside the
    # read budget and pushed the endpoint past the service deadline.
    prices = await resolve_prices({
        t.token_symbol
        for tset in cluster_sets if tset is not None
        for t in tset.transfers
    })
    for wallet, tset in zip(targets, cluster_sets):
        if tset is None:
            continue  # missed the budget — a coverage gap, not an empty wallet
        addr = wallet.address.lower()

        for t in tset.transfers:
            usd = t.amount * prices.get(t.token_symbol, 0.0)
            if usd <= 0:
                continue
            other = t.to_addr if t.from_addr == addr else t.from_addr
            if other in known:
                continue  # movement inside the known cluster
            cluster_counterparties.add(other)
            rec = agg[other]
            rec["chain"] = t.chain
            rec["n"] += 1
            if t.to_addr == addr:
                rec["in"] += usd
            else:
                rec["out"] += usd

    # Shortlist by materiality, then profile each one individually.
    shortlist = [
        (a, r)
        for a, r in agg.items()
        if (r["in"] + r["out"]) >= MIN_SHARED_VALUE_USD
        and r["n"] >= MIN_INTERACTIONS
        and a not in every_known  # already labeled elsewhere
    ]
    shortlist.sort(key=lambda kv: -(kv[1]["in"] + kv[1]["out"]))
    # Each profile is an upstream round trip, so the fan-out is bounded. A
    # wider net ranks slightly better but risks the node timeout, and a timeout
    # is a failed answer — strictly worse than a marginally weaker ordering.
    shortlist = shortlist[: min(max_candidates * 2, MAX_PROFILE_FANOUT)]

    candidates: list[Candidate] = []
    profiles = await gather_within_budget(
        [_profile(a, r["chain"], hours) for a, r in shortlist]
    )

    for (addr, rec), profile in zip(shortlist, profiles):
        if profile is None:
            continue  # unprofiled: ranking it on cluster flow alone would
            # overstate what was actually checked
        parties, n_transfers, avg_usd = profile
        overlap = (
            len(parties & cluster_counterparties) / len(parties) if parties else 0.0
        )
        c = Candidate(
            address=addr,
            chain=rec["chain"],
            proposed_operator=casino.slug,
            proposed_operator_name=casino.name,
            value_with_cluster_usd=rec["in"] + rec["out"],
            interactions_with_cluster=int(rec["n"]),
            bidirectional_with_cluster=rec["in"] > 0 and rec["out"] > 0,
            own_counterparties=len(parties),
            own_transfers=n_transfers,
            shared_counterparty_overlap=overlap,
            avg_transfer_usd=avg_usd,
        )
        c.strength, c.signals = _score(c)
        # Discovery can never propose better than a curated label; a human must
        # attach a source before anything reaches `verified`.
        c.recommended_status = "curated" if c.strength >= 0.7 else "unverified_seed"
        candidates.append(c)

    candidates.sort(key=lambda c: -c.strength)
    top = candidates[:max_candidates]

    return {
        "slug": casino.slug,
        "name": casino.name,
        "window_hours": hours,
        "known_clusters": len(casino.wallets),
        "counterparties_examined": len(agg),
        "candidates_shortlisted": len(shortlist),
        "candidates_profiled": len(candidates),
        "candidates": [c.as_dict() for c in top],
        "max_recommended_confidence": CONFIDENCE_CEILING["curated"],
        "clusters_read": clusters_read,
        "clusters_total": len(targets),
        "coverage_complete": clusters_read == len(targets)
        and len(candidates) == len(shortlist),
        # "derived" only once at least one cluster was actually read. With none,
        # an empty candidate list would look like "we looked and found nothing".
        "data_source": "derived" if clusters_read else "unavailable",
        "methodology": (
            "Candidates are addresses exchanging material value with an already-"
            "attributed cluster while showing hub behaviour of their own. Ranked by "
            "bidirectionality, counterparty overlap, and transfer cadence. This is a "
            "review queue, not an attribution: on-chain behaviour is consistent with "
            "an operator wallet but never proves ownership. Nothing here is added to "
            "the registry automatically."
        ),
    }


async def discover_all(hours: int = 168, per_operator: int = 5) -> dict:
    """Run discovery across every attributed operator."""
    return await cached_aggregate(
        ("discover_all", hours, per_operator),
        lambda: _build_discover_all(hours, per_operator),
    )


async def _build_discover_all(hours: int = 168, per_operator: int = 5) -> dict:
    operators = attributed_operators()
    results = await asyncio.gather(
        *(discover_for_operator(o.slug, hours, per_operator) for o in operators)
    )
    total = sum(len(r.get("candidates", [])) for r in results)
    strong = sum(
        1
        for r in results
        for c in r.get("candidates", [])
        if c["strength"] >= 0.7
    )
    searched = [r for r in results if r.get("data_source") not in (None, "unavailable")]
    return {
        "window_hours": hours,
        "operators_expanded": len(operators),
        "operators_searched": len(searched),
        "candidates_proposed": total,
        "strong_candidates": strong,
        "by_operator": results,
        # Same rule as one operator: without a single completed search, an empty
        # candidate list must not read as "searched and found nothing".
        "data_source": "derived" if searched else "unavailable",
        "coverage_complete": len(searched) == len(operators)
        and all(r.get("coverage_complete") for r in searched),
        "note": (
            f"{total} candidate(s) proposed across {len(operators)} operator(s); "
            f"{strong} rated strong. Every one requires human review before entering "
            f"the registry — a wrong label silently corrupts every derived figure, and "
            f"registration is immutable."
        ),
    }
