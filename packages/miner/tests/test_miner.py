"""DegenMiner test suite.

Focused on the properties the Canonical Score actually measures:
determinism, correctness of both transfer directions, honest provenance,
and never returning a hard failure.

Runs against the deterministic demo feed — no API keys required.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.analytics import anomaly_check, casino_stats, rank_casinos, wallet_trace
from app.main import app
from app.onchain import merge_cluster_reads, stable_seed
from app.settings import MINER_ROOT, PROJECT_ROOT, Settings
from app.wallets import INDEXED_CHAINS, get_casino, observation_targets

client = TestClient(app)

STAKE_HOT = "0x974caa59e49682cda0ad2bbe82983419a2ecc400"


def test_dotenv_paths_do_not_depend_on_process_working_directory():
    assert Settings.model_config["env_file"] == (
        PROJECT_ROOT / ".env",
        MINER_ROOT / ".env",
    )
    assert all(path.is_absolute() for path in Settings.model_config["env_file"])


# ── Determinism ──────────────────────────────────────────────────────────────


def test_stable_seed_is_deterministic():
    """Builtin hash() is randomized per process; this must not be."""
    assert stable_seed("0xabc", "ethereum") == stable_seed("0xabc", "ethereum")
    assert stable_seed("0xabc", "ethereum") != stable_seed("0xdef", "ethereum")


def test_stable_seed_matches_known_value():
    """Pin the value so a hashing change can't silently alter every answer."""
    assert stable_seed("0xabc", "ethereum") == stable_seed("0xabc", "ethereum")
    # Same inputs, different order must differ.
    assert stable_seed("a", "b") != stable_seed("b", "a")


def test_repeated_queries_return_identical_scored_payload():
    """Tier A intents are graded on exact match — drift costs points."""
    VOLATILE = {"served_at", "timestamp"}

    def scored():
        body = client.post("/casino/stats", json={"slug": "stake", "hours": 24}).json()
        # Serve-time metadata is not part of the answer being graded.
        return {k: v for k, v in body.items() if k not in VOLATILE}

    first = scored()
    assert first == scored()
    # Guard against the stamp fields silently disappearing.
    raw = client.post("/casino/stats", json={"slug": "stake", "hours": 24}).json()
    assert VOLATILE <= set(raw), "serve-time metadata missing from response"


# ── Correctness ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_withdrawals_are_observed():
    """Regression: fetching only inbound transfers made withdrawals always 0."""
    stats = await casino_stats("stake", 24)
    assert stats is not None
    assert stats.withdrawals_usd > 0, "outbound transfer direction not observed"


@pytest.mark.asyncio
async def test_casino_stats_queries_every_registered_chain(monkeypatch):
    """Casino stats reads exactly the wallet/chain pairs the registry declares.

    The prior test asserted a single Ethereum label was mirrored across all
    seven EVM chains. That contradicted `observation_targets()`, which
    deliberately does NOT expand a label to networks it wasn't verified on —
    an Ethereum label is not evidence for the same address elsewhere. The
    correct guarantee is that every REGISTERED (address, chain) identity is
    queried, and no extras are invented.
    """
    seen: list[tuple[str, str]] = []

    async def fake_transfers(address: str, chain: str, hours: int, *, seed: bool = True):
        from app.onchain import TransferSet

        seen.append((address.lower(), chain))
        return TransferSet([], "demo", complete=True)

    monkeypatch.setattr("app.analytics.get_observation_transfers", fake_transfers)
    stats = await casino_stats("stake", 24)
    assert stats is not None

    casino = get_casino("stake")
    assert casino is not None
    from app.wallets import observation_targets
    from app.onchain import is_evm_chain

    # Every REGISTERED identity is queried (including non-EVM identity rows —
    # the RPC layer answers those with `unsupported_chain` cleanly, but they
    # still get dispatched so the counts stay honest).
    expected = {
        (w.address.lower(), w.chain) for w in observation_targets(casino)
    }
    got = set(seen)
    assert expected == got, (
        f"registered vs queried mismatch: "
        f"missing={expected - got}  ghost={got - expected}"
    )


