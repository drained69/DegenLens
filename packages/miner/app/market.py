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
import time
from contextlib import contextmanager
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .onchain import (
    NATIVE_SYMBOL,
    background_reads,
    Transfer,
    TransferSet,
    get_observation_transfers,
    merge_cluster_reads,
)
from .prices import resolve_prices
from .settings import settings
from .wallets import INDEXED_CHAINS, Casino, attributed_operators, catalog, observation_targets

STABLE_SYMBOLS = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDP", "FRAX"}

# Provenance marker for an operator whose clusters were not read within the
# collection budget. Like `unsupported_chain` this is a coverage gap, not an
# outage and not a fabrication — see `_worst`.
UNREAD_SOURCE = "unread"


# ── Shared collection ────────────────────────────────────────────────────────


@dataclass
class OperatorFlow:
    """Every observed transfer for one operator, with pricing applied."""

    casino: Casino
    transfers: list[Transfer]
    prices: dict[str, float]
    data_source: str
    complete: bool
    # Set when the operator could not be read; None on a successful collection.
    degraded_reason: str | None = None

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
    # Budgeted like the registry-wide fan-out. One operator can claim a wallet
    # on every indexed chain, so a single cluster read is already big enough to
    # outlive a request deadline on its own; the per-operator endpoints were
    # tripping the deadline while the market-wide ones were safe.
    read = await gather_within_budget([
        get_observation_transfers(
            w.address,
            w.chain,
            hours,
            seed=(w.address.lower(), w.chain) in seed_pairs,
        )
        for w in targets
    ])
    # A wallet that missed the budget is an unavailable read, not a quiet one,
    # so merge_cluster_reads marks the cluster incomplete.
    sets: list[TransferSet] = [
        r if r is not None
        else TransferSet([], "unavailable", "read exceeded the collection budget",
                         complete=False)
        for r in read
    ]
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


def unread_flow(casino: Casino, reason: str) -> OperatorFlow:
    """A placeholder for an operator whose clusters were never read.

    Rule 1 applies: unobserved is not zero. This carries no transfers, so it
    contributes nothing to any total, and `complete=False` propagates a
    coverage gap through `coverage_complete` into the confidence discount.
    """
    return OperatorFlow(
        casino=casino,
        transfers=[],
        prices={},
        data_source=UNREAD_SOURCE,
        complete=False,
        degraded_reason=reason,
    )


async def collect_flows(operators: list[Casino], hours: int) -> list[OperatorFlow]:
    """Collect operator flows under a wall-clock budget.

    A registry-wide view fans out to one paged, bidirectional read per
    wallet/chain identity — tens of upstream calls, each of which may retry.
    Left unbounded that reliably outlived the service deadline, so every
    market-wide endpoint returned nothing at all.

    The budget makes the failure mode partial instead of total: operators that
    answer in time are reported, operators that do not are marked unread. A
    smaller observed set is still a truthful statement about observed flow,
    which is all these endpoints ever claimed to be. Returning a subset is only
    honest because callers surface `coverage_complete` and the unread count —
    do not drop those fields.
    """
    if not operators:
        return []

    # Built per call: a module-level semaphore would bind to whichever event
    # loop happened to touch it first.
    operator_sem = asyncio.Semaphore(settings.max_operator_concurrency)

    async def collect(operator: Casino) -> OperatorFlow:
        async with operator_sem:
            return await collect_flow(operator, hours)

    budget = remaining_budget()
    tasks = [asyncio.create_task(collect(operator)) for operator in operators]
    done, pending = await asyncio.wait(tasks, timeout=budget)

    for task in pending:
        task.cancel()
    if pending:
        # Let cancellation settle so provider connections close cleanly.
        await asyncio.gather(*pending, return_exceptions=True)

    flows: list[OperatorFlow] = []
    for operator, task in zip(operators, tasks):
        if task not in done:
            flows.append(unread_flow(
                operator,
                f"cluster read exceeded the {budget:g}s collection budget",
            ))
            continue
        exc = task.exception()
        if exc is not None:
            # An upstream failure for one operator must not fail the view.
            flows.append(unread_flow(operator, f"cluster read failed: {type(exc).__name__}"))
            continue
        flows.append(task.result())

    return flows


