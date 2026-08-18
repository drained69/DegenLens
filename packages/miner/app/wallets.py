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

from dataclasses import dataclass, field
from typing import Literal

Chain = Literal[
    "ethereum", "base", "polygon", "arbitrum", "optimism", "bsc", "avalanche"
]

EvidenceStatus = Literal["verified", "curated", "unverified_seed"]

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
    role: Literal["deposit", "hot", "cold", "treasury"]
    confidence: float
    evidence_status: EvidenceStatus
    evidence: tuple[str, ...] = ()
    last_reviewed: str = "2026-08-17"

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

    @property
    def is_attributed(self) -> bool:
        """True when we hold at least one wallet claim for this operator."""
        return bool(self.wallets)

    @property
    def chains(self) -> list[str]:
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
        wallets=(
            _seed("0x974caa59e49682cda0ad2bbe82983419a2ecc400", "ethereum", "hot"),
            _seed("0xb1c73f13a26cb15b93c3d2eab7e77f56b8ffe6d3", "ethereum", "cold"),
        ),
    ),
    "rollbit": Casino(
        slug="rollbit",
        name="Rollbit",
        website="https://rollbit.com",
        licensed_in="Curaçao",
        established=2020,
        wallets=(
            _seed("0xef4fb24ad0916217251f553c0596f8edc630eb66", "ethereum", "hot"),
        ),
    ),
    "bcgame": Casino(
        slug="bcgame",
        name="BC.Game",
        website="https://bc.game",
        licensed_in="Curaçao",
        established=2017,
        wallets=(
            _seed("0xdd9f24efc84d93deef3c8745c837ab63e80abd27", "ethereum", "hot"),
        ),
    ),
    "shuffle": Casino(
        slug="shuffle",
        name="Shuffle.com",
        website="https://shuffle.com",
        licensed_in="Anjouan",
        established=2023,
        wallets=(
            _seed("0x9b1f7f645351af3631a656421ed2e40f2802e6c0", "ethereum", "hot"),
        ),
    ),
    "betfury": Casino(
        slug="betfury",
        name="BetFury",
        website="https://betfury.com",
        licensed_in="Curaçao",
        established=2019,
        wallets=(
            _seed("0x03bdf69b1322d623836afbd27679a1c0afa067e9", "ethereum", "hot"),
        ),
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
        "chains_covered": len({w.chain for c in attributed for w in c.wallets}),
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