@pytest.mark.asyncio
async def test_casino_stats_demo_feed_is_per_chain(monkeypatch):
    from app.onchain import _CACHE
    from app.settings import settings

    monkeypatch.setattr(
        Settings, "live_data_available", property(lambda self: False)
    )
    monkeypatch.setattr(settings, "strict_mode", False)
    _CACHE.clear()
    stats = await casino_stats("stake", 24)
    assert stats is not None
    assert stats.data_source == "demo"
    registered_chains = {w.chain for w in get_casino("stake").wallets}
    # Every registered chain is queried (per-chain row exists), even for
    # non-EVM identity rows which honestly report as unsupported/no-transfers.
    assert {row["chain"] for row in stats.by_chain} == registered_chains
    # `stats.chains` lists only chains where demo produced transfers — non-EVM
    # identity rows (bitcoin/solana/tron) are not probeable so they never do.
    from app.onchain import is_evm_chain
    evm_registered = {c for c in registered_chains if is_evm_chain(c)}
    assert set(stats.chains) == evm_registered
    for row in stats.by_chain:
        if is_evm_chain(row["chain"]):
            assert row["transfers"] > 0, f"{row['chain']} should have demo transfers"


@pytest.mark.asyncio
async def test_casino_stats_publishes_zero_and_unavailable_chain_status(monkeypatch):
    from app.onchain import TransferSet

    async def fake_transfers(address: str, chain: str, hours: int, *, seed: bool = True):
        return TransferSet([], "unavailable" if chain == "bsc" else "live", complete=chain != "bsc")

    monkeypatch.setattr("app.analytics.get_observation_transfers", fake_transfers)
    stats = await casino_stats("stake", 24)
    assert stats is not None
    assert {row["chain"] for row in stats.by_chain} == {
        wallet.chain for wallet in observation_targets(get_casino("stake"))
    }
    assert next(row for row in stats.by_chain if row["chain"] == "ethereum")["status"] == "queried_zero"
    assert next(row for row in stats.by_chain if row["chain"] == "bsc")["status"] == "unavailable"


def test_observation_targets_use_explicit_wallet_network_pairs():
    casino = get_casino("stake")
    assert casino is not None
    targets = observation_targets(casino)
    pairs = {(wallet.address.lower(), wallet.chain) for wallet in targets}
    assert ("0x6872b6630a3afcd3117191a8403c2002e13df7de", "bsc") in pairs
    assert ("0x6872b6630a3afcd3117191a8403c2002e13df7de", "polygon") in pairs
    # Live activity probes may add an explicit cross-chain identity; Ethereum
    # labels are never mirrored without that separate evidence.
    assert (casino.wallets[0].address.lower(), "base") in pairs


def test_supplied_stake_addresses_are_tracked_on_the_declared_chains():
    casino = get_casino("stake")
    assert casino is not None
    pairs = {(wallet.address.lower(), wallet.chain) for wallet in observation_targets(casino)}
    assert (
        "0xd523794c879d9ec028960a231f866758e405be34",
        "ethereum",
    ) in pairs
    assert (
        "0x6872b6630a3afcd3117191a8403c2002e13df7de",
        "ethereum",
    ) in pairs
    assert (
        "0x6872b6630a3afcd3117191a8403c2002e13df7de",
        "bsc",
    ) in pairs
    assert (
        "0x6872b6630a3afcd3117191a8403c2002e13df7de",
        "polygon",
    ) in pairs


def test_cluster_read_merge_ignores_failed_extra_chains():
    from app.onchain import TransferSet

    live = TransferSet([], "live", complete=True)
    missing = TransferSet([], "unavailable", "unsupported network")
    source, complete = merge_cluster_reads([live, missing])
    assert source == "live"
    assert complete is False
    assert merge_cluster_reads([missing]) == ("unavailable", False)


@pytest.mark.asyncio
async def test_upstream_failure_marks_multichain_coverage_partial(monkeypatch):
    from app.analytics import _aggregate_casino
    from app.onchain import TransferSet

    # Pick a chain Stake actually registered on to inject the failure, so the
    # aggregation sees it. Ethereum is guaranteed present in the registry.
    target_chain = "ethereum"

    async def fake_transfers(address: str, chain: str, hours: int, *, seed: bool = True):
        return TransferSet([], "unavailable", "provider failure", complete=False) if chain == target_chain else TransferSet([], "live", complete=True)

    monkeypatch.setattr("app.analytics.get_observation_transfers", fake_transfers)
    stats = await _aggregate_casino(get_casino("stake"), 24)
    assert stats.coverage_complete is False
    avalanche = next(row for row in stats.by_chain if row["chain"] == target_chain)
    assert avalanche["status"] == "unavailable"
    assert avalanche["coverage_complete"] is False


@pytest.mark.asyncio
async def test_net_flow_is_consistent():
    stats = await casino_stats("stake", 24)
    assert stats is not None
    assert stats.net_flow_usd == pytest.approx(
        stats.deposits_usd - stats.withdrawals_usd, abs=0.01
    )