async def gather_within_budget(
    coros: list, *, budget: float | None = None
) -> list:
    """Await many independent reads, dropping whatever misses the deadline.

    Returns a result per input, or None where the read did not finish or raised.
    A None is a coverage gap the caller must report — never a zero.

    Shared with `collect_flows` so every registry-wide fan-out degrades the same
    way: partial and labelled, rather than blowing the request deadline.
    """
    if not coros:
        return []
    if budget is None:
        budget = remaining_budget()

    tasks = [asyncio.ensure_future(c) for c in coros]
    done, pending = await asyncio.wait(tasks, timeout=budget)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    results: list = []
    for task in tasks:
        if task not in done or task.exception() is not None:
            results.append(None)
        else:
            results.append(task.result())
    return results


# ── Aggregate cache ──────────────────────────────────────────────────────────
#
# A registry-wide window is on the order of 190k transfer records, fetched over
# ~130 provider calls that the provider itself rate-limits well below the
# parallelism needed to make them fit an HTTP deadline. Measured cold, one such
# read takes over two minutes. No timeout tuning changes that, and the raw
# records are far too large to keep resident.
#
# What is small is the ANSWER: a few dozen rows of sums. So the expensive read
# happens off the request path and only the compact aggregate is cached. A
# request serves the cached answer, or — on a cold cache — an honest empty one
# while the rebuild runs. This is the same shape as `casino_stats`, which backs
# a bounded read with `_refresh_full_stats`.

# An ABSOLUTE deadline, not a duration. One request can run several bounded
# fan-outs back to back (discovery reads the cluster, then profiles each
# candidate); giving each its own fresh duration let a request quietly spend a
# multiple of the budget and trip the service deadline. Sharing one deadline
# means the whole build is bounded, however many phases it has.
_deadline_at: ContextVar[float | None] = ContextVar("flow_deadline_at", default=None)


@contextmanager
def flow_budget(seconds: float, *, inherit: bool = True) -> Iterator[None]:
    """Bound everything read inside this block to `seconds` in total.

    `inherit` (the default) clamps to any deadline already in force, so a
    nested build can only ever shorten the budget, never extend it. Aggregates
    do nest — the all-operator discovery view is built from the per-operator
    one — and without the clamp each level granted itself a fresh full budget,
    multiplying the real cost by the number of operators.

    Detached background work passes `inherit=False`: it starts from a request's
    context (and therefore its deadline) but is no longer bound by it.
    """
    deadline = time.monotonic() + seconds
    if inherit:
        existing = _deadline_at.get()
        if existing is not None:
            deadline = min(deadline, existing)
    token = _deadline_at.set(deadline)
    try:
        yield
    finally:
        _deadline_at.reset(token)


def remaining_budget() -> float:
    """Seconds left in the active budget, or the default when none is set."""
    at = _deadline_at.get()
    if at is None:
        return settings.flow_budget_s
    return max(0.0, at - time.monotonic())

_AGGREGATE_CACHE: dict[tuple, tuple[dict, float]] = {}
_AGGREGATE_TASKS: dict[tuple, asyncio.Task] = {}
# Keys whose inline build recently came back empty; see _inline_is_futile.
_INLINE_FUTILE_UNTIL: dict[tuple, float] = {}
_AGGREGATE_MAX_ENTRIES = 256


def _usable(result: dict) -> bool:
    """Whether an aggregate said anything about observed flow."""
    return result.get("data_source") not in (None, "unavailable")


