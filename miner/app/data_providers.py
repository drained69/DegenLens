"""Provider contract for normalized chain observations.

Alchemy remains the current live adapter. Analytics depend on this contract,
not on provider-specific response shapes, so indexed and non-EVM providers can
be added without rewriting classification.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .intelligence import NormalizedTransfer


class BlockchainProvider(Protocol):
    name: str

    async def get_transfers(self, address: str, chain: str, since: datetime) -> tuple[list[NormalizedTransfer], bool]: ...
    async def get_transactions(self, address: str, chain: str, since: datetime) -> tuple[list[dict], bool]: ...
    async def get_token_transfers(self, address: str, chain: str, since: datetime) -> tuple[list[NormalizedTransfer], bool]: ...
    async def get_balance(self, address: str, chain: str) -> tuple[float, str]: ...
    async def get_block(self, chain: str, block_number: int) -> dict | None: ...
    async def get_transaction(self, tx_hash: str, chain: str) -> dict | None: ...