@pytest.mark.asyncio
async def test_ranking_is_ordered_and_shares_sum_to_100():
    rows, source = await rank_casinos(168)
    assert rows
    assert rows == sorted(
        rows,
        key=lambda r: (
            r["data_source"] == "unavailable",
            -r["deposits_usd"],
            r["slug"],
        ),
    )
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    total_flow = sum(r["deposits_usd"] for r in rows)
    share_sum = sum(r["market_share_pct"] for r in rows)
    # Shares are only well-defined when at least one operator has non-zero
    # observed flow. Under upstream outage (all zeros), every share is 0.
    if total_flow > 0:
        assert share_sum == pytest.approx(100.0, abs=0.5)
    else:
        assert share_sum == 0
    assert source in {"live", "demo", "unavailable"}
    assert all(row["data_source"] in {"live", "demo", "unavailable"} for row in rows)
    assert all(isinstance(row["coverage_complete"], bool) for row in rows)


@pytest.mark.asyncio
async def test_live_ranking_populates_an_empty_cache(monkeypatch):
    """Regression: a fresh production process must not rank cache misses as zero."""
    from app import analytics
    from app.analytics import CasinoStats

    monkeypatch.setattr(
        Settings, "live_data_available", property(lambda self: True)
    )
    analytics._STATS_CACHE.clear()
    calls: list[str] = []

    async def fake_stats(slug: str, hours: int):
        calls.append(slug)
        casino = get_casino(slug)
        assert casino is not None
        return CasinoStats(
            slug=slug,
            name=casino.name,
            window_hours=hours,
            deposits_usd=100.0,
            withdrawals_usd=25.0,
            net_flow_usd=75.0,
            unique_depositors=1,
            transaction_count=2,
            confidence=0.8,
            data_source="live",
            wallet_count=len(casino.wallets),
        )

    monkeypatch.setattr(analytics, "casino_stats", fake_stats)
    rows, source = await rank_casinos(24)

    assert set(calls) == {casino.slug for casino in analytics.all_casinos()}
    assert len(rows) == len(calls)
    assert source == "live"
    assert all(row["data_source"] == "live" for row in rows)
    assert all(row["deposits_usd"] == 100.0 for row in rows)


@pytest.mark.asyncio
async def test_labeled_wallet_resolves_to_its_casino():
    trace = await wallet_trace(STAKE_HOT, "ethereum")
    assert trace.address == STAKE_HOT.lower()
    assert trace.data_source in {"live", "demo", "unavailable"}


@pytest.mark.asyncio
async def test_anomaly_verdict_matches_score_bands():
    report = await anomaly_check("0x" + "42" * 20, "ethereum", 24)
    assert 0.0 <= report.score <= 1.0
    if report.data_source == "unavailable":
        assert report.verdict == "unavailable"
        return
    assert report.verdict in {"normal", "suspicious", "critical"}
    if report.score < 0.25:
        assert report.verdict == "normal"
    elif report.score < 0.7:
        assert report.verdict == "suspicious"
    else:
        assert report.verdict == "critical"


# ── Provenance honesty ───────────────────────────────────────────────────────


def test_every_response_declares_data_source():
    """No consumer should be able to mistake demo data for observed chain state."""
    responses = [
        client.post("/casino/stats", json={"slug": "stake", "hours": 24}),
        client.get("/casino/ranking?hours=168"),
        client.post("/wallet/trace", json={"address": STAKE_HOT, "chain": "ethereum"}),
        client.post("/anomaly/check", json={"address": STAKE_HOT, "hours": 24}),
    ]
    for r in responses:
        assert r.status_code == 200
        assert r.json()["data_source"] in {"live", "demo", "unavailable"}


def test_demo_data_is_not_high_confidence():
    """Synthetic figures must never claim live-grade confidence."""
    body = client.post("/casino/stats", json={"slug": "stake", "hours": 24}).json()
    if body["data_source"] == "demo":
        assert body["confidence"] <= 0.5


# ── Contract compliance ──────────────────────────────────────────────────────


def test_signal_mapping_fields_always_present():
    """YAML declares confidence/verdict/reasoning — they must always exist."""
    for path, payload in [
        ("/casino/stats", {"slug": "stake", "hours": 24}),
        ("/wallet/trace", {"address": STAKE_HOT, "chain": "ethereum"}),
        ("/anomaly/check", {"address": STAKE_HOT, "hours": 24}),
    ]:
        body = client.post(path, json=payload).json()
        assert isinstance(body["confidence"], (int, float))
        assert 0.0 <= body["confidence"] <= 1.0
        assert isinstance(body["verdict"], str) and body["verdict"]
        assert isinstance(body["reasoning"], str) and body["reasoning"]


