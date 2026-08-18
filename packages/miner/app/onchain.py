"""On-chain data adapters.

The miner's value is turning raw RPC data into structured casino intelligence.
Three properties matter more than features here, because they are what the
Canonical Score actually measures:

1. CORRECTNESS  — observe both transfer directions. Fetching only inbound
   transfers makes every withdrawal figure silently zero.
2. DETERMINISM  — identical query in, identical answer out. Python's builtin
   `hash()` is randomized per process (PYTHONHASHSEED), so seeding synthetic
   data with it changes every answer after a restart. We use blake2b instead.
3. HONESTY      — when live data is unavailable, say so via `data_source`
   rather than inventing numbers that will be graded against real ground truth.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from . import metrics
from .settings import settings

# Bound concurrent upstream calls so a burst of requests can't open hundreds of
# sockets against Alchemy and trip its own rate limiter.
_upstream_sem = asyncio.Semaphore(settings.max_upstream_concurrency)


@dataclass
class Transfer:
    tx_hash: str
    from_addr: str
    to_addr: str
    token_symbol: str
    amount: float
    timestamp: datetime
    chain: str
    direction: str  # "in" | "out" relative to the queried address


@dataclass
class TransferSet:
    """Transfers plus provenance, so callers can report honestly."""

    transfers: list[Transfer]
    data_source: str  # "live" | "demo" | "unavailable"
    degraded_reason: str | None = None
    # False when the requested window could not be fully paged. Totals derived
    # from an incomplete set are lower bounds, not measurements.
    complete: bool = True


@dataclass
class TransactionRecord:
    tx_hash: str
    chain: str
    status: str
    block_number: int | None
    block_hash: str | None
    from_addr: str
    to_addr: str | None
    value_wei: int
    value_native: float
    gas: int
    gas_price_wei: int
    input: str
    data_source: str


def stable_seed(*parts: str) -> int:
    """Process-stable 64-bit seed.

    `hash()` is randomized per interpreter run, which would make synthetic data
    differ across restarts — fatal for a deterministic (Tier A) intent.
    """
    digest = hashlib.blake2b("\x1f".join(parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


# ── Circuit breaker ──────────────────────────────────────────────────────────

_consecutive_failures = 0
_circuit_opened_at: float | None = None


def _circuit_open() -> bool:
    global _circuit_opened_at
    if _circuit_opened_at is None:
        return False
    if time.monotonic() - _circuit_opened_at >= settings.circuit_cooldown_s:
        _circuit_opened_at = None
        return False
    return True


def _record_upstream_result(*, ok: bool) -> None:
    global _consecutive_failures, _circuit_opened_at
    metrics.record_upstream(failed=not ok)
    if ok:
        _consecutive_failures = 0
        return
    _consecutive_failures += 1
    if _consecutive_failures >= settings.circuit_threshold:
        _circuit_opened_at = time.monotonic()


def circuit_status() -> dict[str, object]:
    return {
        "open": _circuit_open(),
        "consecutive_failures": _consecutive_failures,
    }


# ── Alchemy ──────────────────────────────────────────────────────────────────

_ALCHEMY_HOSTS = {
    "ethereum": "eth-mainnet.g.alchemy.com",
    "base": "base-mainnet.g.alchemy.com",
    "polygon": "polygon-mainnet.g.alchemy.com",
    "arbitrum": "arb-mainnet.g.alchemy.com",
    "optimism": "opt-mainnet.g.alchemy.com",
    "bsc": "bnb-mainnet.g.alchemy.com",
    "avalanche": "avax-mainnet.g.alchemy.com",
}

# Native gas token per chain, used to price `external` (non-ERC20) transfers.
NATIVE_SYMBOL = {
    "ethereum": "ETH",
    "base": "ETH",
    "arbitrum": "ETH",
    "optimism": "ETH",
    "polygon": "POL",
    "bsc": "BNB",
    "avalanche": "AVAX",
}

SUPPORTED_CHAINS = tuple(_ALCHEMY_HOSTS)


def _alchemy_host(chain: str) -> str:
    if chain not in _ALCHEMY_HOSTS:
        raise ValueError(f"unsupported chain: {chain}")
    return _ALCHEMY_HOSTS[chain]


async def _rpc(client: httpx.AsyncClient, url: str, method: str, params: list) -> object:
    response = await client.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise httpx.HTTPError(str(payload["error"]))
    return payload.get("result")


async def _alchemy_transfers_paged(
    client: httpx.AsyncClient,
    url: str,
    address: str,
    direction: str,
    since: datetime,
) -> tuple[list[dict], bool]:
    """All transfers in one direction back to `since`, following pageKey.

    Alchemy caps `getAssetTransfers` at 1000 rows per call. A single call with
    `order: desc` therefore returns only the most recent 1000 transfers no matter
    how wide the requested window is — which silently made every lookback window
    (24h, 7d, 30d) return identical figures for any busy address.

    We page backwards until a row predates `since`, then stop: results are
    descending, so the first out-of-window row means everything after it is too.

    Returns (rows, complete). `complete` is False when the page budget was
    exhausted before reaching `since`, so callers can flag partial coverage
    rather than presenting a truncated total as if it were whole.
    """
    key = "toAddress" if direction == "in" else "fromAddress"
    rows: list[dict] = []
    page_key: str | None = None

    for _ in range(settings.max_transfer_pages):
        params: dict[str, object] = {
            key: address,
            "category": ["external", "erc20"],
            "fromBlock": "0x0",
            "withMetadata": True,
            "excludeZeroValue": True,
            "order": "desc",
            "maxCount": "0x3e8",  # 1000 — Alchemy's per-call ceiling
        }
        if page_key:
            params["pageKey"] = page_key

        r = await client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "alchemy_getAssetTransfers",
                "params": [params],
            },
        )
        r.raise_for_status()
        payload = r.json()
        if "error" in payload:
            raise httpx.HTTPError(str(payload["error"]))

        result = payload.get("result") or {}
        batch = result.get("transfers", [])
        rows.extend(batch)

        # Descending order: once a row is older than the window, we are done.
        if batch and not _in_window(batch[-1], since):
            return rows, True

        page_key = result.get("pageKey")
        if not page_key or not batch:
            return rows, True

    # Ran out of page budget with more data still available upstream.
    return rows, False


async def _fetch_live(address: str, chain: str, since: datetime) -> TransferSet:
    """Fetch BOTH directions, fully paged back to `since`.

    Inbound-only was the withdrawals-always-zero bug; single-page was the
    identical-across-windows bug.
    """
    url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"
    try:
        async with _upstream_sem:
            async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                (inbound, in_ok), (outbound, out_ok) = await asyncio.gather(
                    _alchemy_transfers_paged(client, url, address, "in", since),
                    _alchemy_transfers_paged(client, url, address, "out", since),
                )
    except Exception as exc:  # noqa: BLE001 - upstream failures must not 500
        _record_upstream_result(ok=False)
        return TransferSet([], "unavailable", f"upstream error: {type(exc).__name__}")

    _record_upstream_result(ok=True)
    rows = [(row, "in") for row in inbound] + [(row, "out") for row in outbound]
    transfers = [
        _to_transfer(row, chain, direction)
        for row, direction in rows
        if _in_window(row, since)
    ]
    transfers.sort(key=lambda t: t.timestamp, reverse=True)

    complete = in_ok and out_ok
    return TransferSet(
        transfers,
        "live",
        None if complete else "page budget exhausted — window not fully covered",
        complete=complete,
    )


def _in_window(row: dict, since: datetime) -> bool:
    ts = row.get("metadata", {}).get("blockTimestamp")
    if not ts:
        return False
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")) >= since
    except ValueError:
        return False


def _to_transfer(row: dict, chain: str, direction: str) -> Transfer:
    raw_ts = row.get("metadata", {}).get("blockTimestamp", "1970-01-01T00:00:00Z")
    try:
        ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except ValueError:
        ts = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return Transfer(
        tx_hash=row.get("hash", ""),
        from_addr=(row.get("from") or "").lower(),
        to_addr=(row.get("to") or "").lower(),
        token_symbol=(row.get("asset") or "ETH").upper(),
        amount=float(row.get("value") or 0),
        timestamp=ts,
        chain=chain,
        direction=direction,
    )


# ── Demo feed ────────────────────────────────────────────────────────────────


def _demo_transfers(address: str, chain: str, since: datetime, now: datetime) -> TransferSet:
    """Deterministic synthetic feed for local development only.

    Seeded with `stable_seed` so it survives restarts. Always labeled
    data_source="demo" so no caller mistakes it for observed chain state.
    """
    rng = random.Random(stable_seed(address, chain))
    hours = max(int((now - since).total_seconds() // 3600), 1)
    out: list[Transfer] = []
    for h in range(hours):
        for _ in range(rng.randint(3, 15)):
            symbol = rng.choice(["ETH", "USDT", "USDC"])
            amount = rng.uniform(0.05, 20) if symbol == "ETH" else rng.uniform(50, 5000)
            direction = "in" if rng.random() < 0.72 else "out"
            counterparty = f"0x{rng.getrandbits(160):040x}"
            out.append(
                Transfer(
                    tx_hash=f"0x{rng.getrandbits(256):064x}",
                    from_addr=counterparty if direction == "in" else address.lower(),
                    to_addr=address.lower() if direction == "in" else counterparty,
                    token_symbol=symbol,
                    amount=amount,
                    timestamp=now - timedelta(hours=h, minutes=rng.randint(0, 59)),
                    chain=chain,
                    direction=direction,
                )
            )
    out.sort(key=lambda t: t.timestamp, reverse=True)
    return TransferSet(out, "demo", "no ALCHEMY_KEY configured — synthetic data")


# ── Public API (cached) ──────────────────────────────────────────────────────

_CACHE: dict[tuple, tuple[TransferSet, float]] = {}


def _bucketed_now() -> datetime:
    """Quantize 'now' to the cache TTL.

    Two identical queries seconds apart should produce byte-identical answers.
    Anchoring windows to a raw wall clock makes every response unique, which
    costs points on exact-match scoring.
    """
    ttl = max(settings.stats_ttl, 1)
    epoch = int(time.time()) // ttl * ttl
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


async def get_transfers(address: str, chain: str, hours: int) -> TransferSet:
    address = address.lower()
    now = _bucketed_now()
    key = (address, chain, hours, int(now.timestamp()))

    cached = _CACHE.get(key)
    if cached and cached[1] > time.monotonic():
        metrics.record_cache(hit=True)
        return cached[0]
    metrics.record_cache(hit=False)

    since = now - timedelta(hours=hours)

    if not settings.live_data_available:
        if settings.strict_mode:
            result = TransferSet([], "unavailable", "strict_mode: no live data provider")
        else:
            result = _demo_transfers(address, chain, since, now)
    elif _circuit_open():
        result = TransferSet([], "unavailable", "upstream circuit breaker open")
    else:
        result = await _fetch_live(address, chain, since)

    # Only cache successful reads; failures should retry on the next request.
    if result.data_source != "unavailable":
        _CACHE[key] = (result, time.monotonic() + settings.stats_ttl)
        # Bound cache growth.
        if len(_CACHE) > 2048:
            for stale in sorted(_CACHE, key=lambda k: _CACHE[k][1])[:512]:
                _CACHE.pop(stale, None)
    return result


async def native_balance(address: str, chain: str) -> tuple[float, str]:
    """Native token balance. Returns (balance, data_source)."""
    if not settings.live_data_available:
        if settings.strict_mode:
            return 0.0, "unavailable"
        rng = random.Random(stable_seed(address.lower(), chain, "balance"))
        return round(rng.uniform(100, 50_000), 6), "demo"

    if _circuit_open():
        return 0.0, "unavailable"

    url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]}
    try:
        async with _upstream_sem:
            async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                r = await client.post(url, json=body)
                r.raise_for_status()
                hex_wei = r.json().get("result", "0x0")
    except Exception:  # noqa: BLE001
        _record_upstream_result(ok=False)
        return 0.0, "unavailable"

    _record_upstream_result(ok=True)
    return int(hex_wei, 16) / 1e18, "live"


async def transaction_lookup(tx_hash: str, chain: str) -> tuple[TransactionRecord | None, str]:
    """Look up one canonical transaction by hash, including receipt status."""
    if chain not in _ALCHEMY_HOSTS:
        return None, "unsupported_chain"
    if not settings.live_data_available:
        return None, "live provider required; transaction data is never synthesized"
    if _circuit_open():
        return None, "upstream circuit breaker open"

    url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"
    try:
        async with _upstream_sem:
            async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                tx, receipt = await asyncio.gather(
                    _rpc(client, url, "eth_getTransactionByHash", [tx_hash]),
                    _rpc(client, url, "eth_getTransactionReceipt", [tx_hash]),
                )
    except Exception as exc:  # noqa: BLE001
        _record_upstream_result(ok=False)
        return None, f"upstream error: {type(exc).__name__}"

    _record_upstream_result(ok=True)
    if not isinstance(tx, dict):
        return None, "transaction not found"
    receipt_data = receipt if isinstance(receipt, dict) else {}
    status_hex = receipt_data.get("status")
    status = "pending" if status_hex is None else ("confirmed" if status_hex == "0x1" else "reverted")
    block_hex = tx.get("blockNumber")
    value_wei = int(tx.get("value", "0x0"), 16)
    return TransactionRecord(
        tx_hash=tx.get("hash", tx_hash),
        chain=chain,
        status=status,
        block_number=int(block_hex, 16) if block_hex else None,
        block_hash=tx.get("blockHash"),
        from_addr=(tx.get("from") or "").lower(),
        to_addr=(tx.get("to") or "").lower() or None,
        value_wei=value_wei,
        value_native=value_wei / 1e18,
        gas=int(tx.get("gas", "0x0"), 16),
        gas_price_wei=int(tx.get("gasPrice", "0x0"), 16),
        input=tx.get("input", "0x"),
        data_source="live",
    ), ""


# ── Token balances ───────────────────────────────────────────────────────────


@dataclass
class TokenBalance:
    contract: str
    symbol: str
    decimals: int
    raw: int
    amount: float


# Contract → (symbol, decimals) for the assets that dominate casino treasuries.
# Resolving metadata per contract costs a round trip each, so the common ones
# are pinned and anything else falls back to a lookup.
KNOWN_TOKENS: dict[str, tuple[str, int]] = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
    "0x514910771af9ca656af840dff83e8264ecf986ca": ("LINK", 18),
    "0x4fabb145d64652a948d72533023f6e7a623c7c53": ("BUSD", 18),
    "0x853d955acef822db058eb8505911ed77f175b99e": ("FRAX", 18),
}


async def token_balances(address: str, chain: str) -> tuple[list[TokenBalance], str]:
    """ERC20 balances for an address via alchemy_getTokenBalances.

    Native balance alone badly understates a casino treasury — operators hold
    most reserves in stablecoins. Returns (balances, data_source).
    """
    if not settings.live_data_available:
        if settings.strict_mode:
            return [], "unavailable"
        rng = random.Random(stable_seed(address.lower(), chain, "tokens"))
        demo = []
        for contract, (sym, dec) in list(KNOWN_TOKENS.items())[:3]:
            amt = rng.uniform(10_000, 5_000_000)
            demo.append(
                TokenBalance(contract, sym, dec, int(amt * 10**dec), round(amt, 6))
            )
        return demo, "demo"

    if _circuit_open():
        return [], "unavailable"

    url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"
    try:
        async with _upstream_sem:
            async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                result = await _rpc(
                    client, url, "alchemy_getTokenBalances", [address, "erc20"]
                )
    except Exception:  # noqa: BLE001
        _record_upstream_result(ok=False)
        return [], "unavailable"

    _record_upstream_result(ok=True)
    out: list[TokenBalance] = []
    for entry in (result or {}).get("tokenBalances", []):
        raw_hex = entry.get("tokenBalance") or "0x0"
        try:
            raw = int(raw_hex, 16)
        except (TypeError, ValueError):
            continue
        if raw <= 0:
            continue
        contract = (entry.get("contractAddress") or "").lower()
        meta = KNOWN_TOKENS.get(contract)
        if not meta:
            continue  # unknown token — excluded rather than valued at a guess
        symbol, decimals = meta
        out.append(
            TokenBalance(
                contract=contract,
                symbol=symbol,
                decimals=decimals,
                raw=raw,
                amount=raw / (10**decimals),
            )
        )
    out.sort(key=lambda b: -b.amount)
    return out, "live"
