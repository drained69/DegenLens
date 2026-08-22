"""Wallet label health.

The product's figures are only as good as its labels, and a label can fail in
ways that are invisible downstream. Verification of the seeded registry found
three distinct failures, all of which rendered identically as "$0.00 observed":

  * DEAD      — the address has never had a single transfer. The label is
                simply wrong; there is nothing to observe and never was.
  * DORMANT   — real activity exists but stopped months ago. Either the
                operator rotated wallets or the label was always stale.
  * QUIET     — active address, no activity inside the requested window.

Reporting all three as "$0.00" states a measurement we did not make. Zero means
"we looked and there was nothing"; only QUIET earns that. DEAD and DORMANT mean
the label needs fixing, and an operator whose every claim is DEAD is not an
operator with no volume — it is an operator we cannot see at all.

This module makes that distinction explicit so bad labels surface as bad labels
instead of quietly deflating every aggregate that includes them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .onchain import get_transfers, probe_activity
from .wallets import Casino, attributed_operators, catalog

# An address with real history but nothing recent is dormant rather than quiet.
DORMANT_AFTER_DAYS = 30

# Lifetime lookback used to tell "never used" apart from "idle lately".
LIFETIME_HOURS = 720


@dataclass
class WalletHealth:
    address: str
    chain: str
    role: str
    evidence_status: str

    lifetime_transfers: int
    window_transfers: int
    last_activity: str | None
    days_since_activity: float | None

    status: str  # active | quiet | dormant | dead | unknown
    detail: str

    @property
    def is_usable(self) -> bool:
        """Whether this claim can contribute to an aggregate at all."""
        return self.status in {"active", "quiet", "dormant"}

    @property
    def is_probeable(self) -> bool:
        """False for identity-only entries on non-EVM chains."""
        return self.status != "not_probeable"


async def check_wallet(address: str, chain: str, role: str, evidence: str, hours: int) -> WalletHealth:
    # A single-page probe, not a full paged history. Health only needs liveness
    # and recency; paginating here collapsed under registry-wide concurrency.
    probe = await probe_activity(address, chain)

    if probe.data_source == "unsupported_chain":
        # Registry identity on a non-EVM chain (bitcoin, solana, tron). Not
        # dead, not quiet — just not probeable with the current toolchain.
        return WalletHealth(
            address, chain, role, evidence, 0, 0, None, None,
            "not_probeable",
            f"{chain} is a registry identity but is not readable via the "
            f"Alchemy EVM API; treat as a public label, not a live-flow source",
        )

    if probe.data_source == "unavailable":
        return WalletHealth(
            address, chain, role, evidence, 0, 0, None, None,
            "unknown", "upstream unavailable — health could not be determined",
        )

    n_life = probe.sampled_transfers
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    n_window = (
        1 if (probe.last_activity and probe.last_activity >= cutoff) else 0
    )

    if n_life == 0:
        return WalletHealth(
            address, chain, role, evidence, 0, 0, None, None,
            "dead",
            f"no transfers in {LIFETIME_HOURS}h of history — the label is unusable, "
            f"not an operator with zero volume",
        )

    last = probe.last_activity
    if last is None:
        return WalletHealth(
            address, chain, role, evidence, n_life, 0, None, None,
            "unknown", "transfers found but no usable timestamps",
        )
    age_days = (datetime.now(timezone.utc) - last).total_seconds() / 86400

    if n_window > 0:
        status = "active"
        detail = (
            f"last transfer {age_days:.1f} days ago — inside the {hours}h window"
        )
    elif age_days > DORMANT_AFTER_DAYS:
        status = "dormant"
        detail = (
            f"last activity {age_days:.0f} days ago — the operator may have rotated "
            f"wallets, or the label is stale"
        )
    else:
        status = "quiet"
        detail = (
            f"active address with no transfers inside the {hours}h window "
            f"(last seen {age_days:.1f} days ago). A genuine zero for this window."
        )

    return WalletHealth(
        address, chain, role, evidence,
        n_life, n_window, last.isoformat(), round(age_days, 2),
        status, detail,
    )


async def operator_health(casino: Casino, hours: int = 168) -> dict:
    if not casino.wallets:
        return {
            "slug": casino.slug,
            "name": casino.name,
            "attribution_status": "unattributed",
            "coverage_quality": "none",
            "usable_claims": 0,
            "total_claims": 0,
            "wallets": [],
            "reportable": False,
            "detail": (
                "No wallet claim exists. This operator is unobserved — that is a "
                "coverage gap, not a statement about its activity."
            ),
        }

    # Health probes are one-page reads and independent per wallet. Keep them
    # parallel while relying on the shared on-chain semaphore for provider load.
    wallet_sem = asyncio.Semaphore(8)

    async def check(wallet):
        async with wallet_sem:
            return await check_wallet(wallet.address, wallet.chain, wallet.role, wallet.evidence_status, hours)

    checks = await asyncio.gather(*(check(w) for w in casino.wallets))
    # Exclude non-probeable (bitcoin/solana/tron identity rows) from the
    # accounting entirely so they neither help nor hurt reportability — they
    # aren't usable AND they aren't broken, they just aren't Alchemy-readable.
    probeable = [c for c in checks if c.is_probeable]
    unprobeable = [c for c in checks if not c.is_probeable]
    usable = [c for c in probeable if c.is_usable]
    dead = [c for c in probeable if c.status == "dead"]
    active = [c for c in probeable if c.status == "active"]

    if not probeable:
        status, quality, reportable = "identity_only", "none", False
        detail = (
            f"All {len(checks)} wallet claim(s) live on non-EVM chains and cannot "
            f"be probed with the current toolchain. Treat as public identity, not "
            f"as a live-flow source."
        )
    elif not usable:
        status, quality, reportable = "invalid", "none", False
        detail = (
            f"All {len(probeable)} probeable wallet claim(s) are dead — no transfer "
            f"history exists at any claimed EVM address. Figures for this operator "
            f"would be structurally zero and must not be presented as measurements."
        )
    elif not active:
        status, quality, reportable = "stale", "poor", False
        detail = (
            f"{len(usable)} usable claim(s), none active in the last {hours}h. "
            f"Any window total would be a genuine zero, but the labels look stale."
        )
    elif dead:
        status, quality, reportable = "partial", "partial", True
        detail = (
            f"{len(active)} of {len(checks)} claims active; {len(dead)} dead label(s) "
            f"contribute nothing and should be removed from the registry."
        )
    else:
        status, quality, reportable = "healthy", "observed", True
        detail = f"All {len(active)} claim(s) active in the last {hours}h."

    return {
        "slug": casino.slug,
        "name": casino.name,
        "attribution_status": status,
        "coverage_quality": quality,
        "usable_claims": len(usable),
        "active_claims": len(active),
        "dead_claims": len(dead),
        "not_probeable_claims": len(unprobeable),
        "total_claims": len(checks),
        "reportable": reportable,
        "detail": detail,
        "wallets": [
            {
                "address": c.address,
                "chain": c.chain,
                "role": c.role,
                "evidence_status": c.evidence_status,
                "status": c.status,
                "lifetime_transfers": c.lifetime_transfers,
                "window_transfers": c.window_transfers,
                "last_activity": c.last_activity,
                "days_since_activity": c.days_since_activity,
                "detail": c.detail,
            }
            for c in checks
        ],
    }


async def registry_health(hours: int = 168) -> dict:
    """Health of every attributed operator, plus a registry-wide verdict."""
    operators = attributed_operators()
    results = await asyncio.gather(*(operator_health(c, hours) for c in operators))

    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["attribution_status"]] = by_status.get(r["attribution_status"], 0) + 1

    dead_labels = [
        {"slug": r["slug"], "address": w["address"], "chain": w["chain"]}
        for r in results
        for w in r["wallets"]
        if w["status"] == "dead"
    ]
    reportable = [r for r in results if r["reportable"]]
    catalogued = len(catalog())

    return {
        "window_hours": hours,
        "operators_attributed": len(results),
        "operators_reportable": len(reportable),
        "operators_catalogued": catalogued,
        "by_attribution_status": by_status,
        "dead_labels": dead_labels,
        "dead_label_count": len(dead_labels),
        "operators": results,
        "registry_verdict": (
            f"{len(reportable)} of {len(results)} attributed operators produce "
            f"reportable figures. {len(dead_labels)} dead label(s) should be removed."
        ),
        "warning": (
            "Rankings built from this registry cover only reportable operators and "
            "only the wallets claimed for them. An operator running many wallets is "
            "under-counted relative to one whose footprint we happen to cover, so "
            "ordering between operators is NOT a market ranking."
            if len(reportable) < len(results) or catalogued > len(results)
            else None
        ),
    }
