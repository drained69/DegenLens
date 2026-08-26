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
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from . import metrics
from .settings import settings

# Bound concurrent upstream calls so a burst of requests can't open hundreds of
# sockets against Alchemy and trip its own rate limiter.
_upstream_sem = asyncio.Semaphore(settings.max_upstream_concurrency)
# Background cache rebuilds read the whole registry and would otherwise hold
# every upstream slot for minutes, starving the single-operator endpoints that
# still read live. Gating them behind a second, narrower semaphore caps how
# much of the shared budget they can hold at once, so foreground requests keep
# making progress while a rebuild runs.
_background_sem = asyncio.Semaphore(settings.max_background_upstream_concurrency)
_is_background: ContextVar[bool] = ContextVar("upstream_is_background", default=False)


_request_deadline_at: ContextVar[float | None] = ContextVar(
    "request_deadline_at", default=None
)


@contextmanager
def request_deadline(seconds: float) -> Iterator[None]:
    """Bound every upstream read in this request to `seconds` from now.

    Retries used to be counted in attempts, not time: three attempts at the
    per-call timeout could spend more than twice the service deadline on a
    single wallet, so the request was already lost before the last try began.
    """
    token = _request_deadline_at.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _request_deadline_at.reset(token)


def remaining_request_time() -> float | None:
    """Seconds left for upstream work, or None when unbounded (background)."""
    at = _request_deadline_at.get()
    if at is None:
        return None
    return max(0.0, at - time.monotonic())


# Time a further page needs to be worth starting: one call plus a margin to
# serialize the response.
_PAGE_TIME_RESERVE_S = 2.0


def upstream_call_timeout() -> float:
    """Per-call timeout, never longer than the time the request has left."""
    remaining = remaining_request_time()
    if remaining is None:
        return settings.upstream_timeout_s
    return max(0.5, min(settings.upstream_timeout_s, remaining))


def should_retry_upstream(attempt: int, delay: float) -> bool:
    """Whether another attempt can still finish inside the request budget."""
    remaining = remaining_request_time()
    if remaining is None:
        return True
    # Needs the backoff plus a usable slice of call time to be worth starting.
    return remaining > delay + 1.0


@contextmanager
def background_reads() -> Iterator[None]:
    """Mark everything read in this context as deprioritised background work."""
    token = _is_background.set(True)
    try:
        yield
    finally:
        _is_background.reset(token)


_foreground_inflight = 0


@asynccontextmanager
async def upstream_slot() -> AsyncIterator[None]:
    """Acquire the shared upstream budget, yielding to foreground reads.

    Capping how many background reads run at once was not enough on its own:
    one cache-warming read still holds a provider slot for seconds at a time,
    and a live request that has to queue behind it can burn its whole deadline
    and answer `unavailable`. So a background read also waits for the live
    traffic to clear before it takes a slot. The wait is bounded — under
    sustained traffic the warm would otherwise never run, and a cache that
    never fills is the problem it was added to solve.
    """
    global _foreground_inflight
    if _is_background.get():
        async with _background_sem:
            waited = 0.0
            poll = settings.background_yield_poll_s
            while _foreground_inflight > 0 and waited < settings.background_yield_max_s:
                await asyncio.sleep(poll)
                waited += poll
            async with _upstream_sem:
                yield
    else:
        _foreground_inflight += 1
        try:
            async with _upstream_sem:
                yield
        finally:
            _foreground_inflight -= 1


_page_limit: ContextVar[int | None] = ContextVar("transfer_page_limit", default=None)
# A REDUCED budget, distinct from `_page_limit`. Kept separate because
# `_page_limit` also means "this is a full scan" to `is_full_scan()`; reusing it
# to shrink a read would make a truncated window claim complete coverage.
_page_budget: ContextVar[int | None] = ContextVar("transfer_page_budget", default=None)


@contextmanager
def page_budget(pages: int) -> Iterator[None]:
    """Cap pagination depth for reads that do not need full history.

    Some questions are answered by the most recent slice — whether an address
    ever touched a known cluster, say — and paging tens of thousands of rows to
    answer them costs seconds and still ends truncated. A shallower read is not
    less honest: it reports `coverage_complete: false` exactly as the deep one
    did, and the counts it produces are documented lower bounds either way.
    """
    token = _page_budget.set(pages)
    try:
        yield
    finally:
        _page_budget.reset(token)


