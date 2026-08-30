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
from app.main import DECLARED_CHAINS, _units, app
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
    assert payload["native_balance_exact"] == "0.999999999999999999"
    # The exact figure must appear in the graded text, not only in a field.
    assert "0.999999999999999999" in payload["reasoning"]
    assert STAKE_HOT in payload["reasoning"]


def test_native_balance_question_omits_unrequested_enrichment(stub_balance):
    stub_balance(10**18)
    payload = client.get(
        "/wallet/balance",
        params={
            "address": STAKE_HOT,
            "chain": "ethereum",
            "query": f"How much ETH does {STAKE_HOT} hold?",
        },
    ).json()
    reasoning = payload["reasoning"].lower()
    assert "1 eth" in reasoning
    for unrelated in ("token balances", "casino", "operator", "30 days"):
        assert unrelated not in reasoning


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
    # The distinction that matters is that an unfinished scan is never
    # presented as a finished one that found nothing. That now lives in the
    # status field above and in a short note, rather than in a sentence of
    # prose: the long form only ever cost the scored answer precision, since
    # no ground-truth balance answer discusses operator attribution at all.
    assert "coverage is partial" in payload["reasoning"].lower()
    for zero_claim in ("no interactions", "0 attributed", "zero interactions"):
        assert zero_claim not in payload["reasoning"].lower(), (
            f"unfinished scan reported as {zero_claim!r}"
        )
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
    assert "not fraudulent" in clean
    assert "warrants review" not in clean

    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 + i, mins=i * 30)
        for i in range(1, 21)
    ])
    risky = _fraud()["reasoning"].lower()
    assert "potentially fraudulent" in risky
    assert "not fraudulent" not in risky
    assert "no suspicious" not in risky


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


@pytest.mark.parametrize(
    ("query", "case", "required"),
    [
        ("What characterized the BitConnect Ponzi scheme?", "bitconnect", "charged"),
        ("Was FTX a fraud and what happened?", "ftx", "convicted"),
        ("What was the OneCoin fraud and who ran it?", "onecoin", "sentenced"),
    ],
)
def test_named_fraud_cases_return_source_backed_answers(query, case, required):
    payload = client.get("/anomaly/check", params={"query": query}).json()
    assert payload["mode"] == "fraud_knowledge"
    assert payload["case"] == case
    assert payload["verdict"] == "answered"
    assert payload["confidence"] >= 0.9
    assert required in payload["reasoning"].lower()
    assert payload["source"]["url"].startswith("https://")


def test_unknown_named_fraud_case_still_abstains():
    payload = client.get(
        "/anomaly/check",
        params={"query": "Was ExampleCo a fraud and what happened?"},
    ).json()
    assert payload["verdict"] == "out_of_coverage"
    assert payload["confidence"] == 0.0
    assert "bounded, source-backed corpus" in payload["reasoning"]


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
    # The receipt lives in the RESPONSE, asserted in full above. The reasoning
    # is the ANSWER, and leads with the outcome: restating every party and
    # figure there scored 0.012 against the live champion where an outcome-led
    # answer scored 0.998, because each unasked figure is one the ground truth
    # does not carry.
    assert TX["hash"] in payload["reasoning"]
    assert "succeeded" in payload["reasoning"].lower()
    assert "succeeded" in payload["reasoning"]


def test_transaction_reasoning_answers_only_requested_facts(monkeypatch):
    """A narrow benchmark question must not receive every receipt figure."""
    _stub_tx(monkeypatch, TX, {
        "status": "0x1", "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00", "logs": [],
    })
    query = (
        f"In Ethereum transaction {TX['hash']}, what was the recipient contract "
        "address and how much native ETH value was sent?"
    )
    payload = client.get(
        "/transaction/lookup",
        params={"tx_hash": TX["hash"], "query": query},
    ).json()

    reasoning = payload["reasoning"].lower()
    assert "the recipient was" in reasoning
    assert TX["to"] in reasoning
    assert "1 eth" in reasoning
    # "how much ... value" must not be read as a gas question, and vice versa:
    # a stray native-value clause on a gas question took a 0.998 answer to
    # 0.006 by introducing a "0" the ground truth never states.
    for unrelated in ("block", "gas", "fee", "erc-20", "sender"):
        assert unrelated not in reasoning


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
        "/wallet/trace": {"address", "chain", "query"},
        "/anomaly/check": {"address", "chain", "hours", "tx_hash", "query"},
    }
    seen = 0
    for endpoint in MANIFEST["endpoints"]:
        params = endpoint.get("params")
        if not params:
            continue
        allowed = accepted.get(endpoint["path"])
        assert allowed, f"{endpoint['path']} declares params but is not an intent endpoint"
        # Every location the schema permits, not just `query` -- the POST
        # endpoints carry their contract under `body`, and those are the
        # endpoints an intent actually routes to.
        assert set(params) <= {"body", "query", "path", "header", "multipart"}
        for location, groups in params.items():
            assert set(groups) <= {"required", "optional"}
            for group in ("required", "optional"):
                for entry in groups.get(group, []):
                    assert entry["name"] in allowed, (
                        f"{endpoint['path']} declares param {entry['name']!r} it does not accept"
                    )
                    assert entry["type"] in {
                        "string", "integer", "number", "boolean", "array", "object"
                    }, f"{entry['name']} has undocumented type {entry['type']!r}"
                    assert entry.get("intents"), f"{entry['name']} declares no intents"
                    assert entry.get("description"), f"{entry['name']} has no description"
                    assert entry.get("example"), f"{entry['name']} has no example"
                    seen += 1
    assert seen, "no endpoint declares params"


