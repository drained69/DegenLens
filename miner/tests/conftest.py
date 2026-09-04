"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_chain_circuits():
    from app.onchain import reset_circuits

    reset_circuits()
    yield
    reset_circuits()


@pytest.fixture(autouse=True)
def _reset_caches():
    """Isolate each test from the service's caches.

    The miner answers repeated questions from cache on purpose — that is what
    makes identical queries return identical payloads. It also means a reading
    produced by one test would otherwise be served to the next, so a test that
    stubs the transfer layer could pass or fail purely on ordering.

    Only the ANSWER caches are cleared. The transfer cache underneath them is
    keyed by address/chain/window and is what keeps this suite from re-fetching
    the same reads for every test; stubbed tests replace the layer above it and
    are unaffected by it.
    """
    from app.analytics import _STATS_CACHE, _FULL_SCAN_TASKS
    from app.market import _AGGREGATE_CACHE, _AGGREGATE_TASKS

    caches = (_STATS_CACHE, _FULL_SCAN_TASKS, _AGGREGATE_CACHE, _AGGREGATE_TASKS)
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()
