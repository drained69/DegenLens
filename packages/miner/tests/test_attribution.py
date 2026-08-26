"""Tests for attribution discovery.

Discovery proposes labels; it must never apply them. A wrong label silently
corrupts every derived figure and registration is immutable, so the safety
properties here matter more than the ranking quality.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.attribution import Candidate, _score
from app.main import app
from app.wallets import CONFIDENCE_CEILING, CASINOS, observation_targets

client = TestClient(app)


def _candidate(**kw) -> Candidate:
    base = dict(
        address="0x" + "ab" * 20,
        chain="ethereum",
        proposed_operator="stake",
        proposed_operator_name="Stake.com",
    )
    base.update(kw)
    return Candidate(**base)  # type: ignore[arg-type]


# ── Safety: discovery must not mutate the registry ───────────────────────────


def test_discovery_does_not_add_to_registry():
    before = {slug: len(c.wallets) for slug, c in CASINOS.items()}
    client.get("/attribution/discover/stake?hours=24&limit=3")
    after = {slug: len(c.wallets) for slug, c in CASINOS.items()}
    assert before == after, "discovery mutated the wallet registry"


def test_every_candidate_is_flagged_for_review():
    body = client.get("/attribution/discover/stake?hours=24&limit=5").json()
    for c in body.get("candidates", []):
        assert c["review_required"] is True
        assert "does not prove ownership" in c["note"]


def test_discovery_cannot_recommend_verified_status():
    """Only a human attaching a source can reach `verified`."""
    body = client.get("/attribution/discover/stake?hours=24&limit=5").json()
    for c in body.get("candidates", []):
        assert c["recommended_status"] in {"unverified_seed", "curated"}
        assert c["recommended_status"] != "verified"


def test_recommended_confidence_is_capped_at_curated():
    body = client.get("/attribution/discover/stake?hours=24&limit=3").json()
    assert body["max_recommended_confidence"] == CONFIDENCE_CEILING["curated"]
    assert body["max_recommended_confidence"] < CONFIDENCE_CEILING["verified"]


# ── Scoring behaviour ────────────────────────────────────────────────────────


def test_bidirectional_scores_above_one_directional():
    bi = _candidate(
        value_with_cluster_usd=100_000,
        interactions_with_cluster=10,
        bidirectional_with_cluster=True,
    )
    uni = _candidate(
        value_with_cluster_usd=100_000,
        interactions_with_cluster=10,
        bidirectional_with_cluster=False,
    )
    assert _score(bi)[0] > _score(uni)[0]


def test_counterparty_overlap_is_the_strongest_signal():
    """The same users reaching both wallets is hard to fake at scale."""
    with_overlap = _candidate(
        bidirectional_with_cluster=True,
        own_counterparties=100,
        shared_counterparty_overlap=0.40,
    )
    without = _candidate(
        bidirectional_with_cluster=True,
        own_counterparties=100,
        shared_counterparty_overlap=0.0,
    )
    assert _score(with_overlap)[0] > _score(without)[0] + 0.2


def test_hub_shape_lifts_score():
    hub = _candidate(bidirectional_with_cluster=True, own_counterparties=500)
    lone = _candidate(bidirectional_with_cluster=True, own_counterparties=2)
    assert _score(hub)[0] > _score(lone)[0]


def test_score_is_bounded():
    maxed = _candidate(
        value_with_cluster_usd=10_000_000,
        interactions_with_cluster=1000,
        bidirectional_with_cluster=True,
        own_counterparties=5000,
        shared_counterparty_overlap=1.0,
        own_transfers=5000,
        avg_transfer_usd=5_000,
    )
    score, _ = _score(maxed)
    assert 0.0 <= score <= 1.0


def test_every_score_explains_itself():
    for c in (
        _candidate(bidirectional_with_cluster=True),
        _candidate(bidirectional_with_cluster=False),
        _candidate(own_counterparties=300, shared_counterparty_overlap=0.5),
    ):
        _, signals = _score(c)
        assert signals and all(isinstance(s, str) and s for s in signals)


def test_stake_explorer_labels_preserve_published_networks():
    """Retained explorer labels must keep the chain attached to each address.

    These live in `legacy_wallets`: they are the pre-Gamstat migration records,
    never queried, but kept so the chain-specific provenance of each claim
    stays reviewable. An Ethereum label is not evidence of ownership on another
    chain, so collapsing these to a single network would fabricate coverage.
    """
    supplied = [
        wallet for wallet in CASINOS["stake"].legacy_wallets
        if wallet.source == "user-supplied block explorer"
    ]

    assert {wallet.chain for wallet in supplied} == {
        "ethereum", "solana", "tron", "bsc", "polygon", "bitcoin",
    }
    assert next(wallet for wallet in supplied if wallet.address.startswith("G9X7F4")).chain == "solana"
    assert next(wallet for wallet in supplied if wallet.address.startswith("TZ8Ksz")).chain == "tron"
    assert next(wallet for wallet in supplied if wallet.address.startswith("bc1qmd")).chain == "bitcoin"


def test_stake_production_cluster_matches_gamstat_published_pairs():
    stake = CASINOS["stake"]
    assert len(stake.wallets) == 17
    assert all(wallet.source == "https://gamstat.io/casinos/stake" for wallet in stake.wallets)
    assert {(wallet.address.lower(), wallet.chain) for wallet in stake.wallets} == {
        ("0xdf1fc5523f2e5ea4f6dac2eaed3263953a391b0c", "ethereum"),
        ("g9x7f4jzlzbsgmcndibdwni5yzzzakmtkdwq7xs3q3fe", "solana"),
        ("tz8ksz21hk1tquztckcujbrxstcav9uyjm", "tron"),
        ("0x6872b6630a3afcd3117191a8403c2002e13df7de", "ethereum"),
        ("0x974caa59e49682cda0ad2bbe82983419a2ecc400", "ethereum"),
        ("0x6872b6630a3afcd3117191a8403c2002e13df7de", "bsc"),
        ("0xfa500178de024bf43cfa69b7e636a28ab68f2741", "bsc"),
        ("0x6872b6630a3afcd3117191a8403c2002e13df7de", "polygon"),
        ("0x787b8840100d9baadd7463f4a73b5ba73b00c6ca", "ethereum"),
        ("0x019d0706d65c4768ec8081ed7ce41f59eef9b86c", "polygon"),
        ("0x6e29f75b0350fd0e85ee34a21ef94767b0186996", "ethereum"),
        ("bc1qmd3nsuw3z7fwr3wt7ac7ydceyeyu2cflft4ltm", "bitcoin"),
        ("0xd523794c879d9ec028960a231f866758e405be34", "ethereum"),
        ("0x019d0706d65c4768ec8081ed7ce41f59eef9b86c", "ethereum"),
        ("0xdebfbe80c8aeba98a32968278463ccb639c6c4e3", "ethereum"),
        ("0x0392b64b8bfda184f0a72ce37d73dc7df978c4f7", "ethereum"),
        ("0xb04c0eb29c72cebc467b9d4944d29116fa02c44a", "ethereum"),
    }


def test_gamstat_roles_are_not_flattened_to_hot():
    """Cold reserves and consolidation addresses are not payout wallets.

    Reporting them as `hot` would overstate how much of the observed flow is
    player-facing, which is the single easiest way for this registry to
    mislead.
    """
    by_pair = {
        (w.address.lower(), w.chain): w for w in CASINOS["stake"].wallets
    }
    assert by_pair[("0xd523794c879d9ec028960a231f866758e405be34", "ethereum")].role == "cold"
    assert by_pair[("bc1qmd3nsuw3z7fwr3wt7ac7ydceyeyu2cflft4ltm", "bitcoin")].role == "cold"
    assert by_pair[("0x0392b64b8bfda184f0a72ce37d73dc7df978c4f7", "ethereum")].role == "consolidation"

    rollbit = {(w.address.lower(), w.chain): w for w in CASINOS["rollbit"].wallets}
    # Gamstat declines to classify these two; so do we.
    assert rollbit[("0x8ae57a027c63fca8070d1bf38622321de8004c67", "ethereum")].role == "unknown"
    assert rollbit[("0x46dca395d20e63cb0fe1edc9f0e6f012e77c0913", "ethereum")].role == "unknown"
    # Every Rollbit BTC address is a cold reserve, not a hot wallet.
    assert all(
        w.role == "cold" for w in CASINOS["rollbit"].wallets if w.chain == "bitcoin"
    )


def test_low_source_confidence_is_carried_through_not_raised_to_the_ceiling():
    """The curated ceiling caps a claim; it must never inflate one.

    Gamstat rates one Stake address 0.3 and flags it with a question mark.
    Publishing that at our 0.75 curated ceiling would convert the source's
    stated doubt into our confidence.
    """
    doubtful = next(
        w for w in CASINOS["stake"].wallets
        if w.address.lower() == "0x019d0706d65c4768ec8081ed7ce41f59eef9b86c"
        and w.chain == "ethereum"
    )
    assert doubtful.source_confidence == 0.3
    assert doubtful.confidence == 0.3
    assert doubtful.label == "Stake cold/reserve?"

    # A high source confidence is still capped by our own provenance ceiling.
    confident = next(
        w for w in CASINOS["yeet"].wallets
        if w.chain == "tron"
    )
    assert confident.source_confidence == 0.97
    assert confident.confidence == CONFIDENCE_CEILING["curated"]

    for casino in CASINOS.values():
        for w in casino.wallets:
            if w.source_confidence is None:
                continue
            assert w.confidence == min(
                w.source_confidence, CONFIDENCE_CEILING[w.evidence_status]
            ), f"{w.address} on {w.chain} does not honour min(source, ceiling)"


def test_rollbit_cluster_keeps_every_published_cold_wallet():
    """A dropped cold reserve silently understates observed treasury."""
    rollbit = CASINOS["rollbit"]
    assert len(rollbit.wallets) == 11
    assert {w.address for w in rollbit.wallets if w.chain == "bitcoin"} == {
        "3Hhh16urMb1fy6mk4jkjYyh4yiRzqyeUNT",
        "3MNNwkVDPWeysqKqp2PCMieia5aSQrasms",
        "3LHMJGV9nzVN4H714yEUTeXZaju91RVvAH",
        "3LyMZcfRiFbyYqi63RUpq53nL4gygMTfnU",
        "39oL1SZiSJWnCdn7uM5xrjbvE8hFMgPnoa",
    }


def test_every_gamstat_wallet_carries_its_source_label():
    for slug in ("stake", "rollbit", "bcgame", "shuffle", "yeet"):
        for w in CASINOS[slug].wallets:
            assert w.label, f"{slug} {w.address} on {w.chain} lost its source label"


def test_yeet_gamstat_cluster_preserves_all_seven_networks():
    targets = observation_targets(CASINOS["yeet"])

    assert len(targets) == 7
    assert {wallet.chain for wallet in targets} == {
        "ethereum", "tron", "solana", "polygon", "bsc", "base", "arbitrum",
    }
    assert all(wallet.source == "https://gamstat.io/casinos/yeet" for wallet in targets)


def test_only_five_operators_are_attributed_and_all_published_chains_are_kept():
    attributed = {casino.slug for casino in CASINOS.values() if casino.is_attributed}
    assert attributed == {"stake", "rollbit", "bcgame", "shuffle", "yeet"}
    assert {wallet.chain for wallet in CASINOS["rollbit"].wallets} == {
        "ethereum", "polygon", "solana", "bitcoin"
    }
    assert {wallet.chain for wallet in CASINOS["bcgame"].wallets} == {
        "ethereum", "base", "polygon", "arbitrum", "bsc", "solana", "bitcoin", "tron"
    }
    assert {wallet.chain for wallet in CASINOS["shuffle"].wallets} == {
        "ethereum", "base", "polygon", "arbitrum", "bsc", "solana", "tron"
    }
    assert {wallet.chain for wallet in CASINOS["yeet"].wallets} == {
        "ethereum", "base", "polygon", "arbitrum", "bsc", "solana", "tron"
    }


# ── Endpoint contract ────────────────────────────────────────────────────────


def test_unattributed_operator_cannot_be_expanded():
    """Discovery grows a cluster; it cannot bootstrap one from nothing."""
    body = client.get("/attribution/discover/gamdom?hours=24").json()
    assert body["verdict"] == "not_expandable"
    assert body["confidence"] == 0.0
    assert body["candidates"] == []


def test_discovery_honors_signal_mapping():
    for path in (
        "/attribution/discover/stake?hours=24&limit=3",
        "/attribution/discover?hours=24&per_operator=2",
    ):
        body = client.get(path).json()
        assert 0.0 <= body["confidence"] <= 1.0
        assert body["verdict"]
        assert body["reasoning"]
        assert body["data_source"]


def test_discovery_confidence_describes_search_not_ownership():
    """Confidence here is about the search having run, never about a label."""
    body = client.get("/attribution/discover/stake?hours=24&limit=3").json()
    assert body["confidence"] <= 0.6
    assert "never proves ownership" in body["reasoning"]


def test_methodology_is_published():
    body = client.get("/attribution/discover/stake?hours=24&limit=3").json()
    assert "review queue, not an attribution" in body["methodology"]
