from __future__ import annotations

from datetime import datetime, timezone

from app.intelligence import (
    DataState,
    TransferClassification,
    aggregate_flows,
    classify_transfer,
    normalize_transfer,
)
from app.onchain import Transfer


def transfer(tx: str, sender: str, receiver: str, amount: float = 1.0) -> Transfer:
    return Transfer(
        tx_hash=tx,
        from_addr=sender,
        to_addr=receiver,
        token_symbol="USDC",
        amount=amount,
        timestamp=datetime(2026, 8, 19, tzinfo=timezone.utc),
        chain="ethereum",
        direction="in",
    )


def test_registry_classifies_customer_directions():
    casino = {"0xcasino"}
    assert classify_transfer(
        normalize_transfer(transfer("deposit", "0xplayer", "0xcasino")),
        casino,
    ).classification == TransferClassification.CUSTOMER_DEPOSIT
    assert classify_transfer(
        normalize_transfer(transfer("withdrawal", "0xcasino", "0xplayer")),
        casino,
    ).classification == TransferClassification.CUSTOMER_WITHDRAWAL


def test_internal_transfers_are_visible_but_not_customer_flow():
    rows = [
        transfer("deposit", "0xplayer", "0xa", 100),
        transfer("sweep", "0xa", "0xb", 40),
        transfer("sweep", "0xa", "0xb", 40),
        transfer("withdrawal", "0xb", "0xplayer", 10),
    ]
    aggregate = aggregate_flows(rows, {"USDC": 1.0}, {"0xa", "0xb"}, 1.0, "live")
    assert aggregate.attributed_customer_inflow_usd == 100
    assert aggregate.attributed_customer_outflow_usd == 10
    assert aggregate.internal_transfers_usd == 40
    assert aggregate.observed_inflow_usd == 140
    assert aggregate.observed_outflow_usd == 50
    assert aggregate.net_customer_flow_usd == 90
    assert aggregate.duplicate_count == 1
    assert all(row.state == DataState.INFERRED for row in aggregate.classifications)


def test_unknown_transfer_does_not_become_customer_flow():
    aggregate = aggregate_flows(
        [transfer("unknown", "0xone", "0xtwo", 25)],
        {"USDC": 1.0},
        {"0xcasino"},
        0.5,
        "live",
    )
    assert aggregate.transaction_count == 1
    assert aggregate.unknown_flow_usd == 25
    assert aggregate.attributed_customer_inflow_usd == 0
    assert aggregate.coverage == 0.5