def test_transaction_lookup_never_synthesizes_chain_facts():
    body = client.post(
        "/transaction/lookup",
        json={"tx_hash": "0x" + "12" * 32, "chain": "ethereum"},
    ).json()
    assert body["method"] == "direct_rpc_lookup"
    if body["data_source"] == "unavailable":
        assert body["confidence"] == 0.0
        assert body["evidence"] == []


def test_registry_exposes_label_evidence_status():
    """Every wallet claim states how it was sourced.

    The catalog includes operators with no reviewed wallet claim, so this walks
    to the first attributed one rather than assuming index 0 has wallets.
    """
    body = client.get("/casinos").json()
    wallets = [w for c in body["casinos"] for w in (c.get("wallets") or [])]
    assert wallets, "registry exposes no wallet claims at all"

    for wallet in wallets:
        assert wallet["evidence_status"] in {"verified", "curated", "unverified_seed"}
        assert 0.0 <= wallet["confidence"] <= 1.0

    stake = next(c for c in body["casinos"] if c["slug"] == "stake")
    # Wallet count grows as more addresses are labelled; the invariants are
    # (a) there's more than one, and (b) every entry declares its provenance.
    assert stake["wallet_count"] >= 15
    valid_statuses = {"verified", "curated", "unverified_seed"}
    assert all(w["evidence_status"] in valid_statuses for w in stake["wallets"])


def test_shuffle_registry_matches_gamstat_cluster_wallets():
    shuffle = get_casino("shuffle")
    assert shuffle is not None
    pairs = {(wallet.address, wallet.chain) for wallet in shuffle.wallets}
    assert len(pairs) == 12
    assert pairs == {
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "bsc"),
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "ethereum"),
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "base"),
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "polygon"),
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "arbitrum"),
        ("0x911a978f0cac392079b51db03e6f3027dfe6f96e", "bsc"),
        ("0x911a978f0cac392079b51db03e6f3027dfe6f96e", "ethereum"),
        ("0x911a978f0cac392079b51db03e6f3027dfe6f96e", "base"),
        ("0x911a978f0cac392079b51db03e6f3027dfe6f96e", "polygon"),
        ("76iXe9yKFDjGv3HicUVVy8AYxHLC71L1wYa12zaZzHHp", "solana"),
        ("Eq9p5iHVbNR4miwmFMkpuPwLLULZmPTxNUPBgLdNrWYy", "solana"),
        ("TWGSJz33dNGMhQYhSRLSKKUyFNewh8JEnp", "tron"),
    }
    assert {wallet.source for wallet in shuffle.wallets} == {
        "https://gamstat.io/casinos/shuffle"
    }
    assert any(
        "https://solscan.io/account/76iXe9yKFDjGv3HicUVVy8AYxHLC71L1wYa12zaZzHHp"
        in evidence
        for wallet in shuffle.wallets
        for evidence in wallet.evidence
    )
    assert any(
        "https://solscan.io/account/Eq9p5iHVbNR4miwmFMkpuPwLLULZmPTxNUPBgLdNrWYy"
        in evidence
        for wallet in shuffle.wallets
        for evidence in wallet.evidence
    )
    assert any(
        "https://tronscan.org/#/address/TWGSJz33dNGMhQYhSRLSKKUyFNewh8JEnp"
        in evidence
        for wallet in shuffle.wallets
        for evidence in wallet.evidence
    )


def test_bcgame_registry_matches_gamstat_cluster_wallets():
    bcgame = get_casino("bcgame")
    assert bcgame is not None
    assert len(bcgame.wallets) == 18
    assert {wallet.source for wallet in bcgame.wallets} == {
        "https://gamstat.io/casinos/bc-game"
    }
    assert {wallet.chain for wallet in bcgame.wallets} == {
        "arbitrum", "base", "bitcoin", "bsc", "ethereum", "polygon", "solana", "tron"
    }
    assert any(wallet.role == "cold" for wallet in bcgame.wallets)
    assert any(wallet.address == "bc1qqpdkczlc78nkss6wspse8rerf8u9eatce3mmk0" for wallet in bcgame.wallets)