def transfer_page_limit() -> int:
    full = _page_limit.get()
    if full is not None:
        return full
    return _page_budget.get() or settings.max_transfer_pages


def is_full_scan() -> bool:
    return _page_limit.get() is not None


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
class TokenTransfer:
    """One ERC-20 Transfer event decoded from the receipt logs."""

    contract: str
    symbol: str | None
    decimals: int | None
    from_addr: str
    to_addr: str
    raw_amount: int
    amount: float | None


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
    # Receipt facts. The receipt was already being fetched for `status` and then
    # discarded; these are the canonical cost fields a transaction lookup is
    # actually asked for, and they cost no extra upstream call.
    gas_used: int | None = None
    effective_gas_price_wei: int | None = None
    fee_wei: int | None = None
    fee_native: float | None = None
    nonce: int | None = None
    transaction_index: int | None = None
    # Set when the transaction deployed a contract.
    contract_address: str | None = None
    # First 4 bytes of calldata; identifies the method invoked.
    method_id: str | None = None
    block_timestamp: str | None = None
    token_transfers: list["TokenTransfer"] = field(default_factory=list)


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
    "solana": "SOL",
    "tron": "TRX",
    "bitcoin": "BTC",
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


async def _fetch_solana(address: str, since: datetime) -> TransferSet:
    """Read native SOL movements through the public Solana JSON-RPC API."""
    try:
        async with upstream_slot():
            async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                response = await client.post(
                    settings.solana_rpc_url,
                    json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": settings.max_transfer_pages * 100}],
                    },
                )
                response.raise_for_status()
                signatures = response.json().get("result") or []
                transfers: list[Transfer] = []
                # Public Solana RPCs rate-limit transaction hydration. A small
                # bounded sample is preferable to turning the whole chain into
                # unavailable after hundreds of rejected requests.
                max_signatures = min(transfer_page_limit() * 100, 25)
                signatures = signatures[:max_signatures]
                complete = len(signatures) < max_signatures

                async def fetch_transaction(entry: dict) -> Transfer | None:
                    try:
                        if entry.get("err") or not entry.get("blockTime"):
                            return None
                        timestamp = datetime.fromtimestamp(entry["blockTime"], tz=timezone.utc)
                        if timestamp < since:
                            return None
                        tx_response = await client.post(
                            settings.solana_rpc_url,
                            json={
                                "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                "params": [entry["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                            },
                        )
                        tx_response.raise_for_status()
                        tx = tx_response.json().get("result") or {}
                        message = tx.get("transaction", {}).get("message", {})
                        keys = [
                            item.get("pubkey") if isinstance(item, dict) else item
                            for item in message.get("accountKeys", [])
                        ]
                        index = keys.index(address) if address in keys else -1
                        balances = tx.get("meta", {})
                        if index < 0 or index >= len(balances.get("preBalances", [])):
                            return None
                        delta = balances["postBalances"][index] - balances["preBalances"][index]
                        if delta == 0:
                            # Casino Solana flow is commonly SPL USDC/USDT. Use
                            # owner-level token balance changes when native SOL
                            # is unchanged.
                            pre_tokens = {
                                row.get("mint"): float(row.get("uiTokenAmount", {}).get("uiAmount") or 0)
                                for row in balances.get("preTokenBalances", [])
                                if row.get("owner") == address
                            }
                            post_tokens = {
                                row.get("mint"): float(row.get("uiTokenAmount", {}).get("uiAmount") or 0)
                                for row in balances.get("postTokenBalances", [])
                                if row.get("owner") == address
                            }
                            token_deltas = {
                                mint: post_tokens.get(mint, 0) - pre_tokens.get(mint, 0)
                                for mint in pre_tokens.keys() | post_tokens.keys()
                            }
                            mint, token_delta = max(
                                token_deltas.items(), key=lambda item: abs(item[1]), default=("", 0.0)
                            )
                            if token_delta == 0:
                                return None
                            symbols = {
                                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
                                "Es9vMFrzaCERmJfrF4H2FYDkN7P8Jd7o1i1V5X7tXh": "USDT",
                            }
                            return Transfer(
                                tx_hash=entry["signature"],
                                from_addr="solana:unknown" if token_delta > 0 else address,
                                to_addr=address if token_delta > 0 else "solana:unknown",
                                token_symbol=symbols.get(mint, "SPL"),
                                amount=abs(token_delta), timestamp=timestamp, chain="solana",
                                direction="in" if token_delta > 0 else "out",
                            )
                        return Transfer(
                            tx_hash=entry["signature"],
                            from_addr="solana:unknown" if delta > 0 else address,
                            to_addr=address if delta > 0 else "solana:unknown",
                            token_symbol="SOL", amount=abs(delta) / 1e9,
                            timestamp=timestamp, chain="solana",
                            direction="in" if delta > 0 else "out",
                        )
                    except Exception:
                        return None

                transfers = [
                    transfer
                    for transfer in await asyncio.gather(
                        *(fetch_transaction(entry) for entry in signatures),
                        return_exceptions=False,
                    )
                    if transfer is not None
                ]
                _record_upstream_result(ok=True, chain="solana")
                return TransferSet(
                    transfers,
                    "live",
                    complete=complete if is_full_scan() else False,
                )
    except Exception as exc:  # noqa: BLE001 - provider failure is structured
        _record_upstream_result(ok=False, chain="solana")
        return TransferSet([], "unavailable", f"upstream error: {type(exc).__name__}", complete=False)


