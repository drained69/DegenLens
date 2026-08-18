from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    max_upstream_concurrency: int = 8
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