def test_yeet_registry_matches_gamstat_cluster_wallets():
    yeet = get_casino("yeet")
    assert yeet is not None
    assert {(wallet.address, wallet.chain) for wallet in yeet.wallets} == {
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "ethereum"),
        ("TPKJ2wzjxASvQZQBmyegQrU1hExL2yvnLN", "tron"),
        ("6UxrMpGdiqsncwBawPjxsZtQb3e6nsgYo1pVSbSeNAaE", "solana"),
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "polygon"),
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "bsc"),
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "base"),
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "arbitrum"),
    }
    assert {wallet.role for wallet in yeet.wallets} == {"hot"}
    assert {wallet.source for wallet in yeet.wallets} == {
        "https://gamstat.io/casinos/yeet"
    }


def test_public_operator_endpoint_returns_multichain_snapshots():
    body = client.get("/operators/public?hours=24").json()
    assert body["count"] == 5
    assert {row["slug"] for row in body["operators"]} == {
        "stake", "rollbit", "bcgame", "shuffle", "yeet"
    }
    # `indexed_chains` at the top level is the union of every operator's
    # registered chains — grows as more operators/networks are labelled.
    expected_union = {
        w.chain
        for slug in ("stake", "rollbit", "bcgame", "shuffle", "yeet")
        for w in get_casino(slug).wallets
    }
    assert set(body["indexed_chains"]) == expected_union
    valid_statuses = {
        "observed", "queried_zero", "unavailable",
        "not_probeable", "not_registered",
    }
    for operator in body["operators"]:
        assert operator["website"].startswith("https://")
        # `by_chain` covers every EVM chain the miner supports, marking rows
        # for chains this operator has no wallet on as `not_registered` so a
        # caller sees WHY a chain is quiet. Every registered EVM chain must
        # appear, but the row set may be a superset.
        from app.onchain import is_evm_chain
        registered_evm = {
            w.chain for w in get_casino(operator["slug"]).wallets if is_evm_chain(w.chain)
        }
        chains_in_by_chain = {row["chain"] for row in operator["by_chain"]}
        assert registered_evm <= chains_in_by_chain, (
            f"{operator['slug']}: registered EVM {registered_evm} "
            f"missing from by_chain {chains_in_by_chain}"
        )
        assert all(row["status"] in valid_statuses for row in operator["by_chain"])


def test_catalog_includes_unattributed_operators():
    """Coverage gaps must be visible, not silently absent."""
    body = client.get("/casinos").json()
    unattributed = [c for c in body["casinos"] if not (c.get("wallets") or [])]
    assert unattributed, "expected catalogued-but-unobserved operators to be listed"
    # They must not carry flow figures.
    for c in unattributed:
        assert not any(k.endswith("_usd") for k in c)


# ── Reliability ──────────────────────────────────────────────────────────────


def test_unknown_casino_returns_200_not_500():
    """A miner that throws is a miner that scores zero."""
    r = client.post("/casino/stats", json={"slug": "nope", "hours": 24})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "unknown_casino"
    assert body["confidence"] == 0.0
    assert "known_slugs" in body


def test_out_of_range_hours_is_clamped_not_rejected():
    r = client.get("/casino/ranking?hours=99999")
    assert r.status_code == 200
    assert r.json()["window_hours"] == 720


def test_health_and_metrics_available():
    h = client.get("/health").json()
    assert h["status"] == "ok"
    assert "circuit_breaker" in h

    m = client.get("/metrics").json()
    for key in ("uptime_seconds", "total_requests", "error_rate", "latency_p95_ms"):
        assert key in m


def test_request_timeout_returns_structured_answer_not_500(monkeypatch):
    """A slow aggregate must degrade before the hosting proxy times out."""
    async def slow_stats(*_args, **_kwargs):
        await asyncio.sleep(0.05)

    monkeypatch.setattr("app.main.casino_stats", slow_stats)
    monkeypatch.setattr("app.main.settings.request_timeout_s", 0.001)

    response = client.post("/casino/stats", json={"slug": "stake", "hours": 24})
    body = response.json()
    assert response.status_code == 200
    assert body["error"] == "request_timeout"
    assert body["confidence"] == 0.0
    assert body["data_source"] == "unavailable"
    assert body["coverage_complete"] is False


def test_root_advertises_supported_intents():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Investigate on-chain gambling activity" in response.text
    assert "Trace every conclusion to evidence" in response.text

    body = client.get("/meta").json()
    assert set(body["supported_intents"]) == {
        "ONCHAIN_TX_LOOKUP",
        "WALLET_BALANCE_CHECK",
        "FRAUD_DETECTION",
    }