async def _fetch_bitcoin(address: str, since: datetime) -> TransferSet:
    """Read native BTC movements through the public Esplora API."""
    try:
        async with upstream_slot():
            async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                response = await client.get(f"{settings.bitcoin_api_url}/address/{address}/txs")
                response.raise_for_status()
                rows = response.json()
        transfers: list[Transfer] = []
        for row in rows[: settings.max_transfer_pages * 100]:
            status = row.get("status", {})
            block_time = status.get("block_time")
            if not block_time:
                continue
            timestamp = datetime.fromtimestamp(block_time, tz=timezone.utc)
            if timestamp < since:
                continue
            received = sum(
                output.get("value", 0) for output in row.get("vout", [])
                if address in output.get("scriptpubkey_address", "")
            )
            input_total = sum(item.get("prevout", {}).get("value", 0) for item in row.get("vin", []))
            delta = received - input_total if input_total else received
            if delta == 0:
                continue
            transfers.append(Transfer(
                tx_hash=row.get("txid", ""),
                from_addr="bitcoin:unknown" if delta > 0 else address,
                to_addr=address if delta > 0 else "bitcoin:unknown",
                token_symbol="BTC", amount=abs(delta) / 1e8,
                timestamp=timestamp, chain="bitcoin",
                direction="in" if delta > 0 else "out",
            ))
        _record_upstream_result(ok=True, chain="bitcoin")
        return TransferSet(transfers, "live", complete=len(rows) < settings.max_transfer_pages * 100)
    except Exception as exc:  # noqa: BLE001
        _record_upstream_result(ok=False, chain="bitcoin")
        return TransferSet([], "unavailable", f"upstream error: {type(exc).__name__}", complete=False)