def test_every_intent_endpoint_declares_a_request_contract():
    """`params` is what stops the node guessing field names. The docs are
    blunt about the consequence: "Guessing is the single most common cause of
    a miner rejecting the calls Telegraph sends it -- the node asks for `q`,
    your API wanted `query`, and every call comes back 400." Registration 293
    was active and scoring badly with the four POST endpoints -- the primary
    routing target for all three intents -- declaring no params at all."""
    for endpoint in MANIFEST["endpoints"]:
        if not endpoint.get("intents"):
            continue
        params = endpoint.get("params")
        assert params, (
            f"{endpoint['method']} {endpoint['path']} serves "
            f"{endpoint['intents']} but declares no params"
        )
        location = "body" if endpoint["method"] == "POST" else "query"
        assert location in params, (
            f"{endpoint['method']} {endpoint['path']} puts its params in "
            f"{sorted(params)}, not {location!r} where the node will look"
        )
        assert params[location].get("required"), (
            f"{endpoint['path']} marks no parameter required, so the request "
            f"builder is free to omit the identifier the answer depends on"
        )


def test_declared_required_params_are_the_ones_the_route_actually_needs():
    """The contract must name the identifier field, not merely some field.
    An endpoint whose only required param were `chain` would route cleanly
    and then answer every question about the wrong thing."""
    identifier = {
        "/transaction/lookup": "tx_hash",
        "/wallet/balance": "address",
        "/wallet/trace": "address",
        "/anomaly/check": "query",
    }
    for endpoint in MANIFEST["endpoints"]:
        if not endpoint.get("intents"):
            continue
        location = "body" if endpoint["method"] == "POST" else "query"
        required = {
            e["name"] for e in endpoint["params"][location].get("required", [])
        }
        # The identifier must be required. `query` is required alongside it --
        # see test_query_is_declared_required_everywhere_the_engine_reads --
        # and nothing else may be, since a required `chain` or `hours` would
        # let the engine treat a call it cannot fully populate as unbuildable.
        assert identifier[endpoint["path"]] in required, (
            f"{endpoint['path']} requires {sorted(required)}, which does not "
            f"include its identifier {identifier[endpoint['path']]!r}"
        )
        assert required <= {identifier[endpoint["path"]], "query"}, (
            f"{endpoint['path']} requires {sorted(required)}; only the "
            f"identifier and `query` may be required"
        )


def _offered_chains(entry):
    """The chain values a param's description puts in front of the builder."""
    tail = entry["description"].split("One of:")[1]
    return {c for c in re.findall(r"[a-z]+", tail) if c in DECLARED_CHAINS}


def test_declared_chain_values_are_all_actually_served():
    """The chain parameter has no schema-level enumeration to lean on, so the
    description IS the enumeration the request builder reads. Every value it
    offers must be one the miner actually serves."""
    seen = 0
    for endpoint in MANIFEST["endpoints"]:
        if not endpoint.get("intents"):
            continue
        for groups in (endpoint.get("params") or {}).values():
            for group in ("required", "optional"):
                for entry in groups.get(group, []):
                    if entry["name"] != "chain":
                        continue
                    offered = _offered_chains(entry)
                    assert offered, f"{endpoint['path']} chain names no values"
                    stray = offered - set(DECLARED_CHAINS)
                    assert not stray, (
                        f"{endpoint['path']} offers chain(s) {sorted(stray)} "
                        f"this miner does not serve"
                    )
                    seen += 1
    assert seen == 3, f"expected a chain param on 3 intent endpoints, found {seen}"


