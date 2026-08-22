"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_chain_circuits():
    from app.onchain import reset_circuits

    reset_circuits()
    yield
    reset_circuits()
