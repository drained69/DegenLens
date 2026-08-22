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
    # Optional. CoinGecko's free tier works unauthenticated; supplying a key
    # switches to the pro endpoint and raises rate limits.
    coingecko_key: str = ""

    # ── Cache TTLs (seconds) ─────────────────────────────────────────────
    price_ttl: int = 60
    stats_ttl: int = 300

    # ── Coverage ─────────────────────────────────────────────────────────
    # Alchemy returns at most 1000 transfers per call. This caps how many
    # pages we will follow before reporting the window as incomplete —
    # bounding worst-case latency for very busy addresses.
    max_transfer_pages: int = 10

    # ── Reliability ──────────────────────────────────────────────────────
    upstream_timeout_s: float = 12.0
    # Alchemy's shared/free plans rate-limit bursts across chain hosts. Keep
    # multi-chain reads broad but serialized enough to avoid turning a burst
    # into a per-chain circuit-breaker outage.
    max_upstream_concurrency: int = 12
    # Keep the API deadline below Telegraph/Railway proxy timeouts. Expensive
    # aggregate reads degrade to a structured unavailable answer instead of a
    # proxy-generated 500 that counts as a failed miner response.
    request_timeout_s: float = 8.0
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


settings = Settings()
