"""Player evaluation from on-chain settlement.

WHAT THIS MEASURES — and what it does not
=========================================

For a given address, this computes the *net observed position* against
attributed operator clusters: value received from those clusters minus value
sent to them, over a window.

That is a real, per-transaction verifiable quantity. It is **not** gambling
profit and loss, and the distinction is not pedantic — the two can diverge
enormously:

  * Off-chain balance is invisible. Funds sitting in a casino account have been
    sent but not returned, and look identical to a loss.
  * Non-wager flows are indistinguishable. Affiliate commission, rakeback,
    bonuses, staff payments, and partner settlements all move through the same
    wallets in the same direction as winnings.
  * Wallet identity is not player identity. One person may split across many
    addresses; one address may be a custodian, a bridge, or an exchange serving
    thousands of people.
  * Deposit and withdrawal addresses often differ, which double-counts one side
    of an individual's real position.

So the vocabulary here stays literal throughout: `received_from_operators`,
`sent_to_operators`, `net_position_usd`. Never "winnings", never "profit",
never "player P&L". A high net position means an address received more than it
sent through clusters we watch — a lead worth investigating, not a conclusion.

Counterparty classification exists for the same reason: an address with a
thousand counterparties moving nine figures is infrastructure, not a punter,
and is flagged as such rather than topping a "biggest winner" board.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .market import collect_flows, _worst
from .onchain import get_transfers
from .prices import resolve_prices
from .wallets import attributed_operators, resolve_wallet

# An address moving through this many distinct operators, or at this scale, is
# far more likely to be infrastructure (exchange, bridge, custodian, payment
# processor) than an individual. Surfacing it as a "top player" would be wrong.
INFRA_OPERATOR_THRESHOLD = 4
INFRA_VALUE_THRESHOLD_USD = 5_000_000
INFRA_TRANSFER_THRESHOLD = 500

# Above this, a single transfer is implausible as an individual wager or
# withdrawal and is far more likely to be a treasury, OTC, or custodial move.
INDIVIDUAL_AVG_TRANSFER_CEILING_USD = 250_000

# Flow below this is too small to characterise an address either way.
MATERIAL_FLOW_USD = 1_000

# Net position is only meaningful when BOTH directions are present. An address
# that only ever received (or only ever sent) has no round trip to net out.
# Ranking such an address as a "top winner" is the single easiest way to make
# this feature wrong, so it gets its own class and is excluded from net boards.
BIDIRECTIONAL_MIN_RATIO = 0.01  # weaker side must be ≥1% of the stronger


@dataclass
class OperatorExposure:
    slug: str
    name: str
    sent_usd: float
    received_usd: float
    transfers: int
    first_seen: str | None
    last_seen: str | None

    @property
    def net_usd(self) -> float:
        return self.received_usd - self.sent_usd


@dataclass
class PlayerProfile:
    address: str
    chain: str
    window_hours: int

    sent_to_operators_usd: float
    received_from_operators_usd: float
    net_position_usd: float

    transfers_with_operators: int
    operators_touched: int
    exposures: list[OperatorExposure] = field(default_factory=list)

    first_seen: str | None = None
    last_seen: str | None = None
    active_hours: float = 0.0
    avg_transfer_usd: float = 0.0
    largest_transfer_usd: float = 0.0

    entity_class: str = "unclassified"  # individual_candidate | infrastructure | ...
    classification_reasons: list[str] = field(default_factory=list)
    behaviour_flags: list[str] = field(default_factory=list)

    is_operator_wallet: bool = False
    operator_label: str | None = None

    data_source: str = "unavailable"
    coverage_complete: bool = True


def _classify_entity(
    operators_touched: int,
    gross_usd: float,
    transfers: int,
    distinct_counterparties: int,
    sent_usd: float = 0.0,
    received_usd: float = 0.0,
) -> tuple[str, list[str]]:
    """Classify what kind of thing an address behaves like.

    Ordering matters: infrastructure is checked first, then one-directional
    flow, then scale. Only what survives all three is a plausible individual.

    The one-directional test is the important one. An address that received
    $3M and sent nothing has no round trip, so its "net position" is just its
    inflow — it is a transfer, not a result. Ranking it as a top winner is the
    single easiest way to make a player board wrong, and it is exactly what a
    naive net-position sort does.
    """
    reasons: list[str] = []

    # 1. Infrastructure — hubs, routers, custodians.
    if operators_touched >= INFRA_OPERATOR_THRESHOLD:
        reasons.append(
            f"interacts with {operators_touched} distinct operators — "
            f"unusual for an individual"
        )
    if gross_usd >= INFRA_VALUE_THRESHOLD_USD:
        reasons.append(f"gross flow ${gross_usd:,.0f} exceeds individual scale")
    if transfers >= INFRA_TRANSFER_THRESHOLD:
        reasons.append(f"{transfers} transfers in window indicates automated routing")
    if distinct_counterparties > 200:
        reasons.append(
            f"{distinct_counterparties} distinct counterparties — behaves as a hub"
        )
    if transfers > 0 and (gross_usd / transfers) >= INDIVIDUAL_AVG_TRANSFER_CEILING_USD:
        reasons.append(
            f"average transfer ${gross_usd / transfers:,.0f} is implausible for an "
            f"individual wager or withdrawal"
        )
    if reasons:
        return "infrastructure", reasons

    # 2. Too small to characterise.
    if gross_usd < MATERIAL_FLOW_USD:
        return "low_activity", [
            f"gross flow ${gross_usd:,.0f} is below the materiality threshold"
        ]

    # 3. One-directional — no round trip, so no meaningful net position.
    weaker, stronger = min(sent_usd, received_usd), max(sent_usd, received_usd)
    if stronger > 0 and (weaker / stronger) < BIDIRECTIONAL_MIN_RATIO:
        direction = "inbound only" if received_usd > sent_usd else "outbound only"
        return "one_directional", [
            f"{direction}: ${stronger:,.0f} in one direction against ${weaker:,.0f} in "
            f"the other. With no round trip the net figure is just the transfer itself, "
            f"not a settled result — consistent with treasury movement, an OTC leg, a "
            f"bridge hop, or a separate withdrawal address"
        ]

    # 4. Bidirectional flow at individual scale.
    return "individual_candidate", [
        f"bidirectional flow (${sent_usd:,.0f} out, ${received_usd:,.0f} in) across "
        f"{transfers} transfers at individual scale — though an address is never "
        f"proven to be one person"
    ]


def _behaviour_flags(transfers: list, addr: str, sent: float, received: float) -> list[str]:
    """Neutral, descriptive observations. Not accusations."""
    flags: list[str] = []
    if not transfers:
        return flags

    if sent > 0 and received == 0:
        flags.append(
            "one-directional: value sent to operators with nothing returned in window "
            "(consistent with losses, an open balance, or a different withdrawal address)"
        )
    if received > 0 and sent == 0:
        flags.append(
            "one-directional: value received from operators with nothing sent in window "
            "(consistent with a withdrawal-only address, affiliate payout, or bonus)"
        )
    if sent > 0 and received > 0:
        ratio = received / sent
        if ratio > 3:
            flags.append(f"received {ratio:.1f}× what it sent within the window")
        elif ratio < 0.33:
            flags.append(f"sent {1 / ratio:.1f}× what it received within the window")

    # Rapid in-out cycling.
    ordered = sorted(transfers, key=lambda t: t.timestamp)
    quick = 0
    for prev, nxt in zip(ordered, ordered[1:]):
        if (nxt.timestamp - prev.timestamp).total_seconds() < 300:
            quick += 1
    if quick >= 10:
        flags.append(f"{quick} transfer pairs under 5 minutes apart — rapid cycling")

    return flags


async def evaluate_player(
    address: str, chain: str = "ethereum", hours: int = 720
) -> PlayerProfile:
    """Evaluate one address against every attributed operator cluster."""
    address = address.lower()

    # Is this address itself a labeled operator wallet? Evaluating a casino's
    # own treasury as a "player" would be a category error.
    claim = resolve_wallet(address)

    tset = await get_transfers(address, chain, hours)
    prices = await resolve_prices({t.token_symbol for t in tset.transfers})

    # Map every attributed cluster address to its operator.
    cluster_owner: dict[str, tuple[str, str]] = {}
    for op in attributed_operators():
        for w in op.wallets:
            cluster_owner[w.address.lower()] = (op.slug, op.name)

    per_op: dict[str, dict] = defaultdict(
        lambda: {
            "sent": 0.0,
            "received": 0.0,
            "transfers": 0,
            "first": None,
            "last": None,
            "name": "",
        }
    )
    counterparties: set[str] = set()
    matched: list = []
    largest = 0.0

    for t in tset.transfers:
        usd = t.amount * prices.get(t.token_symbol, 0.0)
        if usd <= 0:
            continue

        other = t.to_addr if t.from_addr == address else t.from_addr
        counterparties.add(other)

        owner = cluster_owner.get(other)
        if not owner:
            continue  # not an operator interaction

        slug, name = owner
        rec = per_op[slug]
        rec["name"] = name
        rec["transfers"] += 1
        if t.from_addr == address:
            rec["sent"] += usd
        else:
            rec["received"] += usd

        ts = t.timestamp.isoformat()
        rec["first"] = min(rec["first"], ts) if rec["first"] else ts
        rec["last"] = max(rec["last"], ts) if rec["last"] else ts
        matched.append(t)
        largest = max(largest, usd)

    sent = sum(r["sent"] for r in per_op.values())
    received = sum(r["received"] for r in per_op.values())
    gross = sent + received

    exposures = sorted(
        (
            OperatorExposure(
                slug=slug,
                name=r["name"],
                sent_usd=round(r["sent"], 2),
                received_usd=round(r["received"], 2),
                transfers=int(r["transfers"]),
                first_seen=r["first"],
                last_seen=r["last"],
            )
            for slug, r in per_op.items()
        ),
        key=lambda e: -(e.sent_usd + e.received_usd),
    )

    times = [t.timestamp for t in matched]
    first = min(times).isoformat() if times else None
    last = max(times).isoformat() if times else None
    active = (
        round((max(times) - min(times)).total_seconds() / 3600, 2) if len(times) > 1 else 0.0
    )

    entity_class, reasons = _classify_entity(
        len(per_op), gross, len(matched), len(counterparties), sent, received
    )
    if claim:
        entity_class = "operator_wallet"
        reasons = [
            f"address is itself an attributed {claim[0].name} cluster "
            f"({claim[1].evidence_status})"
        ]

    return PlayerProfile(
        address=address,
        chain=chain,
        window_hours=hours,
        sent_to_operators_usd=round(sent, 2),
        received_from_operators_usd=round(received, 2),
        net_position_usd=round(received - sent, 2),
        transfers_with_operators=len(matched),
        operators_touched=len(per_op),
        exposures=exposures,
        first_seen=first,
        last_seen=last,
        active_hours=active,
        avg_transfer_usd=round(gross / len(matched), 2) if matched else 0.0,
        largest_transfer_usd=round(largest, 2),
        entity_class=entity_class,
        classification_reasons=reasons,
        behaviour_flags=_behaviour_flags(matched, address, sent, received),
        is_operator_wallet=bool(claim),
        operator_label=claim[0].name if claim else None,
        data_source=tset.data_source,
        coverage_complete=tset.complete,
    )


# ── Settlement-derived leaderboard ───────────────────────────────────────────


async def player_leaderboard(
    hours: int = 168, limit: int = 25, exclude_infrastructure: bool = True,
    casino_slug: str | None = None,
) -> dict:
    """Rank counterparties by net observed position across operator clusters.

    Built from settlement, not from scraped bet feeds — every row is a sum over
    transactions a reader can verify independently. The tradeoff is that it sees
    only on-chain movement: a player whose balance sits inside the casino is
    invisible, and non-wager flows are indistinguishable from winnings.
    """
    operators = attributed_operators()
    if casino_slug:
        operators = [operator for operator in operators if operator.slug == casino_slug]
    flows = await collect_flows(operators, hours)

    agg: dict[str, dict] = defaultdict(
        lambda: {
            "sent": 0.0,
            "received": 0.0,
            "transfers": 0,
            "operators": set(),
            "chains": set(),
            "first": None,
            "last": None,
        }
    )
    per_casino: dict[str, dict[str, dict]] = {}

    for flow in flows:
        cluster = flow.wallet_addresses
        casino_agg: dict[str, dict] = defaultdict(
            lambda: {
                "sent": 0.0,
                "received": 0.0,
                "transfers": 0,
                "operators": {flow.casino.slug},
                "chains": set(),
                "first": None,
                "last": None,
            }
        )
        per_casino[flow.casino.slug] = casino_agg
        for t in flow.transfers:
            usd = flow.usd(t)
            if usd <= 0:
                continue

            if t.to_addr in cluster and t.from_addr not in cluster:
                party, key = t.from_addr, "sent"      # party sent INTO the operator
            elif t.from_addr in cluster and t.to_addr not in cluster:
                party, key = t.to_addr, "received"    # party received FROM the operator
            else:
                continue  # internal cluster movement

            rec = agg[party]
            rec[key] += usd
            rec["transfers"] += 1
            rec["operators"].add(flow.casino.slug)
            rec["chains"].add(t.chain)
            ts = t.timestamp.isoformat()
            rec["first"] = min(rec["first"], ts) if rec["first"] else ts
            rec["last"] = max(rec["last"], ts) if rec["last"] else ts

            casino_rec = casino_agg[party]
            casino_rec[key] += usd
            casino_rec["transfers"] += 1
            casino_rec["chains"].add(t.chain)
            casino_rec["first"] = (
                min(casino_rec["first"], ts) if casino_rec["first"] else ts
            )
            casino_rec["last"] = (
                max(casino_rec["last"], ts) if casino_rec["last"] else ts
            )

    def make_rows(records: dict[str, dict]) -> list[dict]:
        rows = []
        for addr, r in records.items():
            gross = r["sent"] + r["received"]
            entity_class, reasons = _classify_entity(
                len(r["operators"]), gross, int(r["transfers"]), 0,
                r["sent"], r["received"],
            )
            rows.append({
                "address": addr,
                "sent_to_operators_usd": round(r["sent"], 2),
                "received_from_operators_usd": round(r["received"], 2),
                "net_position_usd": round(r["received"] - r["sent"], 2),
                "gross_flow_usd": round(gross, 2),
                "transfers": int(r["transfers"]),
                "operators_touched": len(r["operators"]),
                "operators": sorted(r["operators"]),
                "chains": sorted(r["chains"]),
                "entity_class": entity_class,
                "classification_reasons": reasons,
                "first_seen": r["first"],
                "last_seen": r["last"],
            })
        return rows

    rows = make_rows(agg)

    considered = (
        [r for r in rows if r["entity_class"] == "individual_candidate"]
        if exclude_infrastructure
        else rows
    )

    net_positive = sorted(considered, key=lambda r: -r["net_position_usd"])[:limit]
    net_negative = sorted(considered, key=lambda r: r["net_position_usd"])[:limit]
    by_volume = sorted(considered, key=lambda r: -r["gross_flow_usd"])[:limit]

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["entity_class"]] = counts.get(r["entity_class"], 0) + 1

    # One-directional flow has no round trip to net out, so it is reported
    # separately rather than being ranked as a result.
    one_way = sorted(
        (r for r in rows if r["entity_class"] == "one_directional"),
        key=lambda r: -r["gross_flow_usd"],
    )[:limit]

    casino_boards = []
    for flow in flows:
        casino_rows = make_rows(per_casino[flow.casino.slug])
        candidates = [
            row for row in casino_rows if row["entity_class"] == "individual_candidate"
        ]
        ranked = [
            row for row in casino_rows if row["entity_class"] != "infrastructure"
        ]
        casino_boards.append({
            "slug": flow.casino.slug,
            "name": flow.casino.name,
            "addresses_observed": len(casino_rows),
            "individual_candidates": len(candidates),
            "chains_observed": sorted({
                chain for row in casino_rows for chain in row["chains"]
            }),
            "chains_attributed": sorted({wallet.chain for wallet in flow.casino.wallets}),
            "chains_queried": flow.casino.queried_chains,
            "by_settlement_volume": sorted(
                ranked, key=lambda row: -row["gross_flow_usd"]
            )[:limit],
            "net_received": sorted(
                (row for row in ranked if row["net_position_usd"] > 0),
                key=lambda row: -row["net_position_usd"],
            )[:limit],
            "net_sent": sorted(
                (row for row in ranked if row["net_position_usd"] < 0),
                key=lambda row: row["net_position_usd"],
            )[:limit],
            "data_source": flow.data_source,
            "coverage_complete": flow.complete,
        })

    return {
        "window_hours": hours,
        "casino": casino_slug,
        "addresses_observed": len(rows),
        "class_counts": counts,
        "individual_candidates": counts.get("individual_candidate", 0),
        "infrastructure_excluded": counts.get("infrastructure", 0),
        "one_directional_excluded": counts.get("one_directional", 0),
        "largest_one_directional": one_way,
        "net_positive": net_positive,
        "net_negative": net_negative,
        "by_volume": by_volume,
        "by_casino": casino_boards,
        "chains_attributed": sorted({
            wallet.chain for operator in operators for wallet in operator.wallets
        }),
        "chains_observed": sorted({
            transfer.chain for flow in flows for transfer in flow.transfers
        }),
        "chains_queried": sorted({
            chain
            for operator in operators
            for chain in operator.queried_chains
        }),
        "data_source": _worst([f.data_source for f in flows]),
        "coverage_complete": all(f.complete for f in flows),
        "methodology": (
            "Net position is value received from attributed operator clusters minus "
            "value sent to them, over the window. It is not gambling profit and loss: "
            "balances held inside an operator are invisible, non-wager flows such as "
            "affiliate payouts and bonuses are indistinguishable from winnings, and one "
            "address is not proven to be one person. Addresses classified as "
            "infrastructure are excluded by default. Per-casino boards include other "
            "counterparty classes and rank observed settlement volume and direction, not "
            "amount wagered or gambling profit/loss."
        ),
    }


# ── Cohort and segment analysis ──────────────────────────────────────────────
#
# Net position only works for the ~0.1% of addresses with a round trip. These
# aggregate views work on ALL observed counterparties, which makes them the more
# useful lens in practice.

VALUE_SEGMENTS = [
    ("minnow", 0, 1_000),
    ("retail", 1_000, 10_000),
    ("mid", 10_000, 100_000),
    ("whale", 100_000, 1_000_000),
    ("mega", 1_000_000, float("inf")),
]


def _segment(gross_usd: float) -> str:
    for name, lo, hi in VALUE_SEGMENTS:
        if lo <= gross_usd < hi:
            return name
    return "minnow"


async def player_cohorts(hours: int = 168) -> dict:
    """Segment every observed counterparty by value, recency, and reach.

    Answers the questions a leaderboard cannot: how concentrated is the player
    base, what share of flow comes from whales, how many addresses are new to
    the window, and how many use more than one operator.
    """
    operators = attributed_operators()
    # Double window so "new this period" is meaningful rather than an artefact
    # of where the window happens to start.
    flows = await collect_flows(operators, hours * 2)

    from datetime import datetime, timedelta, timezone

    midpoint = datetime.now(timezone.utc) - timedelta(hours=hours)

    agg: dict[str, dict] = defaultdict(
        lambda: {
            "sent": 0.0,
            "received": 0.0,
            "n": 0,
            "ops": set(),
            "first": None,
            "seen_before": False,
            "seen_now": False,
        }
    )

    for flow in flows:
        cluster = flow.wallet_addresses
        for t in flow.transfers:
            usd = flow.usd(t)
            if usd <= 0:
                continue
            if t.to_addr in cluster and t.from_addr not in cluster:
                party, key = t.from_addr, "sent"
            elif t.from_addr in cluster and t.to_addr not in cluster:
                party, key = t.to_addr, "received"
            else:
                continue

            r = agg[party]
            r["ops"].add(flow.casino.slug)
            if t.timestamp >= midpoint:
                r[key] += usd
                r["n"] += 1
                r["seen_now"] = True
                ts = t.timestamp.isoformat()
                r["first"] = min(r["first"], ts) if r["first"] else ts
            else:
                r["seen_before"] = True

    current = {a: r for a, r in agg.items() if r["seen_now"]}

    segments: dict[str, dict] = defaultdict(lambda: {"count": 0, "gross_usd": 0.0})
    multi_operator = 0
    new_addresses = 0
    returning = 0
    total_gross = 0.0

    for r in current.values():
        gross = r["sent"] + r["received"]
        total_gross += gross
        seg = segments[_segment(gross)]
        seg["count"] += 1
        seg["gross_usd"] += gross
        if len(r["ops"]) > 1:
            multi_operator += 1
        if r["seen_before"]:
            returning += 1
        else:
            new_addresses += 1

    # Concentration: what share of flow the largest addresses account for.
    by_gross = sorted(
        ((a, r["sent"] + r["received"]) for a, r in current.items()),
        key=lambda kv: -kv[1],
    )
    def top_share(n: int) -> float:
        if not total_gross:
            return 0.0
        return round(sum(v for _, v in by_gross[:n]) / total_gross * 100, 2)

    return {
        "window_hours": hours,
        "addresses_active": len(current),
        "new_this_period": new_addresses,
        "returning": returning,
        "retention_pct": (
            round(returning / len(current) * 100, 2) if current else 0.0
        ),
        "multi_operator_addresses": multi_operator,
        "multi_operator_pct": (
            round(multi_operator / len(current) * 100, 2) if current else 0.0
        ),
        "total_gross_usd": round(total_gross, 2),
        "segments": [
            {
                "segment": name,
                "lower_usd": lo,
                "upper_usd": None if hi == float("inf") else hi,
                "addresses": segments[name]["count"],
                "gross_usd": round(segments[name]["gross_usd"], 2),
                "share_of_addresses_pct": (
                    round(segments[name]["count"] / len(current) * 100, 2)
                    if current else 0.0
                ),
                "share_of_gross_pct": (
                    round(segments[name]["gross_usd"] / total_gross * 100, 2)
                    if total_gross else 0.0
                ),
            }
            for name, lo, hi in VALUE_SEGMENTS
        ],
        "concentration": {
            "top_10_share_pct": top_share(10),
            "top_50_share_pct": top_share(50),
            "top_100_share_pct": top_share(100),
        },
        "data_source": _worst([f.data_source for f in flows]),
        "coverage_complete": all(f.complete for f in flows),
        "note": (
            "Covers every observed counterparty, not only those with a round trip, "
            "so it is the more representative view. An address is still not proven "
            "to be one person, and flow direction does not prove a wager."
        ),
    }


async def cross_operator_overlap(hours: int = 168) -> dict:
    """Addresses transacting with more than one operator.

    Shared users are a genuine competitive signal: high overlap means operators
    draw on the same pool rather than distinct audiences.
    """
    operators = attributed_operators()
    flows = await collect_flows(operators, hours)

    by_address: dict[str, set[str]] = defaultdict(set)
    per_operator: dict[str, set[str]] = defaultdict(set)

    for flow in flows:
        cluster = flow.wallet_addresses
        for t in flow.transfers:
            if flow.usd(t) <= 0:
                continue
            if t.to_addr in cluster and t.from_addr not in cluster:
                party = t.from_addr
            elif t.from_addr in cluster and t.to_addr not in cluster:
                party = t.to_addr
            else:
                continue
            by_address[party].add(flow.casino.slug)
            per_operator[flow.casino.slug].add(party)

    shared = {a: ops for a, ops in by_address.items() if len(ops) > 1}

    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for ops in shared.values():
        ordered = sorted(ops)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                pairs[(a, b)] += 1

    pair_rows = sorted(
        (
            {
                "operator_a": a,
                "operator_b": b,
                "shared_addresses": n,
                "jaccard_pct": round(
                    n
                    / len(per_operator[a] | per_operator[b])
                    * 100,
                    2,
                )
                if (per_operator[a] | per_operator[b])
                else 0.0,
            }
            for (a, b), n in pairs.items()
        ),
        key=lambda r: -r["shared_addresses"],
    )

    return {
        "window_hours": hours,
        "addresses_observed": len(by_address),
        "multi_operator_addresses": len(shared),
        "overlap_pct": (
            round(len(shared) / len(by_address) * 100, 2) if by_address else 0.0
        ),
        "operator_pairs": pair_rows[:25],
        "per_operator_reach": sorted(
            (
                {"slug": slug, "unique_addresses": len(addrs)}
                for slug, addrs in per_operator.items()
            ),
            key=lambda r: -r["unique_addresses"],
        ),
        "data_source": _worst([f.data_source for f in flows]),
        "note": (
            "Two addresses are not proven to be two people, and one person may use "
            "several addresses — overlap is a floor on shared audience, not a count "
            "of shared customers."
        ),
    }
