# DegenLens WASM Scorer

Telegraph scoring modules for `ONCHAIN_TX_LOOKUP` and `FRAUD_DETECTION`. One
source tree builds both: the scoring logic is shared and only
`TELEGRAPH_INTENT` differs, because the two intents fail the same ways — a
figure, a name, an address or a verdict that is nearly right and therefore
wrong.

It uses salience-weighted precision and recall, character n-grams, numeric,
identifier and entity checks, polarity, negation, structure and ordering
signals, and a mean-pooled sentence-embedding cosine (`W_EMB = 0.30`) blended
on top, to judge miner answers against ground truth.

| Artifact | Intent | Bar to clear | keccak256 |
|---|---|---|---|
| `dist/degenlens_onchain_tx_lookup_v6.wasm` | `ONCHAIN_TX_LOOKUP` | champion 642, margin **0.7615** on 15-case comparable subset | `5e7f1952e19f5403e83cd52b1007e86480452471f5074e0a3ca167ed1b146540` |
| `dist/degenlens_onchain_tx_lookup_v7.wasm` | `ONCHAIN_TX_LOOKUP` | same as v6; next-step candidate with `W_EMB = 0.50` | `969e6e5b1faed8b6072b1e4325972144b4f624fea2dfdee8de404278b124e6f9` |
| `dist/degenlens_fraud_detection_v1.wasm` | `FRAUD_DETECTION` | champion 63, margin **0.7796** on 15-case subset + spearman ≥ 0.60 on ~32 historical rows | `5fb3bac08f6819dc7c982f0e91b0c4de5207a19b8d5e8a0acf110b93754820cc` |

The scoring core is derived from
[`zkasuran/telegraph-salience-scorer`](https://github.com/zkasuran/telegraph-salience-scorer)
under the MIT License. See `LICENSE.salience-scorer`. DegenLens adds an
order-preserving contrast pass, enables the mean-pooled embedding blend
(disabled upstream for non-CHAT intents), and packages the intent-specific
build and verification workflow.

## Evaluation

Telegraph's Stage 2 benchmark is built into the node and is not published, so
we grade progress from three sources: local separation on a small hand-built
corpus (in `calibration/`, useful for smoke checks but demonstrably
mis-ranked against the real benchmark), the calibration harness which scores
the same corpus with every already-evaluated competitor wasm we could
download, and — the only ground truth that matters — the `eval_score` and
`rejection_reason` the node returns after each registration.

### ONCHAIN_TX_LOOKUP registrations (author `0xdde7…d133e`)

Live champion: **registration 642** (zkasuran's `otx_t74.wasm`, ~24 MB
transformer/embedding family), overall `eval_score` **0.7923** across 15
comparable cases. Champion margin on the subset a new candidate is scored
against is recomputed per candidate — for v6 it was **0.7615**.

| reg | build | W_EMB | node `candidate_margin` | outcome |
|---|---|---|---|---|
| 551 | v2 | 0.0 | 0.5967 | briefly champion, superseded |
| 710 | v4 | 0.0 | 0.5292 | rejected — regressed on the real benchmark despite scoring 0.9453 on the old local proxy |
| 810 | v5 | 0.0 | — | stuck `pending` on `gateway.pinata.cloud`; every evaluated candidate is on `raw.githubusercontent.com` or a plain HTTPS host, never Pinata |
| — | **v6** | 0.30 | **0.7507** | rejected — lost by **0.011** to champion 642's 0.7615 on separation. **+22 pts on the real benchmark over v4.** |
| — | v7 | 0.50 | pending | single-lever extension of the gradient v6 opened; champion's own recipe is 75 % embedding |

### FRAUD_DETECTION registrations

Live champion: **registration 63** (zkasuran, same 1.04 MB salience-family
architecture as ours — not the big `xfmr` model), `eval_score` **0.7890** over
32 cases. Champion margin on a challenger's 15-case subset is **0.7796**.

Two gates. Every rejected candidate on the leaderboard falls into one bucket
or the other:

- **Margin gate**: `candidate_margin` > `champion_margin` on the 15-case
  comparable subset. Reg 941 lost with 0.7648, reg 903 with 0.7125.
- **Spearman gate**: rank agreement ≥ 0.60 with the champion across ~32
  historical real-traffic answers. Reg 955 aced margin at **1.0000** but
  hit spearman **0.4411**; reg 608 hit **0.9999** margin and spearman
  **0.2423**. Binary-shaped scorers ace margin and fail spearman.

The winning zone needs both. Our v1 build applies v6's recipe (salience
penalties for sharp verdict separation + 30 % embedding blend for ranking
nuance) to preserve gradation and threading. On a fraud-focused smoke
corpus our margin is **+0.6200** vs champion 63's **+0.5878**.

