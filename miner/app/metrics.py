"""In-process metrics.

75% of the Miner Track score is normalized performance against the best miner
in each intent. Uptime, latency, and error rate are therefore first-class
product concerns, not ops trivia — and they need to be *provable* at
submission time. This module is the evidence.

Deliberately dependency-free and bounded: a ring buffer of recent durations
per endpoint, plus monotonic counters. No Prometheus, no external scrape.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

_MAX_SAMPLES = 512

_lock = threading.Lock()
_started_at = time.monotonic()
_started_wall = datetime.now(timezone.utc)

_requests: dict[str, int] = defaultdict(int)
_errors: dict[str, int] = defaultdict(int)
_durations: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=_MAX_SAMPLES))

_cache_hits = 0
_cache_misses = 0
_upstream_calls = 0
_upstream_failures = 0


def record_request(endpoint: str, duration_ms: float, *, error: bool = False) -> None:
    with _lock:
        _requests[endpoint] += 1
        if error:
            _errors[endpoint] += 1
        _durations[endpoint].append(duration_ms)


def record_cache(*, hit: bool) -> None:
    global _cache_hits, _cache_misses
    with _lock:
        if hit:
            _cache_hits += 1
        else:
            _cache_misses += 1


def record_upstream(*, failed: bool = False) -> None:
    global _upstream_calls, _upstream_failures
    with _lock:
        _upstream_calls += 1
        if failed:
            _upstream_failures += 1


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. Returns 0.0 for an empty sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    # Nearest-rank: ceil(pct * n), clamped into range.
    rank = int(-(-pct * len(ordered) // 1))
    return round(ordered[min(max(rank - 1, 0), len(ordered) - 1)], 2)


def snapshot() -> dict[str, Any]:
    with _lock:
        endpoints: dict[str, Any] = {}
        total_requests = 0
        total_errors = 0
        all_durations: list[float] = []

        for name, count in _requests.items():
            samples = list(_durations[name])
            all_durations.extend(samples)
            total_requests += count
            total_errors += _errors[name]
            endpoints[name] = {
                "requests": count,
                "errors": _errors[name],
                "p50_ms": _percentile(samples, 0.50),
                "p95_ms": _percentile(samples, 0.95),
                "p99_ms": _percentile(samples, 0.99),
            }

        cache_total = _cache_hits + _cache_misses
        uptime_s = time.monotonic() - _started_at

        return {
            "uptime_seconds": round(uptime_s, 1),
            "started_at": _started_wall.isoformat(),
            "total_requests": total_requests,
            "total_errors": total_errors,
            "error_rate": round(total_errors / total_requests, 4) if total_requests else 0.0,
            "latency_p50_ms": _percentile(all_durations, 0.50),
            "latency_p95_ms": _percentile(all_durations, 0.95),
            "latency_p99_ms": _percentile(all_durations, 0.99),
            "cache_hit_rate": round(_cache_hits / cache_total, 4) if cache_total else 0.0,
            "upstream_calls": _upstream_calls,
            "upstream_failures": _upstream_failures,
            "upstream_failure_rate": (
                round(_upstream_failures / _upstream_calls, 4) if _upstream_calls else 0.0
            ),
            "endpoints": endpoints,
        }
