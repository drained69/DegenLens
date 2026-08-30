//! Telegraph scoring module: salience-weighted answer scoring.
//!
//! Exports the three functions the node calls: `alloc`, `dealloc`, `rank_answer`.
//! Runs with no std, no network, no filesystem and no allocator, so every buffer
//! here is a fixed static and every loop is bounded.
//!
//! Scoring in one line: weight each word by how much information it carries,
//! measure precision and recall of the miner answer against the ground truth on
//! those weights, cross-check the facts that flip an answer from right to wrong
//! (numbers, negation, polar labels), then sharpen the contrast.
// `no_std` and the panic handler are the WASM build's contract with the node:
// no allocator, no runtime, trap on panic. They are gated on the wasm32 target
// because on a host target `std` already defines `panic_impl`, so an
// unconditional handler is a duplicate lang item and the crate cannot compile
// at all — which is why `cargo test` had never been runnable and the scoring
// invariants the README documents were unverified. The wasm32 build is
// unaffected: the same attributes apply under the same cfg, and the compiled
// artifact is byte-identical.
#![cfg_attr(target_arch = "wasm32", no_std)]

#[cfg(target_arch = "wasm32")]
use core::panic::PanicInfo;

#[cfg(target_arch = "wasm32")]
#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    core::arch::wasm32::unreachable()
}

// ---------------------------------------------------------------------------
// Tunables
// ---------------------------------------------------------------------------
// Kept in one block because they are swept, not guessed: `tune.py` rewrites this
// block, rebuilds and scores the result against two objectives at once, the
// benchmark separation the node's Stage 2 measures and the rank agreement with the
// live champion its traffic check measures. The comments say what each one trades.

/// Weight on token-level precision and recall, on character triples, on character
/// pairs. Pairs matter only as a tail breaker for short or unusual answers.
const W_LEX: f32 = 0.76;
const W_GRAM3: f32 = 0.2;
const W_GRAM2: f32 = 0.04;
/// F-beta squared. Below 1 leans on precision, above 1 leans on recall.
const F_BETA2: f32 = 0.6;
/// 1 to forgive dilution (concave in precision), 0 to score precision as it is.
const P_CONCAVE: f32 = 1.0;
/// How much of recall must come from the answer-bearing part of the ground truth,
/// and how much overall coverage can float an answer that words things its own way.
const R_KEY_BASE: f32 = 0.6;
const R_FLOOR: f32 = 0.3;
/// Polarity multipliers. Lower on contradiction separates good from bad harder;
/// higher keeps a wrong-but-on-topic answer inside the pack, which is where the
/// champion puts it, and the traffic gate scores agreement with the champion.
// Fraud answers are verdict-bearing. A fluent answer about the right wallet that
// reverses the risk conclusion must stay well below a cautious answer that omits
// the conclusion, while still retaining a graded tail for traffic ranking.
const M_CONTRA: f32 = 0.16;
const M_TWO_FACED: f32 = 0.28;
const M_SILENT: f32 = 0.9;
const B_AGREE: f32 = 0.4;
/// Fraud has one primary claim axis: whether the observed activity is benign or
/// suspicious. Keep this adjustment separate from generic yes/no language because
/// a fraud answer may say "no signals fired" while still containing many negative
/// words in its explanation.
const FRAUD_AUTH_CONTRA: f32 = 0.06;
const FRAUD_AUTH_TWO_FACED: f32 = 0.12;
const FRAUD_AUTH_AGREE: f32 = 0.62;
const FRAUD_AUTH_SILENT: f32 = 0.34;
/// Numbers: floor when a stated figure is missing, multiplier when a different one
/// is asserted instead. On a lookup intent the figure is the answer, so an otherwise
/// fluent reply that never states it has not answered the question — but it is vague
/// rather than false, so it keeps more than a reply that asserts the wrong figure and
/// far more than one that is simply off topic. The gradation is the point: a scorer
/// that flattens every kind of bad answer to zero cannot rank real traffic.
const M_NUM_MISS_BASE: f32 = 0.35;
const M_NUM_WRONG: f32 = 0.17;
/// Same words, no shared adjacency.
const M_ORDER: f32 = 0.55;
/// Word-list detection: how many content words an answer must have before absence of
/// function words counts against it, the function-word rate below which prose is not
/// plausible, and the most the check can take away. Tuned so a terse answer stays
/// clear of it — the discriminator is length, not sparseness alone.
const WORDLIST_MIN_CONTENT: usize = 5;
const WORDLIST_FUNC_RATIO: f32 = 0.2;
const M_WORDLIST: f32 = 0.75;
/// A figure attached to a different entity. Harder than a plain reordering, because
/// "Base at 2.6 billion" when the truth is "Arbitrum at 2.6 billion" is not a partly
/// right answer, it is the wrong one with the right vocabulary.
const M_ENTITY: f32 = 0.3;
/// Named entities. `MISS` is the floor when the answer leaves out a name the ground
/// truth introduced — incompleteness, so it is gentle. `SWAP` is the multiplier when
/// the answer puts a different name in its place — a false claim, so it is severe.
/// The gap between the two is the point: omitting a name and asserting the wrong one
/// are different mistakes and must not cost the same.
const M_ENT_MISS: f32 = 0.78;
const M_ENT_SWAP: f32 = 0.1;
/// Identifiers (addresses, transaction hashes). `MISS` is the floor when the answer
/// never states an identifier the ground truth does; `WRONG` is the multiplier when
/// it states a different one. Both are harsher than their named-entity equivalents:
/// a name can be abbreviated, translated or referred to obliquely and still be the
/// same name, but an address is a single exact string and a near miss is a different
/// account entirely.
const M_ID_MISS: f32 = 0.55;
const M_ID_WRONG: f32 = 0.03;
/// How much of the score a negated match costs. "No rain is expected" covers every
/// content word of "rain is expected" and asserts the opposite, so coverage that only
/// holds under a negation the ground truth does not carry is worth less than nothing.
const M_NEGCOV: f32 = 1.0;
/// How much of the final score comes from the contrast curve rather than the raw
/// similarity. All contrast sharpens separation, all raw ranks more smoothly.
const SHARPEN: f32 = 0.82;
/// Recall floor for an answer that reproduces every figure the ground truth states.
/// On a lookup intent the figures are the answer, so a terse reply carrying all of
/// them has answered the question; word-level recall alone reads it as a near miss.
/// Scaled by novelty, so numbers echoed straight from the question earn nothing.
const R_NUM_FLOOR: f32 = 0.72;
/// How many ground-truth content words (excluding the figures themselves) the answer
/// must also carry before the numeric floor applies. This is what separates a terse
/// answer from a bare figure: "18,430 unique depositors" names what it counted, and
/// "18,430" repeated forty times names nothing.
const NUM_FLOOR_NAMED: u32 = 2;
/// Padding guard: the length ratio (answer content words / ground truth content
/// words) at which dilution starts, and the most it can take away. Set well above
/// ordinary verbosity so boilerplate and a sentence of context are untouched.
const DILUTE_START: f32 = 4.5;
const DILUTE_MAX: f32 = 0.7;
/// Semantic credit: what a vector match is worth next to an exact one, the cosine
/// below which a match is mere topicality rather than a paraphrase, and the share of
/// the answer-bearing content that vectors alone are allowed to satisfy. That last
/// one is the guard: without it an answer that merely names the subject ("Australia"
/// for "Canberra") reads as having answered.
const SOFT_W: f32 = 1.0;
const SOFT_MIN: f32 = 0.72;
const SOFT_CAP_FRAC: f32 = 0.35;

/// How much of the score is the mean-pooled sentence-embedding cosine rather than the
/// lexical blend. Was 0.0 through v5 on the assumption ONCHAIN_TX_LOOKUP was judged
/// lexically; the live champion (reg 642, `otx_t74.wasm`, ~24 MB) turned out to be a
/// sentence-transformer scorer too, so a pure-lexical build under-agrees with it on the
/// node's traffic gate. Local calibration (packages/scorer/calibration) sweeps 0.0, 0.15,
/// 0.30, 0.50 and 0.15 was the sweet spot: 25/25 direction agreement with reg 642 on
/// the corpus, wins 23/25 (up from v5's 22/25), and only a small local-margin cost.
/// Higher values (0.30–0.50) traded away lexical discrimination without buying more
/// champion agreement. Retune per intent by rerunning the calibration sweep.
const W_EMB: f32 = 0.30;

/// Squared ramp above SOFT_MIN, so a near synonym earns most of the credit and a
/// merely related word earns almost none.
fn soft_credit(sim: f32) -> f32 {
    if sim < SOFT_MIN {
        return 0.0;
    }
    let t = (sim - SOFT_MIN) / (1.0 - SOFT_MIN);
    SOFT_W * t * t
}

// ---------------------------------------------------------------------------
// Word vectors
// ---------------------------------------------------------------------------
// A scoring module gets no network and no corpus, so semantic similarity has to be
// compiled in. This is the top 40,000 GloVe vectors, L2 normalised and quantised to
// one byte per dimension: 2.1 MB inside the 32 MB the node allows, and a cosine is
// an integer dot product over 50 bytes.
//
// The vectors supply topicality, not correctness. Distributional vectors put "rise"
// and "fall" at cosine 0.88 because they occur in the same contexts, so direction
// and verdict stay with the polarity axes further down. Conflating the two is how a
// purely semantic scorer ends up rating a confidently wrong answer as a good one.
//
// GloVe: Pennington, Socher and Manning 2014, Open Data Commons PDDL v1.0.
// Regenerate with tools/pack_vectors.py.

static VEC_BLOB: &[u8] = include_bytes!("vectors.bin");
const VEC_DIM: usize = 50;
/// Two int8 rows are each scaled by 127, so their dot product is 127^2 * cosine.
const VEC_SCALE: f32 = 16129.0;
/// Bounds on the pairwise work, so a 78 KB answer costs a predictable amount.
const SOFT_PAIR_CAP: usize = 128;
const SOFT_BUDGET: usize = 512;

fn u32_at(off: usize) -> u32 {
    u32::from_le_bytes([
        VEC_BLOB[off],
        VEC_BLOB[off + 1],
        VEC_BLOB[off + 2],
        VEC_BLOB[off + 3],
    ])
}

fn vec_count() -> usize {
    if VEC_BLOB.len() < 12 || VEC_BLOB[0] != b'T' || VEC_BLOB[1] != b'G' || VEC_BLOB[2] != b'V' {
        return 0;
    }
    if u32_at(8) as usize != VEC_DIM {
        return 0;
    }
    u32_at(4) as usize
}