async def cached_aggregate(key: tuple, build: Callable[[], Awaitable[dict]]) -> dict:
    """Serve a flow aggregate, rebuilding it off the request path when stale.

    The inline attempt is kept: a narrow window over a small operator set does
    fit the budget, and there is no reason to make those callers wait for a
    background pass. Only when the inline attempt comes back with nothing do we
    fall back to the cache and queue a rebuild.
    """
    cached = _AGGREGATE_CACHE.get(key)
    if cached and cached[1] > time.monotonic():
        return cached[0]

    if key in _AGGREGATE_TASKS or _inline_is_futile(key):
        # Either a rebuild is already reading this window, or a recent inline
        # attempt proved this key too expensive to build on the request path.
        # Racing it again would burn the entire request budget to reach the
        # same empty answer, so return immediately and let the rebuild land.
        _schedule_rebuild(key, build)
        return _pending(await _empty_build(build), cached)

    try:
        # Two caps, deliberately. The inner budget bounds the fan-outs; the
        # outer one bounds the WHOLE build, including work between them (price
        # lookups, scoring) that no per-gather budget can see. Without the outer
        # cap a build stayed just over its budget and crept toward the service
        # deadline.
        budget = min(settings.flow_budget_s, remaining_budget())
        with flow_budget(budget):
            result = await asyncio.wait_for(
                build(), timeout=budget + settings.flow_overhead_s
            )
    except (TimeoutError, asyncio.TimeoutError):
        _schedule_rebuild(key, build)
        return _pending(await _empty_build(build), cached)
    if result.get("error"):
        # A bad request (unknown slug) is terminal, not a cold cache. Rebuilding
        # it would loop forever against an input that can never succeed.
        return result
    if _usable(result):
        _store_aggregate(key, result)
        return result

    # Nothing readable within the request budget. Prefer a stale answer that
    # says so over an empty one, and queue a rebuild for the next caller.
    _INLINE_FUTILE_UNTIL[key] = time.monotonic() + settings.inline_retry_cooldown_s
    _schedule_rebuild(key, build)
    return _pending(result, cached)


def _inline_is_futile(key: tuple) -> bool:
    """Whether an inline build of this key recently failed to produce anything.

    A registry-wide window that could not be read in one budget will not become
    readable seconds later. Without this, every caller paid the full budget to
    rediscover that — turning a cold cache into a fixed multi-second tax on
    every request until a rebuild finished.
    """
    until = _INLINE_FUTILE_UNTIL.get(key)
    if until is None:
        return False
    if until <= time.monotonic():
        _INLINE_FUTILE_UNTIL.pop(key, None)
        return False
    return True


async def _empty_build(build: Callable[[], Awaitable[dict]]) -> dict:
    """Build the aggregate's own empty shape without reading anything.

    A zero budget makes `collect_flows` return every operator unread, so the
    aggregate produces its normal structure with nothing in it. That keeps the
    response contract identical whether or not a read happened.
    """
    with flow_budget(0.0):
        return await build()


def _pending(empty: dict, cached: tuple[dict, float] | None) -> dict:
    """The answer while a rebuild is in flight: stale if we have it, else empty."""
    if cached:
        return {
            **cached[0],
            "coverage_complete": False,
            "stale": True,
            "caveat": (
                "Served from the last completed read; a refresh is in progress. "
                + str(cached[0].get("caveat", ""))
            ).strip(),
        }
    return {
        **empty,
        "pending_first_read": True,
        "caveat": (
            "No completed registry-wide read is cached yet; one is in progress. "
            "Retry shortly."
        ),
    }


def _store_aggregate(key: tuple, result: dict) -> None:
    _AGGREGATE_CACHE[key] = (result, time.monotonic() + settings.stats_ttl)
    _INLINE_FUTILE_UNTIL.pop(key, None)
    if len(_AGGREGATE_CACHE) > _AGGREGATE_MAX_ENTRIES:
        for stale in sorted(_AGGREGATE_CACHE, key=lambda k: _AGGREGATE_CACHE[k][1])[:64]:
            _AGGREGATE_CACHE.pop(stale, None)


def _schedule_rebuild(key: tuple, build: Callable[[], Awaitable[dict]]) -> None:
    """Run one rebuild per key at a time, with no request deadline over it."""
    if not settings.live_data_available or key in _AGGREGATE_TASKS:
        return
    if len(_AGGREGATE_TASKS) >= _MAX_REBUILD_TASKS:
        return

    async def rebuild() -> None:
        try:
            with flow_budget(settings.flow_warm_timeout_s, inherit=False), background_reads():
                result = await asyncio.wait_for(
                    build(), timeout=settings.flow_warm_timeout_s
                )
            if _usable(result):
                _store_aggregate(key, result)
        except Exception:  # noqa: BLE001 — nothing awaits this; never surface
            pass

    task = asyncio.create_task(rebuild())
    _AGGREGATE_TASKS[key] = task
    task.add_done_callback(lambda _, k=key: _AGGREGATE_TASKS.pop(k, None))


