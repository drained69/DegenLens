from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


MINER_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = MINER_ROOT.parent.parent


class Settings(BaseSettings):
    # Use absolute paths so dotenv loading does not depend on where uvicorn was
    # launched. The miner-specific file takes precedence over the workspace file.
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", MINER_ROOT / ".env"),
        extra="ignore",
    )

    # ── Upstream data providers ──────────────────────────────────────────
    # ALCHEMY_KEY is the only key that gates live data. Without it the miner
    # cannot observe chain state and falls back to the labeled demo feed.
    alchemy_key: str = ""
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    tron_api_url: str = "https://api.trongrid.io"
    bitcoin_api_url: str = "https://mempool.space/api"
    # Optional. CoinGecko's free tier works unauthenticated; supplying a key
    # switches to the pro endpoint and raises rate limits.
    coingecko_key: str = ""

    # ── Cache TTLs (seconds) ─────────────────────────────────────────────
    price_ttl: int = 60
    stats_ttl: int = 300
    # How long past the TTL a real reading may still be served while its
    # refresh runs. The deep rescan outlives the TTL, so without this the
    # good answer is discarded every few minutes.
    stats_stale_max_s: float = 900.0

    # ── Coverage ─────────────────────────────────────────────────────────
    # Alchemy returns at most 1000 transfers per call. This caps how many
    # pages we will follow before reporting the window as incomplete —
    # bounding worst-case latency for very busy addresses.
    max_transfer_pages: int = 10
    # Pagination depth for the wallet-attribution scan. That read only has
    # to spot known cluster addresses among recent counterparties, and its
    # interaction counts are explicit lower bounds, so it does not need the
    # full budget.
    association_scan_pages: int = 3
    # Paging depth for reads that must fit a single request deadline (an
    # arbitrary address that no background pass can pre-warm).
    request_page_budget: int = 2
    full_scan_pages: int = 1000
    full_scan_timeout_s: float = 900.0

    # ── Reliability ──────────────────────────────────────────────────────
    # Must stay below request_timeout_s. At 12s a single slow provider call
    # outlived the entire request budget, so the deadline fired before the
    # read it was waiting on could even time out.
    upstream_timeout_s: float = 6.0
    # Alchemy's shared/free plans rate-limit bursts across chain hosts. Keep
    # multi-chain reads broad but serialized enough to avoid turning a burst
    # into a per-chain circuit-breaker outage.
    # Alchemy rate-limits bursty multi-wallet scans. Four concurrent wallet
    # reads gives Stake better total coverage than a faster 429-heavy burst.
    max_upstream_concurrency: int = 4
    # How much of that budget a background cache rebuild may hold. Keeping
    # this well below the total leaves slots free for live requests while a
    # registry-wide rebuild is running.
    max_background_upstream_concurrency: int = 1
    # How a background read waits for live traffic to clear before taking a
    # provider slot, and how long it will defer before going anyway.
    background_yield_poll_s: float = 0.25
    background_yield_max_s: float = 45.0
    # Keep the API deadline below Telegraph/Railway proxy timeouts. Expensive
    # aggregate reads degrade to a structured unavailable answer instead of a
    # proxy-generated 500 that counts as a failed miner response.
    request_timeout_s: float = 8.0
    # Wall-clock a single-address screening read may take before the endpoint
    # gives up and answers with explicit partial coverage. Held well under
    # request_timeout_s: an answer that arrives at the service deadline is
    # scored as a failure, and a low-confidence answer that names what it could
    # not read beats no answer at all.
    risk_read_budget_s: float = 4.5
    # Same reasoning for the direct balance read. Tighter, because a balance is
    # two RPC calls: if it has not answered in this long the provider is not
    # going to, and the enrichment scan still needs room inside the deadline.
    balance_read_budget_s: float = 3.5
    # Wall-clock budget for a registry-wide operator fan-out, held below
    # request_timeout_s so aggregate endpoints return a real (if partially
    # covered) answer instead of tripping the service deadline. Operators not
    # collected in time are reported unread, never as zero.
    flow_budget_s: float = 4.5
    # Allowance for the non-fan-out work in a build (price lookups,
    # scoring). flow_budget_s + this is the hard cap on an inline build,
    # and must stay clear of request_timeout_s.
    flow_overhead_s: float = 1.5
    # How long to stop retrying an inline build that came back empty. A
    # window too big for one budget stays too big; retrying it per request
    # spends the whole budget to reach the same empty answer.
    inline_retry_cooldown_s: float = 60.0
    # Operators are independent, so they are collected in parallel; the
    # per-provider limit above still bounds actual upstream concurrency.
    max_operator_concurrency: int = 5
    # Ceiling for one off-request cache-warming read. Generous because nothing
    # waits on it, but finite so a wedged provider cannot leak tasks.
    flow_warm_timeout_s: float = 180.0
    # Upstream retry ladder. The provider rate-limits bursty multi-wallet
    # scans, and a retry that lands inside the same 429 window is a wasted
    # attempt — these must be long enough to outlast one.
    retry_base_delay_s: float = 0.75
    # Capped so two retries plus their calls still fit inside
    # request_timeout_s — a backoff that outlives the deadline is just a
    # slower way to return nothing.
    max_retry_delay_s: float = 2.0
    circuit_threshold: int = 5
    circuit_cooldown_s: float = 30.0

    # ── Demo mode ────────────────────────────────────────────────────────
    # When no ALCHEMY_KEY is configured the miner CANNOT observe real chain
    # state. It then serves clearly-labeled synthetic data so local dev works,
    # and every response carries data_source="demo".
    #
    # A miner registered on Telegraph MUST run with real keys. Serving invented
    # numbers to the network as if they were real gets graded against actual
    # ground truth and destroys the Canonical Score. Set strict_mode=true to
    # make the miner refuse synthetic answers outright.
    strict_mode: bool = False

    @property
    def live_data_available(self) -> bool:
        return bool(self.alchemy_key)

    def chain_live_data_available(self, chain: str) -> bool:
        """Return whether the adapter for one chain can make live reads."""
        return self.live_data_available


settings = Settings()