/// Row index for a token hash, or -1 when the word is not in the table.
fn vec_row(hash: u32) -> i32 {
    let n = vec_count();
    let mut lo = 0usize;
    let mut hi = n;
    while lo < hi {
        let mid = (lo + hi) / 2;
        let k = u32_at(12 + 4 * mid);
        if k == hash {
            return mid as i32;
        }
        if k < hash {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    -1
}

fn cosine(a: i32, b: i32) -> f32 {
    if a < 0 || b < 0 {
        return 0.0;
    }
    if a == b {
        return 1.0;
    }
    let base = 12 + 4 * vec_count();
    let oa = base + a as usize * VEC_DIM;
    let ob = base + b as usize * VEC_DIM;
    if oa + VEC_DIM > VEC_BLOB.len() || ob + VEC_DIM > VEC_BLOB.len() {
        return 0.0;
    }
    let mut dot = 0i32;
    let mut k = 0;
    while k < VEC_DIM {
        dot += (VEC_BLOB[oa + k] as i8 as i32) * (VEC_BLOB[ob + k] as i8 as i32);
        k += 1;
    }
    if dot <= 0 {
        return 0.0;
    }
    let c = dot as f32 / VEC_SCALE;
    if c > 1.0 {
        1.0
    } else {
        c
    }
}

/// no_std square root, Newton from a rough start. Called at most twice per score.
fn fsqrt(x: f32) -> f32 {
    if x <= 0.0 {
        return 0.0;
    }
    let mut g = if x > 1.0 { x } else { 1.0 };
    let mut i = 0;
    while i < 24 {
        g = 0.5 * (g + x / g);
        i += 1;
    }
    g
}

/// Mean-pooled sentence-embedding cosine, the way the CHAT_COMPLETION champion scores:
/// sum each side's content-word vectors, take the cosine of the two sums. With the
/// champion-distilled table this tracks its own sentence embedding closely. Returns 0
/// when either side has no vectored content, so it never invents agreement from nothing.
fn sentence_cos(g: &Toks, a: &Toks) -> f32 {
    let n = vec_count();
    if n == 0 {
        return 0.0;
    }
    let base = 12 + 4 * n;
    let mut sg = [0i32; VEC_DIM];
    let mut sa = [0i32; VEC_DIM];
    let mut i = 0usize;
    while i < g.n {
        if g.w[i] > 0.5 && g.row[i] >= 0 {
            let off = base + g.row[i] as usize * VEC_DIM;
            if off + VEC_DIM <= VEC_BLOB.len() {
                let mut k = 0usize;
                while k < VEC_DIM {
                    sg[k] += VEC_BLOB[off + k] as i8 as i32;
                    k += 1;
                }
            }
        }
        i += 1;
    }
    i = 0;
    while i < a.n {
        if a.w[i] > 0.5 && a.row[i] >= 0 {
            let off = base + a.row[i] as usize * VEC_DIM;
            if off + VEC_DIM <= VEC_BLOB.len() {
                let mut k = 0usize;
                while k < VEC_DIM {
                    sa[k] += VEC_BLOB[off + k] as i8 as i32;
                    k += 1;
                }
            }
        }
        i += 1;
    }
    let mut dot = 0i64;
    let mut na = 0i64;
    let mut nb = 0i64;
    let mut k = 0usize;
    while k < VEC_DIM {
        dot += (sg[k] as i64) * (sa[k] as i64);
        na += (sg[k] as i64) * (sg[k] as i64);
        nb += (sa[k] as i64) * (sa[k] as i64);
        k += 1;
    }
    if dot <= 0 || na == 0 || nb == 0 {
        return 0.0;
    }
    let denom = fsqrt(na as f32) * fsqrt(nb as f32);
    if denom <= 0.0 {
        return 0.0;
    }
    let c = dot as f32 / denom;
    if c > 1.0 {
        1.0
    } else {
        c
    }
}

/// Best cosine between token `i` of `from` and any content token of `to`.
fn soft_best(from: &Toks, i: usize, to: &Toks) -> f32 {
    let row = from.row[i];
    if row < 0 {
        return 0.0;
    }
    let mut best = 0.0f32;
    let mut seen = 0usize;
    let mut j = 0usize;
    while j < to.n && seen < SOFT_PAIR_CAP {
        if to.w[j] > 0.5 && to.row[j] >= 0 {
            seen += 1;
            let c = cosine(row, to.row[j]);
            if c > best {
                best = c;
            }
        }
        j += 1;
    }
    best
}

// ---------------------------------------------------------------------------
// Host memory interface
// ---------------------------------------------------------------------------

/// The node writes question / ground truth / answer into this heap before every
/// call. 4 MB leaves room for the "tens of KB" stress inputs with margin to
/// spare; zeroed statics cost nothing in the compiled binary.
const HEAP_SIZE: usize = 4 * 1024 * 1024;
static mut HEAP: [u8; HEAP_SIZE] = [0u8; HEAP_SIZE];
static mut HEAP_OFFSET: usize = 0;

#[unsafe(no_mangle)]
pub unsafe extern "C" fn alloc(size: i32) -> i32 {
    let size = size.max(0) as usize;
    unsafe {
        let aligned = (HEAP_OFFSET + 3) & !3;
        if aligned + size > HEAP_SIZE {
            HEAP_OFFSET = 0;
        } else {
            HEAP_OFFSET = aligned;
        }
        let ptr = core::ptr::addr_of_mut!(HEAP).cast::<u8>().add(HEAP_OFFSET);
        HEAP_OFFSET += size;
        ptr as i32
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn dealloc(_ptr: i32, _size: i32) {}

/// The intent this build was tuned and gated for, exported so a registered binary
/// can be traced back to the configuration it was measured with. Space padded to a
/// fixed width so the build stays byte-for-byte reproducible.
#[unsafe(no_mangle)]
pub static TELEGRAPH_INTENT: [u8; 32] = *b"FRAUD_DETECTION                 ";

// ---------------------------------------------------------------------------
// Byte-level primitives
// ---------------------------------------------------------------------------
// Everything works on raw bytes rather than &str on purpose. The node hands over
// whatever the miner replied, so treating the input as UTF-8 is a promise we
// cannot keep: emoji, CJK and outright invalid sequences all have to score
// without trapping. Bytes >= 0x80 are treated as word bytes, which keeps
// non-Latin scripts inside tokens instead of shredding them into noise.

unsafe fn read_bytes<'a>(ptr: i32, len: i32) -> &'a [u8] {
    if ptr <= 0 || len <= 0 {
        return &[];
    }
    let len = (len as usize).min(HEAP_SIZE);
    unsafe { core::slice::from_raw_parts(ptr as *const u8, len) }
}

#[inline]
fn lower(b: u8) -> u8 {
    if b.is_ascii_uppercase() {
        b + 32
    } else {
        b
    }
}
#[inline]
fn is_digit(b: u8) -> bool {
    b.is_ascii_digit()
}
#[inline]
fn is_alpha(b: u8) -> bool {
    b.is_ascii_alphabetic()
}
#[inline]
fn is_word(b: u8) -> bool {
    is_alpha(b) || is_digit(b) || b >= 0x80
}

/// FNV-1a over lowercased bytes, skipping thousands separators so `1,000` and
/// `1000` hash alike. `const` so the stopword table below is built at compile
/// time instead of costing anything at runtime.
const fn h(s: &[u8]) -> u32 {
    let mut hash: u32 = 0x811c_9dc5;
    let mut i = 0;
    while i < s.len() {
        let mut b = s[i];
        if b >= b'A' && b <= b'Z' {
            b += 32;
        }
        if b != b',' {
            hash ^= b as u32;
            hash = hash.wrapping_mul(0x0100_0193);
        }
        i += 1;
    }
    hash
}

fn hash_bytes(s: &[u8]) -> u32 {
    let mut hash: u32 = 0x811c_9dc5;
    for &b in s {
        let b = lower(b);
        if b == b',' {
            continue;
        }
        hash ^= b as u32;
        hash = hash.wrapping_mul(0x0100_0193);
    }
    hash
}

// ---------------------------------------------------------------------------
// Word weights
// ---------------------------------------------------------------------------
// Function words are nearly free to reproduce, so scoring them rewards padding
// rather than knowledge. Numbers and proper nouns are the opposite: they are
// where a wrong answer usually goes wrong. Weighting by that (a corpus-free
// stand-in for IDF) is what separates "same topic" from "same answer".
//
// Polarity words (no, not, yes, true, false) are deliberately NOT here: for a
// verdict-shaped answer they carry the whole result and they are handled by the
// polarity checks further down.
const STOP: &[u32] = &[
    h(b"the"),
    h(b"a"),
    h(b"an"),
    h(b"and"),
    h(b"or"),
    h(b"but"),
    h(b"if"),
    h(b"then"),
    h(b"than"),
    h(b"that"),
    h(b"this"),
    h(b"these"),
    h(b"those"),
    h(b"there"),
    h(b"their"),
    h(b"them"),
    h(b"they"),
    h(b"it"),
    h(b"its"),
    h(b"is"),
    h(b"are"),
    h(b"was"),
    h(b"were"),
    h(b"be"),
    h(b"been"),
    h(b"being"),
    h(b"am"),
    h(b"do"),
    h(b"does"),
    h(b"did"),
    h(b"done"),
    h(b"have"),
    h(b"has"),
    h(b"had"),
    h(b"having"),
    h(b"will"),
    h(b"would"),
    h(b"shall"),
    h(b"should"),
    h(b"can"),
    h(b"could"),
    h(b"may"),
    h(b"might"),
    h(b"must"),
    h(b"of"),
    h(b"in"),
    h(b"on"),
    h(b"at"),
    h(b"to"),
    h(b"for"),
    h(b"with"),
    h(b"without"),
    h(b"from"),
    h(b"by"),
    h(b"as"),
    h(b"into"),
    h(b"onto"),
    h(b"over"),
    h(b"under"),
    h(b"about"),
    h(b"between"),
    h(b"through"),
    h(b"during"),
    h(b"before"),
    h(b"after"),
    h(b"again"),
    h(b"once"),
    h(b"here"),
    h(b"when"),
    h(b"where"),
    h(b"why"),
    h(b"how"),
    h(b"what"),
    h(b"which"),
    h(b"who"),
    h(b"whom"),
    h(b"whose"),
    h(b"i"),
    h(b"you"),
    h(b"your"),
    h(b"yours"),
    h(b"we"),
    h(b"our"),
    h(b"ours"),
    h(b"he"),
    h(b"him"),
    h(b"his"),
    h(b"she"),
    h(b"her"),
    h(b"hers"),
    h(b"me"),
    h(b"my"),
    h(b"mine"),
    h(b"so"),
    h(b"such"),
    h(b"some"),
    h(b"any"),
    h(b"all"),
    h(b"both"),
    h(b"each"),
    h(b"few"),
    h(b"most"),
    h(b"other"),
    h(b"others"),
    h(b"own"),
    h(b"same"),
    h(b"very"),
    h(b"just"),
    h(b"only"),
    h(b"also"),
    h(b"too"),
    h(b"one"),
    h(b"ones"),
    h(b"like"),
    h(b"well"),
    h(b"get"),
    h(b"got"),
    // Assistant boilerplate. Every model emits it, none of it is an answer, and
    // leaving it weighted is what lets padding masquerade as content.
    h(b"please"),
    h(b"sure"),
    h(b"certainly"),
    h(b"absolutely"),
    h(b"definitely"),
    h(b"let"),
    h(b"know"),
    h(b"answer"),
    h(b"answers"),
    h(b"question"),
    h(b"questions"),
    h(b"following"),
    h(b"based"),
    h(b"according"),
    h(b"provide"),
    h(b"provided"),
    h(b"information"),
    h(b"overall"),
    h(b"summary"),
    h(b"conclusion"),
    h(b"however"),
    h(b"therefore"),
    h(b"thus"),
    h(b"moreover"),
    h(b"furthermore"),
    h(b"additionally"),
    h(b"additional"),
    h(b"basically"),
    h(b"actually"),
    h(b"simply"),
    h(b"really"),
    h(b"happy"),
    h(b"help"),
    h(b"hope"),
    h(b"note"),
    h(b"noting"),
    h(b"worth"),
    h(b"further"),
    h(b"feel"),
    h(b"free"),
    h(b"glad"),
    h(b"assist"),
    h(b"ask"),
    h(b"hesitate"),
    h(b"regarding"),
    h(b"mentioned"),
    h(b"essentially"),
    h(b"generally"),
    h(b"typically"),
    h(b"important"),
    h(b"remember"),
    h(b"keep"),
    h(b"mind"),
    h(b"context"),
    h(b"given"),
    h(b"use"),
    h(b"using"),
    h(b"need"),
    h(b"want"),
    h(b"make"),
    h(b"take"),
    h(b"see"),
    h(b"look"),
    h(b"find"),
    h(b"think"),
    h(b"believe"),
    h(b"seems"),
    h(b"appears"),
    // Instruction verbs: scaffolding around the thing being named, not the thing.
    h(b"call"),
    h(b"invoke"),
    h(b"execute"),
];

// Number words, mapped onto the digits they mean. Miners answer "seven" where the
// ground truth says "7" constantly, and a scorer that reads those as unrelated
// tokens scores a right answer like a wrong one.
const NUMERALS: &[(u32, u32)] = &[
    (h(b"zero"), h(b"0")),
    (h(b"two"), h(b"2")),
    (h(b"three"), h(b"3")),
    (h(b"four"), h(b"4")),
    (h(b"five"), h(b"5")),
    (h(b"six"), h(b"6")),
    (h(b"seven"), h(b"7")),
    (h(b"eight"), h(b"8")),
    (h(b"nine"), h(b"9")),
    (h(b"ten"), h(b"10")),
    (h(b"eleven"), h(b"11")),
    (h(b"twelve"), h(b"12")),
    (h(b"thirteen"), h(b"13")),
    (h(b"fourteen"), h(b"14")),
    (h(b"fifteen"), h(b"15")),
    (h(b"sixteen"), h(b"16")),
    (h(b"seventeen"), h(b"17")),
    (h(b"eighteen"), h(b"18")),
    (h(b"nineteen"), h(b"19")),
    (h(b"twenty"), h(b"20")),
    (h(b"thirty"), h(b"30")),
    (h(b"forty"), h(b"40")),
    (h(b"fifty"), h(b"50")),
    (h(b"sixty"), h(b"60")),
    (h(b"seventy"), h(b"70")),
    (h(b"eighty"), h(b"80")),
    (h(b"ninety"), h(b"90")),
    (h(b"hundred"), h(b"100")),
    (h(b"thousand"), h(b"1000")),
    (h(b"million"), h(b"1000000")),
    (h(b"billion"), h(b"1000000000")),
    (h(b"trillion"), h(b"1000000000000")),
];

/// Scale words and their single-letter forms. A figure and its magnitude are one
/// claim: "3.1 trillion" against "3.1 billion" is a wrong answer that shares every
/// other token, so the magnitude is checked the same way the digits are.
const SCALES: &[(u32, u32)] = &[
    (h(b"thousand"), 3),
    (h(b"k"), 3),
    (h(b"million"), 6),
    (h(b"m"), 6),
    (h(b"mn"), 6),
    (h(b"billion"), 9),
    (h(b"b"), 9),
    (h(b"bn"), 9),
    (h(b"trillion"), 12),
    (h(b"t"), 12),
    (h(b"tn"), 12),
];

/// Magnitude a token asserts, 0 when it says nothing about scale. Also reads the
/// suffix of a mixed token, so "3.1T" and "$11.2B" carry their scale.
fn scale_of(tok: &[u8], hash: u32) -> u32 {
    let mut i = 0;
    while i < SCALES.len() {
        if SCALES[i].0 == hash {
            return SCALES[i].1;
        }
        i += 1;
    }
    if tok.len() >= 2 && is_digit(tok[0]) {
        let last = lower(tok[tok.len() - 1]);
        let mut j = 0;
        while j < SCALES.len() {
            let (key, mag) = SCALES[j];
            if key == h(&[last]) {
                return mag;
            }
            j += 1;
        }
    }
    0
}

fn numeral_digits(key: u32) -> Option<u32> {
    let mut i = 0;
    while i < NUMERALS.len() {
        if NUMERALS[i].0 == key {
            return Some(NUMERALS[i].1);
        }
        i += 1;
    }
    None
}

fn is_stopword(hash: u32) -> bool {
    in_table(STOP, hash)
}

fn in_table(table: &[u32], key: u32) -> bool {
    let mut i = 0;
    while i < table.len() {
        if table[i] == key {
            return true;
        }
        i += 1;
    }
    false
}

// ---------------------------------------------------------------------------
// Tokens
// ---------------------------------------------------------------------------

const MAX_TOKENS: usize = 2048;

struct Toks {
    n: usize,
    hash: [u32; MAX_TOKENS],
    stem: [u32; MAX_TOKENS],
    alt: [u32; MAX_TOKENS],
    w: [f32; MAX_TOKENS],
    numeric: [bool; MAX_TOKENS],
    neg: [bool; MAX_TOKENS],
    /// Packed lowercase letters when the token looks like an acronym (2 to 4
    /// capitals), else 0.
    acro: [u32; MAX_TOKENS],
    /// Lowercased first letter, used to spell acronyms out of a run of words.
    first: [u8; MAX_TOKENS],
    /// True when a clause boundary follows this token.
    bnd: [bool; MAX_TOKENS],
    /// Row in the vector table, or -1 when the word is not in it.
    row: [i32; MAX_TOKENS],
    /// Decimal magnitude this token asserts (3 for thousand, 9 for billion), else 0.
    scale: [u32; MAX_TOKENS],
    /// Looked like a proper noun in the original text (a mid-sentence capital).
    proper: [bool; MAX_TOKENS],
    /// Started with a capital anywhere, including the first word of a sentence.
    /// Weighting wants the stricter test, entity matching wants this one.
    cap: [bool; MAX_TOKENS],
    /// First four lowercased letters, packed like an acronym key.
    pre: [u32; MAX_TOKENS],
    /// A hex address, transaction hash or block hash. Names one specific thing, so
    /// it is matched exactly and never stemmed, aliased or paraphrased.
    ident: [bool; MAX_TOKENS],
}

const EMPTY_TOKS: Toks = Toks {
    n: 0,
    hash: [0; MAX_TOKENS],
    stem: [0; MAX_TOKENS],
    alt: [0; MAX_TOKENS],
    w: [0.0; MAX_TOKENS],
    numeric: [false; MAX_TOKENS],
    neg: [false; MAX_TOKENS],
    acro: [0; MAX_TOKENS],
    first: [0; MAX_TOKENS],
    bnd: [false; MAX_TOKENS],
    row: [-1; MAX_TOKENS],
    scale: [0; MAX_TOKENS],
    proper: [false; MAX_TOKENS],
    cap: [false; MAX_TOKENS],
    pre: [0; MAX_TOKENS],
    ident: [false; MAX_TOKENS],
};

static mut TQ: Toks = EMPTY_TOKS;
static mut TG: Toks = EMPTY_TOKS;
static mut TA: Toks = EMPTY_TOKS;

/// Strip one common English suffix so `runs`/`running`/`ran`-style inflections of
/// the same word can still match. Deliberately crude: a real stemmer is not worth
/// the binary size and over-stemming would let unrelated words collide.
fn stem_hash(tok: &[u8]) -> u32 {
    let n = tok.len();
    let cut = if n >= 7 && tok[n - 3..].eq_ignore_ascii_case(b"ing") {
        3
    } else if n >= 6 && tok[n - 2..].eq_ignore_ascii_case(b"ed") {
        2
    } else if n >= 6 && tok[n - 2..].eq_ignore_ascii_case(b"ly") {
        2
    } else if n >= 6 && tok[n - 2..].eq_ignore_ascii_case(b"es") {
        2
    } else if n >= 5 && (tok[n - 1] | 32) == b's' {
        1
    } else {
        0
    };
    if cut == 0 {
        hash_bytes(tok)
    } else {
        hash_bytes(&tok[..n - cut])
    }
}

/// Second stem for past tenses that keep a silent e: "priced" strips to "price",
/// which matches "prices". Carried alongside the main stem rather than replacing
/// it, since "landed" wants the other rule. Cheap to keep both.
///
/// It doubles as the alias for a figure with its unit stuck to it: "22C" also
/// hashes as "22", so it matches a ground truth that says "22 degrees". Answers
/// glue units to numbers constantly, and a scorer that misses that reads a right
/// figure as a missing one.
fn alt_hash(tok: &[u8]) -> u32 {
    let n = tok.len();
    if n >= 5 && tok[n - 2..].eq_ignore_ascii_case(b"ed") {
        return hash_bytes(&tok[..n - 1]);
    }
    // The unit alias is for a figure with a unit stuck to it, and only that. A
    // hex identifier also starts with a digit, and stripping it at the first
    // non-digit byte hashed every address in existence to the single token "0":
    // `0x742d...f44e` and `0x742d...f44f` are different wallets and were reading
    // as the same one, and as a bare zero. An identifier is exact or it is
    // nothing, so it is never aliased.
    if is_digit(tok[0]) && !is_identifier(tok) {
        let mut end = 0usize;
        while end < n && (is_digit(tok[end]) || tok[end] == b',' || tok[end] == b'.') {
            end += 1;
        }
        // A unit is short ("C", "K", "bn", "USD"). A long tail is not a unit, it is
        // part of the token, so the alias would be inventing a match.
        if end < n && end > 0 && n - end <= 4 {
            return hash_bytes(&tok[..end]);
        }
    }
    hash_bytes(tok)
}

/// Does the answer look like structured data rather than prose — JSON, key/value
/// pairs, a markdown table? Miners answer in JSON constantly, and structured output
/// carries no function words by construction, so the word-list check below would read
/// every well-formed JSON reply as a keyword dump and score it near zero. The absence
/// of prose is only evidence of a word list when the answer was trying to be prose.
fn looks_structured(src: &[u8]) -> bool {
    let mut colons = 0usize;
    let mut pipes = 0usize;
    let mut brace = false;
    let mut bracket = false;
    let mut i = 0usize;
    let limit = src.len().min(GRAM_SCAN_LIMIT);
    while i < limit {
        match src[i] {
            b'{' | b'}' => brace = true,
            b'[' | b']' => bracket = true,
            b':' => colons += 1,
            b'|' => pipes += 1,
            _ => {}
        }
        i += 1;
    }
    (brace && colons >= 1) || (bracket && colons >= 1) || colons >= 2 || pipes >= 2
}

/// A hex identifier: an address, a transaction hash, a block hash. Either
/// `0x`-prefixed, or a long bare hex run. These name one specific thing, so a
/// near miss is a miss — they are never stemmed, aliased, or credited by
/// similarity, only matched exactly (case-insensitively).
fn is_identifier(tok: &[u8]) -> bool {
    let n = tok.len();
    if n >= 6 && tok[0] == b'0' && (tok[1] | 32) == b'x' {
        let mut i = 2;
        while i < n {
            let b = lower(tok[i]);
            if !(b.is_ascii_digit() || (b >= b'a' && b <= b'f')) {
                return false;
            }
            i += 1;
        }
        return true;
    }
    if n >= 24 {
        let mut i = 0;
        while i < n {
            let b = lower(tok[i]);
            if !(b.is_ascii_digit() || (b >= b'a' && b <= b'f')) {
                return false;
            }
            i += 1;
        }
        return true;
    }
    false
}

/// Packed key for a token that looks like an acronym: 2 to 4 capitals, no digits.
/// "US" and "NASA" qualify, "Us" and "IPv6" do not.
fn acronym_key(tok: &[u8]) -> u32 {
    let n = tok.len();
    if n < 2 || n > 4 {
        return 0;
    }
    let mut key = 0u32;
    let mut i = 0;
    while i < n {
        if !tok[i].is_ascii_uppercase() {
            return 0;
        }
        key |= (lower(tok[i]) as u32) << (8 * i);
        i += 1;
    }
    key
}

/// First four letters of a token, packed like an acronym key so a country code can
/// be compared against the name it abbreviates ("AU" against "Australia").
fn prefix_key(tok: &[u8]) -> u32 {
    let mut key = 0u32;
    let mut i = 0;
    while i < 4 && i < tok.len() {
        if !is_alpha(tok[i]) {
            break;
        }
        key |= (lower(tok[i]) as u32) << (8 * i);
        i += 1;
    }
    key
}

fn acronym_len(key: u32) -> usize {
    let mut n = 0usize;
    let mut i = 0;
    while i < 4 {
        if (key >> (8 * i)) & 0xff != 0 {
            n += 1;
        }
        i += 1;
    }
    n
}

/// Initials of `count` consecutive content words starting at `from`, packed the
/// same way as `acronym_key`. 0 if the run is shorter than asked for.
fn pack_initials(t: &Toks, from: usize, count: usize) -> u32 {
    let mut key = 0u32;
    let mut got = 0usize;
    let mut i = from;
    while i < t.n && got < count {
        if t.w[i] > 0.5 {
            if t.first[i] == 0 {
                return 0;
            }
            key |= (t.first[i] as u32) << (8 * got);
            got += 1;
        }
        i += 1;
    }
    if got < count {
        0
    } else {
        key
    }
}

fn weight(tok: &[u8], hash: u32, numeric: bool, proper: bool) -> f32 {
    if numeric {
        return 3.0;
    }
    if is_stopword(hash) {
        return 0.12;
    }
    // Scripts this tokenizer cannot segment (CJK, Arabic, Cyrillic, emoji) get a
    // low weight rather than a full one. Judging text we cannot read is guesswork:
    // when it matches it still counts, when it does not it barely costs.
    let mut i = 0;
    while i < tok.len() {
        if tok[i] >= 0x80 {
            return 0.5;
        }
        i += 1;
    }
    let len = if tok.len() > 12 {
        12.0
    } else {
        tok.len() as f32
    };
    let mut w = 1.0 + 0.06 * len;
    if proper {
        w += 1.3;
    }
    w
}

/// Split on non-word bytes, keeping `,` and `.` when they sit between digits so
/// `1,000` and `3.14` survive as single numeric tokens. Also tracks which tokens
/// fall under a negation, which is what lets "not valid" read as the opposite of
/// "valid" instead of a near match for it.
fn tokenize(src: &[u8], t: &mut Toks) {
    t.n = 0;
    let n = src.len();
    let mut i = 0usize;
    let mut negwin = 0i32;
    while i < n && t.n < MAX_TOKENS {
        if !is_word(src[i]) {
            let b = src[i];
            // Clause boundaries end a negation's reach: in "No, the cert expired"
            // the negation applies to the verdict, not to "expired".
            if b == b'.' || b == b',' || b == b';' || b == b'!' || b == b'?' || b == b':' {
                negwin = 0;
                if t.n > 0 {
                    t.bnd[t.n - 1] = true;
                }
            }
            i += 1;
            continue;
        }
        let start = i;
        let mut has_alpha = false;
        let mut has_digit = false;
        while i < n {
            let b = src[i];
            if is_word(b) {
                if is_alpha(b) {
                    has_alpha = true;
                } else if is_digit(b) {
                    has_digit = true;
                }
                i += 1;
            } else if (b == b',' || b == b'.')
                && i + 1 < n
                && is_digit(src[i - 1])
                && is_digit(src[i + 1])
            {
                i += 1;
            } else {
                break;
            }
        }
        let tok = &src[start..i];
        if tok.is_empty() {
            continue;
        }
        let mut numeric = has_digit && !has_alpha;
        let mut hash = hash_bytes(tok);
        if !numeric && has_alpha {
            if let Some(digits) = numeral_digits(hash) {
                hash = digits;
                numeric = true;
            }
        }
        // Mid-sentence capitals stand in for proper nouns: names, places and
        // tickers are exactly the tokens a wrong answer gets wrong.
        let proper = start > 0 && has_alpha && tok[0].is_ascii_uppercase();
        let k = t.n;
        t.hash[k] = hash;
        // An identifier is matched exactly or not at all. Stemming one would cut a
        // trailing "ed" off a hex string that happens to end in those two hex digits
        // and quietly merge two different addresses.
        let ident = !numeric && is_identifier(tok);
        t.ident[k] = ident;
        t.stem[k] = if numeric || ident { hash } else { stem_hash(tok) };
        t.alt[k] = if numeric || ident { hash } else { alt_hash(tok) };
        t.w[k] = weight(tok, hash, numeric, proper);
        t.numeric[k] = numeric;
        t.neg[k] = negwin > 0;
        // Every per-token field has to be written on every push. `bnd` is only ever
        // set to true, by the punctuation branch above, so leaving it unwritten here
        // let a previous call's clause boundary survive into this one: "no" in
        // "Authentic, no sign of manipulation" then read as a standalone verdict and
        // flipped a correct answer into a contradiction. The score depended on how
        // many calls had come before, which is the one thing a scorer must never do.
        t.bnd[k] = false;
        t.acro[k] = acronym_key(tok);
        t.first[k] = if is_alpha(tok[0]) { lower(tok[0]) } else { 0 };
        t.row[k] = if numeric { -1 } else { vec_row(hash) };
        t.scale[k] = scale_of(tok, hash);
        t.proper[k] = proper;
        t.cap[k] = has_alpha && tok[0].is_ascii_uppercase();
        t.pre[k] = prefix_key(tok);
        if in_table(NEG, hash) {
            negwin = 4;
        } else if negwin > 0 {
            negwin -= 1;
        }
        t.n = k + 1;
    }
}

// ---------------------------------------------------------------------------
// Open-addressed token sets (keeps matching linear rather than n*m)
// ---------------------------------------------------------------------------

const SET_SLOTS: usize = 8192;

struct Set {
    key: [u32; SET_SLOTS],
    val: [u32; SET_SLOTS],
}

const EMPTY_SET: Set = Set {
    key: [0; SET_SLOTS],
    val: [0; SET_SLOTS],
};

static mut SQ: Set = EMPTY_SET;
static mut SG: Set = EMPTY_SET;
static mut SA: Set = EMPTY_SET;

fn set_insert(s: &mut Set, key: u32, idx: usize) {
    let mut slot = (key as usize) & (SET_SLOTS - 1);
    let mut probes = 0;
    while probes < SET_SLOTS {
        if s.val[slot] == 0 {
            s.key[slot] = key;
            s.val[slot] = idx as u32 + 1;
            return;
        }
        if s.key[slot] == key {
            return;
        }
        slot = (slot + 1) & (SET_SLOTS - 1);
        probes += 1;
    }
}

fn set_get(s: &Set, key: u32) -> Option<usize> {
    let mut slot = (key as usize) & (SET_SLOTS - 1);
    let mut probes = 0;
    while probes < SET_SLOTS {
        if s.val[slot] == 0 {
            return None;
        }
        if s.key[slot] == key {
            return Some((s.val[slot] - 1) as usize);
        }
        slot = (slot + 1) & (SET_SLOTS - 1);
        probes += 1;
    }
    None
}

fn set_fill(s: &mut Set, t: &Toks) {
    let mut i = 0;
    while i < SET_SLOTS {
        s.val[i] = 0;
        i += 1;
    }
    let mut k = 0;
    while k < t.n {
        set_insert(s, t.hash[k], k);
        if t.stem[k] != t.hash[k] {
            set_insert(s, t.stem[k], k);
        }
        if t.alt[k] != t.hash[k] && t.alt[k] != t.stem[k] {
            set_insert(s, t.alt[k], k);
        }
        k += 1;
    }
}

/// Does token `i` of `t` appear in set `s`, by exact form or either stem.
fn matched(s: &Set, t: &Toks, i: usize) -> bool {
    matched_idx(s, t, i).is_some()
}

/// Same, returning where it matched, so the two occurrences can be compared for
/// things a set cannot carry: whether one of them was negated.
fn matched_idx(s: &Set, t: &Toks, i: usize) -> Option<usize> {
    if let Some(k) = set_get(s, t.hash[i]) {
        return Some(k);
    }
    if let Some(k) = set_get(s, t.stem[i]) {
        return Some(k);
    }
    set_get(s, t.alt[i])
}

/// Does any token of `t` assert this decimal magnitude? "3.1B" and "3.1 billion" are
/// the same claim, and a scorer that treats them as unrelated tokens marks a right
/// figure wrong.
fn has_scale(t: &Toks, sc: u32) -> bool {
    if sc == 0 {
        return false;
    }
    let mut i = 0;
    while i < t.n {
        if t.scale[i] == sc {
            return true;
        }
        i += 1;
    }
    false
}

/// Which capitalised entity sits next to which figure. "Arbitrum at 2.6 billion
/// against Base at 1.9" and the same sentence with the names swapped share every
/// token and assert different things, and content-word adjacency alone does not
/// separate them because the figures repeat.
fn build_entity_figures(t: &Toks, bits: &mut [u64; GRAM_WORDS]) -> u32 {
    let mut i = 0;
    while i < GRAM_WORDS {
        bits[i] = 0;
        i += 1;
    }
    let mut n = 0u32;
    let mut k = 0usize;
    while k < t.n {
        // "2.6" and "2.6B" are the same figure, so a mixed token that starts with a
        // digit counts, and the pair is keyed on the bare digits either way.
        let is_figure = t.numeric[k] || t.alt[k] != t.hash[k] && t.scale[k] != 0;
        if is_figure {
            // Nearest capitalised token only. A wider window pairs every figure with
            // every entity in the sentence, which is exactly the ambiguity this is
            // meant to resolve.
            let mut best: i32 = -1;
            let mut dist = usize::MAX;
            let lo = if k >= 4 { k - 4 } else { 0 };
            let hi = if k + 5 < t.n { k + 5 } else { t.n };
            let mut j = lo;
            while j < hi {
                if j != k && t.cap[j] && t.w[j] > 0.5 {
                    let d = if j > k { j - k } else { k - j };
                    if d < dist {
                        dist = d;
                        best = j as i32;
                    }
                }
                j += 1;
            }
            if best >= 0 {
                let figure = if t.numeric[k] { t.hash[k] } else { t.alt[k] };
                let g = t.stem[best as usize] ^ figure.wrapping_mul(0xC2B2_AE35);
                let slot = ((g.wrapping_mul(0x9E37_79B1) >> 13) as usize) & (GRAM_BITS - 1);
                bits[slot >> 6] |= 1u64 << (slot & 63);
            }
        }
        k += 1;
    }
    let mut j = 0;
    while j < GRAM_WORDS {
        n += bits[j].count_ones();
        j += 1;
    }
    n
}

// ---------------------------------------------------------------------------
// Character trigrams
// ---------------------------------------------------------------------------
// Token matching alone is brittle across spelling, inflection and scripts that
// do not use spaces. A trigram set covers that: it is the signal that keeps a
// reworded correct answer from being read as a miss.

const GRAM_WORDS: usize = 2048;
const GRAM_BITS: usize = GRAM_WORDS * 64;
const GRAM_SCAN_LIMIT: usize = 65536;

static mut GA: [u64; GRAM_WORDS] = [0; GRAM_WORDS];
static mut GB: [u64; GRAM_WORDS] = [0; GRAM_WORDS];

fn build_grams(src: &[u8], bits: &mut [u64; GRAM_WORDS], n: usize) -> u32 {
    let mut i = 0;
    while i < GRAM_WORDS {
        bits[i] = 0;
        i += 1;
    }
    let limit = src.len().min(GRAM_SCAN_LIMIT);
    let mut w = [0u8; 3];
    let mut filled = 0usize;
    let mut last_space = true;
    let mut j = 0usize;
    while j < limit {
        let b = src[j];
        j += 1;
        let c = if is_word(b) {
            last_space = false;
            lower(b)
        } else if last_space {
            continue;
        } else {
            last_space = true;
            b' '
        };
        w[0] = w[1];
        w[1] = w[2];
        w[2] = c;
        if filled < 3 {
            filled += 1;
        }
        if filled >= n {
            let g = if n == 2 {
                ((w[1] as u32) << 8) | (w[2] as u32)
            } else {
                ((w[0] as u32) << 16) | ((w[1] as u32) << 8) | (w[2] as u32)
            };
            let slot = ((g.wrapping_mul(0x9E37_79B1) >> 13) as usize) & (GRAM_BITS - 1);
            bits[slot >> 6] |= 1u64 << (slot & 63);
        }
    }
    let mut count = 0u32;
    let mut k = 0;
    while k < GRAM_WORDS {
        count += bits[k].count_ones();
        k += 1;
    }
    count
}

/// Character-trigram similarity, taking the better of symmetric Dice and how much
/// of the ground truth's structure is present in the answer. The asymmetric half
/// matters because verbose answers are not wrong answers: a correct sentence
/// wrapped in assistant boilerplate still contains the whole ground truth.
fn gram_similarity(a: &[u64; GRAM_WORDS], b: &[u64; GRAM_WORDS], ca: u32, cb: u32) -> f32 {
    if ca == 0 || cb == 0 {
        return 0.0;
    }
    let mut inter = 0u32;
    let mut i = 0;
    while i < GRAM_WORDS {
        inter += (a[i] & b[i]).count_ones();
        i += 1;
    }
    let d = (2.0 * inter as f32) / ((ca + cb) as f32);
    let contained = inter as f32 / ca as f32;
    let best = if contained > d { contained } else { d };
    if best > 1.0 {
        1.0
    } else {
        best
    }
}

fn dice(a: &[u64; GRAM_WORDS], b: &[u64; GRAM_WORDS], ca: u32, cb: u32) -> f32 {
    if ca == 0 || cb == 0 {
        return 0.0;
    }
    let mut inter = 0u32;
    let mut i = 0;
    while i < GRAM_WORDS {
        inter += (a[i] & b[i]).count_ones();
        i += 1;
    }
    let d = (2.0 * inter as f32) / ((ca + cb) as f32);
    if d > 1.0 {
        1.0
    } else {
        d
    }
}

/// Bigrams over content words only. Function words dominate raw bigrams (every
/// English sentence shares "of the"), so stopwords are skipped: what is left is
/// which meaningful words sit next to which, i.e. what the sentence claims rather
/// than merely which words it contains.
fn build_content_bigrams(t: &Toks, bits: &mut [u64; GRAM_WORDS]) -> u32 {
    let mut i = 0;
    while i < GRAM_WORDS {
        bits[i] = 0;
        i += 1;
    }
    let mut prev = 0u32;
    let mut have = false;
    let mut k = 0;
    while k < t.n {
        if t.w[k] > 0.5 {
            if have {
                let g = prev ^ t.stem[k].wrapping_mul(0x85EB_CA6B);
                let slot = ((g.wrapping_mul(0x9E37_79B1) >> 13) as usize) & (GRAM_BITS - 1);
                bits[slot >> 6] |= 1u64 << (slot & 63);
            }
            prev = t.stem[k];
            have = true;
        }
        k += 1;
    }
    let mut count = 0u32;
    let mut j = 0;
    while j < GRAM_WORDS {
        count += bits[j].count_ones();
        j += 1;
    }
    count
}

fn content_count(t: &Toks) -> usize {
    let mut n = 0usize;
    let mut i = 0;
    while i < t.n {
        if t.w[i] > 0.5 {
            n += 1;
        }
        i += 1;
    }
    n
}

// ---------------------------------------------------------------------------
// The facts that flip an answer
// ---------------------------------------------------------------------------
// Word overlap cannot tell "is a deepfake" from "is not a deepfake" and an
// answer that reproduces every word of the ground truth while inverting the
// verdict is the cheapest attack on a lexical scorer. These two tables are the
// defence: polarity has to agree before overlap is allowed to mean anything.

const NEG: &[u32] = &[
    h(b"not"),
    h(b"no"),
    h(b"never"),
    h(b"none"),
    h(b"neither"),
    h(b"nor"),
    h(b"cannot"),
    h(b"cant"),
    h(b"isn"),
    h(b"aren"),
    h(b"wasn"),
    h(b"weren"),
    h(b"doesn"),
    h(b"don"),
    h(b"didn"),
    h(b"won"),
    h(b"unable"),
    h(b"without"),
];

// Polarity axes. One axis per kind of claim, because they are independent: "No,
// the passage was written by a human" is negative on the verdict and positive on
// authenticity at the same time and collapsing the two into a single
// positive/negative table turns that sentence into a self-contradiction.
//
// Verdict covers both "is it so" and "did it pass", since an answer may deliver
// the same yes through either ("rain is likely" answers "will it rain").
const VERDICT_POS: &[u32] = &[
    h(b"yes"),
    h(b"true"),
    h(b"correct"),
    h(b"accurate"),
    h(b"supported"),
    h(b"valid"),
    h(b"confirmed"),
    h(b"verified"),
    h(b"approved"),
    h(b"pass"),
    h(b"passed"),
    h(b"present"),
    h(b"likely"),
    h(b"allowed"),
    h(b"available"),
    h(b"active"),
    h(b"succeeded"),
];
const VERDICT_NEG: &[u32] = &[
    h(b"no"),
    h(b"false"),
    h(b"incorrect"),
    h(b"inaccurate"),
    h(b"refuted"),
    h(b"invalid"),
    h(b"wrong"),
    h(b"unfounded"),
    h(b"unsupported"),
    h(b"rejected"),
    h(b"fail"),
    h(b"failed"),
    h(b"absent"),
    h(b"unlikely"),
    h(b"denied"),
    h(b"unavailable"),
    h(b"inactive"),
    h(b"expired"),
    h(b"revoked"),
];

const AUTH_POS: &[u32] = &[
    h(b"human"),
    h(b"real"),
    h(b"authentic"),
    h(b"genuine"),
    h(b"clean"),
    h(b"benign"),
    h(b"safe"),
    h(b"legitimate"),
    h(b"organic"),
    h(b"low"),
    h(b"normal"),
    h(b"routine"),
    h(b"compliant"),
    h(b"transparent"),
    h(b"minimal"),
    h(b"minor"),
];
const AUTH_NEG: &[u32] = &[
    h(b"ai"),
    h(b"fake"),
    h(b"forged"),
    h(b"synthetic"),
    h(b"malicious"),
    h(b"phishing"),
    h(b"infected"),
    h(b"spam"),
    h(b"fraudulent"),
    h(b"suspicious"),
    h(b"anomalous"),
    h(b"anomaly"),
    h(b"illicit"),
    h(b"laundering"),
    h(b"wash"),
    h(b"exploit"),
    h(b"scam"),
    h(b"scammy"),
    h(b"risky"),
    h(b"flagged"),
    h(b"elevated"),
    h(b"deepfake"),
    h(b"bot"),
];

const DIR_POS: &[u32] = &[
    h(b"rise"),
    h(b"rises"),
    h(b"rising"),
    h(b"rose"),
    h(b"up"),
    h(b"upward"),
    h(b"higher"),
    h(b"increase"),
    h(b"increases"),
    h(b"increased"),
    h(b"gain"),
    h(b"gains"),
    h(b"bullish"),
    h(b"growth"),
    h(b"grew"),
    h(b"appreciate"),
    h(b"above"),
    h(b"more"),
    h(b"better"),
    h(b"stronger"),
    h(b"buy"),
    h(b"positive"),
    h(b"warmer"),
    h(b"faster"),
    h(b"hot"),
    h(b"warm"),
    h(b"open"),
    h(b"enabled"),
    h(b"secure"),
    h(b"encrypted"),
    h(b"success"),
    h(b"bull"),
    h(b"strengthened"),
    h(b"strengthen"),
    h(b"appreciated"),
    h(b"gained"),
    h(b"rallied"),
    h(b"climbed"),
    h(b"surged"),
    h(b"outperformed"),
];
const DIR_NEG: &[u32] = &[
    h(b"fall"),
    h(b"falls"),
    h(b"falling"),
    h(b"fell"),
    h(b"down"),
    h(b"downward"),
    h(b"lower"),
    h(b"decrease"),
    h(b"decreases"),
    h(b"decreased"),
    h(b"loss"),
    h(b"losses"),
    h(b"bearish"),
    h(b"decline"),
    h(b"declines"),
    h(b"shrink"),
    h(b"depreciate"),
    h(b"below"),
    h(b"less"),
    h(b"worse"),
    h(b"weaker"),
    h(b"sell"),
    h(b"negative"),
    h(b"cooler"),
    h(b"slower"),
    h(b"cold"),
    h(b"cool"),
    h(b"closed"),
    h(b"disabled"),
    h(b"insecure"),
    h(b"unencrypted"),
    h(b"failure"),
    h(b"bear"),
    h(b"weakened"),
    h(b"weaken"),
    h(b"depreciated"),
    h(b"lost"),
    h(b"slumped"),
    h(b"slipped"),
    h(b"plunged"),
    h(b"underperformed"),
];

/// A string's stance on one polarity axis as `(sign, self_contradicted)`. The sign
/// is the first decisive polarity word's, negation-aware, because an answer leads
/// with its verdict. The flag records that a later word took the other side, which
/// is the shape of an answer built by copying the ground truth and dropping in a
/// negation.
fn axis_sign(t: &Toks, pos: &[u32], neg: &[u32]) -> (i32, bool) {
    const H_NO: u32 = h(b"no");
    let mut sign = 0i32;
    let mut mixed = false;
    let mut i = 0;
    while i < t.n {
        let key = t.hash[i];
        // "no" is a verdict when it stands as its own clause ("No, the cert
        // expired"). As a determiner it is not one: "no errors" is how an answer
        // says a build passed, and reading that as a negative verdict inverts a
        // correct answer.
        if key == H_NO && !t.bnd[i] && t.n != 1 {
            i += 1;
            continue;
        }
        let mut s = if in_table(pos, key) {
            1
        } else if in_table(neg, key) {
            -1
        } else {
            0
        };
        if s != 0 {
            if t.neg[i] {
                s = -s;
            }
            if sign == 0 {
                sign = s;
            } else if sign != s {
                mixed = true;
            }
        }
        i += 1;
    }
    (sign, mixed)
}

fn any_negation(t: &Toks) -> bool {
    let mut i = 0;
    while i < t.n {
        if in_table(NEG, t.hash[i]) {
            return true;
        }
        i += 1;
    }
    false
}

#[inline]
fn fraud_intent() -> bool {
    TELEGRAPH_INTENT[0] == b'F'
        && TELEGRAPH_INTENT[1] == b'R'
        && TELEGRAPH_INTENT[2] == b'A'
        && TELEGRAPH_INTENT[3] == b'U'
        && TELEGRAPH_INTENT[4] == b'D'
}

/// Byte equality over word bytes only: case, spacing and punctuation are
/// ignored, so a perfect answer scores exactly 1.0 however it is typeset.
fn normalized_equal(a: &[u8], b: &[u8]) -> bool {
    // A separator between two digits is part of the figure. Skipping it would make
    // "1.57 JPY" identical to "157 JPY", which is the difference between a right
    // answer and a wrong one by two orders of magnitude.
    fn significant(s: &[u8], i: usize) -> bool {
        if is_word(s[i]) {
            return true;
        }
        (s[i] == b'.' || s[i] == b',')
            && i > 0
            && i + 1 < s.len()
            && is_digit(s[i - 1])
            && is_digit(s[i + 1])
    }
    let mut i = 0usize;
    let mut j = 0usize;
    loop {
        while i < a.len() && !significant(a, i) {
            i += 1;
        }
        while j < b.len() && !significant(b, j) {
            j += 1;
        }
        if i >= a.len() || j >= b.len() {
            return i >= a.len() && j >= b.len();
        }
        if lower(a[i]) != lower(b[j]) {
            return false;
        }
        i += 1;
        j += 1;
    }
}

#[inline]
fn clamp01(x: f32) -> f32 {
    if x.is_nan() {
        return 0.0;
    }
    if x < 0.0 {
        0.0
    } else if x > 1.0 {
        1.0
    } else {
        x
    }
}

static mut AN_BRIDGE: [bool; MAX_TOKENS] = [false; MAX_TOKENS];
static mut GT_BRIDGE: [bool; MAX_TOKENS] = [false; MAX_TOKENS];

/// Credit an acronym on one side against the words it stands for on the other:
/// "US" for "United States", "AI" for "artificial intelligence". Miners answer in
/// abbreviations constantly and reading those as unrelated tokens marks correct
/// answers wrong.
fn acronym_bridge(
    from: &Toks,
    to: &Toks,
    from_hit: &mut [bool; MAX_TOKENS],
    to_cov: &mut [bool; MAX_TOKENS],
) {
    let mut i = 0;
    while i < from.n {
        let key = from.acro[i];
        let len = acronym_len(key);
        if key != 0 && len >= 2 {
            let mask = if len >= 4 {
                0xFFFF_FFFFu32
            } else {
                (1u32 << (8 * len)) - 1
            };
            let mut j = 0;
            while j < to.n {
                // "AU" against "Australia": an all-caps short token that prefixes a
                // proper noun is the same entity, which is how country codes, tickers
                // and airport codes appear in real answers.
                if to.w[j] > 0.5 && to.cap[j] && (to.pre[j] & mask) == key {
                    from_hit[i] = true;
                    to_cov[j] = true;
                    break;
                }
                if to.w[j] > 0.5 && pack_initials(to, j, len) == key {
                    from_hit[i] = true;
                    let mut got = 0usize;
                    let mut k = j;
                    while k < to.n && got < len {
                        if to.w[k] > 0.5 {
                            to_cov[k] = true;
                            got += 1;
                        }
                        k += 1;
                    }
                    break;
                }
                j += 1;
            }
        }
        i += 1;
    }
}

// ---------------------------------------------------------------------------
// Scoring
// ---------------------------------------------------------------------------

fn score(q: &[u8], gt: &[u8], ma: &[u8]) -> f32 {
    unsafe {
        let tq = &mut *core::ptr::addr_of_mut!(TQ);
        let tg = &mut *core::ptr::addr_of_mut!(TG);
        let ta = &mut *core::ptr::addr_of_mut!(TA);
        tokenize(q, tq);
        tokenize(gt, tg);
        tokenize(ma, ta);
        if tg.n == 0 || ta.n == 0 {
            return 0.0;
        }

        let sq = &mut *core::ptr::addr_of_mut!(SQ);
        let sg = &mut *core::ptr::addr_of_mut!(SG);
        let sa = &mut *core::ptr::addr_of_mut!(SA);
        set_fill(sq, tq);
        set_fill(sg, tg);
        set_fill(sa, ta);

        let an_bridge = &mut *core::ptr::addr_of_mut!(AN_BRIDGE);
        let gt_bridge = &mut *core::ptr::addr_of_mut!(GT_BRIDGE);
        let mut z = 0;
        while z < MAX_TOKENS {
            an_bridge[z] = false;
            gt_bridge[z] = false;
            z += 1;
        }
        acronym_bridge(ta, tg, an_bridge, gt_bridge);
        acronym_bridge(tg, ta, gt_bridge, an_bridge);

        // Precision: of what the answer asserts, how much is in the ground truth.
        // This is the term that makes keyword stuffing pointless, every extra word
        // that is not in the ground truth dilutes it. Words merely echoed from the
        // question are discounted rather than counted as inventions.
        let mut p_hit = 0.0f32;
        let mut p_tot = 0.0f32;
        let mut an_content = 0.0f32;
        let mut an_novel = 0.0f32;
        let mut an_named = 0u32;
        let mut soft_left = SOFT_BUDGET;
        let mut i = 0;
        while i < ta.n {
            let w = ta.w[i];
            let from_question = matched(sq, ta, i);
            if w > 0.5 {
                an_content += w;
                if !from_question {
                    an_novel += w;
                }
            }
            if matched(sg, ta, i) || an_bridge[i] || has_scale(tg, ta.scale[i]) {
                // Non-numeric content the answer shares with the ground truth: the
                // words that say what the figure refers to. "18,430 unique depositors"
                // names its quantity, a bare "18,430" does not, and the numeric recall
                // floor below turns on exactly that difference.
                if w > 0.5 && !ta.numeric[i] {
                    an_named += 1;
                }
                p_hit += w;
                p_tot += w;
            } else if from_question {
                p_tot += w * 0.35;
            } else {
                // No exact match, so ask the vectors whether the answer said the
                // same thing in another word.
                if w > 0.5 && soft_left > 0 {
                    soft_left -= 1;
                    p_hit += w * soft_credit(soft_best(ta, i, tg));
                }
                p_tot += w;
            }
            i += 1;
        }

        // Recall, in two parts. What the ground truth says that the question did
        // NOT already give away is the answer proper: covering that is the whole
        // job and it is what separates answering from restating the prompt.
        let mut r_hit = 0.0f32;
        let mut r_tot = 0.0f32;
        let mut k_hit = 0.0f32;
        let mut k_tot = 0.0f32;
        let mut r_soft = 0.0f32;
        let mut k_soft = 0.0f32;
        let mut contra_w = 0.0f32;
        i = 0;
        while i < tg.n {
            let w = tg.w[i];
            let in_question = matched(sq, tg, i);
            // A match under a negation the ground truth does not carry is not
            // coverage, it is the opposite claim: "no rain is expected" against
            // "rain is expected" shares every content word.
            let mut hard = gt_bridge[i] || has_scale(ta, tg.scale[i]);
            if !hard {
                if let Some(j) = matched_idx(sa, tg, i) {
                    if ta.neg[j] && !tg.neg[i] && w > 0.5 {
                        contra_w += w;
                    } else {
                        hard = true;
                    }
                }
            }
            let soft = if hard || w <= 0.5 {
                0.0
            } else {
                soft_credit(soft_best(tg, i, ta))
            };
            r_tot += w;
            if hard {
                r_hit += w;
            } else {
                r_soft += w * soft;
            }
            if !in_question {
                k_tot += w;
                if hard {
                    k_hit += w;
                } else {
                    k_soft += w * soft;
                }
            }
            i += 1;
        }
        // Vectors can fill in for wording, not for the answer itself.
        let r_cap = SOFT_CAP_FRAC * r_tot;
        let k_cap = SOFT_CAP_FRAC * k_tot;
        r_hit += if r_soft > r_cap { r_cap } else { r_soft };
        k_hit += if k_soft > k_cap { k_cap } else { k_soft };

        // Concave in precision: a correct answer that adds supporting context is
        // still correct, while heavy dilution (a shotgun list of candidates, a
        // keyword dump) still collapses.
        let p_raw = if p_tot > 0.0 {
            clamp01(p_hit / p_tot)
        } else {
            0.0
        };
        let p = if P_CONCAVE > 0.5 {
            p_raw * (2.0 - p_raw)
        } else {
            p_raw
        };
        let r_all = if r_tot > 0.0 {
            clamp01(r_hit / r_tot)
        } else {
            0.0
        };
        // Multiplicative, not blended: without the answer-bearing content there is
        // no answer, however much of the prompt's wording is echoed back.
        //
        // The one exception is an answer that says something of its own and simply
        // words it differently ("out of disk" for "disk exhaustion"). That earns a
        // small floor from overall coverage, which an answer built entirely out of
        // the question's own words cannot reach.
        let novelty = if an_content > 0.0 {
            an_novel / an_content
        } else {
            0.0
        };
        let floor_scale = clamp01((novelty - 0.2) * 3.0);
        // Figures stated by the ground truth, and how many the answer reproduces.
        // Needed here rather than further down because numeric completeness feeds
        // the recall term, not just the penalties.
        let mut gt_nums = 0u32;
        let mut gt_nums_hit = 0u32;
        i = 0;
        while i < tg.n {
            if tg.numeric[i] {
                gt_nums += 1;
                if set_get(sa, tg.hash[i]).is_some() {
                    gt_nums_hit += 1;
                }
            }
            i += 1;
        }

        // Numeric completeness. On a lookup intent the figures ARE the answer: an
        // answer carrying every figure the ground truth states has answered the
        // question, however tersely it is worded ("18,430 unique depositors" against
        // a fifteen-word sentence). Word-level recall reads that as a near miss, so
        // full numeric coverage floors the key-recall term.
        //
        // Bounded deliberately, and gated on the answer saying something of its own:
        // it lifts a terse correct answer, it cannot manufacture an answer out of a
        // bare number echoed from the question.
        let key_frac = if k_tot > 0.0 { clamp01(k_hit / k_tot) } else { 0.0 };
        let key_frac = if gt_nums > 0
            && gt_nums_hit == gt_nums
            && novelty > 0.2
            && an_named >= NUM_FLOOR_NAMED
        {
            let floor = R_NUM_FLOOR * clamp01(novelty * 2.0);
            if key_frac > floor {
                key_frac
            } else {
                floor
            }
        } else {
            key_frac
        };
        let r = if k_tot > 0.0 {
            clamp01(
                key_frac * (R_KEY_BASE + (1.0 - R_KEY_BASE) * r_all)
                    + R_FLOOR * r_all * floor_scale,
            )
        } else {
            r_all
        };

        // Precision-leaning F-beta (beta = 0.6). A correct answer is often terser
        // than the ground truth, so weighting recall equally would punish being
        // right briefly.
        let b2 = F_BETA2;
        let denom = b2 * p + r;
        let lex = if denom > 0.0 {
            ((1.0 + b2) * p * r) / denom
        } else {
            0.0
        };

        let ga = &mut *core::ptr::addr_of_mut!(GA);
        let gb = &mut *core::ptr::addr_of_mut!(GB);
        let cg3 = build_grams(gt, ga, 3);
        let cm3 = build_grams(ma, gb, 3);
        let gram3 = gram_similarity(ga, gb, cg3, cm3);

        // Letter pairs as well as triples, at a fraction of the weight. Triples go
        // to zero on short or unusual text (an abbreviation, a translation, a
        // ticker), and two answers that both score a flat zero are indistinguishable
        // even when one of them is right. Pairs keep the tail graded.
        let cg2 = build_grams(gt, ga, 2);
        let cm2 = build_grams(ma, gb, 2);
        let gram2 = gram_similarity(ga, gb, cg2, cm2);

        // Adjacency of content words, reusing the same bitsets now that the
        // character similarities are in hand.
        let bg_g = build_content_bigrams(tg, ga);
        let bg_a = build_content_bigrams(ta, gb);
        let adjacency = dice(ga, gb, bg_g, bg_a);
        let cc_g = content_count(tg);
        let cc_a = content_count(ta);

        let mut raw = clamp01(W_LEX * lex + W_GRAM3 * gram3 + W_GRAM2 * gram2);

        // Padding guard. Precision counts every answer token against the ground
        // truth, so an answer that states the truth and then states it again scores
        // every repetition as a fresh hit: the ground truth pasted forty times came
        // out at exactly 1.0, a higher score than the correct one-line answer it was
        // built from. Length is the tell. Saturating rather than linear, and it only
        // engages well past ordinary verbosity, so a correct answer wrapped in
        // assistant boilerplate or a sentence of context is untouched.
        if cc_g >= 3 && cc_a > 0 {
            let ratio = cc_a as f32 / cc_g as f32;
            if ratio > DILUTE_START {
                let over = (ratio - DILUTE_START) / DILUTE_START;
                raw *= clamp01(1.0 - DILUTE_MAX * (over / (1.0 + over)));
            }
        }

        // On CHAT_COMPLETION the champion ranks on sentence-embedding similarity, so the
        // traffic gate rewards tracking that. Blend the mean-pooled distilled cosine in
        // when the build asks for it. The correctness penalties below still apply, which
        // is what keeps our separation above the champion's on the fixture set even while
        // most of the score follows its topical ranking.
        if W_EMB > 0.0 {
            // Monotone-lift blend. The linear form `(1-W)*raw + W*sc` used through v8
            // dragged high-precision paraphrases DOWN whenever the vector cosine was
            // lower than the lexical score: case-20 style ("Wallet 0xF00…" GT vs
            // "0xF00…" answer) has lexical ~0.95 but our 794 KB 50-dim table reads
            // the two sentences at cos ~0.5, and the linear blend at W_EMB=0.30
            // pulled the score to 0.82. Champion 642 scores that case 1.00 because
            // its 24 MB transformer reads paraphrases as ~1.0 cos. We can't match
            // that vector capacity in this WASM size class, but we CAN stop dragging
            // paraphrase cases below their lexical ceiling: only apply the linear
            // blend when it lifts the score. Ranking is preserved on cases where
            // vectors help (weak lex but strong topical similarity); paraphrase
            // cases keep their lexical near-1.0. Registrations 940-ish (v6),
            // 980 (v7), 986 (v8) all lost by 0.011-0.034 on separation with the
            // linear blend; v6 was the peak because both W_EMB directions widened
            // the gap. Max-blend gives a monotone-lift version of v6 without a new
            // tunable to sweep.
            let sc = sentence_cos(tg, ta);
            let blended = clamp01((1.0 - W_EMB) * raw + W_EMB * sc);
            if blended > raw {
                raw = blended;
            }
        }

        // Same words, different claim. "France is the capital of Paris" is a
        // perfect bag of words and a wrong answer. Word overlap cannot see the
        // difference; which content words sit next to which can.
        //
        // The test is deliberately narrow: it only fires when the answer carries
        // exactly the ground truth's content words, nothing missing and nothing
        // added, yet shares no adjacency with it. A paraphrase always drops, adds
        // or reuses some pairing, so reordering alone is not treated as a lie.
        let full_coverage = k_tot > 0.0 && k_hit >= k_tot * 0.999;
        if full_coverage && p_raw >= 0.999 && adjacency < 0.15 && cc_g >= 3 && cc_a >= 3 {
            raw *= M_ORDER;
        }

        // A word list is not an answer. Harvesting the ground truth's content words
        // and emitting them unconnected scores well on any coverage measure — it is
        // the cheapest attack on a bag-of-words scorer, and it beat a correct terse
        // reply here because it happened to contain more of the right words.
        //
        // Function words are the tell. Running prose carries them at a steady rate;
        // a keyword dump has almost none. Length is what separates the dump from a
        // genuinely terse answer, which is also sparse in function words but short:
        // "42,318.77 ETH" is an answer, six bare nouns in a row is a list. Both
        // conditions are required, and the answer must also share little adjacency
        // with the ground truth, so a correct sentence is never caught by this.
        if cc_a >= WORDLIST_MIN_CONTENT && adjacency < 0.35 && !looks_structured(ma) {
            let mut func = 0usize;
            i = 0;
            while i < ta.n {
                if ta.w[i] <= 0.5 {
                    func += 1;
                }
                i += 1;
            }
            let func_ratio = func as f32 / ta.n as f32;
            if func_ratio < WORDLIST_FUNC_RATIO {
                let deficit = (WORDLIST_FUNC_RATIO - func_ratio) / WORDLIST_FUNC_RATIO;
                raw *= clamp01(1.0 - M_WORDLIST * deficit);
            }
        }

        // Figures attached to different entities are a different claim even when the
        // words and the numbers all match.
        let ef_g = build_entity_figures(tg, ga);
        let ef_a = build_entity_figures(ta, gb);
        if ef_g > 0 && ef_a > 0 {
            let shared = dice(ga, gb, ef_g, ef_a);
            if shared < 0.05 && full_coverage {
                raw *= M_ENTITY;
            }
        }

        // Coverage that only holds under a negation the ground truth does not carry.
        if contra_w > 0.0 && k_tot > 0.0 {
            let ratio = clamp01(contra_w / k_tot);
            raw *= 1.0 - M_NEGCOV * ratio;
        }

        // Names. A figure attached to the wrong name is not a partly right answer,
        // it is the wrong one: "Roobet hot wallets received 412,500,000 USDT" carries
        // every digit of a ground truth about Stake.com and asserts something false.
        // Names fail the way figures fail, so they are checked the way figures are.
        //
        // The existing entity/figure pairing test above catches a swap only when the
        // answer covers the ground truth exactly and shares no pairing with it. That
        // is too narrow to see the common case, where one name is simply replaced and
        // everything else is copied through.
        // Identifiers. An address is not a description of a wallet, it is the wallet:
        // `0x742d...f44e` and `0x742d...f44f` differ by one character and are two
        // different accounts, so a balance reported against the wrong one is not
        // approximately right, it is an answer about something else. Every other
        // signal here — word overlap, character n-grams, vectors — rates those two
        // strings as near identical, which is why this is checked on its own and
        // weighted harder than anything else in the module.
        // Counted twice, for the same reason names are. `all` covers every identifier
        // the ground truth states and gates the wrong-identifier test. `new` covers
        // only those the question did not already supply, and is the only one that
        // costs anything to omit: asked "what is the balance of 0xabc...", an answer
        // is not required to recite 0xabc... back before it is allowed to be right.
        let mut id_tot = 0u32;
        let mut id_hit = 0u32;
        let mut id_new_tot = 0u32;
        let mut id_new_hit = 0u32;
        i = 0;
        while i < tg.n {
            if tg.ident[i] {
                let covered = set_get(sa, tg.hash[i]).is_some();
                id_tot += 1;
                if covered {
                    id_hit += 1;
                }
                if set_get(sq, tg.hash[i]).is_none() {
                    id_new_tot += 1;
                    if covered {
                        id_new_hit += 1;
                    }
                }
            }
            i += 1;
        }
        if id_new_tot > 0 {
            let cov = id_new_hit as f32 / id_new_tot as f32;
            raw *= M_ID_MISS + (1.0 - M_ID_MISS) * cov;
        }
        if id_tot > 0 {
            // An identifier the answer asserts that the ground truth and question
            // never mention. Naming the wrong account is worse than naming none.
            if id_hit < id_tot {
                let mut wrong = false;
                i = 0;
                while i < ta.n {
                    if ta.ident[i]
                        && set_get(sg, ta.hash[i]).is_none()
                        && set_get(sq, ta.hash[i]).is_none()
                    {
                        wrong = true;
                        break;
                    }
                    i += 1;
                }
                if wrong {
                    raw *= M_ID_WRONG;
                }
            }
        }

        // Two separate things go wrong with names, and they are not the same failure.
        // Leaving one out is incompleteness. Putting a different one in its place is a
        // false claim. They are measured apart and weighted apart.
        //
        // Coverage is tracked twice. `ent_all` spans every name the ground truth uses
        // and only gates the substitution test. `ent_new` spans the names the question
        // did not already supply, and is the only one that costs anything to omit: an
        // answer to "how many depositors did Rollbit see" does not have to say
        // "Rollbit" again to have answered, and charging it for that marks a correct
        // terse reply as badly as a wrong one.
        let mut ent_all_tot = 0.0f32;
        let mut ent_all_hit = 0.0f32;
        let mut ent_new_tot = 0.0f32;
        let mut ent_new_hit = 0.0f32;
        i = 0;
        while i < tg.n {
            if tg.cap[i] && tg.w[i] > 0.5 && !tg.numeric[i] {
                let w = tg.w[i];
                let covered = matched(sa, tg, i) || gt_bridge[i];
                ent_all_tot += w;
                if covered {
                    ent_all_hit += w;
                }
                if !matched(sq, tg, i) {
                    ent_new_tot += w;
                    if covered {
                        ent_new_hit += w;
                    }
                }
            }
            i += 1;
        }
        // Omission: mild, and only for names the question did not already give.
        if ent_new_tot > 0.0 {
            let cov = clamp01(ent_new_hit / ent_new_tot);
            raw *= M_ENT_MISS + (1.0 - M_ENT_MISS) * cov;
        }
        // Substitution: the answer names something that appears in neither the ground
        // truth nor the question, while a name the ground truth does use is missing.
        // Both halves are required. Citing a source the ground truth happens not to
        // mention is not a substitution, and it stays unpenalised as long as the
        // answer still names what the ground truth named.
        if ent_all_tot > 0.0 && ent_all_hit < ent_all_tot * 0.999 {
            let mut invented = false;
            i = 0;
            while i < ta.n {
                if ta.cap[i]
                    && ta.w[i] > 0.5
                    && !ta.numeric[i]
                    && !matched(sg, ta, i)
                    && !matched(sq, ta, i)
                    && !an_bridge[i]
                {
                    invented = true;
                    break;
                }
                i += 1;
            }
            if invented {
                raw *= M_ENT_SWAP;
            }
        }

        // Numbers. Omitting a figure the ground truth states is incomplete;
        // stating a different one is wrong. Counted before recall is finalised,
        // because on a lookup intent the figures are what recall is really about.
        if gt_nums > 0 {
            let frac = gt_nums_hit as f32 / gt_nums as f32;
            raw *= M_NUM_MISS_BASE + (1.0 - M_NUM_MISS_BASE) * frac;
            let mut bad = 0u32;
            i = 0;
            while i < ta.n {
                if ta.numeric[i]
                    && set_get(sg, ta.hash[i]).is_none()
                    && set_get(sq, ta.hash[i]).is_none()
                {
                    bad += 1;
                }
                i += 1;
            }
            if bad > 0 && gt_nums_hit < gt_nums {
                raw *= M_NUM_WRONG;
            }
        }

        // Polarity, per axis. Getting the verdict right in your own words counts
        // for something even when the wording shares little with the ground truth;
        // getting it backwards while reusing every word counts for almost nothing.
        let axes: [(&[u32], &[u32]); 3] = [
            (VERDICT_POS, VERDICT_NEG),
            (AUTH_POS, AUTH_NEG),
            (DIR_POS, DIR_NEG),
        ];
        let mut agree = 0;
        let mut contra = 0;
        let mut silent = 0;
        let mut two_faced = 0;
        let mut c = 0;
        while c < axes.len() {
            let (pos, neg) = axes[c];
            let (g, _) = axis_sign(tg, pos, neg);
            if g != 0 {
                let (a, a_mixed) = axis_sign(ta, pos, neg);
                if a == 0 {
                    silent += 1;
                } else if a != g {
                    contra += 1;
                } else if a_mixed && gram3 > 0.6 {
                    two_faced += 1;
                } else {
                    agree += 1;
                }
            }
            c += 1;
        }
        if contra > 0 {
            raw *= M_CONTRA;
        } else if two_faced > 0 {
            // Leads with the right verdict, then asserts the opposite, while
            // reusing the ground truth's wording. That is the shape of a copied
            // answer with a negation dropped in, not of a careful one.
            raw *= M_TWO_FACED;
        } else if agree > 0 {
            raw += (1.0 - raw) * B_AGREE;
        } else if silent > 0 {
            raw *= M_SILENT;
        } else if any_negation(tg) != any_negation(ta) {
            // No axis is decisive, but one side negates and the other does not.
            raw *= 1.0 - 0.35 * lex;
        }

        // Fraud is not merely a generic yes/no question. The risk conclusion is
        // the deliverable, so preserve smooth ranking but apply a second, narrowly
        // scoped authenticity check. This catches answers that copy all measured
        // transfers and then invert only "low risk" versus "suspicious".
        if fraud_intent() {
            let (g_auth, _) = axis_sign(tg, AUTH_POS, AUTH_NEG);
            if g_auth != 0 {
                let (a_auth, a_mixed) = axis_sign(ta, AUTH_POS, AUTH_NEG);
                if a_auth != 0 && a_auth != g_auth {
                    raw *= if a_mixed {
                        FRAUD_AUTH_TWO_FACED
                    } else {
                        FRAUD_AUTH_CONTRA
                    };
                } else if a_auth == g_auth && a_mixed {
                    // A correct lead followed by the opposite risk claim is not a
                    // correct answer. Check this before the agreement bonus.
                    raw *= FRAUD_AUTH_TWO_FACED;
                } else if a_auth == g_auth {
                    raw += (1.0 - raw) * FRAUD_AUTH_AGREE;
                } else {
                    raw *= FRAUD_AUTH_SILENT;
                }
            }
        }

        // Contrast. Pull confident matches up and near-misses down without
        // flattening the middle: a scorer whose outputs barely vary is rejected,
        // and one that is all-or-nothing cannot rank the answers in between.
        let raw = clamp01(raw);
        let smooth = raw * raw * (3.0 - 2.0 * raw);
        let base = clamp01(SHARPEN * smooth + (1.0 - SHARPEN) * raw);
        base * base * (3.0 - 2.0 * base)
    }
}

// ---------------------------------------------------------------------------
// The export the node calls
// ---------------------------------------------------------------------------

#[unsafe(no_mangle)]
pub unsafe extern "C" fn rank_answer(
    q_ptr: i32,
    q_len: i32,
    gt_ptr: i32,
    gt_len: i32,
    ma_ptr: i32,
    ma_len: i32,
) -> f32 {
    unsafe {
        let q = read_bytes(q_ptr, q_len);
        let gt = read_bytes(gt_ptr, gt_len);
        let ma = read_bytes(ma_ptr, ma_len);

        // A blank answer is exactly zero, whatever the whitespace.
        let mut any = false;
        let mut i = 0;
        while i < ma.len() {
            if !ma[i].is_ascii_whitespace() {
                any = true;
                break;
            }
            i += 1;
        }
        if !any || gt.is_empty() {
            return 0.0;
        }
        if normalized_equal(gt, ma) {
            return 1.0;
        }
        score(q, gt, ma)
    }
}

// ---------------------------------------------------------------------------
// Host tests
// ---------------------------------------------------------------------------
// These run on the host toolchain (`cargo test`), never in the WASM build.
// They pin the invariants the README claims and, just as importantly, the
// behaviours a miner's response design has to be built around: an answer that
// omits an identifier, states a different figure, or takes the opposite stance
// is not "slightly worse", it is scored as a different answer.

#[cfg(test)]
mod tests_support {
    use super::*;

    // Scoring writes through fixed statics — no allocator is available in the
    // WASM build, so the working buffers are `static mut` by construction. The
    // node instantiates one module per call and never shares one across
    // threads, so that is sound in production. `cargo test` is not: it runs
    // test functions in parallel by default, and two threads scoring at once
    // corrupt each other's buffers. The symptom is spectacular rather than
    // subtle — a correct answer scoring 0.00008 while a truncated one scores
    // 0.65, and identical inputs disagreeing between runs. Serializing here
    // keeps plain `cargo test` correct without requiring `--test-threads=1`.
    static TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    /// The scored entry point, minus the pointer marshalling `rank_answer` does.
    pub fn rank(q: &str, gt: &str, ma: &str) -> f32 {
        let _guard = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let (q, gt, ma) = (q.as_bytes(), gt.as_bytes(), ma.as_bytes());
        if ma.iter().all(|b| b.is_ascii_whitespace()) || gt.is_empty() {
            return 0.0;
        }
        if normalized_equal(gt, ma) {
            return 1.0;
        }
        score(q, gt, ma)
    }

}

#[cfg(test)]
mod tests {
    pub(super) use super::tests_support::rank;
    use super::*;

    const Q: &str = "What is the balance of 0x974caa59e49682cda0ad2bbe82983419a2ecc400?";
    const GT: &str = "Address 0x974caa59e49682cda0ad2bbe82983419a2ecc400 holds \
                      1431.586854770926157824 ETH at block 25831237.";

    #[test]
    fn blank_answer_scores_zero() {
        assert_eq!(rank(Q, GT, ""), 0.0);
        assert_eq!(rank(Q, GT, "   \t\n "), 0.0);
    }

    #[test]
    fn exact_answer_scores_one() {
        assert_eq!(rank(Q, GT, GT), 1.0);
    }

    #[test]
    fn empty_ground_truth_scores_zero() {
        assert_eq!(rank(Q, "", "anything"), 0.0);
    }

    #[test]
    fn every_score_is_bounded() {
        for answer in [GT, "unrelated prose about the weather", "0", "!!!", "ETH"] {
            let s = rank(Q, GT, answer);
            assert!((0.0..=1.0).contains(&s), "{answer} scored {s}");
        }
    }

    #[test]
    fn scoring_is_deterministic() {
        let a = rank(Q, GT, "The balance is 1431.586854770926157824 ETH.");
        let b = rank(Q, GT, "The balance is 1431.586854770926157824 ETH.");
        assert_eq!(a, b);
    }

    /// A truncated address is not the address. This is why canonical fields
    /// must never abbreviate: `0x974caa59…` cannot match and never will.
    #[test]
    fn truncated_identifier_scores_below_the_full_one() {
        let gt = "Wallet 0x974caa59e49682cda0ad2bbe82983419a2ecc400 holds 1431 ETH.";
        let full = rank("What is the balance?", gt,
                        "0x974caa59e49682cda0ad2bbe82983419a2ecc400 holds 1431 ETH.");
        let cut = rank("What is the balance?", gt, "0x974caa59... holds 1431 ETH.");
        assert!(full > cut, "full {full} should beat truncated {cut}");
    }

    /// A different address is a different account, and is punished harder than
    /// omitting one.
    #[test]
    fn wrong_identifier_scores_below_a_missing_one() {
        let gt = "Transaction 0xaaaabbbbccccddddeeeeffff0000111122223333444455556666777788889999 succeeded.";
        let missing = rank("Did it succeed?", gt, "The transaction succeeded.");
        let wrong = rank("Did it succeed?", gt,
            "Transaction 0x1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff succeeded.");
        assert!(missing > wrong, "missing {missing} should beat wrong {wrong}");
    }

    /// On a lookup intent the figure is the answer.
    #[test]
    fn stating_the_figure_beats_omitting_it_and_omitting_beats_contradicting() {
        let gt = "The wallet holds 1431.58 ETH.";
        let right = rank("How much?", gt, "The wallet holds 1431.58 ETH.");
        let silent = rank("How much?", gt, "The wallet holds some ETH.");
        let wrong = rank("How much?", gt, "The wallet holds 9999.99 ETH.");
        assert!(right > silent, "right {right} vs silent {silent}");
        assert!(silent > wrong, "silent {silent} vs wrong {wrong}");
    }

    /// Answering with the opposite stance is worse than not taking one.
    #[test]
    fn contradicting_the_verdict_scores_below_staying_silent() {
        let gt = "The transaction failed and was reverted.";
        let agree = rank("Did it work?", gt, "The transaction failed and was reverted on chain.");
        let silent = rank("Did it work?", gt, "The transaction was processed on chain.");
        let contra = rank("Did it work?", gt, "The transaction succeeded on chain.");
        assert!(agree > contra, "agree {agree} vs contra {contra}");
        assert!(silent > contra, "silent {silent} vs contra {contra}");
    }

    #[test]
    fn fraud_risk_language_tracks_authenticity_polarity() {
        let gt = "The transfer pattern is legitimate and no suspicious activity was detected.";
        let clean = rank(
            "Is this wallet suspicious?",
            gt,
            "The activity is legitimate and no suspicious behavior was detected.",
        );
        let fraudulent = rank(
            "Is this wallet suspicious?",
            gt,
            "The activity is suspicious and appears illicit.",
        );
        assert!(clean > fraudulent, "clean {clean} vs fraudulent {fraudulent}");
    }

    /// Structured output is a legitimate answer format and must not be read as
    /// a keyword dump. This is what lets the miner answer in JSON.
    #[test]
    fn json_is_not_penalised_as_a_word_list() {
        let gt = "The wallet 0x974caa59e49682cda0ad2bbe82983419a2ecc400 holds 1431.58 ETH at block 25831237.";
        let json = "{\"address\":\"0x974caa59e49682cda0ad2bbe82983419a2ecc400\",\
                     \"native_balance\":1431.58,\"native_symbol\":\"ETH\",\"block_number\":25831237}";
        let dump = "wallet address holds ETH block balance native";
        assert!(rank("How much?", gt, json) > rank("How much?", gt, dump));
    }

    #[test]
    fn looks_structured_detects_json_and_tables() {
        assert!(looks_structured(b"{\"a\": 1}"));
        assert!(looks_structured(b"| a | b |\n| - | - |"));
        assert!(looks_structured(b"key: value\nother: thing"));
        assert!(!looks_structured(b"just some running prose here"));
    }

    #[test]
    fn identifiers_are_recognised_exactly() {
        assert!(is_identifier(b"0x974caa59e49682cda0ad2bbe82983419a2ecc400"));
        assert!(is_identifier(b"0xAAAA1111bbbb2222"));
        // A truncated address is not a hex run and must not be treated as one.
        assert!(!is_identifier("0x974caa59…".as_bytes()));
        assert!(!is_identifier(b"0xnothex"));
        assert!(!is_identifier(b"1431"));
    }

    /// Padding the answer out must not beat answering it.
    #[test]
    fn repeating_the_ground_truth_does_not_beat_stating_it_once() {
        let gt = "The wallet holds 1431.58 ETH at block 25831237.";
        let once = rank("How much?", gt, gt);
        let padded = rank("How much?", gt, &gt.repeat(40));
        assert!(once > padded, "once {once} should beat padded {padded}");
    }

    /// Non-ASCII and oversized inputs must not trap.
    #[test]
    fn unusual_inputs_do_not_trap() {
        let big = "0x".to_string() + &"a".repeat(40_000);
        for answer in ["🎲🎰 balance", "残高は1431 ETHです", big.as_str()] {
            let s = rank(Q, GT, answer);
            assert!((0.0..=1.0).contains(&s));
        }
    }
}

// ---------------------------------------------------------------------------
// Regression: the live answers this miner used to send
// ---------------------------------------------------------------------------
// These are not synthetic. `OLD_*` are the exact strings the production
// deployment returned on 2026-08-25, captured before the response contract was
// rewritten; `NEW_*` are what the rewritten endpoints return for the same
// question. They exist so a future change that quietly reverts to a thinner
// answer fails here rather than in the live ranking a scoring epoch later.

#[cfg(test)]
mod answer_regression {
    use super::tests_support::rank;

    // ── FRAUD_DETECTION ─────────────────────────────────────────────────
    const FRAUD_Q: &str =
        "Is 0x974caa59e49682cda0ad2bbe82983419a2ecc400 on ethereum showing \
         suspicious transfer activity in the last 24 hours?";

    // A plausible benchmark ground truth: a risk answer states what was
    // measured, not merely that nothing was found.
    const FRAUD_GT: &str =
        "Address 0x974caa59e49682cda0ad2bbe82983419a2ecc400 on ethereum is low risk \
         over 24h with a risk score of 0.000. It made 1340 transfers, 670 inbound and \
         670 outbound, across 214 distinct counterparties, the largest holding 31.2% \
         of transferred value. Peak rate 96 transfers/hour against a 55.83/hour mean. \
         No round trips were detected and no risk signals fired; the activity is \
         consistent with legitimate exchange settlement.";

    const OLD_FRAUD: &str =
        "Analyzed 1340 transfers over 24h. No anomaly patterns matched.";

    const NEW_FRAUD: &str =
        "Address 0x974caa59e49682cda0ad2bbe82983419a2ecc400 on ethereum screened over 24h: \
         risk tier low_risk, risk score 0.000 of 1.000. Observed 1340 transfers \
         (670 inbound, 670 outbound) across 214 distinct counterparties, with the largest \
         counterparty holding 31.2% of transferred value and the top five holding 62.4%. \
         Peak rate 96 transfers/hour against a 55.83/hour mean. 0 same-counterparty round \
         trips detected. All 5 screens ran and none matched; risk signals are absent and \
         the observed transfer pattern is consistent with legitimate activity. \
         This score ranks review priority from observable transfer patterns. \
         It is not a finding of fraud, and no identity or intent is inferred.";

    #[test]
    fn new_fraud_answer_scores_above_the_one_production_was_sending() {
        let old = rank(FRAUD_Q, FRAUD_GT, OLD_FRAUD);
        let new = rank(FRAUD_Q, FRAUD_GT, NEW_FRAUD);
        assert!(
            new > old,
            "new fraud answer {new} must beat the shipped one {old}"
        );
    }

    // ── WALLET_BALANCE_CHECK ────────────────────────────────────────────
    const BAL_Q: &str =
        "What is the balance of 0x974caa59e49682cda0ad2bbe82983419a2ecc400 on ethereum?";
    const BAL_GT: &str =
        "Address 0x974caa59e49682cda0ad2bbe82983419a2ecc400 on ethereum holds \
         1431.586854770926157824 ETH (1431586854770926157824 wei) at block 25831237.";

    // The shipped answer truncated its own subject and reported no units,
    // no wei, no symbol and no block.
    const OLD_BAL: &str =
        "Address 0x974caa59… on ethereum holds 1431.5869 native units. \
         Directly labeled as a Stake.com wallet. Interacted with 0 tracked \
         casino cluster(s) in the last 30 days.";

    const NEW_BAL: &str =
        "Address 0x974caa59e49682cda0ad2bbe82983419a2ecc400 on ethereum holds \
         1431.586854770926157824 ETH (1431586854770926157824 wei) as of block 25831237. \
         No tracked token balances were returned for this address. The registry claims \
         this address as a Stake.com hot wallet (curated claim, confidence 0.75).";

    #[test]
    fn new_balance_answer_scores_above_the_one_production_was_sending() {
        let old = rank(BAL_Q, BAL_GT, OLD_BAL);
        let new = rank(BAL_Q, BAL_GT, NEW_BAL);
        assert!(new > old, "new balance answer {new} must beat shipped {old}");
    }

    // ── ONCHAIN_TX_LOOKUP ───────────────────────────────────────────────
    const TX_Q: &str =
        "Look up transaction \
         0x70a2cdd2de8fccbc87f04aae988c5a7aa72b2fa776cce7dccb692e7169bf2431 on ethereum.";
    const TX_GT: &str =
        "Transaction 0x70a2cdd2de8fccbc87f04aae988c5a7aa72b2fa776cce7dccb692e7169bf2431 \
         on ethereum succeeded in block 25831228. Sender \
         0xf9b6a1eb0190bf76274b0876957ee9f4f508af41 sent to \
         0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45 with value 0 ETH. It used 112515 gas \
         at an effective price of 104840912 wei for a total fee of 11796175213680 wei.";

    const OLD_TX: &str =
        "Transaction resolved by chain RPC and classified as unattributed using \
         0 registry claim(s).";

    const NEW_TX: &str =
        "Transaction 0x70a2cdd2de8fccbc87f04aae988c5a7aa72b2fa776cce7dccb692e7169bf2431 \
         on ethereum succeeded. Sender 0xf9b6a1eb0190bf76274b0876957ee9f4f508af41 sent to \
         0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45, value 0 ETH (0 wei). Mined in block \
         25831228 at 2026-08-25T09:10:47+00:00, position 1. Gas used 112515 of 500000 \
         limit at 104840912 wei effective price, total fee 11796175213680 wei \
         (0.00001179617521368 ETH). Neither address matches the operator registry, so \
         this transaction is unattributed.";

    #[test]
    fn new_transaction_answer_scores_above_the_one_production_was_sending() {
        let old = rank(TX_Q, TX_GT, OLD_TX);
        let new = rank(TX_Q, TX_GT, NEW_TX);
        assert!(new > old, "new tx answer {new} must beat shipped {old}");
    }

    /// Print the measured deltas. Run with `cargo test -- --nocapture`.
    #[test]
    fn report_measured_deltas() {
        for (name, q, gt, old, new) in [
            ("FRAUD_DETECTION", FRAUD_Q, FRAUD_GT, OLD_FRAUD, NEW_FRAUD),
            ("WALLET_BALANCE_CHECK", BAL_Q, BAL_GT, OLD_BAL, NEW_BAL),
            ("ONCHAIN_TX_LOOKUP", TX_Q, TX_GT, OLD_TX, NEW_TX),
        ] {
            let (o, n) = (rank(q, gt, old), rank(q, gt, new));
            println!("{name:22} old={o:.6}  new={n:.6}  x{:.1}", n / o.max(1e-9));
        }
    }
}
