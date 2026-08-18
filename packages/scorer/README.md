# DegenLens WASM Scorer

Telegraph scoring module (Track 2). Judges miner answers for gambling-intelligence intents.

## Composite score

```
score = 0.50 × numeric_precision
      + 0.25 × address_accuracy
      + 0.15 × field_completeness
      + 0.10 × recency
```

Verbatim match = 1.0. Blank answer = 0.0.

## Build

```bash
rustup target add wasm32-unknown-unknown
cargo build --release --target wasm32-unknown-unknown
ls -lh target/wasm32-unknown-unknown/release/degenlens_scorer.wasm
```

Compiled `.wasm` should be well under 200KB (target: 32MB max per the Telegraph spec).

## Test

Host tests use `std`; the WASM build stays `#![no_std]`.

```bash
cargo test
```

## Register

Upload the compiled `.wasm` to IPFS or any HTTPS host, then submit through
[integrate.telegraphprotocol.com](https://integrate.telegraphprotocol.com).
Registration is gas-only — no bond required.

## Why this beats word-overlap scorers

Generic scoring modules treat answers as bags of words. On-chain data has hard structure:

- **Addresses** — 42-hex characters, case-insensitive but position-sensitive. Wrong-by-one is worthless.
- **Amounts** — a miner off by 1% is roughly right; off by 10× is wrong. Word-overlap can't tell.
- **Recency** — stale gambling data is useless. Word-overlap rewards it identically to fresh data.
- **Schema** — expected fields (`deposits_usd`, `unique_depositors`, `verdict`, `confidence`, `timestamp`).

A miner cannot game this scorer by keyword-stuffing because the address/number bands are strict.