# One registry-wide rebuild at a time. Each one already saturates the shared
# upstream budget, so running several in parallel does not finish any of them
# sooner — it just starves the single-operator endpoints that still read live.
_MAX_REBUILD_TASKS = 1


def coverage_fields(flows: list[OperatorFlow]) -> dict:
    """Provenance and coverage for a set of collected flows.

    Every aggregate endpoint reports these together. `operators_unread` is what
    keeps a budget-truncated answer honest: without it a reader cannot tell a
    quiet registry from one we ran out of time to read.
    """
    unread = [f for f in flows if f.data_source == UNREAD_SOURCE]
    return {
        "data_source": _worst([f.data_source for f in flows]),
        "coverage_complete": all(f.complete for f in flows),
        "operators_read": len(flows) - len(unread),
        "operators_unread": len(unread),
        "unread_operators": sorted(f.casino.slug for f in unread),
    }


async def prime_aggregates() -> None:
    """Populate the aggregate cache for the windows the miner advertises.

    Without this the first caller after boot gets `pending_first_read` for the
    two minutes the first registry-wide read takes. Priming is sequential on
    purpose: these reads all contend for the same provider budget, so running
    them together makes every one of them slower.
    """
    if not settings.live_data_available:
        return
    for key, build in _default_aggregates():
        # A fresh budget per window: they are primed one after another, so they
        # must not share a single deadline.
        try:
            with flow_budget(settings.flow_warm_timeout_s, inherit=False), background_reads():
                result = await asyncio.wait_for(
                    build(), timeout=settings.flow_warm_timeout_s
                )
        except Exception:  # noqa: BLE001 — priming must never block startup
            continue
        if _usable(result):
            _store_aggregate(key, result)


def _default_aggregates() -> list[tuple[tuple, Callable[[], Awaitable[dict]]]]:
    """The exact keys the default-parameter endpoint calls will look up."""
    from .players import _build_player_leaderboard

    return [
        (("networks", 168), lambda: _build_network_distribution(168)),
        (("assets", None, 168), lambda: _build_asset_mix(None, 168)),
        (
            ("large_transfers", 24, 100_000.0, 50),
            lambda: _build_large_transfers(24, 100_000.0, 50),
        ),
        (
            ("leaderboard", 168, 25, True, None),
            lambda: _build_player_leaderboard(168, 25, True, None),
        ),
    ]


# ── Network distribution ─────────────────────────────────────────────────────


async def _build_network_distribution(hours: int) -> dict:
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
        **coverage_fields(flows),
    }


# ── Asset / token mix ────────────────────────────────────────────────────────


async def _build_asset_mix(slug: str | None, hours: int) -> dict:
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
        **coverage_fields(flows),
    }


async def network_distribution(hours: int = 168) -> dict:
    """Observed flow split by chain, served from the aggregate cache."""
    return await cached_aggregate(("networks", hours), lambda: _build_network_distribution(hours))


async def asset_mix(slug: str | None = None, hours: int = 168) -> dict:
    """Asset composition of observed flow, served from the aggregate cache."""
    return await cached_aggregate(
        ("assets", slug, hours), lambda: _build_asset_mix(slug, hours)
    )


async def large_transfers(
    hours: int = 24, min_usd: float = 100_000, limit: int = 50
) -> dict:
    """Transfers above a USD threshold, served from the aggregate cache."""
    return await cached_aggregate(
        ("large_transfers", hours, min_usd, limit),
        lambda: _build_large_transfers(hours, min_usd, limit),
    )


# ── Flow time series ─────────────────────────────────────────────────────────


async def flow_series(slug: str, hours: int = 168, bucket_hours: int = 1) -> dict:
    """Bucketed inbound/outbound series for one operator, served from cache."""
    return await cached_aggregate(
        ("series", slug, hours, bucket_hours),
        lambda: _build_flow_series(slug, hours, bucket_hours),
    )


async def _build_flow_series(slug: str, hours: int = 168, bucket_hours: int = 1) -> dict:
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


