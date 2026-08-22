"""Tests for market-level analysis.

The properties under test are mostly epistemic: that unobserved operators never
render as zero, that shares are labelled as shares of *observed* flow, and that
truncated windows are reported rather than silently presented as complete.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import market
from app.main import app
from app.wallets import CONFIDENCE_CEILING, attributed_operators, catalog

client = TestClient(app)


# ── Registry invariants ──────────────────────────────────────────────────────


def test_unattributed_operators_are_catalogued_but_excluded_from_stats():
    """An operator with no wallet claim must never contribute a zero."""
    everything = catalog()
    attributed = attributed_operators()
    assert len(everything) > len(attributed), "expected some unattributed operators"
    for c in attributed:
        assert c.wallets, f"{c.slug} in attributed set with no wallets"


def test_confidence_never_exceeds_evidence_ceiling():
    """A live RPC read cannot upgrade an attribution claim."""
    for c in catalog():
        for w in c.wallets:
            assert w.confidence <= CONFIDENCE_CEILING[w.evidence_status], (
                f"{w.address} claims {w.confidence} at {w.evidence_status}"
            )


def test_wallet_cluster_rejects_overconfident_construction():
    from app.wallets import WalletCluster

    with pytest.raises(ValueError, match="exceeds"):
        WalletCluster(
            address="0x" + "11" * 20,
            chain="ethereum",
            role="hot",
            confidence=0.99,
            evidence_status="unverified_seed",
        )


# ── Coverage reporting ───────────────────────────────────────────────────────


def test_coverage_report_separates_observed_from_unobserved():
    r = market.coverage_report()
    assert r["operators_attributed"] + r["operators_unattributed"] == (
        r["operators_catalogued"]
    )
    assert r["caveat"]
    # Unattributed entries must state their status, not carry figures.
    for row in r["unattributed"]:
        assert "attribution_status" in row
        assert not any(k.endswith("_usd") for k in row)


def test_coverage_endpoint_is_served():
    body = client.get("/coverage").json()
    assert body["confidence"] == 1.0
    assert body["data_source"] == "registry"
    assert "unobserved" in body["reasoning"]


def test_catalog_endpoint_includes_attributed_and_unobserved_operators():
    body = client.get("/casinos").json()
    assert body["count"] == len(catalog())
    assert body["attributed_count"] == len(attributed_operators())
    assert body["unattributed_count"] == len(catalog()) - len(attributed_operators())
    statuses = {row["attribution_status"] for row in body["casinos"]}
    assert statuses == {"attributed", "unobserved"}
    from app.wallets import get_casino

    # Registry expanded from single-network to real multi-chain identity:
    # Stake publishes distinct hot wallets on multiple chains including
    # non-EVM identity rows (bitcoin/solana/tron). Precise counts drift as
    # more addresses are labelled, so assert what the registry actually
    # declares rather than a fixed number.
    stake = next(row for row in body["casinos"] if row["slug"] == "stake")
    registered_chains = get_casino("stake").chains
    assert "ethereum" in stake["chains"]
    assert stake["wallet_count"] >= 15
    assert set(stake["queried_chains"]) == set(registered_chains), (
        "queried chains must equal registered chains — no auto-mirroring, "
        "no silent omissions"
    )


# ── Naming discipline ────────────────────────────────────────────────────────


def test_network_distribution_reflects_registered_chains(monkeypatch):
    """The market view surfaces every chain that has a registered wallet claim.

    The prior test asserted that every EVM chain in INDEXED_CHAINS appeared —
    which required auto-mirroring a single Ethereum label onto seven networks.
    That contradicted `observation_targets()` (an Ethereum label is not
    evidence for the same address on another chain) so the assertion is now
    scoped to what the registry actually declares.
    """
    from app.onchain import _CACHE
    from app.settings import Settings, settings
    from app.wallets import attributed_operators

    monkeypatch.setattr(
        Settings, "live_data_available", property(lambda self: False)
    )
    monkeypatch.setattr(settings, "strict_mode", False)
    _CACHE.clear()

    registered = {
        w.chain
        for c in attributed_operators()
        for w in c.wallets
        if w.chain in {"ethereum", "base", "polygon", "arbitrum",
                       "optimism", "bsc", "avalanche"}
    }
    body = client.get("/market/networks?hours=24").json()
    chains = {row["chain"] for row in body["chains"]}
    assert chains >= registered, (
        f"missing chain(s) with a registered wallet: {registered - chains}"
    )


def test_shares_are_scoped_to_observed_flow():
    """No key may claim whole-market share — we only see attributed clusters."""
    for path in ("/market/networks?hours=24", "/market/assets?hours=24"):
        body = client.get(path).json()
        rows = body.get("chains") or body.get("assets") or []
        for row in rows:
            assert not any(
                k == "market_share_pct" for k in row
            ), f"{path} exposes an unqualified market_share_pct"


def test_unattributed_operator_series_says_unobserved_not_zero():
    body = client.get("/operator/gamdom/series?hours=24").json()
    assert body["verdict"] == "not_attributed"
    assert body["confidence"] == 0.0
    assert "not a statement that its flow is zero" in body["reasoning"]
    assert body["series"] == []


# ── Endpoint contract ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/market/networks?hours=24",
        "/market/assets?hours=24",
        "/market/large-transfers?hours=24",
        "/operator/stake/series?hours=24",
        "/operator/stake/counterparties?hours=24",
        "/coverage",
    ],
)
def test_market_endpoints_honor_signal_mapping(path):
    """Every declared endpoint returns confidence/verdict/reasoning."""
    r = client.get(path)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["confidence"], (int, float))
    assert 0.0 <= body["confidence"] <= 1.0
    assert isinstance(body["verdict"], str) and body["verdict"]
    assert isinstance(body["reasoning"], str) and body["reasoning"]
    assert "data_source" in body


def test_window_params_are_clamped_not_rejected():
    assert client.get("/market/networks?hours=99999").json()["window_hours"] == 720
    assert client.get("/operator/stake/series?hours=0").json()["window_hours"] == 1


def test_flow_confidence_is_capped_by_attribution_uncertainty():
    """Even perfect chain reads cannot exceed the attribution ceiling."""
    from app.main import _flow_confidence

    assert _flow_confidence("live", True) <= 0.55
    assert _flow_confidence("live", False) < _flow_confidence("live", True)
    assert _flow_confidence("demo", True) < _flow_confidence("live", True)
    assert _flow_confidence("unavailable", True) == 0.0


# ── Module/function shadowing ────────────────────────────────────────────────


def test_health_module_is_not_shadowed_by_the_health_endpoint():
    """`async def health()` shadowed the `health` module import, so
    `health.registry_health` resolved against the function object and every
    call 500'd internally. The never-5xx middleware masked it as a 200 with
    verdict "unavailable", which is why it reached production unnoticed.

    Pin the aliased import so the collision cannot silently return.
    """
    from app import main

    assert hasattr(main, "health_checks"), "health module must be imported aliased"
    assert hasattr(main.health_checks, "registry_health")
    assert hasattr(main.health_checks, "operator_health")


def test_health_endpoints_do_not_return_internal_errors():
    for path in ("/health/registry?hours=24", "/health/operator/stake?hours=24"):
        body = client.get(path).json()
        assert body.get("error") is None, f"{path} returned {body.get('error')}"
        assert body["verdict"] != "unavailable"


def test_no_endpoint_returns_an_internal_error():
    """Sweep every GET route for masked exceptions.

    The middleware converts a throw into a 200, which protects the Canonical
    Score but hides bugs — so assert explicitly that none are firing.
    """
    from app.main import app as fastapi_app

    skip = {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect", "/"}
    for route in fastapi_app.routes:
        methods = getattr(route, "methods", set())
        path = getattr(route, "path", "")
        if "GET" not in methods or path in skip or "{" in path:
            continue
        body = client.get(f"{path}?hours=24").json()
        if isinstance(body, dict):
            assert body.get("error") is None, f"{path} raised {body.get('error')}"