async def _fetch_tron(address: str, since: datetime) -> TransferSet:
    """Read TRX and TRC-20 movements through TronGrid's public REST API."""
    try:
        async with upstream_slot():
            async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                response = await client.get(
                    f"{settings.tron_api_url}/v1/accounts/{address}/transactions",
                    params={"limit": 100, "only_confirmed": "true", "min_timestamp": int(since.timestamp() * 1000)},
                )
                response.raise_for_status()
                rows = response.json().get("data") or []
                token_complete = True
                try:
                    token_response = await client.get(
                        f"{settings.tron_api_url}/v1/accounts/{address}/transactions/trc20",
                        params={"limit": 100, "only_confirmed": "true", "min_timestamp": int(since.timestamp() * 1000)},
                    )
                    token_response.raise_for_status()
                    token_rows = token_response.json().get("data") or []
                except Exception:
                    # TronGrid commonly rate-limits the optional token feed. A
                    # successful native read is still useful, but incomplete.
                    token_rows = []
                    token_complete = False
        transfers: list[Transfer] = []
        for row in rows:
            contract = (row.get("raw_data", {}).get("contract") or [{}])[0]
            value = contract.get("parameter", {}).get("value", {})
            amount = value.get("amount", 0)
            if not amount:
                continue
            sender = value.get("owner_address", "")
            recipient = value.get("to_address", "")
            if sender != address and recipient != address:
                continue
            timestamp = datetime.fromtimestamp(row.get("block_timestamp", 0) / 1000, tz=timezone.utc)
            transfers.append(Transfer(
                tx_hash=row.get("txID", ""), from_addr=sender, to_addr=recipient,
                token_symbol="TRX", amount=amount / 1e6, timestamp=timestamp,
                chain="tron", direction="in" if recipient == address else "out",
            ))
        for row in token_rows:
            timestamp = datetime.fromtimestamp(row.get("block_timestamp", 0) / 1000, tz=timezone.utc)
            sender = row.get("from", "")
            recipient = row.get("to", "")
            if timestamp < since or (sender != address and recipient != address):
                continue
            decimals = int(row.get("token_info", {}).get("decimals", 6))
            transfers.append(Transfer(
                tx_hash=row.get("transaction_id", ""),
                from_addr=sender,
                to_addr=recipient,
                token_symbol=(row.get("token_info", {}).get("symbol") or "TRC20").upper(),
                amount=float(row.get("value", 0)) / (10 ** decimals),
                timestamp=timestamp,
                chain="tron",
                direction="in" if recipient == address else "out",
            ))
        _record_upstream_result(ok=True, chain="tron")
        return TransferSet(
            transfers,
            "live",
            complete=token_complete and len(rows) < 100 and len(token_rows) < 100,
        )
    except Exception as exc:  # noqa: BLE001
        _record_upstream_result(ok=False, chain="tron")
        return TransferSet([], "unavailable", f"upstream error: {type(exc).__name__}", complete=False)


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

    for page in range(transfer_page_limit()):
        # The page budget bounds how MUCH we read; the request deadline bounds
        # how LONG. A busy address can exhaust the clock long before the pages,
        # and a truncated read delivered on time beats a complete one delivered
        # after the caller gave up — provided it is reported as truncated, which
        # returning complete=False here does.
        if page and remaining_request_time() is not None:
            if remaining_request_time() < _PAGE_TIME_RESERVE_S:
                return rows, False

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

        try:
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
        except Exception:
            # Keep what we already paged. Discarding it turned a rate-limited
            # page N into a zero-transfer, zero-confidence answer even though
            # pages 1..N-1 were real observations — the difference between a
            # partial answer and no answer at all. With nothing in hand there
            # is nothing to salvage, so let the caller retry the whole read.
            if rows:
                return rows, False
            raise

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


