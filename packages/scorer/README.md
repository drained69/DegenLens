# DegenLens WASM Scorer

Telegraph scoring module for `ONCHAIN_TX_LOOKUP`. It uses salience-weighted
precision and recall, character n-grams, numeric and entity checks, polarity,
negation, and ordering signals to judge miner answers against ground truth.

The scoring core is derived from
[`zkasuran/telegraph-salience-scorer`](https://github.com/zkasuran/telegraph-salience-scorer)
under the MIT License. See `LICENSE.salience-scorer`. DegenLens adds an
order-preserving contrast pass and packages the intent-specific build and
verification workflow.

## Evaluation

- Blank answers score exactly `0.0`.
- Perfect answers score `1.0`.
- The active champion and this candidate both win `40/40` cases in the public
  proxy benchmark.
- The candidate margin is `0.7705`, compared with `0.6909` for the exact active
  champion binary on that benchmark.
- Structural checks and the `12/12` attack and robustness suite pass.

## Build

```bash
rustup target add wasm32-unknown-unknown
cargo build --release --target wasm32-unknown-unknown
ls -lh target/wasm32-unknown-unknown/release/degenlens_scorer.wasm
```

From the repository root, `pnpm scorer:verify` builds the exact
`wasm32-unknown-unknown` artifact and, when `wasm-tools` is installed, checks
that it has no imports and exports `alloc`, `dealloc`, and `rank_answer`.

Compiled `.wasm` is approximately 1.04 MB, below Telegraph's 32 MB limit.

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

A miner cannot game this scorer with question echoing, keyword stuffing,
negation insertion, verdict flips, reordered claims, or contradictory figures.