### Structural invariants

Zero imports, `alloc` / `dealloc` / `rank_answer` / `memory` /
`TELEGRAPH_INTENT` exported. Blank answers score exactly `0.0`, perfect
answers score `1.0`, tens-of-KB, emoji and non-Latin inputs are handled
without trapping. The build is reproducible — repeated `cargo build` runs
produce identical bytes.

### What the current build judges that upstream did not

- **Substituted names.** `Roobet hot wallets received 412,500,000 USDT`
  against a ground truth about Stake.com scored `1.0000` upstream: every
  figure matched and one name did not, which word overlap cannot see.
  Omitting a name is now incompleteness and is mild; putting a different one
  in its place is a false claim and is severe.
- **Padding.** The ground truth pasted forty times scored `1.0000` upstream —
  higher than the one-line correct answer it was built from, because
  precision counted every repetition as a fresh hit. Length ratio now
  dampens it, well above ordinary verbosity.
- **Terse answers.** An answer carrying every figure the ground truth states
  earns a bounded recall floor, gated on it also naming what the figure
  refers to. `18,430 unique depositors` counts; a bare `18,430` does not.
- **Identifiers.** Upstream's `alt_hash` aliased any token starting with a
  digit to its numeric prefix — every hex address collided with every
  other, and a balance reported against the wrong wallet scored `1.0000`.
  Identifiers are now matched exactly, never stemmed or aliased.
- **Word lists.** Harvesting the ground truth's content words and emitting
  them unconnected beat a correct terse answer upstream. Function-word rate
  separates prose from a keyword dump, gated on length so a terse answer is
  untouched and skipped for structured output (JSON, key/value, tables).
- **Sentence embeddings enabled.** `src/vectors.bin` ships 794 KB of int8
  50-dim vectors distilled against the CHAT champion. Upstream disables the
  blend for non-CHAT intents (`W_EMB = 0.0`); v6 turns it on at 0.30. This
  produced the +0.22 node jump for `ONCHAIN_TX_LOOKUP` over v4.

## Build

```bash
rustup target add wasm32-unknown-unknown
cargo build --release --target wasm32-unknown-unknown
ls -lh target/wasm32-unknown-unknown/release/degenlens_scorer.wasm
```

To build for a different intent, edit the `TELEGRAPH_INTENT` static (padded to
32 bytes) in `src/lib.rs` and re-run the same command. `W_EMB` in the tunables
block controls the embedding-vs-lexical blend; 0.30 is the current default.

Compiled `.wasm` is approximately 1.05 MB, well below Telegraph's 32 MB
limit. Registration commits the **keccak256** of the exact hosted bytes
(`registerWasm(wasmHash, wasmUrl, intent)`), not the sha256.

## Calibration

`calibration/` contains a Python harness that loads any Telegraph scoring
wasm via `wasmtime`, scores it against a hand-built corpus of
question/ground-truth/good-answer/bad-answer quadruples, and prints
local-vs-node rank agreement across every competitor wasm we have on disk.

```bash
.venv/bin/python packages/scorer/calibration/run_calibration.py
```

The corpus is small (25 cases) and demonstrably mis-ranks the champion
against the salience family — so use it to catch regressions, not to
predict node scores. The only reliable ground truth is a real registration.

## Test

Host tests use `std`; the WASM build stays `#![no_std]`.

```bash
cargo test
```

## Register

Host the compiled `.wasm` on `raw.githubusercontent.com` or any plain HTTPS
origin, then submit through
[integrate.telegraphprotocol.com](https://integrate.telegraphprotocol.com).
Registration is gas-only — no bond required.

`gateway.pinata.cloud` URLs have stalled in `pending` indefinitely for both
of our registrations there. GitHub raw is the only host we've confirmed the
node reliably fetches from.

## Why this beats word-overlap scorers

Generic scoring modules treat answers as bags of words. On-chain data has
hard structure:

- **Addresses** — 42-hex characters, case-insensitive but position-sensitive.
  Wrong-by-one is worthless.
- **Amounts** — a miner off by 1 % is roughly right; off by 10× is wrong.
  Word-overlap can't tell.
- **Recency** — stale gambling data is useless. Word-overlap rewards it
  identically to fresh data.
- **Verdict** — for `FRAUD_DETECTION` the polar answer (yes-fraud /
  no-fraud) is the whole point. Word-overlap misses a negation flip
  entirely.

A miner cannot game this scorer with question echoing, keyword stuffing,
negation insertion, verdict flips, reordered claims, or contradictory
figures.
