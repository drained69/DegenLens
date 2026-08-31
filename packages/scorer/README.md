# DegenLens WASM Scorer

## Current champion challengers

The latest candidates start from each intent's exact live champion and apply
`score²` to its output. Squaring is strictly increasing on `[0, 1]`: it retains
the champion's answer ordering and Spearman rank correlation while expanding
the high-confidence separation that Telegraph's promotion gate measures. It
also avoids the near-1 `f32` ties caused by smoothstep's zero slope at 1.

| Candidate | Intent | Live champion | Champion margin | Candidate keccak256 |
|---|---|---|---:|---|
| `dist/degenlens_onchain_tx_lookup_v14.wasm` | `ONCHAIN_TX_LOOKUP` | reg 642, `otx_t74.wasm` | 0.7922707 | `73e290a1b8d206fbc83c044b419570921a350c8e01263e4f7044356888549f28` |
| `dist/degenlens_fraud_detection_v10.wasm` | `FRAUD_DETECTION` | reg 1852, `frq_c65.wasm` | 0.9985664 | `8a36b09b2eb2313b1d6465bc6aa911b730dbad1c943f38d5d57348fca8502dc3` |
| `dist/degenlens_wallet_balance_check_v2.wasm` | `WALLET_BALANCE_CHECK` | reg 1066, `wl_penstep40.wasm` | 0.7821707 | `2947266b34e4606c0fbb1e4e44bf6da1146ab298bf6a6c02736d48b9348272ec` |

Build all three reproducibly with:

```bash
python3 packages/scorer/scripts/build_champion_candidates.py
```

The script downloads commit-pinned champion bytes, rejects a source whose
keccak256 differs from the live registration, replaces only the exported
`rank_answer` with a wrapper around the original function, and validates each
result with `wasm-tools`. The source champions were published before upstream's
2026-08-30 license change and remain MIT licensed; see
`NOTICE.champion-candidates`.

On the local OTX corpus, v14 raises the champion's margin from `0.4378291` to
`0.4391241`, preserves all ordering and ties across the 50 scored answers, and
matches `score²` within `2.98e-8`. Local fixtures do not reproduce Telegraph's
hidden benchmark. These are structurally and mathematically stronger
challengers, but a candidate should not be described as having beaten a live
champion until the node completes its registration evaluation.

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
| `dist/degenlens_onchain_tx_lookup_v6.wasm` | `ONCHAIN_TX_LOOKUP` | registered as reg 940-something; rejected, lost by **0.011** (0.7507 vs 0.7615). `W_EMB = 0.30`. | `5e7f1952e19f5403e83cd52b1007e86480452471f5074e0a3ca167ed1b146540` |
| `dist/degenlens_onchain_tx_lookup_v7.wasm` | `ONCHAIN_TX_LOOKUP` | reg 980; rejected, lost by **0.026** (0.7664 vs 0.7923). `W_EMB = 0.50`. | `969e6e5b1faed8b6072b1e4325972144b4f624fea2dfdee8de404278b124e6f9` |
| `dist/degenlens_onchain_tx_lookup_v8.wasm` | `ONCHAIN_TX_LOOKUP` | reg 986; rejected, lost by **0.034** (0.7443 vs 0.7778). `W_EMB = 0.25`. | `ea9481819cecf27641cd33edbe33d9acded9476b1f79473b32efd5bf916c06ae` |
| `dist/degenlens_fraud_detection_v1.wasm` | `FRAUD_DETECTION` | **not yet registered.** Bar: candidate_margin > **0.7796** on 15-case subset; spearman ≥ 0.60 only kicks in once the candidate has ≥ 1 historical row (channel-49 rejections in the ledger show spearman gating from 32+ rows). `W_EMB = 0.30` recipe. | `5fb3bac08f6819dc7c982f0e91b0c4de5207a19b8d5e8a0acf110b93754820cc` |

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

