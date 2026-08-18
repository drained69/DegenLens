"""Tests for player evaluation.

The classifier is the whole feature. A naive net-position sort ranks treasury
movements and exchange wallets as "top winners", so most of these tests pin the
behaviour that prevents that.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.players import (
    BIDIRECTIONAL_MIN_RATIO,
    INDIVIDUAL_AVG_TRANSFER_CEILING_USD,
    _classify_entity,
)

client = TestClient(app)


# ── Classifier ───────────────────────────────────────────────────────────────


def test_whale_inflow_is_not_a_player():
    """The exact failure this feature must avoid: $3M received, nothing sent,
    ranked as the biggest winner.

    Two independent rules reject this — implausible average transfer size, and
    absence of a round trip. Either is a correct rejection, so the assertion is
    on the outcome rather than on which rule fired.
    """
    cls, reasons = _classify_entity(
        operators_touched=1,
        gross_usd=3_000_000,
        transfers=2,
        distinct_counterparties=0,
        sent_usd=0.0,
        received_usd=3_000_000.0,
    )
    assert cls != "individual_candidate"
    assert reasons


def test_one_directional_rule_fires_at_individual_scale():
    """Isolate the round-trip rule: transfer sizes are plausible for a person,
    but every transfer goes the same way, so there is nothing to net out."""
    cls, reasons = _classify_entity(
        operators_touched=1,
        gross_usd=40_000,
        transfers=20,  # avg $2k — well under the individual ceiling
        distinct_counterparties=0,
        sent_usd=0.0,
        received_usd=40_000.0,
    )
    assert cls == "one_directional"
    assert any("inbound only" in r for r in reasons)


def test_one_directional_outflow_is_not_a_player():
    cls, reasons = _classify_entity(
        1, 40_000, 20, 0, sent_usd=40_000.0, received_usd=0.0
    )
    assert cls == "one_directional"
    assert any("outbound only" in r for r in reasons)


def test_bidirectional_flow_at_individual_scale_is_a_candidate():
    cls, _ = _classify_entity(
        operators_touched=1,
        gross_usd=60_000,
        transfers=12,
        distinct_counterparties=0,
        sent_usd=35_000.0,
        received_usd=25_000.0,
    )
    assert cls == "individual_candidate"


def test_many_operators_reads_as_infrastructure():
    cls, reasons = _classify_entity(6, 40_000, 20, 0, 20_000.0, 20_000.0)
    assert cls == "infrastructure"
    assert any("distinct operators" in r for r in reasons)


def test_implausible_average_transfer_reads_as_infrastructure():
    """Bidirectional but moving a quarter million per transfer."""
    big = INDIVIDUAL_AVG_TRANSFER_CEILING_USD * 4
    cls, reasons = _classify_entity(1, big, 2, 0, big / 2, big / 2)
    assert cls == "infrastructure"
    assert any("implausible for an individual" in r for r in reasons)


def test_dust_is_low_activity_not_a_player():
    cls, _ = _classify_entity(1, 50, 2, 0, 25.0, 25.0)
    assert cls == "low_activity"


def test_bidirectional_threshold_boundary():
    """Just inside the ratio counts; just outside does not."""
    strong = 100_000.0
    inside = strong * (BIDIRECTIONAL_MIN_RATIO * 2)
    outside = strong * (BIDIRECTIONAL_MIN_RATIO / 2)

    cls_in, _ = _classify_entity(1, strong + inside, 20, 0, inside, strong)
    cls_out, _ = _classify_entity(1, strong + outside, 20, 0, outside, strong)

    assert cls_in == "individual_candidate"
    assert cls_out == "one_directional"


def test_classification_always_explains_itself():
    for args in [
        (1, 3_000_000, 2, 0, 0.0, 3_000_000.0),
        (6, 40_000, 20, 0, 20_000.0, 20_000.0),
        (1, 50, 2, 0, 25.0, 25.0),
        (1, 60_000, 12, 0, 35_000.0, 25_000.0),
    ]:
        _, reasons = _classify_entity(*args)
        assert reasons and all(isinstance(r, str) and r for r in reasons)


# ── Endpoint contract ────────────────────────────────────────────────────────


def test_evaluate_never_claims_profit_and_loss():
    """Vocabulary discipline: no key or caveat may call this P&L."""
    body = client.post(
        "/player/evaluate",
        json={"address": "0x" + "11" * 20, "chain": "ethereum", "hours": 24},
    ).json()

    for key in body:
        assert "profit" not in key.lower()
        assert "winnings" not in key.lower()
        assert "pnl" not in key.lower()

    assert "net_position_usd" in body
    assert "not gambling profit and loss" in body["caveat"]


def test_evaluate_honors_signal_mapping():
    body = client.post(
        "/player/evaluate", json={"address": "0x" + "22" * 20, "hours": 24}
    ).json()
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["verdict"]
    assert body["reasoning"]
    assert body["data_source"]


def test_evaluate_confidence_is_capped_by_offchain_invisibility():
    """Even a perfect chain read cannot see balances held inside an operator."""
    body = client.post(
        "/player/evaluate", json={"address": "0x" + "33" * 20, "hours": 24}
    ).json()
    assert body["confidence"] <= 0.5


def test_operator_wallet_is_not_evaluated_as_a_player():
    stake_hot = "0x974caa59e49682cda0ad2bbe82983419a2ecc400"
    body = client.post(
        "/player/evaluate", json={"address": stake_hot, "hours": 24}
    ).json()
    assert body["is_operator_wallet"] is True
    assert body["verdict"] == "operator_wallet"
    assert body["entity_class"] == "operator_wallet"


def test_no_activity_reads_as_absence_not_zero_position():
    body = client.post(
        "/player/evaluate", json={"address": "0x" + "ab" * 20, "hours": 24}
    ).json()
    if body["transfers_with_operators"] == 0:
        assert body["verdict"] == "no_operator_activity"
        assert "only clusters we have labeled" in body["reasoning"]


def test_leaderboard_reports_what_it_excluded():
    body = client.get("/players/leaderboard?hours=24&limit=5").json()
    assert "class_counts" in body
    assert "one_directional_excluded" in body
    assert "infrastructure_excluded" in body
    # Excluded one-directional flow is surfaced separately, not silently dropped.
    assert "largest_one_directional" in body
    assert "not gambling profit and loss" in body["methodology"]


def test_leaderboard_ranks_only_individual_candidates_by_default():
    body = client.get("/players/leaderboard?hours=24&limit=10").json()
    for row in body["net_positive"] + body["net_negative"]:
        assert row["entity_class"] == "individual_candidate"
