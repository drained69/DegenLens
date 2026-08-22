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
    """Stake coverage must retain the chain attached to each source address."""
    targets = observation_targets(CASINOS["stake"])
    supplied = [
        wallet for wallet in targets
        if wallet.source == "user-supplied block explorer"
    ]

    assert {wallet.chain for wallet in supplied} == {
        "ethereum", "solana", "tron", "bsc", "polygon", "bitcoin",
    }
    assert next(wallet for wallet in supplied if wallet.address.startswith("G9X7F4")).chain == "solana"
    assert next(wallet for wallet in supplied if wallet.address.startswith("TZ8Ksz")).chain == "tron"
    assert next(wallet for wallet in supplied if wallet.address.startswith("bc1qmd")).chain == "bitcoin"


def test_yeet_gamstat_cluster_preserves_all_seven_networks():
    targets = observation_targets(CASINOS["yeet"])

    assert len(targets) == 7
    assert {wallet.chain for wallet in targets} == {
        "ethereum", "tron", "solana", "polygon", "bsc", "base", "arbitrum",
    }
    assert all(wallet.source == "https://gamstat.io/casinos/yeet" for wallet in targets)


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
