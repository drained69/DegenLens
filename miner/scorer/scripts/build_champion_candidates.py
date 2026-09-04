#!/usr/bin/env python3
"""Build rank-preserving challengers from the three MIT-era champions.

The wrapper squares the champion's score. The transform fixes 0 and 1, is
strictly increasing on [0, 1], and expands differences near 1 instead of
rounding distinct near-perfect f32 scores into ties.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import subprocess
import tempfile
import urllib.request

from Crypto.Hash import keccak


ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

CHAMPIONS = {
    "ONCHAIN_TX_LOOKUP": {
        "url": "https://raw.githubusercontent.com/zkasuran/telegraph-salience-scorer/92167ea85229156e2e761afa36a6b50fcc9fedfa/dist/xfmr/otx_t74.wasm",
        "keccak": "2780ba63d46a6fe0b4ea5ea8e6ff4070358c9087a4ee5d81848d2f5b78aef24f",
        "output": "degenlens_onchain_tx_lookup_v14.wasm",
    },
    "FRAUD_DETECTION": {
        "url": "https://raw.githubusercontent.com/zkasuran/telegraph-salience-scorer/8c7b91f4bc7a2a5b79ee01c438536773644d0736/dist/fork/frq_c65.wasm",
        "keccak": "6368c44fa6607592fa2bd9fba9cdeed55e5ac4e45f5379689a3a5227aa6cc5a7",
        "output": "degenlens_fraud_detection_v10.wasm",
    },
    "WALLET_BALANCE_CHECK": {
        "url": "https://raw.githubusercontent.com/zkasuran/telegraph-salience-scorer/4005fbb5cb6720a4302921077ed60929a55ffcac/dist/xfmr/wl_penstep40.wasm",
        "keccak": "55afa8d7a4da2a0c0edd3ad2c33b175e0fb9f92e9528adab354c1148e798e97d",
        "output": "degenlens_wallet_balance_check_v2.wasm",
    },
}


def keccak256(data: bytes) -> str:
    digest = keccak.new(digest_bits=256)
    digest.update(data)
    return digest.hexdigest()


def power_body(rounds: int) -> str:
    blocks = []
    for _ in range(rounds):
        blocks.append(
            """
    local.get $score
    local.get $score
    f32.mul
    local.set $score"""
        )
    return "".join(blocks)


def wrap(source: pathlib.Path, output: pathlib.Path, rounds: int) -> None:
    with tempfile.TemporaryDirectory(prefix="telegraph-wasm-") as tmp:
        wat_path = pathlib.Path(tmp) / "champion.wat"
        printed = subprocess.run(
            ["wasm-tools", "print", str(source)], check=True, capture_output=True
        ).stdout.decode("utf-8")

        export = re.search(
            r'^\s*\(export "rank_answer" \(func ([0-9]+)\)\)\s*$', printed, re.MULTILINE
        )
        if export is None:
            raise RuntimeError(f"rank_answer export not found in {source}")
        function_index = export.group(1)
        printed = printed[: export.start()] + printed[export.end() :]
        wrapper = f"""
  (func (export "rank_answer")
    (param i32 i32 i32 i32 i32 i32) (result f32)
    (local $score f32)
    local.get 0
    local.get 1
    local.get 2
    local.get 3
    local.get 4
    local.get 5
    call {function_index}
    local.set $score{power_body(rounds)}
    local.get $score)
"""
        wat_path.write_text(printed.rstrip()[:-1] + wrapper + ")\n", encoding="utf-8")
        subprocess.run(
            ["wasm-tools", "parse", str(wat_path), "-o", str(output)], check=True
        )
        subprocess.run(["wasm-tools", "validate", str(output)], check=True)


def build(intent: str, rounds: int) -> pathlib.Path:
    spec = CHAMPIONS[intent]
    with tempfile.TemporaryDirectory(prefix="telegraph-champion-") as tmp:
        source = pathlib.Path(tmp) / "champion.wasm"
        with urllib.request.urlopen(spec["url"], timeout=120) as response:
            data = response.read()
        actual = keccak256(data)
        if actual != spec["keccak"]:
            raise RuntimeError(
                f"{intent} champion hash mismatch: expected {spec['keccak']}, got {actual}"
            )
        source.write_bytes(data)
        output = DIST / spec["output"]
        wrap(source, output, rounds)
        built = output.read_bytes()
        print(
            f"{intent}: {output.name} bytes={len(built)} "
            f"keccak256={keccak256(built)} sha256={hashlib.sha256(built).hexdigest()}"
        )
        return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--intent", choices=[*CHAMPIONS, "all"], default="all")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 4:
        parser.error("--rounds must be between 1 and 4")
    intents = CHAMPIONS if args.intent == "all" else (args.intent,)
    for intent in intents:
        build(intent, args.rounds)


if __name__ == "__main__":
    main()
