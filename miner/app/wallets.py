"""Versioned seed registry for operator wallet attribution.

Labels are claims, not ground truth. Each wallet carries an evidence status and
review metadata so downstream consumers can distinguish a curated label from a
verified one.

Two ideas are kept strictly separate here, because conflating them is how this
kind of product starts lying:

  * An operator with NO attributed wallets is *unobserved*. We know nothing
    about its flow. It must never render as "$0" — that is a measurement claim
    we have not earned.
  * An operator with attributed wallets showing no transfers in a window has
    *observed zero flow* through the clusters we know about. That is a real
    (if partial) statement.

`attributed_operators()` returns only the former-excluded set — operators that
can actually produce flow figures. `catalog()` returns everything, with an
`attribution_status` so the UI can say "not yet attributed" honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

Chain = Literal[
    "ethereum", "base", "polygon", "arbitrum", "optimism", "bsc", "avalanche",
    "solana", "tron", "bitcoin"
]

# Every chain the EVM adapter can query. An EVM address is an identity, not a
# single-network claim — operators reuse the same hot wallet on every chain
# they accept deposits on.
INDEXED_CHAINS: tuple[Chain, ...] = (
    "ethereum",
    "base",
    "polygon",
    "arbitrum",
    "optimism",
    "bsc",
    "avalanche",
    "solana",
    "tron",
    "bitcoin",
)
EVM_CHAINS = set(INDEXED_CHAINS)

EvidenceStatus = Literal["verified", "curated", "unverified_seed"]

# Roles mirror what upstream cluster registries actually distinguish. A wallet
# that only consolidates balances between an operator's own addresses, or one
# whose function has not been classified, is not a "hot" wallet — flattening
# either into "hot" would overstate how much of the observed flow is
# player-facing.
WalletRole = Literal[
    "deposit", "hot", "cold", "treasury", "consolidation", "unknown"
]

# Confidence ceilings by evidence status. A live RPC read can prove what an
# address DID; it cannot prove WHOSE address it is. Attribution confidence is
# therefore capped by label provenance, independent of data freshness.
CONFIDENCE_CEILING: dict[str, float] = {
    "verified": 0.95,
    "curated": 0.75,
    "unverified_seed": 0.55,
}


@dataclass(frozen=True)
class WalletCluster:
    address: str
    chain: Chain
    role: WalletRole
    confidence: float
    evidence_status: EvidenceStatus
    evidence: tuple[str, ...] = ()
    last_reviewed: str = "2026-08-17"
    source: str = "unverified"
    discovered_at: str = "2026-08-17T00:00:00+00:00"
    # The source's own human label for this cluster, when it publishes one.
    label: str | None = None
    # The source's own confidence, BEFORE our provenance ceiling is applied.
    # `confidence` is the capped figure; this is what the source actually said.
    source_confidence: float | None = None

    def __post_init__(self) -> None:
        ceiling = CONFIDENCE_CEILING[self.evidence_status]
        if self.confidence > ceiling:
            raise ValueError(
                f"{self.address}: confidence {self.confidence} exceeds the "
                f"{self.evidence_status} ceiling of {ceiling}"
            )


@dataclass(frozen=True)
class Casino:
    slug: str
    name: str
    website: str
    licensed_in: str | None = None
    established: int | None = None
    wallets: tuple[WalletCluster, ...] = ()
    # Retained while the registry migration is reviewed; never queried.
    legacy_wallets: tuple[WalletCluster, ...] = ()

    @property
    def is_attributed(self) -> bool:
        """True when we hold at least one wallet claim for this operator."""
        return bool(self.wallets)

    @property
    def chains(self) -> list[str]:
        """Networks with an explicit wallet claim in the registry."""
        return sorted({w.chain for w in self.wallets})

    @property
    def queried_chains(self) -> list[str]:
        """Networks with explicit claims that the pipeline should attempt."""
        return sorted({w.chain for w in self.wallets})

    @property
    def best_evidence(self) -> EvidenceStatus | None:
        if not self.wallets:
            return None
        order: list[EvidenceStatus] = ["verified", "curated", "unverified_seed"]
        for status in order:
            if any(w.evidence_status == status for w in self.wallets):
                return status
        return None


def observation_targets(casino: Casino) -> list[WalletCluster]:
    """Return only explicitly registered wallet/network identities.

    An Ethereum label is not evidence that the same address belongs to the
    operator on another chain. Gamstat publishes chain-specific cluster
    identities, so expanding Ethereum addresses would manufacture coverage and
    produce misleading zero-flow rows.
    """
    seen: set[tuple[str, str]] = set()
    targets: list[WalletCluster] = []
    for wallet in casino.wallets:
        key = (wallet.address.lower(), wallet.chain)
        if key not in seen:
            seen.add(key)
            targets.append(wallet)
    return targets


def _seed(address: str, chain: Chain, role: str = "hot") -> WalletCluster:
    """Shorthand for an unreviewed candidate address.

    These come from public discussion and block-explorer labels. They have NOT
    been confirmed against operator disclosure, so they sit at the
    unverified_seed ceiling until someone reviews them.
    """
    return WalletCluster(
        address=address,
        chain=chain,
        role=role,  # type: ignore[arg-type]
        confidence=CONFIDENCE_CEILING["unverified_seed"],
        evidence_status="unverified_seed",
        evidence=("public block-explorer label; not confirmed by operator",),
        source="public block-explorer label",
    )


def _public_label(address: str, label: str) -> WalletCluster:
    """A public explorer label with enough provenance for a curated seed."""
    return WalletCluster(
        address=address,
        chain="ethereum",
        role="hot",
        confidence=CONFIDENCE_CEILING["curated"],
        evidence_status="curated",
        evidence=(
            f"Etherscan/chain explorer public label: {label}; "
            "cross-chain label returned by supported explorers",
        ),
        source="Etherscan public label",
    )


def _gamstat_label(
    address: str,
    chain: Chain,
    role: str = "hot",
    source_confidence: float = 0.9,
    explorer_url: str | None = None,
    casino_name: str = "Shuffle",
    source_url: str = "https://gamstat.io/casinos/shuffle",
    label: str | None = None,
) -> WalletCluster:
    """A Gamstat cluster label; not independently verified here.

    Confidence is `min(source_confidence, curated_ceiling)`. The ceiling is a
    statement about OUR provenance — a label we have not independently
    confirmed cannot be presented as verified, however sure Gamstat is. The
    source figure is a statement about THEIRS, and it can only pull the number
    down. Publishing a wallet Gamstat rates 0.3 at our 0.75 curated ceiling
    would launder their stated doubt into our confidence, which is exactly the
    kind of quiet overstatement this registry exists to prevent.

    `source_confidence` is retained verbatim so the ungated figure stays
    auditable next to the capped one.
    """
    evidence = [
        f"Gamstat {casino_name} cluster wallet listing; source confidence {source_confidence}",
        "Requires independent explorer/RPC confirmation before verified status",
    ]
    if label:
        evidence.append(f"Gamstat wallet label: {label}")
    if explorer_url:
        evidence.append(f"Explorer source: {explorer_url}")
    return WalletCluster(
        address=address,
        chain=chain,
        role=role,  # type: ignore[arg-type]
        confidence=min(source_confidence, CONFIDENCE_CEILING["curated"]),
        source_confidence=source_confidence,
        label=label,
        evidence_status="curated",
        evidence=tuple(evidence),
        last_reviewed="2026-08-23",
        source=source_url,
        discovered_at="2026-08-20T00:00:00+00:00",
    )


def _supplied_explorer_label(address: str, chain: Chain, explorer_url: str) -> WalletCluster:
    """Record an explicit chain-specific wallet supplied for review."""
    return WalletCluster(
        address=address,
        chain=chain,
        role="hot",
        confidence=CONFIDENCE_CEILING["curated"],
        evidence=(
            "User-supplied public block-explorer address; requires independent ownership review",
            f"Explorer source: {explorer_url}",
        ),
        evidence_status="curated",
        source="user-supplied block explorer",
    )


# One Gamstat cluster row: address, network, role, their confidence, their label.
GamstatRow = tuple[str, Chain, str, float, str]


def _gamstat_wallets(
    casino_name: str,
    slug: str,
    rows: tuple[GamstatRow, ...],
) -> tuple[WalletCluster, ...]:
    """Build chain-specific curated labels from Gamstat's public registry.

    Rows mirror Gamstat's published cluster table one-for-one, including the
    role and per-wallet confidence it assigns. Roles are NOT normalised to
    "hot": Gamstat distinguishes cold reserves, consolidation addresses, and
    unclassified operational wallets, and collapsing those would erase the
    distinction between a treasury and a payout wallet.
    """
    source_url = f"https://gamstat.io/casinos/{slug}"
    return tuple(
        _gamstat_label(
            address,
            chain,
            role,
            source_confidence=source_confidence,
            casino_name=casino_name,
            source_url=source_url,
            label=label,
        )
        for address, chain, role, source_confidence, label in rows
    )


ROLLBIT_GAMSTAT_WALLETS = _gamstat_wallets(
    "Rollbit",
    "rollbit",
    (
        ("0x8ae57a027c63fca8070d1bf38622321de8004c67", "ethereum", "unknown", 0.6, "Rollbit ops (Rollbot/NFT)"),
        ("0xef8801eaf234ff82801821ffe2d78d60a0237f97", "ethereum", "hot", 0.9, "Rollbit ERC-20 hot"),
        ("3Hhh16urMb1fy6mk4jkjYyh4yiRzqyeUNT", "bitcoin", "cold", 0.7, "Cold Wallet"),
        ("3MNNwkVDPWeysqKqp2PCMieia5aSQrasms", "bitcoin", "cold", 0.7, "Cold Wallet"),
        ("3LHMJGV9nzVN4H714yEUTeXZaju91RVvAH", "bitcoin", "cold", 0.7, "Cold Wallet"),
        ("RBHdGVfDfMjfU6iUfCb1LczMJcQLx7hGnxbzRsoDNvx", "solana", "cold", 0.9, "Rollbit SOL treasury"),
        ("0xcbd6832ebc203e49e2b771897067fce3c58575ac", "ethereum", "hot", 0.9, "Rollbit hot (ETH)"),
        ("0x46dca395d20e63cb0fe1edc9f0e6f012e77c0913", "ethereum", "unknown", 0.6, "Rollbit ops (rollbit.eth)"),
        ("3LyMZcfRiFbyYqi63RUpq53nL4gygMTfnU", "bitcoin", "cold", 0.7, "Cold Wallet"),
        ("39oL1SZiSJWnCdn7uM5xrjbvE8hFMgPnoa", "bitcoin", "cold", 0.7, "Cold Wallet"),
        ("0xcbd6832ebc203e49e2b771897067fce3c58575ac", "polygon", "hot", 0.6, "Rollbit hot (Polygon)"),
    ),
)

BCGAME_GAMSTAT_WALLETS = _gamstat_wallets(
    "BC.Game",
    "bc-game",
    (
        ("JEBRptmAAjqtxg6c4WLQDaZPeEA8RXnW4dVyhvsvZnxQ", "solana", "hot", 0.9, "Hot Wallet"),
        ("0xd352e0d71e14c45b719fe31d1eaa13051ede129b", "bsc", "hot", 0.9, "Hot Wallet"),
        ("0xa7b9874d15742358fb455dd56f97c6d19ad74f5c", "base", "hot", 0.9, "Hot Wallet"),
        ("0x6adc35bbdd759be047d9d28b94f5734a9c0cb563", "polygon", "hot", 0.9, "Hot Wallet"),
        ("0xc199feb7ce2b17fa84162ee705ebb35a2f19407d", "ethereum", "hot", 0.9, "Hot Wallet"),
        ("0xe7176831c898d585cd999bcee9984a7fa9a6be96", "arbitrum", "hot", 0.9, "Hot Wallet"),
        ("0x120a5b1fd4782cd8639e3814781a5d30382e65db", "ethereum", "hot", 0.9, "Hot Wallet"),
        ("0x49395574019ae44d46d535215303a09fd596727c", "bsc", "hot", 0.9, "Hot Wallet"),
        ("bc1qqpdkczlc78nkss6wspse8rerf8u9eatce3mmk0", "bitcoin", "hot", 0.7, "Hot Wallet"),
        ("0x3ba9ea0ffeff9efdd7cb7eafb3ac6788a21b5aa7", "ethereum", "cold", 0.9, "Cold Wallet"),
        ("0xf09214d414312980446c5a6133b9c3db5918b7c5", "ethereum", "hot", 0.8, "Hot Wallet (cross-referenced)"),
        ("0x788529118f2a28c60b9de2ba0353f5ee4293e044", "ethereum", "hot", 0.9, "BC.Game hot 1"),
        ("0x41fc802e01bcf85d91e5708b42d41c2eaf01f375", "ethereum", "hot", 0.9, "BC.Game hot"),
        ("0xe983fd1798689eee00c0fb77e79b8f372df41060", "ethereum", "hot", 0.9, "BC.Game hot 4"),
        ("0x5472356f1de00bca5d729cfb6419c44b8d4488ab", "ethereum", "hot", 0.9, "BC.Game hot 3"),
        ("0x9d2a0e32633d9be838bfde19d510e6aa6eb202dd", "ethereum", "hot", 0.9, "BC.Game hot 5"),
        ("0x8aaf720bbbcac82c592ac8f6c628bbac1590e079", "ethereum", "hot", 0.9, "BC.Game hot 2"),
        ("TTUM1sLKN5735BdrdsJqLPnYaKESeWQGkB", "tron", "hot", 0.9, "Hot Wallet"),
    ),
)

SHUFFLE_GAMSTAT_WALLETS = _gamstat_wallets(
    "Shuffle",
    "shuffle",
    (
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "ethereum", "cold", 0.9, "Shuffle hot/treasury (ETH)"),
        ("76iXe9yKFDjGv3HicUVVy8AYxHLC71L1wYa12zaZzHHp", "solana", "hot", 0.9, "Hot Wallet"),
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "bsc", "cold", 0.9, "Shuffle hot/treasury (BSC)"),
        ("TWGSJz33dNGMhQYhSRLSKKUyFNewh8JEnp", "tron", "hot", 0.9, "Hot Wallet"),
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "base", "hot", 0.9, "Hot Wallet"),
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "polygon", "hot", 0.9, "Hot Wallet"),
        ("0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "arbitrum", "hot", 0.9, "Hot Wallet"),
        ("0x911a978f0cac392079b51db03e6f3027dfe6f96e", "ethereum", "hot", 0.7, "Hot Wallet"),
        ("Eq9p5iHVbNR4miwmFMkpuPwLLULZmPTxNUPBgLdNrWYy", "solana", "hot", 0.9, "Hot Wallet"),
        ("0x911a978f0cac392079b51db03e6f3027dfe6f96e", "bsc", "hot", 0.9, "Hot Wallet"),
        ("0x911a978f0cac392079b51db03e6f3027dfe6f96e", "polygon", "hot", 0.9, "Hot Wallet"),
        ("0x911a978f0cac392079b51db03e6f3027dfe6f96e", "base", "hot", 0.7, "Hot Wallet"),
    ),
)

YEET_GAMSTAT_WALLETS = _gamstat_wallets(
    "Yeet",
    "yeet",
    (
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "ethereum", "hot", 0.97, "Yeet omnichain hot"),
        ("TPKJ2wzjxASvQZQBmyegQrU1hExL2yvnLN", "tron", "hot", 0.97, "Yeet hot"),
        ("6UxrMpGdiqsncwBawPjxsZtQb3e6nsgYo1pVSbSeNAaE", "solana", "hot", 0.97, "Yeet hot"),
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "bsc", "hot", 0.97, "Omnichain Hot Wallet"),
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "polygon", "hot", 0.9, "Omnichain Hot Wallet"),
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "arbitrum", "hot", 0.9, "Omnichain Hot Wallet"),
        ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "base", "hot", 0.9, "Omnichain Hot Wallet"),
    ),
)


def _cross_chain_activity_label(address: str, chain: Chain) -> WalletCluster:
    """Record live activity without claiming cross-chain ownership."""
    return WalletCluster(
        address=address,
        chain=chain,
        role="hot",
        confidence=CONFIDENCE_CEILING["curated"],
        evidence=(
            "Live RPC activity probe found transfers for this address on this chain",
            "Cross-chain activity does not independently prove Stake ownership; review required",
        ),
        evidence_status="curated",
        source="Alchemy cross-chain activity probe",
    )


STAKE_CROSS_CHAIN_WALLETS: tuple[WalletCluster, ...] = (
    # These are added only where the live probe found activity and a usable
    # activity timestamp. Zero-activity and ambiguous rows remain unregistered.
    _cross_chain_activity_label("0x019d0706d65c4768ec8081ed7ce41f59eef9b86c", "base"),
    _cross_chain_activity_label("0x019d0706d65c4768ec8081ed7ce41f59eef9b86c", "arbitrum"),
    _cross_chain_activity_label("0x019d0706d65c4768ec8081ed7ce41f59eef9b86c", "optimism"),
    _cross_chain_activity_label("0x019d0706d65c4768ec8081ed7ce41f59eef9b86c", "bsc"),
    _cross_chain_activity_label("0x0392b64b8bfda184f0a72ce37d73dc7df978c4f7", "base"),
    _cross_chain_activity_label("0x0392b64b8bfda184f0a72ce37d73dc7df978c4f7", "polygon"),
    _cross_chain_activity_label("0x0392b64b8bfda184f0a72ce37d73dc7df978c4f7", "bsc"),
    _cross_chain_activity_label("0x6872b6630a3afcd3117191a8403c2002e13df7de", "base"),
    _cross_chain_activity_label("0x6872b6630a3afcd3117191a8403c2002e13df7de", "optimism"),
    _cross_chain_activity_label("0x6e29f75b0350fd0e85ee34a21ef94767b0186996", "base"),
    _cross_chain_activity_label("0x6e29f75b0350fd0e85ee34a21ef94767b0186996", "polygon"),
    _cross_chain_activity_label("0x6e29f75b0350fd0e85ee34a21ef94767b0186996", "arbitrum"),
    _cross_chain_activity_label("0x6e29f75b0350fd0e85ee34a21ef94767b0186996", "optimism"),
    _cross_chain_activity_label("0x6e29f75b0350fd0e85ee34a21ef94767b0186996", "bsc"),
    _cross_chain_activity_label("0x758be77a3ee14e7193730560daa07dd3fcbfd200", "base"),
    _cross_chain_activity_label("0x758be77a3ee14e7193730560daa07dd3fcbfd200", "polygon"),
    _cross_chain_activity_label("0x758be77a3ee14e7193730560daa07dd3fcbfd200", "arbitrum"),
    _cross_chain_activity_label("0x758be77a3ee14e7193730560daa07dd3fcbfd200", "optimism"),
    _cross_chain_activity_label("0x758be77a3ee14e7193730560daa07dd3fcbfd200", "bsc"),
    _cross_chain_activity_label("0x787b8840100d9baadd7463f4a73b5ba73b00c6ca", "base"),
    _cross_chain_activity_label("0x787b8840100d9baadd7463f4a73b5ba73b00c6ca", "polygon"),
    _cross_chain_activity_label("0x787b8840100d9baadd7463f4a73b5ba73b00c6ca", "arbitrum"),
    _cross_chain_activity_label("0x787b8840100d9baadd7463f4a73b5ba73b00c6ca", "optimism"),
    _cross_chain_activity_label("0x787b8840100d9baadd7463f4a73b5ba73b00c6ca", "bsc"),
    _cross_chain_activity_label("0x974caa59e49682cda0ad2bbe82983419a2ecc400", "base"),
    _cross_chain_activity_label("0x974caa59e49682cda0ad2bbe82983419a2ecc400", "polygon"),
    _cross_chain_activity_label("0x974caa59e49682cda0ad2bbe82983419a2ecc400", "arbitrum"),
    _cross_chain_activity_label("0x974caa59e49682cda0ad2bbe82983419a2ecc400", "optimism"),
    _cross_chain_activity_label("0x974caa59e49682cda0ad2bbe82983419a2ecc400", "bsc"),
    _cross_chain_activity_label("0xa29148c2a656e5ddc68acb95626d6b64a1131c06", "bsc"),
    _cross_chain_activity_label("0xb04c0eb29c72cebc467b9d4944d29116fa02c44a", "base"),
    _cross_chain_activity_label("0xb04c0eb29c72cebc467b9d4944d29116fa02c44a", "polygon"),
    _cross_chain_activity_label("0xb04c0eb29c72cebc467b9d4944d29116fa02c44a", "bsc"),
    _cross_chain_activity_label("0xb2723beacce4bc54f23544343927f048cef6bd5a", "base"),
    _cross_chain_activity_label("0xb2723beacce4bc54f23544343927f048cef6bd5a", "polygon"),
    _cross_chain_activity_label("0xb2723beacce4bc54f23544343927f048cef6bd5a", "bsc"),
    _cross_chain_activity_label("0xbbc43c282b2f829176f4fc3802436d8fad3413f3", "polygon"),
    _cross_chain_activity_label("0xbbc43c282b2f829176f4fc3802436d8fad3413f3", "bsc"),
    _cross_chain_activity_label("0xd523794c879d9ec028960a231f866758e405be34", "base"),
    _cross_chain_activity_label("0xd523794c879d9ec028960a231f866758e405be34", "polygon"),
    _cross_chain_activity_label("0xd523794c879d9ec028960a231f866758e405be34", "optimism"),
    _cross_chain_activity_label("0xd523794c879d9ec028960a231f866758e405be34", "bsc"),
    _cross_chain_activity_label("0xdebfbe80c8aeba98a32968278463ccb639c6c4e3", "polygon"),
    _cross_chain_activity_label("0xdebfbe80c8aeba98a32968278463ccb639c6c4e3", "bsc"),
    _cross_chain_activity_label("0xdf1fc5523f2e5ea4f6dac2eaed3263953a391b0c", "base"),
    _cross_chain_activity_label("0xdf1fc5523f2e5ea4f6dac2eaed3263953a391b0c", "polygon"),
    _cross_chain_activity_label("0xdf1fc5523f2e5ea4f6dac2eaed3263953a391b0c", "arbitrum"),
    _cross_chain_activity_label("0xdf1fc5523f2e5ea4f6dac2eaed3263953a391b0c", "optimism"),
    _cross_chain_activity_label("0xdf1fc5523f2e5ea4f6dac2eaed3263953a391b0c", "bsc"),
    _cross_chain_activity_label("0xf598b81ef8c7b52a7f2a89253436e72ec6dc871f", "base"),
    _cross_chain_activity_label("0xf598b81ef8c7b52a7f2a89253436e72ec6dc871f", "polygon"),
    _cross_chain_activity_label("0xf598b81ef8c7b52a7f2a89253436e72ec6dc871f", "bsc"),
    _cross_chain_activity_label("0xfa500178de024bf43cfa69b7e636a28ab68f2741", "base"),
    _cross_chain_activity_label("0xfa500178de024bf43cfa69b7e636a28ab68f2741", "polygon"),
    _cross_chain_activity_label("0xfa500178de024bf43cfa69b7e636a28ab68f2741", "arbitrum"),
    _cross_chain_activity_label("0xfa500178de024bf43cfa69b7e636a28ab68f2741", "optimism"),
)

# Gamstat's current Stake page publishes 17 reviewed address/network pairs,
# each with its own role, label, and source confidence. Keep this
# source-matched set separate from activity-probe discoveries: the latter are
# useful review candidates, but including them in production totals increases
# rate-limit pressure and can double-count a wallet cluster.
STAKE_GAMSTAT_WALLETS: tuple[WalletCluster, ...] = _gamstat_wallets(
    "Stake",
    "stake",
    (
        ("0xdf1fc5523f2e5ea4f6dac2eaed3263953a391b0c", "ethereum", "cold", 0.9, "Cold Wallet"),
        ("0x6872b6630a3afcd3117191a8403c2002e13df7de", "ethereum", "hot", 0.85, "Hot Wallet (cross-referenced)"),
        ("G9X7F4JzLzbSGMCndiBdWNi5YzZZakmtkdwq7xS3Q3FE", "solana", "cold", 0.9, "Stake SOL treasury"),
        ("TZ8Ksz21Hk1tQuztCKCUJBRXStCav9uyjM", "tron", "hot", 0.9, "Hot Wallet"),
        ("0x6872b6630a3afcd3117191a8403c2002e13df7de", "bsc", "hot", 0.85, "Hot Wallet (cross-referenced)"),
        ("0x6872b6630a3afcd3117191a8403c2002e13df7de", "polygon", "hot", 0.85, "Hot Wallet (cross-referenced)"),
        ("0xfa500178de024bf43cfa69b7e636a28ab68f2741", "bsc", "hot", 0.9, "Stake BSC hot"),
        ("0x974caa59e49682cda0ad2bbe82983419a2ecc400", "ethereum", "hot", 0.97, "Stake.com hot wallet"),
        ("0x787b8840100d9baadd7463f4a73b5ba73b00c6ca", "ethereum", "hot", 0.9, "Stake.com 11"),
        ("0x019d0706d65c4768ec8081ed7ce41f59eef9b86c", "polygon", "hot", 0.9, "Hot Wallet"),
        ("0x6e29f75b0350fd0e85ee34a21ef94767b0186996", "ethereum", "cold", 0.9, "Cold Wallet"),
        ("0xd523794c879d9ec028960a231f866758e405be34", "ethereum", "cold", 0.9, "Cold Wallet (Everstake staking pool)"),
        ("bc1qmd3nsuw3z7fwr3wt7ac7ydceyeyu2cflft4ltm", "bitcoin", "cold", 0.7, "Cold Wallet"),
        # Gamstat rates this one 0.3 and marks it with a question mark. The
        # low source confidence is carried through rather than flattened to
        # the curated ceiling — see `_gamstat_label`.
        ("0x019d0706d65c4768ec8081ed7ce41f59eef9b86c", "ethereum", "cold", 0.3, "Stake cold/reserve?"),
        ("0xdebfbe80c8aeba98a32968278463ccb639c6c4e3", "ethereum", "hot", 0.9, "Stake.com 2 (retired)"),
        ("0x0392b64b8bfda184f0a72ce37d73dc7df978c4f7", "ethereum", "consolidation", 0.9, "Stake.com 8"),
        ("0xb04c0eb29c72cebc467b9d4944d29116fa02c44a", "ethereum", "hot", 0.9, "Stake.com 4"),
    ),
)


# ── Operator catalog ─────────────────────────────────────────────────────────
# Operator identity fields (name, site) are public facts. Wallet attribution is
# a claim. Where we hold no candidate address the operator is still catalogued,
# with `wallets=()` — it will report as unobserved rather than as zero flow.
CASINOS: dict[str, Casino] = {
    "stake": Casino(
        slug="stake",
        name="Stake.com",
        website="https://stake.com",
        licensed_in="Curaçao",
        established=2017,
        legacy_wallets=(
            _supplied_explorer_label(
                "0x974caa59e49682cda0ad2bbe82983419a2ecc400",
                "ethereum",
                "https://etherscan.io/address/0x974caa59e49682cda0ad2bbe82983419a2ecc400",
            ),
            _public_label("0xa29148c2a656e5ddc68acb95626d6b64a1131c06", "Stake.com 10"),
            _supplied_explorer_label(
                "0x787b8840100d9baadd7463f4a73b5ba73b00c6ca",
                "ethereum",
                "https://etherscan.io/address/0x787b8840100d9baadd7463f4a73b5ba73b00c6ca",
            ),
            _public_label("0xbbc43c282b2f829176f4fc3802436d8fad3413f3", "Stake.com 12"),
            _public_label("0x758be77a3ee14e7193730560daa07dd3fcbfd200", "Stake.com 13"),
            # "Stake.com 14" (0x6f4196…9cece) was verified to have zero lifetime
            # transfers on ethereum (2026-08-20 probe). Removed so it stops
            # contributing to the operator's usable-claims count. If the address
            # is a mislabelled cold or setup wallet on a chain we don't yet
            # index, re-add with an `observe_on=(…)` targeted at that chain.
            _supplied_explorer_label(
                "0xdf1fc5523f2e5ea4f6dac2eaed3263953a391b0c",
                "ethereum",
                "https://etherscan.io/address/0xdf1fc5523f2e5ea4f6dac2eaed3263953a391b0c",
            ),
            _supplied_explorer_label(
                "0xd523794c879d9ec028960a231f866758e405be34",
                "ethereum",
                "https://etherscan.io/address/0xd523794c879d9ec028960a231f866758e405be34",
            ),
            _supplied_explorer_label(
                "0xdebfbe80c8aeba98a32968278463ccb639c6c4e3",
                "ethereum",
                "https://etherscan.io/address/0xdebfbe80c8aeba98a32968278463ccb639c6c4e3",
            ),
            _supplied_explorer_label(
                "0x6e29f75b0350fd0e85ee34a21ef94767b0186996",
                "ethereum",
                "https://etherscan.io/address/0x6e29f75b0350fd0e85ee34a21ef94767b0186996",
            ),
            _supplied_explorer_label(
                "0xb04c0eb29c72cebc467b9d4944d29116fa02c44a",
                "ethereum",
                "https://etherscan.io/address/0xb04c0eb29c72cebc467b9d4944d29116fa02c44a",
            ),
            _public_label("0xb2723beacce4bc54f23544343927f048cef6bd5a", "Stake.com 5"),
            _public_label("0xfa500178de024bf43cfa69b7e636a28ab68f2741", "Stake.com 6"),
            _public_label("0xf598b81ef8c7b52a7f2a89253436e72ec6dc871f", "Stake.com 7"),
            _public_label("0x0392b64b8bfda184f0a72ce37d73dc7df978c4f7", "Stake.com 8"),
            _supplied_explorer_label(
                "0x019d0706d65c4768ec8081ed7ce41f59eef9b86c",
                "ethereum",
                "https://etherscan.io/address/0x019d0706d65c4768ec8081ed7ce41f59eef9b86c",
            ),
            _supplied_explorer_label(
                "G9X7F4JzLzbSGMCndiBdWNi5YzZZakmtkdwq7xS3Q3FE",
                "solana",
                "https://solscan.io/account/G9X7F4JzLzbSGMCndiBdWNi5YzZZakmtkdwq7xS3Q3FE",
            ),
            _supplied_explorer_label(
                "TZ8Ksz21Hk1tQuztCKCUJBRXStCav9uyjM",
                "tron",
                "https://tronscan.org/#/address/TZ8Ksz21Hk1tQuztCKCUJBRXStCav9uyjM",
            ),
            _supplied_explorer_label(
                "0x6872b6630a3afcd3117191a8403c2002e13df7de",
                "bsc",
                "https://bscscan.com/address/0x6872b6630a3afcd3117191a8403c2002e13df7de",
            ),
            _supplied_explorer_label(
                "0x6872b6630a3afcd3117191a8403c2002e13df7de",
                "ethereum",
                "https://etherscan.io/address/0x6872b6630a3afcd3117191a8403c2002e13df7de",
            ),
            _supplied_explorer_label(
                "0xfa500178de024bf43cfa69b7e636a28ab68f2741",
                "bsc",
                "https://bscscan.com/address/0xfa500178de024bf43cfa69b7e636a28ab68f2741",
            ),
            _supplied_explorer_label(
                "0x6872b6630a3afcd3117191a8403c2002e13df7de",
                "polygon",
                "https://polygonscan.com/address/0x6872b6630a3afcd3117191a8403c2002e13df7de",
            ),
            _supplied_explorer_label(
                "0x019d0706d65c4768ec8081ed7ce41f59eef9b86c",
                "polygon",
                "https://polygonscan.com/address/0x019d0706d65c4768ec8081ed7ce41f59eef9b86c",
            ),
            _supplied_explorer_label(
                "bc1qmd3nsuw3z7fwr3wt7ac7ydceyeyu2cflft4ltm",
                "bitcoin",
                "https://mempool.space/address/bc1qmd3nsuw3z7fwr3wt7ac7ydceyeyu2cflft4ltm",
            ),
            *STAKE_CROSS_CHAIN_WALLETS,
        ),
        wallets=STAKE_GAMSTAT_WALLETS,
    ),
    "rollbit": Casino(
        slug="rollbit",
        name="Rollbit",
        website="https://rollbit.com",
        licensed_in="Curaçao",
        established=2020,
        wallets=ROLLBIT_GAMSTAT_WALLETS,
    ),
    "bcgame": Casino(
        slug="bcgame",
        name="BC.Game",
        website="https://bc.game",
        licensed_in="Curaçao",
        established=2017,
        wallets=BCGAME_GAMSTAT_WALLETS,
        # The legacy inline list is retained in source history only; use the
        # complete chain-specific Gamstat cluster above.
        legacy_wallets=(
            _gamstat_label(
                "JEBRptmAAjqtxg6c4WLQDaZPeEA8RXnW4dVyhvsvZnxQ", "solana",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0xd352e0d71e14c45b719fe31d1eaa13051ede129b", "bsc",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0xa7b9874d15742358fb455dd56f97c6d19ad74f5c", "base",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0x6adc35bbdd759be047d9d28b94f5734a9c0cb563", "polygon",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0xc199feb7ce2b17fa84162ee705ebb35a2f19407d", "ethereum",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0xe7176831c898d585cd999bcee9984a7fa9a6be96", "arbitrum",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0x120a5b1fd4782cd8639e3814781a5d30382e65db", "ethereum",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0x49395574019ae44d46d535215303a09fd596727c", "bsc",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "bc1qqpdkczlc78nkss6wspse8rerf8u9eatce3mmk0", "bitcoin",
                source_confidence=0.7, casino_name="BC.Game",
                source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0x3ba9ea0ffeff9efdd7cb7eafb3ac6788a21b5aa7", "ethereum", "cold",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0xf09214d414312980446c5a6133b9c3db5918b7c5", "ethereum",
                source_confidence=0.8, casino_name="BC.Game",
                source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0x788529118f2a28c60b9de2ba0353f5ee4293e044", "ethereum",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0x41fc802e01bcf85d91e5708b42d41c2eaf01f375", "ethereum",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0xe983fd1798689eee00c0fb77e79b8f372df41060", "ethereum",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0x5472356f1de00bca5d729cfb6419c44b8d4488ab", "ethereum",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0x9d2a0e32633d9be838bfde19d510e6aa6eb202dd", "ethereum",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "0x8aaf720bbbcac82c592ac8f6c628bbac1590e079", "ethereum",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
            _gamstat_label(
                "TTUM1sLKN5735BdrdsJqLPnYaKESeWQGkB", "tron",
                casino_name="BC.Game", source_url="https://gamstat.io/casinos/bc-game",
            ),
        ),
    ),
    "shuffle": Casino(
        slug="shuffle",
        name="Shuffle.com",
        website="https://shuffle.com",
        licensed_in="Anjouan",
        established=2023,
        wallets=SHUFFLE_GAMSTAT_WALLETS,
        legacy_wallets=(
            _gamstat_label(
                "0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "bsc", "cold"
            ),
            _gamstat_label(
                "76iXe9yKFDjGv3HicUVVy8AYxHLC71L1wYa12zaZzHHp",
                "solana",
                explorer_url="https://solscan.io/account/76iXe9yKFDjGv3HicUVVy8AYxHLC71L1wYa12zaZzHHp",
            ),
            _gamstat_label(
                "0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "ethereum", "cold"
            ),
            _gamstat_label(
                "0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "base"
            ),
            _gamstat_label(
                "TWGSJz33dNGMhQYhSRLSKKUyFNewh8JEnp",
                "tron",
                explorer_url="https://tronscan.org/#/address/TWGSJz33dNGMhQYhSRLSKKUyFNewh8JEnp",
            ),
            _gamstat_label(
                "0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "polygon"
            ),
            _gamstat_label(
                "0xdfaa75323fb721e5f29d43859390f62cc4b600b8", "arbitrum"
            ),
            _gamstat_label(
                "Eq9p5iHVbNR4miwmFMkpuPwLLULZmPTxNUPBgLdNrWYy",
                "solana",
                explorer_url="https://solscan.io/account/Eq9p5iHVbNR4miwmFMkpuPwLLULZmPTxNUPBgLdNrWYy",
            ),
            _gamstat_label(
                "0x911a978f0cac392079b51db03e6f3027dfe6f96e", "bsc"
            ),
            _gamstat_label(
                "0x911a978f0cac392079b51db03e6f3027dfe6f96e",
                "ethereum",
                source_confidence=0.7,
            ),
            _gamstat_label(
                "0x911a978f0cac392079b51db03e6f3027dfe6f96e", "polygon"
            ),
            _gamstat_label(
                "0x911a978f0cac392079b51db03e6f3027dfe6f96e",
                "base",
                source_confidence=0.7,
            ),
        ),
    ),
    "yeet": Casino(
        slug="yeet",
        name="Yeet",
        website="https://yeet.com",
        established=2023,
        legacy_wallets=tuple(
            _gamstat_label(
                address,
                chain,
                casino_name="Yeet",
                source_url="https://gamstat.io/casinos/yeet",
            )
            for address, chain in (
                ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "ethereum"),
                ("TPKJ2wzjxASvQZQBmyegQrU1hExL2yvnLN", "tron"),
                ("6UxrMpGdiqsncwBawPjxsZtQb3e6nsgYo1pVSbSeNAaE", "solana"),
                ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "polygon"),
                ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "bsc"),
                ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "base"),
                ("0xc55b68e4e97a945b150c0c6865a3cb4c22ccefd4", "arbitrum"),
            )
        ),
        wallets=YEET_GAMSTAT_WALLETS,
    ),
    "betfury": Casino(
        slug="betfury",
        name="BetFury",
        website="https://betfury.com",
        licensed_in="Curaçao",
        established=2019,
    ),
    # ── Catalogued, not yet attributed ───────────────────────────────────────
    # Known operators with no reviewed wallet claim. Present so coverage gaps
    # are visible rather than silently absent; they report as unobserved.
    "gamdom": Casino(
        slug="gamdom", name="Gamdom", website="https://gamdom.com",
        licensed_in="Curaçao", established=2016,
    ),
    "roobet": Casino(
        slug="roobet", name="Roobet", website="https://roobet.com",
        licensed_in="Curaçao", established=2019,
    ),
    "duelbits": Casino(
        slug="duelbits", name="Duelbits", website="https://duelbits.com",
        licensed_in="Curaçao", established=2020,
    ),
    "csgoempire": Casino(
        slug="csgoempire", name="CSGOEmpire", website="https://csgoempire.com",
        established=2016,
    ),
    "bitcasino": Casino(
        slug="bitcasino", name="Bitcasino.io", website="https://bitcasino.io",
        licensed_in="Curaçao", established=2014,
    ),
    "cloudbet": Casino(
        slug="cloudbet", name="Cloudbet", website="https://cloudbet.com",
        licensed_in="Curaçao", established=2013,
    ),
    "fortunejack": Casino(
        slug="fortunejack", name="FortuneJack", website="https://fortunejack.com",
        licensed_in="Curaçao", established=2014,
    ),
    "1xbit": Casino(
        slug="1xbit", name="1xBit", website="https://1xbit.com",
        licensed_in="Curaçao", established=2016,
    ),
    "sportsbet": Casino(
        slug="sportsbet", name="Sportsbet.io", website="https://sportsbet.io",
        licensed_in="Curaçao", established=2016,
    ),
    "thunderpick": Casino(
        slug="thunderpick", name="Thunderpick", website="https://thunderpick.io",
        licensed_in="Curaçao", established=2017,
    ),
    "vave": Casino(
        slug="vave", name="Vave", website="https://vave.com",
        licensed_in="Curaçao", established=2022,
    ),
    "betplay": Casino(
        slug="betplay", name="BetPlay.io", website="https://betplay.io",
        licensed_in="Curaçao", established=2021,
    ),
    "trustdice": Casino(
        slug="trustdice", name="TrustDice", website="https://trustdice.win",
        licensed_in="Curaçao", established=2018,
    ),
    "wildio": Casino(
        slug="wildio", name="Wild.io", website="https://wild.io",
        licensed_in="Curaçao", established=2022,
    ),
    "metaspins": Casino(
        slug="metaspins", name="Metaspins", website="https://metaspins.com",
        established=2022,
    ),
    "jackbit": Casino(
        slug="jackbit", name="Jackbit", website="https://jackbit.com",
        licensed_in="Curaçao", established=2022,
    ),
    "coinpoker": Casino(
        slug="coinpoker", name="CoinPoker", website="https://coinpoker.com",
        established=2017,
    ),
    "betonline": Casino(
        slug="betonline", name="BetOnline", website="https://betonline.ag",
    ),
    "bovada": Casino(
        slug="bovada", name="Bovada", website="https://bovada.lv",
    ),
    "ignition": Casino(
        slug="ignition", name="Ignition Casino", website="https://ignitioncasino.eu",
    ),
    "mystake": Casino(
        slug="mystake", name="MyStake", website="https://mystake.com",
    ),
    "betpanda": Casino(
        slug="betpanda", name="BetPanda", website="https://betpanda.io",
    ),
    "coinsgame": Casino(
        slug="coinsgame", name="Coins.Game", website="https://coins.game",
    ),
    "bitstarz": Casino(
        slug="bitstarz", name="BitStarz", website="https://bitstarz.com",
    ),
    "mbit": Casino(
        slug="mbit", name="mBit Casino", website="https://mbitcasino.com",
    ),
    "7bit": Casino(
        slug="7bit", name="7Bit Casino", website="https://7bitcasino.com",
    ),
    "betsio": Casino(
        slug="betsio", name="Bets.io", website="https://bets.io",
    ),
    "betfinal": Casino(
        slug="betfinal", name="BetFinal", website="https://betfinal.com",
    ),
    "cryptogames": Casino(
        slug="cryptogames", name="Crypto.Games", website="https://crypto.games",
    ),
    "primedice": Casino(
        slug="primedice", name="Primedice", website="https://primedice.com",
    ),
    "freebitco": Casino(
        slug="freebitco", name="FreeBitco.in", website="https://freebitco.in",
    ),
    "nitrogen": Casino(
        slug="nitrogen", name="Nitrogen Sports", website="https://nitrogensports.eu",
    ),
    "betcoin": Casino(
        slug="betcoin", name="Betcoin.ag", website="https://betcoin.ag",
    ),
    "coinplay": Casino(
        slug="coinplay", name="Coinplay", website="https://coinplay.com",
    ),
    "wolfbet": Casino(
        slug="wolfbet", name="Wolf.bet", website="https://wolf.bet",
    ),
    "winz": Casino(
        slug="winz", name="Winz.io", website="https://winz.io",
    ),
    "coinbet24": Casino(
        slug="coinbet24", name="Coinbet24", website="https://coinbet24.com",
    ),
    "betandyou": Casino(
        slug="betandyou", name="BetAndYou", website="https://betandyou.com",
    ),
    "22bet": Casino(
        slug="22bet", name="22Bet", website="https://22bet.com",
    ),
    "melbet": Casino(
        slug="melbet", name="MelBet", website="https://melbet.com",
    ),
    "linebet": Casino(
        slug="linebet", name="Linebet", website="https://linebet.com",
    ),
    "megapari": Casino(
        slug="megapari", name="Megapari", website="https://megapari.com",
    ),
    "mostbet": Casino(
        slug="mostbet", name="Mostbet", website="https://mostbet.com",
    ),
    "parimatch": Casino(
        slug="parimatch", name="Parimatch", website="https://parimatch.com",
    ),
    "stakeus": Casino(
        slug="stakeus", name="Stake.us", website="https://stake.us",
    ),
    "chanced": Casino(
        slug="chanced", name="Chanced", website="https://chanced.com",
    ),
    "rainbet": Casino(
        slug="rainbet", name="Rainbet", website="https://rainbet.com",
    ),
    "chipsgg": Casino(
        slug="chipsgg", name="Chips.gg", website="https://chips.gg",
    ),
    "clashgg": Casino(
        slug="clashgg", name="Clash.gg", website="https://clash.gg",
    ),
    "csgoroll": Casino(
        slug="csgoroll", name="CSGORoll", website="https://csgoroll.com",
    ),
}


# ── Accessors ────────────────────────────────────────────────────────────────


def get_casino(slug: str) -> Casino | None:
    return CASINOS.get(slug.lower())


def attributed_operators() -> list[Casino]:
    """Operators with at least one wallet claim — the only ones that can
    produce flow figures. Sorted for deterministic output."""
    return sorted(
        (c for c in CASINOS.values() if c.is_attributed), key=lambda c: c.slug
    )


def catalog() -> list[Casino]:
    """Every catalogued operator, attributed or not."""
    return sorted(CASINOS.values(), key=lambda c: c.slug)


def coverage_summary() -> dict[str, int]:
    attributed = attributed_operators()
    return {
        "operators_catalogued": len(CASINOS),
        "operators_attributed": len(attributed),
        "operators_unattributed": len(CASINOS) - len(attributed),
        "wallet_clusters": sum(len(c.wallets) for c in attributed),
        "chains_covered": len(INDEXED_CHAINS) if attributed else 0,
    }


# Backwards-compatible alias. `all_casinos()` historically meant "operators we
# report stats for", which is exactly the attributed set.
def all_casinos() -> list[Casino]:
    return attributed_operators()


def resolve_address(address: str) -> Casino | None:
    """Reverse-lookup: which operator, if any, claims this wallet?"""
    normalized = address.lower()
    for casino in CASINOS.values():
        for wallet in casino.wallets:
            if wallet.address.lower() == normalized:
                return casino
    return None


def resolve_wallet(address: str) -> tuple[Casino, WalletCluster] | None:
    """Return the exact attribution claim for an address, when one exists."""
    normalized = address.lower()
    for casino in CASINOS.values():
        for wallet in casino.wallets:
            if wallet.address.lower() == normalized:
                return casino, wallet
    return None
