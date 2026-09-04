#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wasm="$root/target/wasm32-unknown-unknown/release/degenlens_scorer.wasm"

cargo build --manifest-path "$root/Cargo.toml" --release --target wasm32-unknown-unknown

if [[ ! -s "$wasm" ]]; then
  printf 'missing WASM artifact: %s\n' "$wasm" >&2
  exit 1
fi

size="$(wc -c < "$wasm" | tr -d ' ')"
if (( size > 33554432 )); then
  printf 'WASM artifact exceeds 32 MiB: %s bytes\n' "$size" >&2
  exit 1
fi

if command -v wasm-tools >/dev/null 2>&1; then
  imports="$(wasm-tools print "$wasm" | grep -Fc '(import ' || true)"
  if (( imports != 0 )); then
    printf 'WASM artifact has %s imports; Telegraph modules must be self-contained\n' "$imports" >&2
    exit 1
  fi
  exports="$(wasm-tools print "$wasm")"
  for name in alloc dealloc rank_answer; do
    if ! printf '%s\n' "$exports" | grep -F "(export \"$name\"" >/dev/null; then
      printf 'WASM artifact is missing export: %s\n' "$name" >&2
      exit 1
    fi
  done
else
  printf 'wasm-tools not installed; skipped import/export inspection\n' >&2
fi

printf 'verified %s (%s bytes)\n' "$wasm" "$size"
