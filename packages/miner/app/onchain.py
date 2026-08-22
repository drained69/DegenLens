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


def merge_cluster_reads(sets: list[TransferSet]) -> tuple[str, bool]:
    """Provenance across a multi-chain cluster read.

    Extra-chain RPC failures must not poison a successful read. An Alchemy
    plan that lacks BSC should not make Ethereum figures look unavailable.
    """
    useful = [s for s in sets if s.data_source != "unavailable"]
    if not useful:
        return "unavailable", False
    source = "demo" if any(s.data_source == "demo" for s in useful) else "live"
    # An unavailable chain is a coverage gap even when another chain returned
    # live data. Never let a partial multi-chain read report complete coverage.
    return source, all(s.complete and s.data_source != "unavailable" for s in sets)


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
# Per-chain: a BSC plan gap must not open the breaker for Ethereum. Multi-chain
# observation would otherwise trip the global breaker after a few extra-chain
# 404s and make Stake look like it had no data at all.

_consecutive_failures: dict[str, int] = {}
_circuit_opened_at: dict[str, float] = {}


def _circuit_open(chain: str) -> bool:
    opened = _circuit_opened_at.get(chain)
    if opened is None:
        return False
    if time.monotonic() - opened >= settings.circuit_cooldown_s:
        _circuit_opened_at.pop(chain, None)
        return False
    return True


def _record_upstream_result(*, ok: bool, chain: str) -> None:
    metrics.record_upstream(failed=not ok)
    if ok:
        _consecutive_failures[chain] = 0
        return
    _consecutive_failures[chain] = _consecutive_failures.get(chain, 0) + 1
    if _consecutive_failures[chain] >= settings.circuit_threshold:
        _circuit_opened_at[chain] = time.monotonic()


def circuit_status() -> dict[str, object]:
    open_chains = [chain for chain in list(_circuit_opened_at) if _circuit_open(chain)]
    return {
        "open": bool(open_chains),
        "fully_open": bool(open_chains) and set(open_chains) >= set(_ALCHEMY_HOSTS),
        "open_chains": open_chains,
        "consecutive_failures": dict(_consecutive_failures),
    }


def reset_circuits() -> None:
    """Test helper: drop per-chain breaker state."""
    _consecutive_failures.clear()
    _circuit_opened_at.clear()


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


def is_evm_chain(chain: str) -> bool:
    """True when we can read the chain via the Alchemy EVM API.

    Non-EVM chains (bitcoin, solana, tron) legitimately appear in the wallet
    registry — their identity is public gambling infrastructure — but they
    cannot be probed with `alchemy_getAssetTransfers`. Callers must skip
    cleanly rather than treat the raise as an outage.
    """
    return chain in _ALCHEMY_HOSTS


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
    if not is_evm_chain(chain):
        return TransferSet(
            [], "unsupported_chain",
            f"{chain} is a registry identity, not an Alchemy-readable EVM chain",
        )
    url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"
    try:
        # A burst of seven chains x two directions can trigger provider 429s.
        # Retry the complete pair after a short backoff; retrying one direction
        # alone would produce asymmetric inbound/outbound totals.
        for attempt in range(3):
            try:
                async with _upstream_sem:
                    async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                        (inbound, in_ok), (outbound, out_ok) = await asyncio.gather(
                            _alchemy_transfers_paged(client, url, address, "in", since),
                            _alchemy_transfers_paged(client, url, address, "out", since),
                        )
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))
    except Exception as exc:  # noqa: BLE001 - upstream failures must not 500
        _record_upstream_result(ok=False, chain=chain)
        return TransferSet(
            [],
            "unavailable",
            f"upstream error: {type(exc).__name__}",
            complete=False,
        )

    _record_upstream_result(ok=True, chain=chain)
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


async def get_observation_transfers(
    address: str, chain: str, hours: int, *, seed: bool
) -> TransferSet:
    """Fetch transfers for an operator identity on one chain.

    Operator aggregation always performs the full bidirectional, paginated
    read. A cheap probe is not sufficient for a completeness claim: it can hide
    older activity and makes a quiet chain indistinguishable from an incomplete
    read.
    """
    return await get_transfers(address, chain, hours)


