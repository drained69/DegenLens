#!/usr/bin/env python3
"""Build small, independently identifiable text-intent scorer modules."""
from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess

from Crypto.Hash import keccak


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "target" / "wasm32-unknown-unknown" / "release" / "degenlens_scorer.wasm"
TARGETS = {
    "RESEARCH_SYNTHESIS": "degenlens_research_synthesis_v1.wasm",
    "TEXT_GENERATION": "degenlens_text_generation_v1.wasm",
    "WEB_SEARCH": "degenlens_web_search_v1.wasm",
}


def digest(data: bytes) -> str:
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.hexdigest()


def main() -> None:
    for intent, filename in TARGETS.items():
        env = os.environ.copy()
        env["SCORER_INTENT"] = intent
        subprocess.run(
            ["cargo", "build", "--release", "--target", "wasm32-unknown-unknown"],
            cwd=ROOT,
            env=env,
            check=True,
        )
        output = ROOT / "dist" / filename
        shutil.copyfile(SOURCE, output)
        data = output.read_bytes()
        print(f"{intent}: {filename} bytes={len(data)} keccak256={digest(data)} sha256={hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
