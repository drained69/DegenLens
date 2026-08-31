#!/usr/bin/env python3
"""Build the latency-safe ONCHAIN_TX_LOOKUP scorer.

The source crate contains the OTX-specific bounded lexical scorer. Do not use
the champion-derived v14 artifact for fixture evaluation: it embeds the
24 MB transformer champion and spends most of the budget during module load.
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess

from Crypto.Hash import keccak


ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "degenlens_onchain_tx_lookup_v15.wasm"
SOURCE = ROOT / "target" / "wasm32-unknown-unknown" / "release" / "degenlens_scorer.wasm"


def keccak256(data: bytes) -> str:
    digest = keccak.new(digest_bits=256)
    digest.update(data)
    return digest.hexdigest()


def main() -> None:
    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--target",
            "wasm32-unknown-unknown",
        ],
        cwd=ROOT,
        check=True,
    )
    if not SOURCE.is_file():
        raise RuntimeError(f"WASM build output not found: {SOURCE}")
    shutil.copyfile(SOURCE, OUTPUT)
    data = OUTPUT.read_bytes()
    print(
        f"{OUTPUT.name} bytes={len(data)} keccak256={keccak256(data)} "
        f"sha256={hashlib.sha256(data).hexdigest()}"
    )


if __name__ == "__main__":
    main()
