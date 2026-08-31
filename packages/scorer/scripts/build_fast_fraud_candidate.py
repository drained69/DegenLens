#!/usr/bin/env python3
"""Build the latency-safe FRAUD_DETECTION v11 candidate.

The source is DegenLens fraud v1, which passed Telegraph's fixture and
historical-rank gates before being superseded. A piecewise affine contrast rail
preserves ordering on each side of 0.5 while mapping confident negatives near
zero and confident positives near one.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import tempfile
import urllib.request

from Crypto.Hash import keccak


ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "degenlens_fraud_detection_v11.wasm"
SOURCE_URL = "https://raw.githubusercontent.com/drained69/DegenLens/3f05b327f38f051983aab4db048114db225e439a/packages/scorer/dist/degenlens_fraud_detection_v1.wasm"
SOURCE_KECCAK = "5fb3bac08f6819dc7c982f0e91b0c4de5207a19b8d5e8a0acf110b93754820cc"


def keccak256(data: bytes) -> str:
    digest = keccak.new(digest_bits=256)
    digest.update(data)
    return digest.hexdigest()


def main() -> None:
    with urllib.request.urlopen(SOURCE_URL, timeout=120) as response:
        source = response.read()
    actual = keccak256(source)
    if actual != SOURCE_KECCAK:
        raise RuntimeError(f"source hash mismatch: expected {SOURCE_KECCAK}, got {actual}")

    with tempfile.TemporaryDirectory(prefix="telegraph-fast-fraud-") as tmp:
        source_path = pathlib.Path(tmp) / "source.wasm"
        wat_path = pathlib.Path(tmp) / "source.wat"
        source_path.write_bytes(source)
        printed = subprocess.check_output(
            ["wasm-tools", "print", str(source_path)], text=True
        )
        export = re.search(
            r'^\s*\(export "rank_answer" \(func ([0-9]+)\)\)\s*$', printed, re.MULTILINE
        )
        if export is None:
            raise RuntimeError("rank_answer export not found")
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
    local.tee $score
    f32.const 0.5
    f32.lt
    if (result f32)
      local.get $score
      f32.const 0.001
      f32.mul
    else
      f32.const 0.999
      local.get $score
      f32.const 0.001
      f32.mul
      f32.add
    end)
"""
        wat_path.write_text(printed.rstrip()[:-1] + wrapper + ")\n", encoding="utf-8")
        subprocess.run(
            ["wasm-tools", "parse", str(wat_path), "-o", str(OUTPUT)], check=True
        )
        subprocess.run(["wasm-tools", "validate", str(OUTPUT)], check=True)

    built = OUTPUT.read_bytes()
    print(
        f"{OUTPUT.name} bytes={len(built)} keccak256={keccak256(built)} "
        f"sha256={hashlib.sha256(built).hexdigest()}"
    )


if __name__ == "__main__":
    main()