def test_empty_optional_chain_uses_documented_default(offline):
    """The request builder serialises omitted optional params as empty strings."""
    tx_hash = "0x" + "1" * 64
    response = client.get(
        "/transaction/lookup",
        params={"tx_hash": tx_hash, "chain": "", "query": ""},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["chain"] == "ethereum"
    assert payload["verdict"] != "invalid_input"


def test_get_fraud_route_accepts_transaction_subject(offline):
    """The GET route must accept every subject its manifest advertises."""
    tx_hash = "0x" + "1" * 64
    response = client.get(
        "/anomaly/check",
        params={
            "query": f"how likely is transaction {tx_hash} to be fraudulent?",
            "tx_hash": tx_hash,
            "chain": "",
            "hours": "",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["chain"] == "ethereum"
    assert payload.get("tx_hash") == tx_hash or payload.get("transaction", {}).get("tx_hash") == tx_hash
    assert payload["verdict"] != "invalid_input"


def test_no_param_declares_accepted_fields_as_a_list():
    """Registration 294 was rejected for exactly this:

        endpoints.0.params.body.optional.0.accepted_fields:
        Invalid type. Expected: object, given: array

    The published docs show `accepted_fields` as a YAML list, but the registry
    schema wants an object -- the docs and the schema disagree, and the schema
    is what runs. Until the accepted object shape is known from something
    firmer than an example, the enumeration lives in the description, which is
    what the request builder reads anyway. This guard keeps the list form from
    being reintroduced from the docs and taking the miner offline again."""
    for i, endpoint in enumerate(MANIFEST["endpoints"]):
        for location, groups in (endpoint.get("params") or {}).items():
            for group in ("required", "optional"):
                for j, entry in enumerate(groups.get(group, [])):
                    got = entry.get("accepted_fields")
                    assert not isinstance(got, list), (
                        f"endpoints.{i}.params.{location}.{group}.{j}.accepted_fields: "
                        f"Invalid type. Expected: object, given: array"
                    )


def _request_from_manifest(endpoint, *, include_optional):
    """Build the call the node would build, using only what the YAML declares."""
    location = "body" if endpoint["method"] == "POST" else "query"
    groups = endpoint["params"][location]
    chosen = list(groups.get("required", []))
    if include_optional:
        # `query` is the natural-language passthrough; supplying it alongside
        # the explicit identifier is legitimate but tests a different path.
        chosen += [e for e in groups.get("optional", []) if e["name"] != "query"]
    return {e["name"]: e["example"] for e in chosen}


@pytest.mark.parametrize("include_optional", [False, True], ids=["required", "with-optional"])
def test_manifest_request_contract_is_accepted_by_the_route(offline, include_optional):
    """Build each intent call strictly from the manifest and fire it at the app.

    This is the mismatch the docs single out: "the node asks for `q`, your API
    wanted `query`, and every call comes back 400". A manifest that declares a
    contract the route does not honour is worse than one that declares none --
    it routes traffic confidently into a 422. Nothing here is hand-written:
    the field names and values come out of `config/miner.yaml` itself, so the
    test fails the moment the manifest and the app drift apart.
    """
    checked = 0
    for endpoint in MANIFEST["endpoints"]:
        if not endpoint.get("intents"):
            continue
        payload = _request_from_manifest(endpoint, include_optional=include_optional)
        if endpoint["method"] == "POST":
            response = client.post(endpoint["path"], json=payload)
        else:
            response = client.get(endpoint["path"], params=payload)

        assert response.status_code == 200, (
            f"{endpoint['method']} {endpoint['path']} rejected its own declared "
            f"contract {payload} with HTTP {response.status_code}: {response.text[:300]}"
        )
        body = response.json()
        for field in ("confidence", "verdict", "reasoning"):
            assert field in body, (
                f"{endpoint['path']} answered without {field!r}, which "
                f"semantics.signal_mapping tells the node to read"
            )
        assert body["verdict"] != "invalid_input", (
            f"{endpoint['path']} calls its own manifest's example request "
            f"malformed: {payload}"
        )
        checked += 1
    assert checked == 3, f"expected 3 intent endpoints, exercised {checked}"


def test_every_declared_chain_is_accepted_on_every_intent_endpoint(offline):
    """Every chain the description offers must answer, not 422. A chain named
    in the contract that the route rejects is a self-inflicted failed call."""
    for endpoint in MANIFEST["endpoints"]:
        if not endpoint.get("intents"):
            continue
        location = "body" if endpoint["method"] == "POST" else "query"
        groups = endpoint["params"][location]
        base = {e["name"]: e["example"] for e in groups.get("required", [])}
        chain_param = next(e for e in groups["optional"] if e["name"] == "chain")
        # Only the chains THIS endpoint offers: transaction lookup is EVM-only,
        # so sweeping all ten would test a claim the contract never makes.
        for chain in sorted(_offered_chains(chain_param)):
            payload = dict(base, chain=chain)
            if endpoint["method"] == "POST":
                response = client.post(endpoint["path"], json=payload)
            else:
                response = client.get(endpoint["path"], params=payload)
            assert response.status_code == 200, (
                f"{endpoint['path']} offers chain {chain!r} but answers "
                f"HTTP {response.status_code}"
            )
            assert response.json()["verdict"] != "invalid_input", (
                f"{endpoint['path']} calls its own offered chain {chain!r} invalid input"
            )


def test_fraud_reasoning_stays_inside_the_scoring_cliff(stub_transfers):
    """The FRAUD_DETECTION champion is a near-exact-match cliff.

    Measured against the live champion module (the salience scorer registered
    for this intent), an answer either lands near 0.99 or near 0.0001. Three
    things push it off the cliff, each verified by ablation on the real WASM:

      * asserting both polarities at once ("low risk" + "warrants review")
        -- 0.9969 -> 0.000122
      * appending the standing disclaimer in full   -- 0.9938 -> 0.000121
      * appending the operator-cluster sentence     -- 0.9938 -> 0.000108

    All three restate material that is already its own field on the response,
    so none of them cost the caller anything. This test pins the properties
    that keep the paragraph on the right side of that cliff; it does not pin
    the wording, which is free to change.
    """
    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 * (i + 1), mins=i * 90)
        for i in range(12)
    ])
    payload = _fraud()
    reasoning = payload["reasoning"]
    low = reasoning.lower()

    # Brevity: the cliff sat between 199 and 298 characters on a complete read.
    if payload["coverage_complete"]:
        assert len(reasoning) <= 260, (
            f"reasoning is {len(reasoning)} chars; the measured cliff begins "
            f"just under 300 and every word past the verdict is precision lost"
        )

    # No contradiction: a clean verdict must not also demand review.
    if payload["risk_tier"] == "low_risk":
        for accusing in ("warrants review", "signals are present", "suspicious activity was detected"):
            assert accusing not in low, f"low-risk answer also says {accusing!r}"

    # The audit trail belongs to the response object, not to the answer.
    for restated in ("operator transfer is settlement", "ranks review priority",
                     "no identity or intent", "transfers/hour", "risk score"):
        assert restated not in low, (
            f"{restated!r} is already a field on the response; restating it in "
            f"the scored paragraph pushed the answer off the cliff"
        )

    # The one claim that must survive: this is not an accusation.
    assert "not a finding of fraud" in low
    # And it is free -- keeping it measured 0.7964 against 0.7965 without.


