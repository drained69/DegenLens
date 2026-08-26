"""USD price normalization with batched lookups and a TTL cache.

The naive shape — `await get_price(sym)` inside a per-transfer loop — issues
hundreds of sequential round-trips for a single aggregate. Latency is scored,
so we resolve the *set* of symbols once per request instead.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

import httpx

from . import metrics
from .onchain import upstream_call_timeout
from .settings import settings

_cache: dict[str, tuple[float, float]] = {}  # symbol -> (price_usd, expiry_monotonic)
_inflight: dict[str, asyncio.Future] = {}

STABLECOINS = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "FDUSD", "FRAX"}

_COINGECKO_IDS = {
    "eth": "ethereum",
    "weth": "ethereum",
    "btc": "bitcoin",
    "wbtc": "wrapped-bitcoin",
    "sol": "solana",
    "matic": "matic-network",
    "pol": "matic-network",
    "bnb": "binancecoin",
    "wbnb": "binancecoin",
    "avax": "avalanche-2",
    "wavax": "avalanche-2",
    "trx": "tron",
    "arb": "arbitrum",
    "op": "optimism",
    "link": "chainlink",
}


async def _fetch_batch(coingecko_ids: list[str]) -> dict[str, float]:
    if not coingecko_ids:
        return {}
    params: dict[str, str] = {
        "ids": ",".join(sorted(set(coingecko_ids))),
        "vs_currencies": "usd",
    }
    if settings.coingecko_key:
        params["x_cg_pro_api_key"] = settings.coingecko_key
    try:
        # Bounded by whatever the request has left, not a fixed upstream budget.
        # CoinGecko's free tier rate-limits hard, and a price lookup that
        # outlives the deadline costs the whole answer to decorate figures the
        # caller will never receive.
        async with httpx.AsyncClient(timeout=upstream_call_timeout()) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/simple/price", params=params
            )
            r.raise_for_status()
            body = r.json()
    except Exception:  # noqa: BLE001 - price failure must not fail the request
        metrics.record_upstream(failed=True)
        return {}
    metrics.record_upstream(failed=False)
    return {k: float(v.get("usd", 0.0)) for k, v in body.items() if isinstance(v, dict)}


async def resolve_prices(symbols: Iterable[str]) -> dict[str, float]:
    """Resolve a set of token symbols to USD prices in ONE upstream call.

    Stablecoins short-circuit to 1.0. Unknown symbols resolve to 0.0 and are
    excluded from USD aggregates by the caller rather than guessed at.
    """
    wanted = {s.upper() for s in symbols if s}
    out: dict[str, float] = {}
    to_fetch: dict[str, str] = {}  # coingecko_id -> symbol

    now = time.monotonic()
    for sym in wanted:
        if sym in STABLECOINS:
            out[sym] = 1.0
            continue
        cached = _cache.get(sym)
        if cached and cached[1] > now:
            out[sym] = cached[0]
            continue
        cg_id = _COINGECKO_IDS.get(sym.lower())
        if cg_id:
            to_fetch[cg_id] = sym
        else:
            out[sym] = 0.0  # unknown symbol — caller skips it

    if to_fetch:
        fetched = await _fetch_batch(list(to_fetch))
        expiry = time.monotonic() + settings.price_ttl
        for cg_id, sym in to_fetch.items():
            price = fetched.get(cg_id, 0.0)
            out[sym] = price
            if price > 0:
                _cache[sym] = (price, expiry)

    return out


async def get_price(symbol: str) -> float:
    """Single-symbol convenience wrapper. Prefer `resolve_prices` in loops."""
    prices = await resolve_prices([symbol])
    return prices.get(symbol.upper(), 0.0)
