"""Infrastructure provider analysis.

A NOTE ON THE WORD "PROVIDER"
=============================

In gambling-analytics products "provider" usually means a *game* provider —
Pragmatic Play, Evolution, Hacksaw. That is not what this module measures, and
it cannot be: a slot spin settles inside the operator's own ledger and never
touches a chain. No on-chain record anywhere attributes a wager to a game
studio. Any product claiming otherwise is reading a scraped bet feed.

What IS observable is the layer underneath — the **infrastructure providers**
that actually move value in and out of casino wallets: bridges, exchange hot
wallets, payment processors, market makers, and routing contracts. These leave
an unambiguous on-chain trail, and they matter: they determine how liquidity
reaches an operator, and a shift between them is a real market signal.

So this ranks the rails, and says so plainly. Naming it "providers" without
that distinction would be the same overclaim the rest of the codebase avoids.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .market import cached_aggregate, collect_flows, coverage_fields
from .wallets import attributed_operators

# Behavioural thresholds separating a rail from an end user.
#
# NOTE: these are measured against the ATTRIBUTED OPERATOR SET, which is small.
# An earlier version required 8 distinct operators — impossible when only 5 are
# attributed, so nothing ever qualified. Serving multiple operators is now a
# strong signal rather than a hard gate, and volume alone can also qualify.
HUB_MIN_OPERATORS = 2          # touches more than one operator
HUB_MIN_TRANSFERS = 25         # or routes at volume through a single one
HUB_MIN_VALUE_USD = 250_000

# Well-known infrastructure addresses. Anything not listed is classified by
# behaviour alone and labelled as unidentified rather than guessed at.
KNOWN_INFRA: dict[str, tuple[str, str]] = {
    # (label, category)
    "0x28c6c06298d514db089934071355e5743bf21d60": ("Binance 14", "exchange"),
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": ("Binance 15", "exchange"),
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": ("Binance 16", "exchange"),
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": ("Binance 17", "exchange"),
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": ("Binance 18", "exchange"),
    "0x4976a4a02f38326660d17bf34b431dc6e2eb2327": ("Binance 19", "exchange"),
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": ("Coinbase 10", "exchange"),
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": ("Coinbase 1", "exchange"),
    "0x503828976d22510aad0201ac7ec88293211d23da": ("Coinbase 2", "exchange"),
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740": ("Coinbase 3", "exchange"),
    "0x3cd751e6b0078be393132286c442345e5dc49699": ("Coinbase 4", "exchange"),
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": ("Bybit", "exchange"),
    "0x1522900b6dafac587d499a862861c0869be6e428": ("OKX", "exchange"),
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": ("Gate.io", "exchange"),
    "0x8103683202aa8da10536036edef04cdd865c225e": ("Stargate", "bridge"),
    "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae": ("LI.FI", "bridge"),
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad": ("Uniswap Universal Router", "dex"),
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": ("Uniswap V2 Router", "dex"),
    "0xe592427a0aece92de3edee1f18e0157c05861564": ("Uniswap V3 Router", "dex"),
    "0x1111111254eeb25477b68fb85ed929f73a960582": ("1inch", "dex"),
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff": ("0x Protocol", "dex"),
    "0x74de5d4fcbf63e00296fd95d33236b9794016631": ("Metamask Swap", "dex"),
}


def _classify(
    operators_served: int, transfers: int, value_usd: float, address: str
) -> tuple[str, str, list[str]]:
    """Return (label, category, evidence).

    Two independent routes qualify an address as a rail, because operators
    reach liquidity differently: some rails fan out across many operators,
    others push high volume through a single one.
    """
    known = KNOWN_INFRA.get(address)
    if known:
        return known[0], known[1], [
            f"address matches a published infrastructure label ({known[0]})"
        ]

    evidence: list[str] = []

    serves_many = operators_served >= HUB_MIN_OPERATORS and value_usd > 0
    routes_volume = transfers >= HUB_MIN_TRANSFERS and value_usd >= HUB_MIN_VALUE_USD

    if serves_many:
        evidence.append(
            f"serves {operators_served} distinct operators — a shared rail rather "
            f"than one operator's user"
        )
    if routes_volume:
        evidence.append(
            f"{transfers} transfers moving ${value_usd:,.0f} through the operator set "
            f"— routing cadence, not individual play"
        )

    if serves_many or routes_volume:
        return "unidentified rail", "unidentified", evidence

    evidence.append(
        f"{operators_served} operator(s), {transfers} transfers, ${value_usd:,.0f} "
        f"— below both routing thresholds"
    )
    return "unidentified counterparty", "endpoint", evidence


async def provider_activity(hours: int = 168, limit: int = 25) -> dict:
    """Rank infrastructure providers by casino flow carried, served from cache.

    This reads DOUBLE the requested window to derive trend, so it is the most
    expensive aggregate in the miner — the one that least belongs on the
    request path.
    """
    return await cached_aggregate(
        ("providers", hours, limit), lambda: _build_provider_activity(hours, limit)
    )


async def _build_provider_activity(hours: int = 168, limit: int = 25) -> dict:
    """Rank infrastructure providers by casino flow carried, with trend.

    Trend compares the requested window against the window immediately before
    it, so `hours=168` reads 336 hours and splits. A provider appearing only in
    the recent half is genuinely new rather than merely unranked before.
    """
    operators = attributed_operators()
    # Fetch double the window once, then split — cheaper than two passes and
    # guarantees both halves come from an identical snapshot.
    flows = await collect_flows(operators, hours * 2)

    now = datetime.now(timezone.utc)
    midpoint = now - timedelta(hours=hours)

    cur: dict[str, dict] = defaultdict(
        lambda: {"usd": 0.0, "n": 0, "ops": set(), "in": 0.0, "out": 0.0}
    )
    prev: dict[str, dict] = defaultdict(lambda: {"usd": 0.0, "n": 0})

    for flow in flows:
        cluster = flow.wallet_addresses
        for t in flow.transfers:
            usd = flow.usd(t)
            if usd <= 0:
                continue
            if t.to_addr in cluster and t.from_addr not in cluster:
                party, direction = t.from_addr, "in"
            elif t.from_addr in cluster and t.to_addr not in cluster:
                party, direction = t.to_addr, "out"
            else:
                continue

            if t.timestamp >= midpoint:
                rec = cur[party]
                rec["usd"] += usd
                rec["n"] += 1
                rec["ops"].add(flow.casino.slug)
                rec[direction] += usd
            else:
                p = prev[party]
                p["usd"] += usd
                p["n"] += 1

    rows = []
    for addr, r in cur.items():
        label, category, evidence = _classify(len(r["ops"]), r["n"], r["usd"], addr)
        before = prev.get(addr, {"usd": 0.0, "n": 0})
        if before["usd"] > 0:
            change = (r["usd"] - before["usd"]) / before["usd"] * 100
            trend = "rising" if change > 15 else "falling" if change < -15 else "steady"
        else:
            change = None
            trend = "new"

        rows.append(
            {
                "address": addr,
                "label": label,
                "category": category,
                "flow_usd": round(r["usd"], 2),
                "inbound_to_operators_usd": round(r["in"], 2),
                "outbound_from_operators_usd": round(r["out"], 2),
                "transfers": int(r["n"]),
                "operators_served": len(r["ops"]),
                "operators": sorted(r["ops"]),
                "previous_flow_usd": round(before["usd"], 2),
                "change_pct": round(change, 2) if change is not None else None,
                "trend": trend,
                "evidence": evidence,
            }
        )

    # Rails only — endpoints are individual users, covered by player analysis.
    rails = [r for r in rows if r["category"] != "endpoint"]
    rails.sort(key=lambda r: -r["flow_usd"])
    total = sum(r["flow_usd"] for r in rails) or 1.0
    for r in rails:
        r["share_of_rail_flow_pct"] = round(r["flow_usd"] / total * 100, 2)

    ranked = rails[:limit]
    trending = sorted(
        (r for r in rails if r["trend"] in {"rising", "new"} and r["flow_usd"] > 0),
        key=lambda r: (-(r["change_pct"] or 1e9), -r["flow_usd"]),
    )[:limit]

    by_category: dict[str, dict] = defaultdict(lambda: {"usd": 0.0, "count": 0})
    for r in rails:
        c = by_category[r["category"]]
        c["usd"] += r["flow_usd"]
        c["count"] += 1

    return {
        "window_hours": hours,
        "comparison_window_hours": hours,
        "ranked": ranked,
        "trending": trending,
        "categories": sorted(
            (
                {
                    "category": k,
                    "flow_usd": round(v["usd"], 2),
                    "providers": v["count"],
                    "share_pct": round(v["usd"] / total * 100, 2),
                }
                for k, v in by_category.items()
            ),
            key=lambda c: -c["flow_usd"],
        ),
        "rails_identified": sum(1 for r in rails if r["category"] != "unidentified"),
        "rails_total": len(rails),
        "total_rail_flow_usd": round(total if rails else 0.0, 2),
        **coverage_fields(flows),
        "scope": (
            "Infrastructure providers — the bridges, exchanges, and routing contracts "
            "that move value in and out of operator wallets. NOT game providers: a "
            "wager settles inside an operator's ledger and leaves no on-chain record "
            "attributing it to a game studio, so game-provider ranking is not "
            "derivable from chain data at all."
        ),
    }