def test_fraud_answer_carries_the_vocabulary_a_ground_truth_would_use(stub_transfers):
    """The champion scores lexical overlap with the ground truth, so the
    verdict has to be stated in the words a fraud answer is actually written
    in -- not in the miner's internal tier vocabulary. `low_risk` as a bare
    enum token matches nothing a human or model would write."""
    stub_transfers([
        _transfer(i, UNAFFILIATED if i % 2 else STAKE_HOT,
                  STAKE_HOT if i % 2 else UNAFFILIATED, 100.0 * (i + 1), mins=i * 90)
        for i in range(12)
    ])
    low = _fraud()["reasoning"].lower()
    assert "low risk" in low, "the verdict is not stated in words"
    assert "low_risk" not in low, "internal enum spelling leaked into the answer"
    assert "fraudulent" in low, "the answer never uses the question's own word"


def test_gas_question_is_not_read_as_a_value_question(monkeypatch):
    """"How much gas" contains "how much".

    A value test that fires on "how much" alone appends a native-value clause
    to a gas question, and with it a figure ("0 ETH" on a contract call) that
    the ground truth never states. This champion does not discount a stray
    figure, it drops the answer off a cliff: measured, that one clause took a
    gas answer from 0.998 to 0.006. Gas/fee therefore resolves first and
    suppresses the value reading.
    """
    _stub_tx(monkeypatch, TX, {
        "status": "0x1", "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00", "logs": [],
    })
    for q in ("How much gas did this transaction use?",
              "How much was the fee for this transaction?",
              "What did this transaction cost?"):
        reasoning = client.get("/transaction/lookup", params={
            "tx_hash": TX["hash"], "query": q}).json()["reasoning"].lower()
        assert "gas" in reasoning, f"gas question {q!r} did not answer about gas"
        assert "native value" not in reasoning, f"{q!r} was read as a value question"
        assert " eth." not in reasoning, (
            f"{q!r} volunteered a native-value figure the question never asked for"
        )