async def get_transfers(address: str, chain: str, hours: int) -> TransferSet:
    address = address.lower()
    if chain not in _ALCHEMY_HOSTS:
        return TransferSet(
            [],
            "unavailable",
            f"no configured provider for chain: {chain}",
            complete=False,
        )
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
    elif _circuit_open(chain):
        result = TransferSet([], "unavailable", "upstream circuit breaker open")
    else:
        result = await _fetch_live(address, chain, since)

    # Only cache successful reads; failures should retry on the next request.
    # Busy operator wallets can return thousands of transfers. Retaining several
    # such sets exhausts small production containers during registry-wide views;
    # cache only compact reads and let large reads be garbage-collected.
    if result.data_source != "unavailable" and len(result.transfers) <= 1000:
        _CACHE[key] = (result, time.monotonic() + settings.stats_ttl)
        # Bound cache growth.
        if len(_CACHE) > 2048:
            for stale in sorted(_CACHE, key=lambda k: _CACHE[k][1])[:512]:
                _CACHE.pop(stale, None)
    return result


async def native_balance(address: str, chain: str) -> tuple[float, str]:
    """Native token balance. Returns (balance, data_source)."""
    if not is_evm_chain(chain):
        # Non-EVM registry entries (bitcoin, solana, tron) cannot be read with
        # the Alchemy EVM API. Return a clean degradation instead of raising —
        # a raise here bubbles all the way to a 200 with confidence 0 and
        # deflates every aggregate that iterates the whole registry.
        return 0.0, "unsupported_chain"
    if not settings.live_data_available:
        if settings.strict_mode:
            return 0.0, "unavailable"
        rng = random.Random(stable_seed(address.lower(), chain, "balance"))
        return round(rng.uniform(100, 50_000), 6), "demo"

    if _circuit_open(chain):
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
        _record_upstream_result(ok=False, chain=chain)
        return 0.0, "unavailable"

    _record_upstream_result(ok=True, chain=chain)
    return int(hex_wei, 16) / 1e18, "live"


async def transaction_lookup(tx_hash: str, chain: str) -> tuple[TransactionRecord | None, str]:
    """Look up one canonical transaction by hash, including receipt status."""
    if chain not in _ALCHEMY_HOSTS:
        return None, "unsupported_chain"
    if not settings.live_data_available:
        return None, "live provider required; transaction data is never synthesized"
    if _circuit_open(chain):
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
        _record_upstream_result(ok=False, chain=chain)
        return None, f"upstream error: {type(exc).__name__}"

    _record_upstream_result(ok=True, chain=chain)
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


# Contract → (symbol, decimals) per chain. USDT/USDC are not the same
# contract on Polygon as they are on Ethereum; a single Ethereum map made
# every other chain's treasury look empty aside from native gas.
KNOWN_TOKENS: dict[str, dict[str, tuple[str, int]]] = {
    "ethereum": {
        "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
        "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
        "0x514910771af9ca656af840dff83e8264ecf986ca": ("LINK", 18),
        "0x4fabb145d64652a948d72533023f6e7a623c7c53": ("BUSD", 18),
        "0x853d955acef822db058eb8505911ed77f175b99e": ("FRAX", 18),
    },
    "base": {
        "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": ("USDT", 6),
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6),
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": ("DAI", 18),
        "0x4200000000000000000000000000000000000006": ("WETH", 18),
    },
    "polygon": {
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": ("USDT", 6),
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": ("USDC", 6),
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": ("USDC", 6),
        "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": ("DAI", 18),
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": ("WETH", 18),
        "0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6": ("WBTC", 8),
    },
    "arbitrum": {
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": ("USDT", 6),
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USDC", 6),
        "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": ("USDC", 6),
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": ("DAI", 18),
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": ("WETH", 18),
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": ("WBTC", 8),
    },
    "optimism": {
        "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": ("USDT", 6),
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85": ("USDC", 6),
        "0x7f5c764cbc14f9669b88837ca1490cca17c31607": ("USDC", 6),
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": ("DAI", 18),
        "0x4200000000000000000000000000000000000006": ("WETH", 18),
    },
    "bsc": {
        "0x55d398326f99059ff775485246999027b3197955": ("USDT", 18),
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": ("USDC", 18),
        "0xe9e7cea3dedca5984780bafc599bd69add087d56": ("BUSD", 18),
        "0x2170ed0880ac9a755fd29b2688956bd959f933f8": ("ETH", 18),
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": ("WBNB", 18),
    },
    "avalanche": {
        "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7": ("USDT", 6),
        "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e": ("USDC", 6),
        "0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664": ("USDC", 6),
        "0xd586e7f844cea2f87f50152665bcbc2c279d8d70": ("DAI", 18),
        "0x49d5c2bdffac6ce2bfdb6640f4f80f226bc10bab": ("WETH", 18),
        "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7": ("WAVAX", 18),
    },
}


