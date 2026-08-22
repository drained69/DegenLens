"""Market-level analysis across the attributed operator set.

Everything here is derived from the same observed transfer records the rest of
the miner uses. Three rules hold throughout:

  1. Unobserved is not zero. Operators without wallet attribution are counted
     separately and never contribute a 0 to a total or an average.
  2. Shares are shares OF OBSERVED FLOW, never "market share". We see a subset
     of clusters on a subset of chains; calling that market share would be a
     claim about the whole market that the data cannot support.
  3. Direction is direction. Inbound flow to an operator wallet is not proven
     to be a player deposit — it is an inbound transfer. Naming stays literal.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .onchain import (
    NATIVE_SYMBOL,
    Transfer,
    TransferSet,
    get_observation_transfers,
    merge_cluster_reads,
)
from .prices import resolve_prices
from .wallets import INDEXED_CHAINS, Casino, attributed_operators, catalog, observation_targets

STABLE_SYMBOLS = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDP", "FRAX"}


# ── Shared collection ────────────────────────────────────────────────────────


@dataclass
class OperatorFlow:
    """Every observed transfer for one operator, with pricing applied."""

    casino: Casino
    transfers: list[Transfer]
    prices: dict[str, float]
    data_source: str
    complete: bool

    def usd(self, t: Transfer) -> float:
        return t.amount * self.prices.get(t.token_symbol, 0.0)

    @property
    def wallet_addresses(self) -> set[str]:
        return {w.address.lower() for w in self.casino.wallets}


async def collect_flow(casino: Casino, hours: int) -> OperatorFlow:
    """Fetch and price every transfer across an operator's clusters.

    Each EVM identity claim is read on every indexed chain, not just the seed
    network. That is how Stake and the other multi-chain casinos actually settle.
    """
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
    transfers = [t for s in sets for t in s.transfers]
    prices = await resolve_prices({t.token_symbol for t in transfers})
    source, complete = merge_cluster_reads(sets)
    return OperatorFlow(
        casino=casino,
        transfers=transfers,
        prices=prices,
        data_source=source,
        complete=complete,
    )


async def collect_flows(operators: list[Casino], hours: int) -> list[OperatorFlow]:
    """Collect operator flows with a bounded number of cluster aggregates."""
    # Operators are independent. Serializing them made market-wide endpoints
    # scale linearly with the registry and routinely exceed the node timeout.
    # The lower-level on-chain adapter still limits concurrent provider calls.
    operator_flow_sem = asyncio.Semaphore(3)

    async def collect(operator: Casino) -> OperatorFlow:
        async with operator_flow_sem:
            return await collect_flow(operator, hours)

    return await asyncio.gather(*(collect(operator) for operator in operators))


# ── Network distribution ─────────────────────────────────────────────────────


async def network_distribution(hours: int = 168) -> dict:
    """Observed flow split by chain.

    The analogue of Gamstat's network breakdown, but scoped honestly: this is
    the distribution across the chains we actually watch, not the whole market.
    """
    operators = attributed_operators()
    flows = await collect_flows(operators, hours)

    by_chain: dict[str, dict[str, float]] = defaultdict(
        lambda: {"inbound_usd": 0.0, "outbound_usd": 0.0, "transfers": 0}
    )

    for flow in flows:
        addrs = flow.wallet_addresses
        for t in flow.transfers:
            usd = flow.usd(t)
            if usd <= 0:
                continue
            bucket = by_chain[t.chain]
            bucket["transfers"] += 1
            if t.to_addr in addrs:
                bucket["inbound_usd"] += usd
            elif t.from_addr in addrs:
                bucket["outbound_usd"] += usd

    total_in = sum(b["inbound_usd"] for b in by_chain.values()) or 1.0
    chains = sorted(
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
            }
            for chain, b in by_chain.items()
        ),
        key=lambda r: -r["inbound_usd"],
    )

    return {
        "window_hours": hours,
        "chains": chains,
        "chains_observed": len(chains),
        "total_inbound_usd": round(total_in if by_chain else 0.0, 2),
        "data_source": _worst([f.data_source for f in flows]),
        "coverage_complete": all(f.complete for f in flows),
    }


# ── Asset / token mix ────────────────────────────────────────────────────────


async def asset_mix(slug: str | None = None, hours: int = 168) -> dict:
    """Which assets the observed flow is denominated in.

    Stablecoin dominance vs volatile assets is a genuine behavioural signal and
    is fully derivable from transfer records.
    """
    operators = (
        [c for c in attributed_operators() if c.slug == slug]
        if slug
        else attributed_operators()
    )
    if not operators:
        return {"error": "no attributed operator matched", "assets": []}

    flows = await collect_flows(operators, hours)

    by_token: dict[str, dict[str, float]] = defaultdict(
        lambda: {"inbound_usd": 0.0, "outbound_usd": 0.0, "transfers": 0}
    )
    for flow in flows:
        addrs = flow.wallet_addresses
        for t in flow.transfers:
            usd = flow.usd(t)
            if usd <= 0:
                continue
            b = by_token[t.token_symbol]
            b["transfers"] += 1
            if t.to_addr in addrs:
                b["inbound_usd"] += usd
            elif t.from_addr in addrs:
                b["outbound_usd"] += usd

    total = sum(b["inbound_usd"] for b in by_token.values()) or 1.0
    STABLES = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDP"}

    assets = sorted(
        (
            {
                "symbol": sym,
                "inbound_usd": round(b["inbound_usd"], 2),
                "outbound_usd": round(b["outbound_usd"], 2),
                "transfers": int(b["transfers"]),
                "share_of_observed_inbound_pct": round(b["inbound_usd"] / total * 100, 2),
                "is_stablecoin": sym in STABLES,
            }
            for sym, b in by_token.items()
        ),
        key=lambda r: -r["inbound_usd"],
    )
    stable_share = sum(
        a["share_of_observed_inbound_pct"] for a in assets if a["is_stablecoin"]
    )

    return {
        "slug": slug,
        "window_hours": hours,
        "assets": assets,
        "distinct_assets": len(assets),
        "stablecoin_share_pct": round(stable_share, 2),
        "data_source": _worst([f.data_source for f in flows]),
        "coverage_complete": all(f.complete for f in flows),
    }


# ── Flow time series ─────────────────────────────────────────────────────────


async def flow_series(slug: str, hours: int = 168, bucket_hours: int = 1) -> dict:
    """Bucketed inbound/outbound series for one operator.

    This is what turns a single number into a trend — the backbone of any
    operator profile chart.
    """
    operators = [c for c in attributed_operators() if c.slug == slug]
    if not operators:
        return {"slug": slug, "error": "operator not attributed", "series": []}

    flow = await collect_flow(operators[0], hours)
    addrs = flow.wallet_addresses
    bucket_seconds = max(bucket_hours, 1) * 3600

    buckets: dict[int, dict[str, float]] = defaultdict(
        lambda: {"inbound_usd": 0.0, "outbound_usd": 0.0, "transfers": 0}
    )
    for t in flow.transfers:
        usd = flow.usd(t)
        if usd <= 0:
            continue
        key = int(t.timestamp.timestamp()) // bucket_seconds * bucket_seconds
        b = buckets[key]
        b["transfers"] += 1
        if t.to_addr in addrs:
            b["inbound_usd"] += usd
        elif t.from_addr in addrs:
            b["outbound_usd"] += usd

    series = [
        {
            "t": datetime.fromtimestamp(k, tz=timezone.utc).isoformat(),
            "inbound_usd": round(v["inbound_usd"], 2),
            "outbound_usd": round(v["outbound_usd"], 2),
            "net_usd": round(v["inbound_usd"] - v["outbound_usd"], 2),
            "transfers": int(v["transfers"]),
        }
        for k, v in sorted(buckets.items())
    ]

    return {
        "slug": slug,
        "name": operators[0].name,
        "window_hours": hours,
        "bucket_hours": bucket_hours,
        "points": len(series),
        "series": series,
        "data_source": flow.data_source,
        "coverage_complete": flow.complete,
    }


# ── Large-transfer feed ──────────────────────────────────────────────────────


async def large_transfers(
    hours: int = 24, min_usd: float = 100_000, limit: int = 50
) -> dict:
    """Individual transfers above a USD threshold, across all attributed
    operators. Each row is a single verifiable transaction — the most directly
    checkable output this miner produces."""
    operators = attributed_operators()
    flows = await collect_flows(operators, hours)

    rows = []
    for flow in flows:
        addrs = flow.wallet_addresses
        for t in flow.transfers:
            usd = flow.usd(t)
            if usd < min_usd:
                continue
            direction = (
                "inbound" if t.to_addr in addrs
                else "outbound" if t.from_addr in addrs
                else "unrelated"
            )
            if direction == "unrelated":
                continue
            rows.append(
                {
                    "tx_hash": t.tx_hash,
                    "chain": t.chain,
                    "operator_slug": flow.casino.slug,
                    "operator_name": flow.casino.name,
                    "direction": direction,
                    "counterparty": t.from_addr if direction == "inbound" else t.to_addr,
                    "token": t.token_symbol,
                    "amount": round(t.amount, 6),
                    "usd_value": round(usd, 2),
                    "timestamp": t.timestamp.isoformat(),
                }
            )

    rows.sort(key=lambda r: -r["usd_value"])
    return {
        "window_hours": hours,
        "min_usd": min_usd,
        "count": len(rows),
        "transfers": rows[:limit],
        "data_source": _worst([f.data_source for f in flows]),
        "coverage_complete": all(f.complete for f in flows),
    }


# ── Counterparty concentration ───────────────────────────────────────────────


async def counterparty_concentration(slug: str, hours: int = 168, top: int = 20) -> dict:
    """Which addresses account for most of an operator's observed flow.

    High concentration means the flow is driven by a handful of counterparties
    (often bridges, exchanges, or the operator's own routing) rather than by a
    broad user base — important context before reading any total as user activity.
    """
    operators = [c for c in attributed_operators() if c.slug == slug]
    if not operators:
        return {"slug": slug, "error": "operator not attributed", "counterparties": []}

    flow = await collect_flow(operators[0], hours)
    addrs = flow.wallet_addresses

    agg: dict[str, dict[str, float]] = defaultdict(
        lambda: {"inbound_usd": 0.0, "outbound_usd": 0.0, "transfers": 0}
    )
    for t in flow.transfers:
        usd = flow.usd(t)
        if usd <= 0:
            continue
        if t.to_addr in addrs:
            party, key = t.from_addr, "inbound_usd"
        elif t.from_addr in addrs:
            party, key = t.to_addr, "outbound_usd"
        else:
            continue
        if party in addrs:  # internal cluster movement, not a counterparty
            continue
        agg[party][key] += usd
        agg[party]["transfers"] += 1

    ranked = sorted(
        (
            {
                "address": addr,
                "inbound_usd": round(v["inbound_usd"], 2),
                "outbound_usd": round(v["outbound_usd"], 2),
                "total_usd": round(v["inbound_usd"] + v["outbound_usd"], 2),
                "transfers": int(v["transfers"]),
            }
            for addr, v in agg.items()
        ),
        key=lambda r: -r["total_usd"],
    )

    grand_total = sum(r["total_usd"] for r in ranked) or 1.0
    top_rows = ranked[:top]
    for r in top_rows:
        r["share_of_observed_flow_pct"] = round(r["total_usd"] / grand_total * 100, 2)

    top10_share = sum(r["total_usd"] for r in ranked[:10]) / grand_total * 100

    return {
        "slug": slug,
        "name": operators[0].name,
        "window_hours": hours,
        "distinct_counterparties": len(ranked),
        "top10_share_of_observed_flow_pct": round(top10_share, 2),
        "counterparties": top_rows,
        "data_source": flow.data_source,
        "coverage_complete": flow.complete,
    }


# ── Coverage reporting ───────────────────────────────────────────────────────


def coverage_report() -> dict:
    """What this miner can and cannot see. Published so consumers can weight
    the figures rather than assuming full market coverage."""
    everything = catalog()
    attributed = [c for c in everything if c.is_attributed]
    unattributed = [c for c in everything if not c.is_attributed]

    return {
        "operators_catalogued": len(everything),
        "operators_attributed": len(attributed),
        "operators_unattributed": len(unattributed),
        "wallet_clusters": sum(len(c.wallets) for c in attributed),
        "chains_covered": sorted({w.chain for c in attributed for w in c.wallets}),
        "chains_claimed": sorted({w.chain for c in attributed for w in c.wallets}),
        "attributed": [
            {
                "slug": c.slug,
                "name": c.name,
                "wallets": len(c.wallets),
                "chains": c.chains,
                "chains_queried": c.queried_chains,
                "evidence_status": c.best_evidence,
            }
            for c in attributed
        ],
        "unattributed": [
            {"slug": c.slug, "name": c.name, "attribution_status": "no reviewed wallet claim"}
            for c in unattributed
        ],
        "caveat": (
            "Figures cover only attributed clusters on covered chains. Operators "
            "without attribution are unobserved, not zero. Shares are shares of "
            "observed flow, not market share."
        ),
    }


def _worst(sources: list[str]) -> str:
    """Provenance downgrade order.

    `unsupported_chain` sits between `unavailable` and `demo` — it is an
    honest coverage gap for non-EVM registry entries, not an outage or a
    fabrication. Filter it out before ranking; if it is the ONLY source
    (nothing readable exists), degrade to `unavailable`.
    """
    if not sources:
        return "unavailable"
    readable = [s for s in sources if s != "unsupported_chain"]
    if not readable:
        return "unavailable"
    for level in ("unavailable", "demo"):
        if level in readable:
            return level
    return "live"


# ── Treasury / reserves ──────────────────────────────────────────────────────


async def operator_treasury(slug: str) -> dict:
    """Current holdings across an operator's attributed clusters.

    Distinct from flow: flow is movement over a window, treasury is the balance
    right now. Native balance alone badly understates it — operators hold most
    reserves in stablecoins — so this reads ERC20 balances too.

    This is emphatically NOT a solvency statement. We see the clusters we have
    labeled, on the chains we cover. Liabilities to players are off-chain and
    invisible, and reserves may sit in wallets we have not attributed.
    """
    from .onchain import native_balance, token_balances

    operators = [c for c in attributed_operators() if c.slug == slug]
    if not operators:
        return {"slug": slug, "error": "operator not attributed", "holdings": []}
    casino = operators[0]
    targets = observation_targets(casino)

    async def per_wallet(w) -> tuple[str, float, list, str]:
        (native, nsrc), (tokens, tsrc) = await asyncio.gather(
            native_balance(w.address, w.chain),
            token_balances(w.address, w.chain),
        )
        return w.address, native, tokens, _worst([nsrc, tsrc])

    results = await asyncio.gather(*(per_wallet(w) for w in targets)) if targets else []

    # Price every distinct symbol in one call.
    symbols = {t.symbol for _, _, toks, _ in results for t in toks}
    native_syms = {NATIVE_SYMBOL.get(w.chain, "ETH") for w in targets}
    prices = await resolve_prices(symbols | native_syms)

    by_symbol: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "usd": 0.0})
    per_wallet_rows = []

    for (addr, native, tokens, src), wallet in zip(results, targets):
        nsym = NATIVE_SYMBOL.get(wallet.chain, "ETH")
        wallet_usd = 0.0

        if native > 0:
            usd = native * prices.get(nsym, 0.0)
            by_symbol[nsym]["amount"] += native
            by_symbol[nsym]["usd"] += usd
            wallet_usd += usd

        for t in tokens:
            usd = t.amount * prices.get(t.symbol, 0.0)
            by_symbol[t.symbol]["amount"] += t.amount
            by_symbol[t.symbol]["usd"] += usd
            wallet_usd += usd

        per_wallet_rows.append(
            {
                "address": addr,
                "chain": wallet.chain,
                "role": wallet.role,
                "native_amount": round(native, 6),
                "token_count": len(tokens),
                "total_usd": round(wallet_usd, 2),
                "data_source": src,
            }
        )

    total_usd = sum(v["usd"] for v in by_symbol.values())
    holdings = sorted(
        (
            {
                "symbol": sym,
                "amount": round(v["amount"], 6),
                "usd_value": round(v["usd"], 2),
                "share_pct": round(v["usd"] / total_usd * 100, 2) if total_usd else 0.0,
                "is_stablecoin": sym in STABLE_SYMBOLS,
            }
            for sym, v in by_symbol.items()
            if v["usd"] > 0
        ),
        key=lambda h: -h["usd_value"],
    )
    stable_usd = sum(h["usd_value"] for h in holdings if h["is_stablecoin"])

    return {
        "slug": casino.slug,
        "name": casino.name,
        "total_usd": round(total_usd, 2),
        "stablecoin_usd": round(stable_usd, 2),
        "stablecoin_share_pct": (
            round(stable_usd / total_usd * 100, 2) if total_usd else 0.0
        ),
        "distinct_assets": len(holdings),
        "holdings": holdings,
        "wallets": per_wallet_rows,
        "clusters_read": len(casino.wallets),
        "chains": sorted({row["chain"] for row in per_wallet_rows if row["total_usd"] > 0}),
        "chains_queried": casino.queried_chains,
        "data_source": _worst([r["data_source"] for r in per_wallet_rows]),
        "caveat": (
            "Balances of attributed clusters on covered chains only. This is not a "
            "solvency statement: player liabilities are off-chain and invisible, and "
            "reserves may sit in wallets that have not been attributed."
        ),
    }


async def treasury_ranking() -> dict:
    """All attributed operators ranked by observed on-chain reserves."""
    operators = attributed_operators()
    results = await asyncio.gather(*(operator_treasury(o.slug) for o in operators))
    rows = sorted(
        (r for r in results if not r.get("error")),
        key=lambda r: -r["total_usd"],
    )
    total = sum(r["total_usd"] for r in rows) or 1.0
    return {
        "operators": [
            {
                "rank": i + 1,
                "slug": r["slug"],
                "name": r["name"],
                "total_usd": r["total_usd"],
                "stablecoin_share_pct": r["stablecoin_share_pct"],
                "distinct_assets": r["distinct_assets"],
                "share_of_observed_reserves_pct": round(r["total_usd"] / total * 100, 2),
            }
            for i, r in enumerate(rows)
        ],
        "total_observed_usd": round(total if rows else 0.0, 2),
        "operators_read": len(rows),
        "data_source": _worst([r["data_source"] for r in rows]),
        "caveat": (
            "Reserves observed across attributed clusters only. Shares are shares of "
            "observed reserves, not of the market. Not a solvency comparison."
        ),
    }
