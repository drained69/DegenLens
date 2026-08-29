"""Per-intent response-contract tests for the three registered Telegraph intents.

These cover the failure modes that were actually costing live score, verified
against the production deployment before the fix:

  * `/anomaly/check` p50 7852ms and `/wallet/trace` p50 7014ms against an 8s
    deadline, while `/transaction/lookup` ran at 124ms — the ranking followed
    that ordering exactly (fraud 6th, the other two 3rd).
  * A declared chain (`solana`) and an obvious field spelling (`txHash`) both
    returned HTTP 422, whose body carries none of the three fields
    `semantics.signal_mapping` tells the node to read.
  * The fraud answer was a stub: no measurements, no evidence, and a verdict
    word that takes no position.

Everything here runs against stubbed providers so the assertions are about the
contract, not about the chain. Determinism is asserted directly.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.analytics as analytics
import app.onchain as onchain
from app.main import _units, app
from app.onchain import Transfer, TransferSet

client = TestClient(app)

STAKE_HOT = "0x974caa59e49682cda0ad2bbe82983419a2ecc400"
BINANCE_14 = "0x28c6c06298d514db089934071355e5743bf21d60"
UNAFFILIATED = "0xdead000000000000000000000000000000000001"
NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)

SIGNAL_FIELDS = ("confidence", "verdict", "reasoning", "data_source")


def _transfer(i: int, frm: str, to: str, amount: float, symbol: str = "USDT", mins: int = 0) -> Transfer:
    return Transfer(
        tx_hash=f"0x{i:064x}",
        from_addr=frm,
        to_addr=to,
        token_symbol=symbol,
        amount=amount,
        timestamp=NOW - timedelta(minutes=mins),
        chain="ethereum",
        direction="in" if to == STAKE_HOT else "out",
    )


@pytest.fixture
def stub_transfers(monkeypatch):
    """Replace the transfer layer with a fixed set."""

    def _install(transfers, source="live", complete=True, reason=None):
        async def fake(address, chain, hours):
            return TransferSet(list(transfers), source, reason, complete=complete)

        monkeypatch.setattr(analytics, "get_transfers", fake)

    return _install


@pytest.fixture
def stub_balance(monkeypatch):
    def _install(wei, source="live", tokens=(), block=25_000_000):
        async def fake_wei(address, chain):
            return wei, source

        async def fake_tokens(address, chain):
            return list(tokens), source

        async def fake_rpc(client_, url, method, params):
            return hex(block)

        monkeypatch.setattr(onchain, "native_balance_wei", fake_wei)
        monkeypatch.setattr(onchain, "token_balances", fake_tokens)
        monkeypatch.setattr(onchain, "_rpc", fake_rpc)

    return _install


@pytest.fixture
def offline(monkeypatch):
    """Answer every provider call from memory.

    Contract-level tests assert on response SHAPE, which must not depend on a
    live provider: a network-bound assertion is slow, flaky, and stops testing
    the thing it names the moment upstream has a bad minute.
    """

    async def no_transfers(address, chain, hours):
        return TransferSet([], "live", complete=True)

    async def some_wei(address, chain):
        return 10**18, "live"

    async def no_tokens(address, chain):
        return [], "live"

    async def no_rpc(client_, url, method, params):
        if method == "eth_blockNumber":
            return hex(25_000_000)
        return None

    monkeypatch.setattr(analytics, "get_transfers", no_transfers)
    monkeypatch.setattr(onchain, "get_transfers", no_transfers)
    monkeypatch.setattr(onchain, "native_balance_wei", some_wei)
    monkeypatch.setattr(onchain, "token_balances", no_tokens)
    monkeypatch.setattr(onchain, "_rpc", no_rpc)


# ── Shared contract ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,body",
    [
        ("/transaction/lookup", {"tx_hash": "0x" + "a" * 64, "chain": "ethereum"}),
        ("/wallet/trace", {"address": STAKE_HOT, "chain": "ethereum"}),
        ("/anomaly/check", {"address": STAKE_HOT, "chain": "ethereum", "hours": 24}),
    ],
)
def test_every_intent_endpoint_returns_the_declared_signal_fields(path, body, offline):
    """`semantics.signal_mapping` promises these on every answer."""
    response = client.post(path, json=body)
    assert response.status_code == 200
    payload = response.json()
    for field in SIGNAL_FIELDS:
        assert field in payload, f"{path} response is missing {field}"
    assert 0.0 <= payload["confidence"] <= 1.0
    assert payload["data_source"] in {"live", "demo", "unavailable", "registry", "derived"}
    assert isinstance(payload["reasoning"], str) and payload["reasoning"]


@pytest.mark.parametrize(
    "path,body",
    [
        ("/transaction/lookup", {"tx_hash": "not-a-hash"}),
        ("/wallet/trace", {}),
        ("/anomaly/check", {"address": STAKE_HOT, "hours": 99999}),
    ],
)
def test_malformed_requests_answer_instead_of_returning_422(path, body):
    """A 422 body has no confidence/verdict/reasoning, so the node cannot score
    it — it is indistinguishable from a dead miner rather than a bad request."""
    response = client.post(path, json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["verdict"] == "invalid_input"
    assert payload["confidence"] == 0.0
    assert payload["data_source"] == "unavailable"
    assert payload["invalid_fields"]


def test_every_declared_chain_is_accepted_by_every_intent_endpoint(offline):
    """The manifest publishes a ten-chain enum. Rejecting one at the schema
    boundary produced a 422 the node counted as a failure."""
    from app.main import DECLARED_CHAINS

    for chain in DECLARED_CHAINS:
        response = client.post("/anomaly/check", json={"address": STAKE_HOT, "chain": chain})
        assert response.status_code == 200, chain
        assert response.json().get("verdict") != "invalid_input", chain


def test_unsupported_chain_is_reported_as_coverage_not_as_a_rejection(offline):
    response = client.post(
        "/transaction/lookup",
        json={"tx_hash": "0x" + "b" * 64, "chain": "bitcoin"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["data_source"] == "unavailable"
    assert payload["confidence"] == 0.0
    # Distinguishable from "not found" — a chain we cannot read is not a chain
    # on which the transaction does not exist.
    assert "unsupported" in payload["reasoning"].lower() or payload["verdict"] == "unavailable"


# ── Request-shape tolerance ──────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["tx_hash", "txHash", "hash", "transaction_hash", "transactionHash"])
def test_transaction_hash_aliases_are_accepted(key, offline):
    response = client.post("/transaction/lookup", json={key: "0x" + "c" * 64, "chain": "ethereum"})
    assert response.status_code == 200
    assert response.json()["verdict"] != "invalid_input", key


@pytest.mark.parametrize("key", ["address", "wallet", "wallet_address", "account"])
def test_address_aliases_are_accepted(key, offline):
    response = client.post("/anomaly/check", json={key: STAKE_HOT, "chain": "ethereum"})
    assert response.status_code == 200
    assert response.json()["verdict"] != "invalid_input", key


def test_checksummed_and_padded_hashes_are_normalized_not_rejected(offline):
    response = client.post(
        "/transaction/lookup",
        json={"tx_hash": "  0x" + "A" * 64 + "  ", "chain": "eth"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["verdict"] != "invalid_input"
    assert payload["tx_hash"] == "0x" + "a" * 64
    assert payload["chain"] == "ethereum"


# ── Numeric exactness ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,decimals,expected",
    [
        (1431586854770926157824, 18, "1431.586854770926157824"),
        (999999999999999999, 18, "0.999999999999999999"),
        (1000000000000000000, 18, "1"),
        (0, 18, "0"),
        (1000000, 6, "1"),
        (1, 18, "0.000000000000000001"),
    ],
)
def test_base_units_convert_exactly_without_float_rounding(raw, decimals, expected):
    """`999999999999999999` wei through a float renders as a whole unit — a
    different number from the one the chain reported."""
    assert _units(raw, decimals) == expected


def test_units_of_none_is_none_not_zero():
    assert _units(None) is None


# ── WALLET_BALANCE_CHECK ─────────────────────────────────────────────────────


def test_balance_reports_exact_wei_symbol_and_block(stub_balance):
    stub_balance(999999999999999999)
    payload = client.post("/wallet/trace", json={"address": STAKE_HOT, "chain": "ethereum"}).json()

    assert payload["address"] == STAKE_HOT  # exact, never truncated
    assert payload["native_balance_wei"] == "999999999999999999"
    assert payload["native_symbol"] == "ETH"
    assert payload["block_number"] == 25_000_000
    assert payload["balance_status"] == "observed"
    # The exact figure must appear in the graded text, not only in a field.
    assert "0.999999999999999999" in payload["reasoning"]
    assert STAKE_HOT in payload["reasoning"]


def test_zero_balance_is_reported_as_an_observation(stub_balance):
    stub_balance(0)
    payload = client.post("/wallet/trace", json={"address": STAKE_HOT, "chain": "ethereum"}).json()
    assert payload["native_balance_wei"] == "0"
    assert payload["balance_status"] == "observed"
    assert payload["data_source"] == "live"


def test_provider_failure_is_never_reported_as_a_zero_balance(stub_balance):
    """The single most damaging thing a balance endpoint can do."""
    stub_balance(None, source="unavailable")
    payload = client.post("/wallet/trace", json={"address": STAKE_HOT, "chain": "ethereum"}).json()

    assert payload["native_balance_wei"] is None
    assert payload["native_balance"] is None
    assert payload["balance_native"] is None  # legacy alias must degrade too
    assert payload["balance_status"] == "unavailable"
    assert payload["confidence"] == 0.0
    assert "not zero" in payload["reasoning"]


def test_token_balances_are_reported_with_contract_symbol_and_decimals(stub_balance):
    from app.onchain import TokenBalance

    stub_balance(
        10**18,
        tokens=[TokenBalance(
            contract="0xdac17f958d2ee523a2206206994597c13d831ec7",
            symbol="USDT", decimals=6, raw=1_500_000, amount=1.5,
        )],
    )
    payload = client.post("/wallet/trace", json={"address": STAKE_HOT, "chain": "ethereum"}).json()
    assert payload["token_count"] == 1
    row = payload["token_balances"][0]
    assert row["contract"] == "0xdac17f958d2ee523a2206206994597c13d831ec7"
    assert row["symbol"] == "USDT"
    assert row["decimals"] == 6
    assert row["raw_balance"] == "1500000"
    assert row["balance"] == 1.5


def test_balance_alias_path_serves_the_same_contract(stub_balance):
    stub_balance(10**18)
    a = client.post("/wallet/balance", json={"address": STAKE_HOT, "chain": "ethereum"}).json()
    b = client.post("/wallet/trace", json={"address": STAKE_HOT, "chain": "ethereum"}).json()
    assert a["native_balance_wei"] == b["native_balance_wei"]
    assert set(SIGNAL_FIELDS) <= set(a)


def test_incomplete_association_scan_is_not_reported_as_zero_interactions(stub_balance, monkeypatch):
    stub_balance(10**18)

    async def never(*_a, **_k):
        await asyncio.sleep(10)

    monkeypatch.setattr("app.main.wallet_trace", never)
    monkeypatch.setattr("app.main._ASSOCIATION_BUDGET_S", 0.05)

    payload = client.post("/wallet/trace", json={"address": STAKE_HOT, "chain": "ethereum"}).json()
    assert payload["association_scan_status"] == "not_completed_in_budget"
    assert "not an observation of zero" in payload["reasoning"]
    # The balance answer still arrived.
    assert payload["native_balance_wei"] == "1000000000000000000"


# ── FRAUD_DETECTION ──────────────────────────────────────────────────────────


def _fraud(hours=24, address=STAKE_HOT):
    return client.post(
        "/anomaly/check", json={"address": address, "chain": "ethereum", "hours": hours}
    ).json()


def test_thin_window_is_insufficient_data_not_low_risk(stub_transfers):
    """An address with three transfers is unmeasured, not clean."""
    stub_transfers([_transfer(i, UNAFFILIATED, STAKE_HOT, 10.0, mins=i) for i in range(3)])
    payload = _fraud()
    assert payload["risk_tier"] == "insufficient_data"
    assert payload["is_suspicious"] is False
    assert "absence of data" in payload["reasoning"]


def test_unavailable_provider_is_insufficient_data_with_zero_confidence(stub_transfers):
    stub_transfers([], source="unavailable", complete=False, reason="upstream error")
    payload = _fraud()
    assert payload["risk_tier"] == "insufficient_data"
    assert payload["confidence"] == 0.0
    assert payload["is_suspicious"] is False


def test_clean_activity_is_low_risk_and_reports_its_measurements(stub_transfers):
    """A low-risk verdict that cannot say what was measured is not evidence."""
    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 * (i + 1), mins=i * 90)
        for i in range(12)
    ])
    payload = _fraud()

    assert payload["risk_tier"] == "low_risk"
    assert payload["is_suspicious"] is False
    assert payload["screens_run"] == 5
    assert payload["transfers_analyzed"] == 12
    assert payload["distinct_counterparties"] >= 1
    # Every screen reports a measurement whether or not it fired.
    assert len(payload["risk_signals"]) == 5
    assert all(s["measurement"] for s in payload["risk_signals"])


def test_round_trips_with_an_unaffiliated_counterparty_raise_the_tier(stub_transfers):
    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 + i, mins=i * 30)
        for i in range(1, 21)
    ])
    payload = _fraud()
    assert payload["risk_tier"] in {"elevated_risk", "high_risk"}
    assert payload["is_suspicious"] is True
    assert "round_trip_return" in payload["signals_fired"]
    assert payload["round_trip_count"] > 0


def test_identical_pattern_with_a_known_exchange_is_not_flagged(stub_transfers):
    """Normal exchange settlement must not be scored as wash trading. Same
    transfer shape as the test above, only the counterparty differs."""
    stub_transfers([
        _transfer(i, BINANCE_14 if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else BINANCE_14, 100.0 + i, mins=i * 30)
        for i in range(1, 21)
    ])
    payload = _fraud()

    assert payload["risk_tier"] == "low_risk"
    assert payload["is_suspicious"] is False
    assert "round_trip_return" not in payload["signals_fired"]
    # The excluded evidence stays visible rather than silently vanishing.
    assert payload["round_trip_count"] > 0
    assert payload["infrastructure_counterparties"]
    assert payload["infrastructure_counterparties"][0]["label"] == "Binance 14"


def test_risk_tier_verdict_and_is_suspicious_never_disagree(stub_transfers):
    cases = [
        [_transfer(i, UNAFFILIATED, STAKE_HOT, 5.0, mins=i) for i in range(3)],
        [_transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                   STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 * (i + 1), mins=i * 90)
         for i in range(12)],
        [_transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                   STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 + i, mins=i * 30)
         for i in range(1, 21)],
    ]
    elevated = {"elevated_risk", "high_risk"}
    legacy = {"insufficient_data": "unavailable", "low_risk": "normal",
              "elevated_risk": "suspicious", "high_risk": "critical"}
    for transfers in cases:
        stub_transfers(transfers)
        p = _fraud()
        assert p["is_suspicious"] is (p["risk_tier"] in elevated)
        assert p["verdict"] == legacy[p["risk_tier"]]
        if p["risk_tier"] in elevated:
            assert p["risk_score"] >= 0.30
            assert p["signals_fired"]
        else:
            assert not p["signals_fired"] or p["risk_score"] < 0.30


def test_fraud_polarity_does_not_contradict_the_tier(stub_transfers):
    """A low-risk answer must not read as an accusation, and an elevated one
    must not read as a clean bill of health."""
    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 * (i + 1), mins=i * 90)
        for i in range(12)
    ])
    clean = _fraud()["reasoning"].lower()
    assert "signals are absent" in clean
    assert "signals are present" not in clean

    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 + i, mins=i * 30)
        for i in range(1, 21)
    ])
    risky = _fraud()["reasoning"].lower()
    assert "signals are present" in risky
    assert "signals are absent" not in risky
    assert "consistent with legitimate" not in risky


def test_no_fraud_claim_is_ever_made(stub_transfers):
    """The miner observes settlement. It cannot see intent, identity, or an
    offence, and must never say otherwise."""
    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 + i, mins=i * 30)
        for i in range(1, 21)
    ])
    payload = _fraud()
    blob = (payload["reasoning"] + " " + payload["verdict"] + " " + payload["risk_tier"]).lower()
    for forbidden in ("confirmed_fraud", "confirmed fraud", "is fraudulent",
                      "proven fraud", "criminal", "money laundering"):
        assert forbidden not in blob
    assert "not a finding of fraud" in payload["reasoning"]
    assert payload["risk_tier"] in {
        "insufficient_data", "low_risk", "elevated_risk", "high_risk",
    }


def test_risk_score_is_bounded_and_deterministic(stub_transfers):
    transfers = [
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 + i, mins=i * 7)
        for i in range(1, 60)
    ]
    stub_transfers(transfers)
    first = _fraud()
    stub_transfers(list(reversed(transfers)))
    second = _fraud()

    assert 0.0 <= first["risk_score"] <= 1.0
    assert first["risk_score"] == second["risk_score"]
    assert first["risk_tier"] == second["risk_tier"]
    assert first["reasoning"] == second["reasoning"]


def test_partial_coverage_is_stated_rather_than_treated_as_no_risk(stub_transfers):
    stub_transfers(
        [_transfer(i, UNAFFILIATED, STAKE_HOT, 10.0, mins=i) for i in range(30)],
        complete=False,
        reason="page budget reached",
    )
    payload = _fraud()
    assert payload["coverage_complete"] is False
    assert payload["confidence"] <= 0.6
    assert "partial" in payload["reasoning"].lower()


def test_fraud_answer_carries_gradeable_evidence(stub_transfers):
    """The live answer was a stub — five numbers and no identifiers beyond the
    address the question supplied."""
    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 + i, mins=i * 30)
        for i in range(1, 21)
    ])
    payload = _fraud()
    for field in (
        "risk_score", "risk_tier", "transfers_analyzed", "inbound_transfers",
        "outbound_transfers", "distinct_counterparties",
        "top_counterparty_share_pct", "round_trip_count",
        "peak_hourly_transfers", "mean_hourly_transfers", "window_hours",
    ):
        assert field in payload, field
    # Evidence names concrete transactions, not just a count.
    fired = [s for s in payload["risk_signals"] if s["score"] > 0]
    assert any(s["evidence"] for s in fired)


# ── ONCHAIN_TX_LOOKUP ────────────────────────────────────────────────────────


def _stub_tx(monkeypatch, tx, receipt, block_ts="0x66cb0000"):
    async def fake_rpc(client_, url, method, params):
        if method == "eth_getTransactionByHash":
            return tx
        if method == "eth_getTransactionReceipt":
            return receipt
        if method == "eth_getBlockByNumber":
            return {"timestamp": block_ts}
        return None

    monkeypatch.setattr(onchain, "_rpc", fake_rpc)


TX = {
    "hash": "0x" + "1" * 64,
    "blockNumber": "0x1188ab",
    "blockHash": "0x" + "2" * 64,
    "from": "0x" + "3" * 40,
    "to": "0x" + "4" * 40,
    "value": hex(10**18),
    "gas": "0x7a120",
    "gasPrice": "0x3b9aca00",
    "nonce": "0x2a",
    "transactionIndex": "0x5",
    "input": "0xa9059cbb0000",
}


def test_confirmed_transaction_reports_full_rpc_facts(monkeypatch):
    _stub_tx(monkeypatch, TX, {
        "status": "0x1", "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00", "logs": [],
    })
    payload = client.post(
        "/transaction/lookup", json={"tx_hash": TX["hash"], "chain": "ethereum"}
    ).json()

    assert payload["tx_hash"] == TX["hash"]  # exact, never truncated
    assert payload["status"] == "confirmed"
    assert payload["verdict"] == "confirmed"
    assert payload["block_number"] == 0x1188AB
    assert payload["block_timestamp"] is not None
    assert payload["from_address"] == TX["from"]
    assert payload["to_address"] == TX["to"]
    assert payload["value_wei"] == str(10**18)
    assert payload["native_symbol"] == "ETH"
    assert payload["gas_limit"] == 0x7A120
    assert payload["gas_used"] == 0x5208
    assert payload["effective_gas_price_wei"] == str(0x3B9ACA00)
    assert payload["fee_wei"] == str(0x5208 * 0x3B9ACA00)
    assert payload["method_id"] == "0xa9059cbb"
    assert payload["nonce"] == 42
    # The graded text carries the identifiers and the figures.
    assert TX["hash"] in payload["reasoning"]
    assert TX["from"] in payload["reasoning"]
    assert "succeeded" in payload["reasoning"]


def test_reverted_transaction_is_distinguishable_from_a_confirmed_one(monkeypatch):
    _stub_tx(monkeypatch, TX, {
        "status": "0x0", "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00", "logs": [],
    })
    payload = client.post(
        "/transaction/lookup", json={"tx_hash": TX["hash"], "chain": "ethereum"}
    ).json()
    assert payload["status"] == "reverted"
    assert payload["verdict"] == "reverted"
    assert "reverted" in payload["reasoning"]
    assert "succeeded" not in payload["reasoning"]


def test_pending_transaction_is_distinguishable_from_not_found(monkeypatch):
    pending = dict(TX, blockNumber=None, blockHash=None)
    _stub_tx(monkeypatch, pending, None)
    payload = client.post(
        "/transaction/lookup", json={"tx_hash": TX["hash"], "chain": "ethereum"}
    ).json()
    assert payload["status"] == "pending"
    assert payload["block_number"] is None
    # Pending consumed no gas; that must not be reported as zero gas used.
    assert payload["gas_used"] is None
    assert "pending" in payload["reasoning"]


def test_not_found_transaction_is_a_structured_answer(monkeypatch):
    _stub_tx(monkeypatch, None, None)
    payload = client.post(
        "/transaction/lookup", json={"tx_hash": TX["hash"], "chain": "ethereum"}
    ).json()
    assert payload["verdict"] == "not_found"
    assert payload["confidence"] == 0.0
    assert payload["data_source"] == "unavailable"
    assert payload["tx_hash"] == TX["hash"]


def test_erc20_transfers_are_decoded_with_exact_amounts(monkeypatch):
    topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    _stub_tx(monkeypatch, TX, {
        "status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x3b9aca00",
        "logs": [{
            "address": "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "topics": [topic, "0x" + "0" * 24 + "3" * 40, "0x" + "0" * 24 + "4" * 40],
            "data": hex(1_500_000),
        }],
    })
    payload = client.post(
        "/transaction/lookup", json={"tx_hash": TX["hash"], "chain": "ethereum"}
    ).json()
    assert payload["token_transfer_count"] == 1
    row = payload["token_transfers"][0]
    assert row["contract"] == "0xdac17f958d2ee523a2206206994597c13d831ec7"
    assert row["symbol"] == "USDT"
    assert row["decimals"] == 6
    assert row["raw_amount"] == "1500000"
    assert row["amount"] == 1.5


def test_unknown_token_keeps_the_raw_amount_and_declines_to_guess_decimals(monkeypatch):
    topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    _stub_tx(monkeypatch, TX, {
        "status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x3b9aca00",
        "logs": [{
            "address": "0x" + "9" * 40,
            "topics": [topic, "0x" + "0" * 24 + "3" * 40, "0x" + "0" * 24 + "4" * 40],
            "data": hex(12345),
        }],
    })
    row = client.post(
        "/transaction/lookup", json={"tx_hash": TX["hash"], "chain": "ethereum"}
    ).json()["token_transfers"][0]
    assert row["raw_amount"] == "12345"
    assert row["decimals"] is None
    assert row["amount"] is None  # assuming 18 decimals would be a wrong number


def test_transaction_lookup_is_deterministic(monkeypatch):
    _stub_tx(monkeypatch, TX, {
        "status": "0x1", "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00", "logs": [],
    })
    body = {"tx_hash": TX["hash"], "chain": "ethereum"}
    first = client.post("/transaction/lookup", json=body).json()
    second = client.post("/transaction/lookup", json=body).json()
    volatile = {"served_at", "timestamp"}
    assert {k: v for k, v in first.items() if k not in volatile} == \
           {k: v for k, v in second.items() if k not in volatile}


# ── Deadline behaviour ───────────────────────────────────────────────────────


def test_slow_screening_provider_degrades_to_a_partial_answer_not_a_timeout(monkeypatch):
    """Hitting the service deadline returns `unavailable` with confidence 0 —
    the worst answer available and indistinguishable from an outage. A read
    that overruns its own budget must answer instead."""
    import app.onchain as oc
    from app.settings import settings

    async def never(address, chain, hours):
        await asyncio.sleep(30)

    monkeypatch.setattr(analytics, "get_transfers", never)
    monkeypatch.setattr(settings, "risk_read_budget_s", 0.05)

    started = __import__("time").monotonic()
    payload = client.post(
        "/anomaly/check", json={"address": STAKE_HOT, "chain": "ethereum"}
    ).json()
    elapsed = __import__("time").monotonic() - started

    assert elapsed < settings.request_timeout_s, "answered only at the service deadline"
    assert payload["risk_tier"] == "insufficient_data"
    assert payload["coverage_complete"] is False
    assert "budget" in payload["reasoning"] or "budget" in str(payload.get("caveat", ""))
    # Still a well-formed, scoreable answer.
    for field in SIGNAL_FIELDS:
        assert field in payload
    assert payload["address"] == STAKE_HOT


def test_slow_balance_provider_answers_within_budget(monkeypatch):
    import app.onchain as oc
    from app.settings import settings

    async def never(address, chain):
        await asyncio.sleep(30)

    monkeypatch.setattr(oc, "native_balance_wei", never)
    monkeypatch.setattr(settings, "balance_read_budget_s", 0.05)
    monkeypatch.setattr("app.main._ASSOCIATION_BUDGET_S", 0.05)

    started = __import__("time").monotonic()
    payload = client.post(
        "/wallet/trace", json={"address": STAKE_HOT, "chain": "ethereum"}
    ).json()
    elapsed = __import__("time").monotonic() - started

    assert elapsed < settings.request_timeout_s
    assert payload["balance_status"] == "unavailable"
    assert payload["native_balance_wei"] is None
    assert payload["confidence"] == 0.0
    assert "not zero" in payload["reasoning"]


def test_balance_and_association_reads_do_not_serialize(monkeypatch):
    """Two independent reads run concurrently; in series their budgets could
    sum past the deadline that kills both."""
    import app.onchain as oc

    async def slow_balance(address, chain):
        await asyncio.sleep(0.4)
        return 10**18, "live"

    async def slow_trace(address, chain):
        await asyncio.sleep(0.4)
        raise RuntimeError("stubbed")

    async def no_tokens(address, chain):
        return [], "live"

    async def no_rpc(client_, url, method, params):
        return hex(25_000_000)

    monkeypatch.setattr(oc, "native_balance_wei", slow_balance)
    monkeypatch.setattr(oc, "token_balances", no_tokens)
    monkeypatch.setattr(oc, "_rpc", no_rpc)
    monkeypatch.setattr("app.main.wallet_trace", slow_trace)

    started = __import__("time").monotonic()
    client.post("/wallet/trace", json={"address": STAKE_HOT, "chain": "ethereum"})
    elapsed = __import__("time").monotonic() - started

    assert elapsed < 0.75, f"reads serialized: {elapsed:.2f}s for two 0.4s reads"


# ── Manifest ↔ implementation contract ───────────────────────────────────────
# The registered YAML is how the Telegraph router decides which endpoint serves
# an intent AND how it builds the request. Every claim it makes is therefore a
# promise the service has to keep, and a promise it cannot keep is scored as a
# failed answer rather than a bad one. These tests pin the manifest to the code.

import pathlib

import yaml

MANIFEST = yaml.safe_load(
    (pathlib.Path(__file__).resolve().parents[3] / "config" / "miner.yaml").read_text()
)

# The documented closed sets. A key outside them fails registration outright:
# "Additional property <name> is not allowed".
_ALLOWED_TOP = {
    "version", "kind", "id", "slug", "protocol", "name", "description", "base_url",
    "input_schema", "output_schema", "polling", "cache_ttl_sec", "rate_limit_per_sec",
    "circuit_threshold", "circuit_cooldown_seconds", "docs", "limitations", "errors",
    "auth", "endpoints", "semantics", "on_chain",
}
_ALLOWED_ENDPOINT = {
    "path", "external_path", "method", "description", "endpoint_base_url",
    "content_type", "multipart_fields", "param_map",
    # `intents` and `params` are required by the node's request-contract
    # validator: a manifest whose endpoints declare no `intents` is rejected
    # as unroutable ("no endpoint declares any intents"). Registration 291 was
    # rejected for exactly this. The shape below mirrors an accepted manifest.
    "intents", "params",
}


def test_manifest_uses_only_documented_top_level_keys():
    extra = set(MANIFEST) - _ALLOWED_TOP
    assert not extra, f"(root): Additional property {sorted(extra)} is not allowed"


def test_manifest_endpoints_use_only_documented_keys():
    for i, endpoint in enumerate(MANIFEST["endpoints"]):
        extra = set(endpoint) - _ALLOWED_ENDPOINT
        assert not extra, f"endpoints.{i} ({endpoint.get('path')}): {sorted(extra)} not allowed"
        assert {"path", "external_path", "method"} <= set(endpoint)


def test_every_supported_intent_is_served_by_a_declared_endpoint():
    """The node rejects a manifest where no endpoint declares `intents:` --
    without it no intent can select an endpoint and the miner is unroutable."""
    declared = MANIFEST["semantics"]["supported_intents"]
    covered = set()
    for endpoint in MANIFEST["endpoints"]:
        covered |= set(endpoint.get("intents") or [])
    assert covered, "no endpoint declares any intents: the miner is unroutable"
    missing = set(declared) - covered
    assert not missing, f"supported_intents with no endpoint: {sorted(missing)}"
    unknown = covered - set(declared)
    assert not unknown, f"endpoint declares intents absent from supported_intents: {sorted(unknown)}"


def test_declared_endpoint_params_name_real_accepted_fields():
    """A param the endpoint does not actually accept sends the engine to a
    field we ignore, so the answer looks unresponsive rather than wrong."""
    accepted = {
        "/transaction/lookup": {"tx_hash", "chain", "query"},
        "/wallet/balance": {"address", "chain", "query"},
        "/anomaly/check": {"address", "chain", "hours", "tx_hash", "query"},
    }
    seen = 0
    for endpoint in MANIFEST["endpoints"]:
        params = endpoint.get("params")
        if not params:
            continue
        allowed = accepted.get(endpoint["path"])
        assert allowed, f"{endpoint['path']} declares params but is not an intent endpoint"
        query = params.get("query", {})
        for group in ("required", "optional"):
            for entry in query.get(group, []):
                assert entry["name"] in allowed, (
                    f"{endpoint['path']} declares param {entry['name']!r} it does not accept"
                )
                assert entry.get("description"), f"{entry['name']} has no description"
                assert entry.get("example"), f"{entry['name']} has no example"
                seen += 1
    assert seen, "no endpoint declares params"


def test_signal_mapping_matches_what_every_response_returns():
    mapping = MANIFEST["semantics"]["signal_mapping"]
    assert set(mapping) <= {"confidence_field", "label_field", "reason_field"}
    assert mapping["confidence_field"] == "confidence"
    assert mapping["label_field"] == "verdict"
    assert mapping["reason_field"] == "reasoning"


def test_every_declared_intent_names_itself_in_an_endpoint_description():
    """The router reads the description to pick an endpoint. An intent that
    appears nowhere in one has no endpoint the router can select."""
    for intent in MANIFEST["semantics"]["supported_intents"]:
        serving = [
            e for e in MANIFEST["endpoints"]
            if (e.get("description") or "").lstrip().startswith(intent)
        ]
        assert serving, f"{intent} is declared but no endpoint description leads with it"


def test_no_endpoint_example_contains_a_truncated_identifier():
    """A shortened address or hash in an example teaches the request builder to
    send one, and a shortened address fails our own validator. This was live:
    the manifest carried `{"address": "0x974caa...c400"}` as the documented
    request shape for two of the three intent endpoints."""
    blob = yaml.dump(MANIFEST)
    hits = re.findall(r"0x[0-9a-fA-F]{2,10}(?:\.\.\.|…)", blob)
    assert not hits, f"truncated identifiers in manifest examples: {hits[:5]}"


def test_intent_endpoints_declare_their_params():
    """Ahmed's point, in the form the schema actually allows: the params and
    their descriptions live in the description text, because there is no
    `params` key."""
    for endpoint in MANIFEST["endpoints"]:
        desc = (endpoint.get("description") or "").lstrip()
        if not any(desc.startswith(i) for i in MANIFEST["semantics"]["supported_intents"]):
            continue
        assert "aram" in desc, f"{endpoint['method']} {endpoint['path']} declares no params"


def test_non_intent_endpoints_do_not_compete_for_intent_routing():
    intents = set(MANIFEST["semantics"]["supported_intents"])
    for endpoint in MANIFEST["endpoints"]:
        desc = (endpoint.get("description") or "").lstrip()
        if any(desc.startswith(i) for i in intents):
            continue
        assert "NOT an intent target" in desc, (
            f"{endpoint['method']} {endpoint['path']} neither serves an intent "
            "nor says it is not an intent target"
        )


@pytest.mark.parametrize(
    "method,path",
    [(e["method"], e["path"]) for e in MANIFEST["endpoints"]
     if any((e.get("description") or "").lstrip().startswith(i)
            for i in MANIFEST["semantics"]["supported_intents"])],
)
def test_every_declared_intent_route_is_actually_served(method, path, offline):
    """A manifest entry with no route behind it is a guaranteed failed answer."""
    body = {
        "tx_hash": "0x" + "a" * 64,
        "address": STAKE_HOT,
        "chain": "ethereum",
        "hours": 24,
    }
    if method == "GET":
        response = client.get(path, params=body)
    else:
        response = client.post(path, json=body)
    assert response.status_code == 200, f"{method} {path} -> {response.status_code}"
    payload = response.json()
    assert payload.get("verdict") != "invalid_input"
    for field in SIGNAL_FIELDS:
        assert field in payload


# ── Natural-language intake and ENS ──────────────────────────────────────────


def test_transaction_hash_is_extracted_from_a_plain_question(offline):
    tx = "0x" + "b" * 64
    payload = client.post(
        "/transaction/lookup",
        json={"query": f"what is the status of transaction {tx} on base"},
    ).json()
    assert payload["tx_hash"] == tx
    assert payload["chain"] == "base"


def test_address_chain_and_window_are_extracted_from_a_plain_question(stub_transfers):
    stub_transfers([])
    payload = client.post(
        "/anomaly/check",
        json={"query": f"how likely is {STAKE_HOT} to be fraudulent over the last 3 days on polygon"},
    ).json()
    assert payload["address"] == STAKE_HOT
    assert payload["chain"] == "polygon"
    assert payload["window_hours"] == 72


def test_explicit_fields_beat_extraction(offline):
    """Extraction is a fallback for the router shape that passes the raw
    question, never a reinterpretation of a caller who already said what
    they meant."""
    explicit = "0x" + "c" * 64
    payload = client.post(
        "/transaction/lookup",
        json={"tx_hash": explicit, "query": "status of 0x" + "d" * 64},
    ).json()
    assert payload["tx_hash"] == explicit


def test_an_ens_name_is_accepted_and_resolved(monkeypatch, stub_balance):
    import app.main as main

    async def fake_resolve(name):
        return "0xd8da6bf26964af9d7eed9e03e53415d37aa96045", "live"

    monkeypatch.setattr(main, "resolve_ens", fake_resolve)
    stub_balance(10**18)
    payload = client.post("/wallet/balance", json={"address": "vitalik.eth"}).json()
    assert payload["ens_name"] == "vitalik.eth"
    assert payload["address"] == "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
    # Both the name asked about and the address it resolved to are in the text.
    assert "vitalik.eth" in payload["reasoning"]
    assert "0xd8da6bf26964af9d7eed9e03e53415d37aa96045" in payload["reasoning"]


def test_an_unresolvable_ens_name_is_never_answered_against_another_account(monkeypatch):
    import app.main as main

    async def fake_resolve(name):
        return None, f"no ENS resolver is registered for {name}"

    monkeypatch.setattr(main, "resolve_ens", fake_resolve)
    payload = client.post("/wallet/balance", json={"address": "nosuchname12345.eth"}).json()
    assert payload["verdict"] == "unresolved_name"
    assert payload["native_balance_wei"] is None
    assert payload["balance_native"] is None
    assert payload["confidence"] == 0.0


def test_keccak256_matches_published_vectors():
    """ENS namehash is only correct if this is Keccak-256 and not SHA3-256 —
    they differ by one padding byte and produce entirely different digests."""
    from app.onchain import _keccak, _namehash

    assert _keccak(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    assert _keccak(b"abc").hex() == (
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )
    # EIP-137 namehash of "eth".
    assert _namehash("eth").hex() == (
        "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    )
    assert _namehash("").hex() == "00" * 32


# ── Coverage vs malformed input ──────────────────────────────────────────────
# Three outcomes that must never collapse into each other. `invalid_input` says
# the caller should fix the request. `out_of_coverage` says the request was fine
# and the subject is outside what this miner reads — nothing the caller can fix.
# An answer says we read it. Reporting the second as the first is what the
# manifest previously promised and the code did not do.


@pytest.mark.parametrize(
    "path,question",
    [
        ("/anomaly/check", "was BitConnect a scam"),
        ("/anomaly/check", "how likely is FTX to be fraudulent"),
        ("/wallet/balance", "how much does Coinbase hold"),
        ("/transaction/lookup", "show me the biggest transaction yesterday"),
    ],
)
def test_a_question_we_cannot_read_is_out_of_coverage_not_malformed(path, question):
    payload = client.post(path, json={"query": question}).json()
    assert payload["verdict"] == "out_of_coverage"
    assert payload["confidence"] == 0.0
    assert payload["data_source"] == "unavailable"
    assert payload["subject"] == question
    # It must say what it CAN do, so the caller learns the shape of an
    # answerable question rather than just being refused.
    assert len(payload["reasoning"]) > 200


@pytest.mark.parametrize(
    "path,body",
    [
        ("/anomaly/check", {}),
        ("/wallet/balance", {}),
        ("/transaction/lookup", {}),
        ("/transaction/lookup", {"tx_hash": "not-a-hash"}),
        ("/anomaly/check", {"address": STAKE_HOT, "hours": 99999}),
    ],
)
def test_a_malformed_request_stays_invalid_input(path, body):
    payload = client.post(path, json=body).json()
    assert payload["verdict"] == "invalid_input"
    assert payload["confidence"] == 0.0


def test_out_of_coverage_never_guesses_a_fraud_verdict():
    """The canonical intent is wider than what this miner observes. The honest
    miss is saying so — not inventing a rating for a company we cannot see."""
    payload = client.post(
        "/anomaly/check", json={"query": "how likely is Acme Corp to be fraudulent"}
    ).json()
    assert payload["verdict"] == "out_of_coverage"
    assert payload["risk_tier"] == "insufficient_data"
    assert payload["risk_score"] == 0.0
    assert payload["is_suspicious"] is False
    lowered = payload["reasoning"].lower()
    for forbidden in ("is fraudulent", "is a scam", "confirmed fraud", "likely fraudulent"):
        assert forbidden not in lowered


def test_out_of_coverage_still_carries_the_declared_signal_fields():
    payload = client.post("/wallet/balance", json={"query": "how rich is Binance"}).json()
    for field in SIGNAL_FIELDS:
        assert field in payload
    # A balance that could not be read is null, never zero.
    assert payload["native_balance_wei"] is None
    assert payload["balance_native"] is None


def test_manifest_out_of_coverage_claim_is_actually_implemented():
    """The manifest tells callers an off-chain question is answered as out of
    coverage. A manifest promise the service does not keep is worse than no
    promise: it is scored as a failed answer."""
    fraud = next(
        e for e in MANIFEST["endpoints"]
        if e["path"] == "/anomaly/check" and e["method"] == "POST"
    )
    assert "outside what this miner can observe" in fraud["description"]
    payload = client.post(
        "/anomaly/check", json={"query": "is the Acme pyramid scheme a fraud"}
    ).json()
    assert payload["verdict"] == "out_of_coverage"


# ── FRAUD_DETECTION over a transaction ───────────────────────────────────────
# The canonical intent is "how likely a specific entity, TRANSACTION or action
# is to be fraudulent". Only addresses were accepted, so a transaction question
# — squarely inside the intent, and answerable from data already in hand — fell
# through to out_of_coverage.


def _stub_tx_for_fraud(monkeypatch, tx, receipt):
    async def fake_rpc(client_, url, method, params):
        if method == "eth_getTransactionByHash":
            return tx
        if method == "eth_getTransactionReceipt":
            return receipt
        if method == "eth_getBlockByNumber":
            return {"timestamp": "0x66cb0000"}
        return None

    monkeypatch.setattr(onchain, "_rpc", fake_rpc)


def test_a_transaction_is_a_valid_fraud_subject(monkeypatch, stub_transfers):
    _stub_tx_for_fraud(monkeypatch, TX, {
        "status": "0x1", "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00", "logs": [],
    })
    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else TX["from"],
                  TX["from"] if i % 2 else UNAFFILIATED, 100.0 * (i + 1), mins=i * 90)
        for i in range(12)
    ])
    payload = client.post(
        "/anomaly/check",
        json={"query": f"how likely is transaction {TX['hash']} to be fraudulent"},
    ).json()

    assert payload["verdict"] != "out_of_coverage"
    assert payload["risk_tier"] in {"low_risk", "elevated_risk", "high_risk"}
    ctx = payload["transaction"]
    assert ctx["tx_hash"] == TX["hash"]
    assert ctx["from_address"] == TX["from"]
    assert ctx["screened_party"] == "from_address"
    # The screened address is the sender, not the hash.
    assert payload["address"] == TX["from"]


def test_transaction_fraud_answer_states_what_it_actually_assessed(monkeypatch, stub_transfers):
    """The chain records no intent. Claiming a verdict on the transaction
    itself would be a claim the data cannot support; the honest answer names
    the transaction's facts and assesses its originator's behaviour."""
    _stub_tx_for_fraud(monkeypatch, TX, {
        "status": "0x1", "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00", "logs": [],
    })
    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else TX["from"],
                  TX["from"] if i % 2 else UNAFFILIATED, 100.0 * (i + 1), mins=i * 90)
        for i in range(12)
    ])
    reasoning = client.post(
        "/anomaly/check", json={"tx_hash": TX["hash"], "chain": "ethereum"}
    ).json()["reasoning"]

    assert "records no intent" in reasoning
    # All three identifiers appear, so the answer matches a question phrased
    # around any of them.
    assert TX["hash"] in reasoning
    assert TX["from"] in reasoning
    assert TX["to"] in reasoning


def test_an_unreadable_transaction_is_insufficient_data_not_a_clean_verdict(monkeypatch):
    _stub_tx_for_fraud(monkeypatch, None, None)
    payload = client.post(
        "/anomaly/check", json={"tx_hash": TX["hash"], "chain": "ethereum"}
    ).json()
    assert payload["risk_tier"] == "insufficient_data"
    assert payload["is_suspicious"] is False
    assert payload["confidence"] == 0.0
    assert payload["tx_hash"] == TX["hash"]


def test_an_explicit_address_still_wins_over_a_hash_in_the_question(stub_transfers):
    stub_transfers([])
    payload = client.post(
        "/anomaly/check",
        json={"address": STAKE_HOT, "query": f"and what about {TX['hash']}"},
    ).json()
    assert payload["address"] == STAKE_HOT
    assert payload["transaction"] is None