def known_tokens_for(chain: str) -> dict[str, tuple[str, int]]:
    return KNOWN_TOKENS.get(chain, {})


async def token_balances(address: str, chain: str) -> tuple[list[TokenBalance], str]:
    """ERC20 balances for an address via alchemy_getTokenBalances.

    Native balance alone badly understates a casino treasury — operators hold
    most reserves in stablecoins. Returns (balances, data_source).
    """
    if not is_evm_chain(chain):
        return [], "unsupported_chain"
    if not settings.live_data_available:
        if settings.strict_mode:
            return [], "unavailable"
        rng = random.Random(stable_seed(address.lower(), chain, "tokens"))
        demo = []
        for contract, (sym, dec) in list(known_tokens_for(chain).items())[:3]:
            amt = rng.uniform(10_000, 5_000_000)
            demo.append(
                TokenBalance(contract, sym, dec, int(amt * 10**dec), round(amt, 6))
            )
        return demo, "demo"

    if _circuit_open(chain):
        return [], "unavailable"

    url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"
    try:
        async with _upstream_sem:
            async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                result = await _rpc(
                    client, url, "alchemy_getTokenBalances", [address, "erc20"]
                )
    except Exception:  # noqa: BLE001
        _record_upstream_result(ok=False, chain=chain)
        return [], "unavailable"

    _record_upstream_result(ok=True, chain=chain)
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
        meta = known_tokens_for(chain).get(contract)
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


# ── Lightweight activity probe ───────────────────────────────────────────────


@dataclass
class ActivityProbe:
    """Cheap liveness check for an address: does it transact, and when last?"""

    address: str
    chain: str
    has_history: bool
    last_activity: datetime | None
    sampled_transfers: int
    data_source: str


async def probe_activity(address: str, chain: str) -> ActivityProbe:
    """One page per direction, no pagination.

    Health checking only needs to know IF an address has ever transacted and
    WHEN it last did. Answering that with the full paged history costs ~20
    upstream calls per address, which collapses under concurrency — running it
    across a registry timed every request out and reported healthy wallets as
    unavailable. A single most-recent page answers both questions.
    """
    address = address.lower()

    if not is_evm_chain(chain):
        # Non-EVM entries live in the registry as public identity, not for
        # health probing. Report a distinct source so the caller can label
        # them "not_probeable" rather than "dead" or "quiet".
        return ActivityProbe(address, chain, False, None, 0, "unsupported_chain")

    if not settings.live_data_available:
        if settings.strict_mode:
            return ActivityProbe(address, chain, False, None, 0, "unavailable")
        rng = random.Random(stable_seed(address, chain, "probe"))
        has = rng.random() > 0.2
        return ActivityProbe(
            address, chain, has,
            datetime.now(timezone.utc) - timedelta(hours=rng.randint(1, 400)) if has else None,
            rng.randint(1, 100) if has else 0,
            "demo",
        )

    if _circuit_open(chain):
        return ActivityProbe(address, chain, False, None, 0, "unavailable")

    url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"

    async def one(direction: str) -> list[dict]:
        key = "toAddress" if direction == "in" else "fromAddress"
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "alchemy_getAssetTransfers",
            "params": [
                {
                    key: address,
                    "category": ["external", "erc20"],
                    "fromBlock": "0x0",
                    "withMetadata": True,
                    "excludeZeroValue": True,
                    "order": "desc",
                    "maxCount": "0x64",  # 100 — one page is enough
                }
            ],
        }
        r = await client.post(url, json=body)
        r.raise_for_status()
        payload = r.json()
        if "error" in payload:
            raise httpx.HTTPError(str(payload["error"]))
        return (payload.get("result") or {}).get("transfers", [])

    try:
        for attempt in range(3):
            try:
                async with _upstream_sem:
                    async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                        inbound, outbound = await asyncio.gather(one("in"), one("out"))
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))
    except Exception:  # noqa: BLE001
        _record_upstream_result(ok=False, chain=chain)
        return ActivityProbe(address, chain, False, None, 0, "unavailable")

    _record_upstream_result(ok=True, chain=chain)
    rows = inbound + outbound
    stamps: list[datetime] = []
    for row in rows:
        raw = (row.get("metadata") or {}).get("blockTimestamp")
        if not raw:
            continue
        try:
            stamps.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue

    return ActivityProbe(
        address=address,
        chain=chain,
        has_history=bool(rows),
        last_activity=max(stamps) if stamps else None,
        sampled_transfers=len(rows),
        data_source="live",
    )