def _retry_delay(exc: Exception, attempt: int) -> float:
    """How long to wait before retrying a failed upstream read.

    The old fixed 0.25s/0.5s ladder was shorter than any real rate-limit
    window, so all three attempts landed inside the same 429 and the read was
    reported unavailable — a zero-confidence answer caused by backing off too
    little rather than by missing data. Honour `Retry-After` when the provider
    sends one, otherwise back off exponentially with jitter so concurrent
    wallet reads do not retry in lockstep.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), settings.max_retry_delay_s)
            except ValueError:
                pass
    # Jitter first, then clamp — clamping first let the jitter multiplier push
    # the delay back over the ceiling the setting is supposed to guarantee.
    backoff = settings.retry_base_delay_s * (2 ** attempt) * (0.5 + random.random())
    return min(backoff, settings.max_retry_delay_s)


async def _fetch_live(address: str, chain: str, since: datetime) -> TransferSet:
    """Fetch BOTH directions, fully paged back to `since`.

    Inbound-only was the withdrawals-always-zero bug; single-page was the
    identical-across-windows bug.
    """
    if chain == "solana":
        return await _fetch_solana(address, since)
    if chain == "tron":
        return await _fetch_tron(address, since)
    if chain == "bitcoin":
        return await _fetch_bitcoin(address, since)
    if not is_evm_chain(chain):
        return TransferSet([], "unavailable", f"no configured provider for chain: {chain}", complete=False)
    url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"
    try:
        # A burst of seven chains x two directions can trigger provider 429s.
        # Retry the complete pair after a short backoff; retrying one direction
        # alone would produce asymmetric inbound/outbound totals.
        for attempt in range(3):
            try:
                async with upstream_slot():
                    async with httpx.AsyncClient(timeout=upstream_call_timeout()) as client:
                        (inbound, in_ok), (outbound, out_ok) = await asyncio.gather(
                            _alchemy_transfers_paged(client, url, address, "in", since),
                            _alchemy_transfers_paged(client, url, address, "out", since),
                        )
                break
            except Exception as exc:
                delay = _retry_delay(exc, attempt)
                if attempt == 2 or not should_retry_upstream(attempt, delay):
                    raise
                await asyncio.sleep(delay)
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
    if is_evm_chain(chain):
        address = address.lower()
    if chain not in _ALCHEMY_HOSTS and chain not in {"solana", "tron", "bitcoin"}:
        return TransferSet([], "unavailable", f"no configured provider for chain: {chain}", complete=False)
    now = _bucketed_now()
    key = (address, chain, hours, int(now.timestamp()))

    cached = _CACHE.get(key)
    if cached and cached[1] > time.monotonic():
        metrics.record_cache(hit=True)
        return cached[0]
    metrics.record_cache(hit=False)

    since = now - timedelta(hours=hours)

    # Order matters. The demo check has to come FIRST: with no provider key
    # configured, `chain_live_data_available` is false for every chain, so
    # testing it first made the demo branch unreachable and turned local
    # development into a uniform "unavailable" feed. Chains the EVM adapter
    # would otherwise cover fall back to labeled synthetic data; chains with no
    # adapter at all stay honestly unavailable.
    if not settings.live_data_available and chain in _ALCHEMY_HOSTS:
        if settings.strict_mode:
            result = TransferSet([], "unavailable", "strict_mode: no live data provider")
        else:
            result = _demo_transfers(address, chain, since, now)
    elif not settings.chain_live_data_available(chain):
        result = TransferSet([], "unavailable", "live provider required for chain", complete=False)
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
    if chain == "solana":
        if not settings.live_data_available:
            if settings.strict_mode:
                return 0.0, "unavailable"
            rng = random.Random(stable_seed(address.lower(), chain, "balance"))
            return round(rng.uniform(100, 50_000), 6), "demo"
        try:
            async with upstream_slot():
                async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                    response = await client.post(
                        settings.solana_rpc_url,
                        json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]},
                    )
                    response.raise_for_status()
                    value = response.json().get("result", {}).get("value")
                    if value is None:
                        raise ValueError("missing Solana balance")
            _record_upstream_result(ok=True, chain=chain)
            return int(value) / 1e9, "live"
        except Exception:  # noqa: BLE001
            _record_upstream_result(ok=False, chain=chain)
            return 0.0, "unavailable"
    if chain == "tron":
        if not settings.live_data_available:
            if settings.strict_mode:
                return 0.0, "unavailable"
            rng = random.Random(stable_seed(address.lower(), chain, "balance"))
            return round(rng.uniform(100, 50_000), 6), "demo"
        try:
            async with upstream_slot():
                async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                    response = await client.get(f"{settings.tron_api_url.rstrip('/')}/v1/accounts/{address}")
                    response.raise_for_status()
                    rows = response.json().get("data") or []
                    value = rows[0].get("balance", 0) if rows else 0
            _record_upstream_result(ok=True, chain=chain)
            return int(value) / 1e6, "live"
        except Exception:  # noqa: BLE001
            _record_upstream_result(ok=False, chain=chain)
            return 0.0, "unavailable"
    if chain == "bitcoin":
        if not settings.live_data_available:
            if settings.strict_mode:
                return 0.0, "unavailable"
            rng = random.Random(stable_seed(address.lower(), chain, "balance"))
            return round(rng.uniform(0.01, 20), 8), "demo"
        try:
            async with upstream_slot():
                async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                    response = await client.get(f"{settings.bitcoin_api_url.rstrip('/')}/address/{address}")
                    response.raise_for_status()
                    stats = response.json().get("chain_stats", {})
            funded = int(stats.get("funded_txo_sum", 0))
            spent = int(stats.get("spent_txo_sum", 0))
            _record_upstream_result(ok=True, chain=chain)
            return max(funded - spent, 0) / 1e8, "live"
        except Exception:  # noqa: BLE001
            _record_upstream_result(ok=False, chain=chain)
            return 0.0, "unavailable"
    if not is_evm_chain(chain):
        # Keep unknown registry entries explicit instead of letting an adapter
        # exception deflate an aggregate that iterates the whole registry.
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
        async with upstream_slot():
            async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                r = await client.post(url, json=body)
                r.raise_for_status()
                hex_wei = r.json().get("result", "0x0")
    except Exception:  # noqa: BLE001
        _record_upstream_result(ok=False, chain=chain)
        return 0.0, "unavailable"

    _record_upstream_result(ok=True, chain=chain)
    return int(hex_wei, 16) / 1e18, "live"


# keccak256("Transfer(address,address,uint256)") — the ERC-20 transfer topic.
_ERC20_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)


def _hex_int(value: object) -> int | None:
    """Parse an RPC hex quantity, returning None rather than guessing a zero.

    None and 0 are different answers here: a pending transaction has no
    `gasUsed`, and reporting that as 0 would state that it consumed no gas.
    """
    if not isinstance(value, str) or not value.startswith("0x"):
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _topic_address(topic: object) -> str | None:
    """The low 20 bytes of a 32-byte log topic, as a 0x address."""
    if not isinstance(topic, str) or len(topic) < 42:
        return None
    return "0x" + topic[-40:].lower()


def _decode_token_transfers(receipt: dict, chain: str) -> list[TokenTransfer]:
    """Decode ERC-20 Transfer events from receipt logs.

    The logs are already in hand from the receipt call, so this adds no upstream
    cost. Where the contract is one we hold metadata for, the amount is scaled
    to human units; where it is not, `raw_amount` is still exact and `amount`
    stays None rather than assuming 18 decimals — a wrong scale is a wrong
    number, and 6-decimal stablecoins are the common case.
    """
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        return []
    known = KNOWN_TOKENS.get(chain, {})
    out: list[TokenTransfer] = []
    for log in logs[:50]:  # bound: a batch transfer can carry hundreds
        if not isinstance(log, dict):
            continue
        topics = log.get("topics")
        if not isinstance(topics, list) or len(topics) < 3:
            continue
        if not isinstance(topics[0], str) or topics[0].lower() != _ERC20_TRANSFER_TOPIC:
            continue
        contract = (log.get("address") or "").lower()
        sender = _topic_address(topics[1])
        recipient = _topic_address(topics[2])
        raw = _hex_int(log.get("data"))
        if sender is None or recipient is None or raw is None:
            continue
        symbol, decimals = known.get(contract, (None, None))
        out.append(
            TokenTransfer(
                contract=contract,
                symbol=symbol,
                decimals=decimals,
                from_addr=sender,
                to_addr=recipient,
                raw_amount=raw,
                amount=(raw / (10 ** decimals)) if decimals is not None else None,
            )
        )
    return out


async def native_balance_wei(address: str, chain: str) -> tuple[int | None, str]:
    """Native balance in EXACT base units.

    `native_balance` divides by 1e18 and returns a float, which is lossy at wei
    scale: 999999999999999999 wei round-trips to 1000000000000000000, reporting
    a whole extra unit the wallet does not have. Balance is the answer this
    intent exists to give, so the integer the chain actually returned is read
    here and never reconstructed from the float.

    Returns (wei, source). `None` on failure — never 0.
    """
    if chain not in _ALCHEMY_HOSTS:
        # Non-EVM adapters return floats natively; scale them and accept the
        # precision they were given rather than pretending to more.
        amount, source = await native_balance(address, chain)
        if source == "unavailable":
            return None, source
        decimals = {"solana": 9, "tron": 6, "bitcoin": 8}.get(chain, 18)
        return int(round(amount * (10 ** decimals))), source

    if not settings.live_data_available:
        if settings.strict_mode:
            return None, "unavailable"
        amount, source = await native_balance(address, chain)
        return (None if source == "unavailable" else int(round(amount * 1e18))), source

    if _circuit_open(chain):
        return None, "unavailable"

    url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]}
    try:
        async with upstream_slot():
            async with httpx.AsyncClient(timeout=upstream_call_timeout()) as client:
                r = await client.post(url, json=body)
                r.raise_for_status()
                hex_wei = r.json().get("result")
        if not isinstance(hex_wei, str):
            raise ValueError("missing balance result")
    except Exception:  # noqa: BLE001
        _record_upstream_result(ok=False, chain=chain)
        return None, "unavailable"
    _record_upstream_result(ok=True, chain=chain)
    return int(hex_wei, 16), "live"


@dataclass
class BalanceSnapshot:
    """A wallet's holdings right now, with the block the read was taken at.

    Split out from `wallet_trace` because a balance question and an attribution
    question have very different costs. The balance is two RPC calls; the
    30-day association scan is a paged transfer crawl that took the median
    `/wallet/trace` response to ~7s. Blocking an exact, cheap, directly
    observable fact behind an expensive derived one is what made the balance
    intent slow, and slow answers are scored as failures.
    """

    address: str
    chain: str
    block_number: int | None
    native_symbol: str
    native_wei: int | None
    native_amount: float | None
    tokens: list[TokenBalance]
    data_source: str
    reason: str | None = None


async def balance_snapshot(address: str, chain: str, include_tokens: bool = True) -> BalanceSnapshot:
    """Direct balance facts for one address. No attribution, no transfer crawl.

    A provider failure returns `native_wei=None` with `data_source="unavailable"`
    — never 0. Reporting an unreadable provider as a zero balance is the single
    most damaging thing a balance endpoint can do: it is indistinguishable from
    a real empty wallet and it is wrong.
    """
    symbol = NATIVE_SYMBOL.get(chain, "ETH")

    if chain not in _ALCHEMY_HOSTS and chain not in {"solana", "tron", "bitcoin"}:
        return BalanceSnapshot(
            address=address, chain=chain, block_number=None, native_symbol=symbol,
            native_wei=None, native_amount=None, tokens=[],
            data_source="unavailable", reason=f"no configured provider for chain: {chain}",
        )

    async def _block() -> int | None:
        """Read block height alongside the balance so the answer can say what
        state it reflects. Best-effort: a missing height must not lose the
        balance itself."""
        if chain not in _ALCHEMY_HOSTS or not settings.live_data_available:
            return None
        try:
            url = f"https://{_alchemy_host(chain)}/v2/{settings.alchemy_key}"
            async with httpx.AsyncClient(timeout=upstream_call_timeout()) as client:
                result = await _rpc(client, url, "eth_blockNumber", [])
            return int(result, 16) if isinstance(result, str) else None
        except Exception:  # noqa: BLE001
            return None

    native_task = native_balance_wei(address, chain)
    token_task = token_balances(address, chain) if include_tokens else None

    if token_task is not None:
        (wei, native_source), (tokens, token_source), block = await asyncio.gather(
            native_task, token_task, _block()
        )
    else:
        (wei, native_source), block = await asyncio.gather(native_task, _block())
        tokens, token_source = [], native_source

    if native_source == "unavailable" or wei is None:
        return BalanceSnapshot(
            address=address, chain=chain, block_number=block, native_symbol=symbol,
            native_wei=None, native_amount=None, tokens=tokens,
            data_source="unavailable",
            reason="native balance provider unavailable; balance is unknown, not zero",
        )

    decimals = {"solana": 9, "tron": 6, "bitcoin": 8}.get(chain, 18)
    return BalanceSnapshot(
        address=address,
        chain=chain,
        block_number=block,
        native_symbol=symbol,
        native_wei=wei,
        # Convenience float for existing consumers. `native_wei` is canonical:
        # this loses precision at wei scale by construction.
        native_amount=wei / (10 ** decimals),
        tokens=tokens,
        data_source=native_source,
        reason=None if token_source == native_source else f"token balances {token_source}",
    )


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
        async with upstream_slot():
            async with httpx.AsyncClient(timeout=upstream_call_timeout()) as client:
                tx, receipt = await asyncio.gather(
                    _rpc(client, url, "eth_getTransactionByHash", [tx_hash]),
                    _rpc(client, url, "eth_getTransactionReceipt", [tx_hash]),
                )
                # The block header carries the timestamp, which is one of the
                # facts a transaction lookup is most often asked for and is not
                # present on either the transaction or the receipt. Fetched only
                # once the transaction is known to exist and to be mined, and
                # allowed to fail on its own: a missing timestamp must not lose
                # the answer.
                block_ts = None
                if isinstance(tx, dict) and tx.get("blockNumber"):
                    try:
                        block = await _rpc(
                            client, url, "eth_getBlockByNumber",
                            [tx["blockNumber"], False],
                        )
                        if isinstance(block, dict) and block.get("timestamp"):
                            block_ts = datetime.fromtimestamp(
                                int(block["timestamp"], 16), tz=timezone.utc
                            ).isoformat()
                    except Exception:  # noqa: BLE001
                        block_ts = None
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

    gas_used = _hex_int(receipt_data.get("gasUsed"))
    # Post-1559 the receipt's effectiveGasPrice is the price actually paid, and
    # it differs from the transaction's gasPrice on type-2 transactions. Prefer
    # it, and fall back only when the receipt does not carry one.
    effective_price = _hex_int(receipt_data.get("effectiveGasPrice"))
    if effective_price is None:
        effective_price = _hex_int(tx.get("gasPrice"))
    fee_wei = gas_used * effective_price if (gas_used is not None and effective_price is not None) else None

    calldata = tx.get("input", "0x") or "0x"
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
        input=calldata,
        data_source="live",
        gas_used=gas_used,
        effective_gas_price_wei=effective_price,
        fee_wei=fee_wei,
        fee_native=(fee_wei / 1e18) if fee_wei is not None else None,
        nonce=_hex_int(tx.get("nonce")),
        transaction_index=_hex_int(tx.get("transactionIndex")),
        contract_address=(receipt_data.get("contractAddress") or None),
        method_id=(calldata[:10] if len(calldata) >= 10 else None),
        block_timestamp=block_ts,
        token_transfers=_decode_token_transfers(receipt_data, chain),
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
    if chain == "solana":
        if not settings.live_data_available:
            if settings.strict_mode:
                return [], "unavailable"
            return [], "demo"
        symbols = {
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
            "Es9vMFrzaCERmJfrF4H2FYDkN7P8Jd7o1i1V5X7tXh": ("USDT", 6),
        }
        try:
            async with upstream_slot():
                async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                    response = await client.post(
                        settings.solana_rpc_url,
                        json={
                            "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
                            "params": [address, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding": "jsonParsed"}],
                        },
                    )
                    response.raise_for_status()
                    rows = response.json().get("result", {}).get("value") or []
            out = []
            for row in rows:
                info = row.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                mint = info.get("mint", "")
                meta = symbols.get(mint)
                amount = (info.get("tokenAmount") or {}).get("uiAmount")
                if meta and amount:
                    symbol, decimals = meta
                    out.append(TokenBalance(mint, symbol, decimals, int(float(amount) * 10**decimals), float(amount)))
            _record_upstream_result(ok=True, chain=chain)
            return out, "live"
        except Exception:  # noqa: BLE001
            _record_upstream_result(ok=False, chain=chain)
            return [], "unavailable"
    if chain == "tron":
        if not settings.live_data_available:
            if settings.strict_mode:
                return [], "unavailable"
            return [], "demo"
        known = {
            "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": ("USDT", 6),
            "TEkxiTehnz5NbloZphRzA1q5Bq8q8Z2r3": ("USDC", 6),
        }
        try:
            async with upstream_slot():
                async with httpx.AsyncClient(timeout=settings.upstream_timeout_s) as client:
                    response = await client.get(f"{settings.tron_api_url.rstrip('/')}/v1/accounts/{address}/trc20")
                    response.raise_for_status()
                    rows = response.json().get("data") or []
            out = []
            for row in rows:
                contract = row.get("token_info", {}).get("address", "")
                meta = known.get(contract)
                if meta:
                    symbol, decimals = meta
                    raw = int(row.get("balance", 0))
                    if raw > 0:
                        out.append(TokenBalance(contract.lower(), symbol, decimals, raw, raw / 10**decimals))
            _record_upstream_result(ok=True, chain=chain)
            return out, "live"
        except Exception:  # noqa: BLE001
            _record_upstream_result(ok=False, chain=chain)
            return [], "unavailable"
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
        async with upstream_slot():
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
                async with upstream_slot():
                    async with httpx.AsyncClient(timeout=upstream_call_timeout()) as client:
                        inbound, outbound = await asyncio.gather(one("in"), one("out"))
                break
            except Exception:
                delay = 0.25 * (attempt + 1)
                if attempt == 2 or not should_retry_upstream(attempt, delay):
                    raise
                await asyncio.sleep(delay)
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