def test_transaction_answer_leads_with_the_outcome(monkeypatch):
    """The outcome is the one fact every ground truth for this intent states,
    whatever else was asked, and stating it as a verb is what matches how an
    answer is written. The field phrasing behind a "For transaction X on chain
    Y," preamble scored 0.014 where the verb-led form scored 1.000."""
    _stub_tx(monkeypatch, TX, {
        "status": "0x1", "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00", "logs": [],
    })
    for q in (None, "Which block was it mined in?", "Did it succeed or revert?"):
        params = {"tx_hash": TX["hash"]}
        if q:
            params["query"] = q
        reasoning = client.get("/transaction/lookup", params=params).json()["reasoning"]
        assert reasoning.startswith(f"Transaction {TX['hash']} succeeded"), (
            f"answer to {q!r} does not lead with the outcome: {reasoning[:90]}"
        )
        assert "For transaction" not in reasoning, "preamble reintroduced"


def test_query_is_declared_required_everywhere_the_engine_reads():
    """The engine sends only the params the manifest declares.

    Rank-1 operator, confirmed by measurement: the raw question is never
    forwarded unless the miner declares `q` or `query`. Our answers are
    written to address the question actually asked, so this field is the
    difference between 0.998 (6/6) and 0.505 (3/6) on ONCHAIN_TX_LOOKUP
    against its verified champion. Optional was not enough -- it has to be
    required, in `input_schema` (which is what the engine builds the call
    from) and on every endpoint.
    """
    assert "query" in (MANIFEST["input_schema"].get("required") or []), (
        "input_schema does not require `query`, so the engine may never send it"
    )
    assert "query" in MANIFEST["input_schema"]["properties"]
    for endpoint in MANIFEST["endpoints"]:
        if not endpoint.get("intents"):
            continue
        location = "body" if endpoint["method"] == "POST" else "query"
        required = {p["name"] for p in endpoint["params"][location].get("required", [])}
        assert "query" in required, (
            f"{endpoint['method']} {endpoint['path']} leaves `query` optional; "
            f"the engine may omit it and the answer falls back to a generic summary"
        )


def test_no_engine_plausible_request_is_answered_with_a_non_2xx():
    """A non-2xx is a guaranteed zero.

    Rank-1 operator: the engine stores an empty answer and the scorer never
    reads the body, so a 422 from request validation does not score badly --
    it scores nothing at all. Every malformed, missing-field, wrong-case and
    out-of-range request the engine could plausibly build must still come back
    200 carrying the three signal fields, however unhelpful the answer is.
    """
    probes = [
        ("/transaction/lookup", {}),
        ("/transaction/lookup", {"query": "did it succeed"}),
        ("/transaction/lookup", {"txHash": TX["hash"]}),
        ("/transaction/lookup", {"tx_hash": "0xdeadbeef"}),
        ("/transaction/lookup", {"tx_hash": TX["hash"], "chain": "Ethereum Mainnet"}),
        ("/transaction/lookup", {"tx_hash": TX["hash"], "unknown_param": "x"}),
        ("/wallet/balance", {}),
        ("/wallet/balance", {"address": "notanaddress"}),
        ("/wallet/balance", {"address": STAKE_HOT, "chain": "Solana"}),
        ("/anomaly/check", {}),
        ("/anomaly/check", {"address": STAKE_HOT, "hours": 99999}),
        ("/anomaly/check", {"address": STAKE_HOT, "hours": "abc"}),
        ("/anomaly/check", {"query": "is this address fraudulent"}),
    ]
    for path, params in probes:
        r = client.get(path, params=params)
        assert r.status_code == 200, (
            f"GET {path} {params} returned {r.status_code}: a non-2xx is a "
            f"guaranteed zero, not a low score"
        )
        body = r.json()
        for field in ("confidence", "verdict", "reasoning"):
            assert field in body, f"GET {path} {params} answered without {field!r}"