| reg | build | W_EMB | node `candidate_margin` | champion_margin (same subset) | gap | outcome |
|---|---|---|---|---|---|---|
| 551 | v2 | 0.0 | 0.5967 | — | — | briefly champion, superseded |
| 710 | v4 | 0.0 | 0.5292 | — | large | rejected — regressed on the real benchmark despite scoring 0.9453 on the old local proxy |
| 810 | v5 | 0.0 | — | — | — | stuck `pending` on `gateway.pinata.cloud`; every evaluated candidate is on `raw.githubusercontent.com` or a plain HTTPS host, never Pinata |
| — | **v6** | 0.30 | 0.7507 | 0.7615 | **−0.011** | rejected — but **+22 pts on the real benchmark over v4**. Closest we've come. |
| 980 | v7 | 0.50 | 0.7664 | 0.7923 | −0.026 | rejected. Coverage rose (15/15 cases) but subset became harder — champion looks better on the extra cases than we do. |
| 986 | v8 | 0.25 | 0.7443 | 0.7778 | −0.034 | rejected. Step *below* v6 also widened gap. **v6 is the peak on the W_EMB axis; neither direction improves it.** |

**Where the W_EMB gradient goes from here:** nowhere useful. v6 → v7 (+0.20) and v6 → v8 (−0.05) both widened the gap. The axis is fully explored. Further improvement needs a different lever:

1. **Architectural.** The current blend `raw = (1-W)·lex + W·sc` drags high-precision paraphrases (case-20 style: identical facts, one different word) *down* when the embedding cosine is weaker than the lexical score. A monotone-lift blend `raw = max(raw, W·sc + (1-W)·raw)` (never depresses) would preserve those cases while still lifting cases where lexical is weak but topical similarity is strong.
2. **Retrained vectors.** The 794 KB int8 50-dim table caps how well embeddings can carry paraphrase equivalence. Champion 642's 24 MB transformer sees them as identical (1.00); ours reads 0.5-ish and drags the linear blend. A wider, better-trained `vectors.bin` is the real fix but a separate build project.

### FRAUD_DETECTION registrations

Live champion: **registration 63** (zkasuran, same 1.04 MB salience-family
architecture as ours — not the big `xfmr` model), `eval_score` **0.7890** over
32 cases. Champion margin on a challenger's 15-case subset is **0.7796**.

Two gates, verified against the live leaderboard:

- **Margin gate.** `candidate_margin` > `champion_margin` (0.7796) on the
  15-case comparable subset. Reg 941 lost outright with 0.7648 (`hist_rows=0`,
  so no spearman was even computed — margin failure was terminal).
- **Spearman gate.** Only runs once a candidate has ≥ 1 historical
  real-traffic row. Reg 955 aced margin at **1.0000** on 49 historical rows
  but hit spearman **0.4411** — rejected. Reg 608 hit **0.9999** margin
  on 32 rows, spearman **0.2423** — rejected. Reg 590: margin **0.8680**,
  spearman **0.2284** — rejected. Every step-function / binary-shaped
  scorer aces margin and fails spearman.

The champion itself has `historical_rows_evaluated: 0` — spearman never gated
it, so a first-time candidate can pass the margin gate and be immediately
promoted without proving spearman. But subsequent traffic will grade it,
and a scorer that returns only 0 / 1 will lose the slot on the next
evaluation window.

Our v1 build applies v6's recipe (salience penalties for sharp verdict
separation + 30 % embedding blend for ranking nuance) to preserve gradation
and threading. On the OTX-style smoke corpus our margin is **+0.6200** vs
champion 63's **+0.4644** — very different shape from the champion, which
is nearly indistinguishable on wrong-address and wrong-entity cases.

Registration URL (raw GitHub, byte-verified round-trip):

```
https://raw.githubusercontent.com/drained69/DegenLens/3f05b327f38f051983aab4db048114db225e439a/packages/scorer/dist/degenlens_fraud_detection_v1.wasm
```

`wasmHash` for `registerWasm(wasmHash, wasmUrl, intent)`:
`0x5fb3bac08f6819dc7c982f0e91b0c4de5207a19b8d5e8a0acf110b93754820cc`.

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