async def _build_large_transfers(
    hours: int, min_usd: float, limit: int
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
        **coverage_fields(flows),
    }


# ── Counterparty concentration ───────────────────────────────────────────────


async def counterparty_concentration(slug: str, hours: int = 168, top: int = 20) -> dict:
    """Counterparty concentration for one operator, served from cache."""
    return await cached_aggregate(
        ("counterparties", slug, hours, top),
        lambda: _build_counterparty_concentration(slug, hours, top),
    )


async def _build_counterparty_concentration(
    slug: str, hours: int = 168, top: int = 20
) -> dict:
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


# Sources that mean "we did not look here", as opposed to "we looked and the
# provider failed". Both are coverage gaps, neither is an outage.
_COVERAGE_GAP_SOURCES = {"unsupported_chain", UNREAD_SOURCE}


def _worst(sources: list[str]) -> str:
    """Provenance downgrade order.

    `unsupported_chain` and `unread` sit between `unavailable` and `demo` —
    they are honest coverage gaps (a non-EVM registry entry, or an operator
    dropped at the collection budget), not an outage or a fabrication. Filter
    them out before ranking; if they are the ONLY sources (nothing readable
    exists), degrade to `unavailable`.

    The gap is never silently discarded: every caller pairs this with
    `coverage_complete`, which is False whenever any flow was incomplete.
    """
    if not sources:
        return "unavailable"
    readable = [s for s in sources if s not in _COVERAGE_GAP_SOURCES]
    if not readable:
        return "unavailable"
    for level in ("unavailable", "demo"):
        if level in readable:
            return level
    return "live"


# ── Treasury / reserves ──────────────────────────────────────────────────────


async def operator_treasury(slug: str) -> dict:
    """Current holdings across an operator's attributed clusters, from cache."""
    return await cached_aggregate(("treasury", slug), lambda: _build_operator_treasury(slug))


async def _build_operator_treasury(slug: str) -> dict:
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
                "label": wallet.label,
                "native_amount": round(native, 6),
                "token_count": len(tokens),
                "total_usd": round(wallet_usd, 2),
                "data_source": src,
            }
        )

    total_usd = sum(v["usd"] for v in by_symbol.values())
    chain_sources: dict[str, list[str]] = defaultdict(list)
    for row in per_wallet_rows:
        chain_sources[row["chain"]].append(row["data_source"])
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
        "chains_complete": sorted(
            chain for chain in casino.queried_chains
            if chain_sources.get(chain)
            and all(source in {"live", "demo"} for source in chain_sources[chain])
        ),
        "coverage_complete": all(
            chain_sources.get(chain)
            and all(source in {"live", "demo"} for source in chain_sources[chain])
            for chain in casino.queried_chains
        ),
        "data_source": _worst([r["data_source"] for r in per_wallet_rows]),
        "caveat": (
            "Balances of attributed clusters on covered chains only. This is not a "
            "solvency statement: player liabilities are off-chain and invisible, and "
            "reserves may sit in wallets that have not been attributed."
        ),
    }


async def treasury_ranking() -> dict:
    """All attributed operators ranked by observed on-chain reserves."""
    return await cached_aggregate(("treasury_ranking",), _build_treasury_ranking)


async def _build_treasury_ranking() -> dict:
    operators = attributed_operators()
    # One balance read per wallet per operator: the same registry-wide shape as
    # the flow aggregates, and bounded the same way.
    results = await gather_within_budget(
        [operator_treasury(o.slug) for o in operators]
    )
    unread = [o.slug for o, r in zip(operators, results) if r is None]
    rows = sorted(
        (r for r in results if r is not None and not r.get("error")),
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
        "operators_unread": len(unread),
        "unread_operators": sorted(unread),
        "coverage_complete": not unread,
        "data_source": _worst([r["data_source"] for r in rows]),
        "caveat": (
            "Reserves observed across attributed clusters only. Shares are shares of "
            "observed reserves, not of the market. Not a solvency comparison."
            + (
                f" {len(unread)} operator(s) were not read in time and are absent "
                f"from the ranking rather than ranked at zero." if unread else ""
            )
        ),
    }
