//! DegenLens WASM scoring module for Telegraph Protocol.
//!
//! Scores on-chain miner answers against ground truth for gambling-intelligence intents:
//!   `ONCHAIN_TX_LOOKUP`, `WALLET_BALANCE_CHECK`, `FRAUD_DETECTION`.
//!
//! Composite score (0..1):
//!   - 50%  numerical precision (relative error on USD/token amounts)
//!   - 25%  address accuracy (checksum-aware exact match on hex addresses)
//!   - 15%  completeness (expected fields present in the response)
//!   - 10%  recency (recent timestamp = 1.0, older = penalized)
//!
//! Blank miner answer always returns 0.0 as required by the spec.

#![cfg_attr(target_arch = "wasm32", no_std)]

#[cfg(target_arch = "wasm32")]
mod wasm_runtime {
    use core::panic::PanicInfo;

    #[panic_handler]
    fn panic(_info: &PanicInfo) -> ! {
        core::arch::wasm32::unreachable()
    }
}

// ---- Memory ABI expected by the Telegraph node --------------------------------------

const HEAP_SIZE: usize = 2 * 1024 * 1024; // 2 MiB
static mut HEAP: [u8; HEAP_SIZE] = [0u8; HEAP_SIZE];
static mut HEAP_OFFSET: usize = 0;

#[no_mangle]
pub unsafe extern "C" fn alloc(size: i32) -> i32 {
    let size = size.max(0) as usize;
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

#[no_mangle]
pub unsafe extern "C" fn dealloc(_ptr: i32, _size: i32) {}

unsafe fn read_str<'a>(ptr: i32, len: i32) -> &'a str {
    let slice = core::slice::from_raw_parts(ptr as *const u8, len.max(0) as usize);
    core::str::from_utf8_unchecked(slice)
}

#[no_mangle]
pub unsafe extern "C" fn rank_answer(
    q_ptr: i32,
    q_len: i32,
    gt_ptr: i32,
    gt_len: i32,
    ma_ptr: i32,
    ma_len: i32,
) -> f32 {
    let question = read_str(q_ptr, q_len);
    let ground_truth = read_str(gt_ptr, gt_len);
    let miner_answer = read_str(ma_ptr, ma_len);
    if miner_answer.trim().is_empty() {
        return 0.0;
    }
    score_composite(question, ground_truth, miner_answer)
}

// ---- Scoring -----------------------------------------------------------------------

/// Public helper so tests can call the scoring logic directly.
pub fn score_composite(question: &str, ground_truth: &str, miner_answer: &str) -> f32 {
    if miner_answer.trim().is_empty() {
        return 0.0;
    }
    // Verbatim short-circuit — deterministic responses with exact match get 1.0.
    if miner_answer.trim() == ground_truth.trim() {
        return 1.0;
    }

    let numeric = numeric_precision(ground_truth, miner_answer);
    let address = address_accuracy(ground_truth, miner_answer);
    let completeness = field_completeness(ground_truth, miner_answer);
    let recency = recency_score(question, miner_answer);

    let score = 0.50 * numeric + 0.25 * address + 0.15 * completeness + 0.10 * recency;
    score.clamp(0.0, 1.0)
}

/// Extract all numeric values (integers or floats) from a string, in order.
/// Skips numbers embedded in double-quoted strings so timestamps like "2026-08-14"
/// don't inflate the numeric-precision score.
fn extract_numbers(text: &str, out: &mut [f64; 64]) -> usize {
    let mut count = 0;
    let bytes = text.as_bytes();
    let mut i = 0;
    let mut in_string = false;
    while i < bytes.len() && count < 64 {
        let c = bytes[i];
        if c == b'"' {
            in_string = !in_string;
            i += 1;
            continue;
        }
        if in_string {
            i += 1;
            continue;
        }
        let is_start = c.is_ascii_digit()
            || (c == b'-' && i + 1 < bytes.len() && bytes[i + 1].is_ascii_digit());
        if is_start {
            let start = i;
            if c == b'-' {
                i += 1;
            }
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
            }
            if i < bytes.len() && bytes[i] == b'.' {
                i += 1;
                while i < bytes.len() && bytes[i].is_ascii_digit() {
                    i += 1;
                }
            }
            if let Ok(s) = core::str::from_utf8(&bytes[start..i]) {
                if let Ok(n) = s.parse::<f64>() {
                    out[count] = n;
                    count += 1;
                }
            }
        } else {
            i += 1;
        }
    }
    count
}

/// Score how well the miner's numeric values match ground truth via tolerance bands.
fn numeric_precision(ground_truth: &str, miner_answer: &str) -> f32 {
    let mut gt_nums = [0.0f64; 64];
    let mut ma_nums = [0.0f64; 64];
    let gt_count = extract_numbers(ground_truth, &mut gt_nums);
    let ma_count = extract_numbers(miner_answer, &mut ma_nums);

    if gt_count == 0 {
        return 0.5; // no numbers to score — neutral
    }

    let mut total: f32 = 0.0;
    let mut matched = 0usize;
    for i in 0..gt_count {
        let gt = gt_nums[i];
        if gt == 0.0 {
            continue;
        }
        let mut best: f32 = 0.0;
        for j in 0..ma_count {
            let ma = ma_nums[j];
            let diff = (ma - gt).abs();
            let rel = diff / gt.abs();
            let s: f32 = if rel <= 0.001 {
                1.0
            } else if rel <= 0.01 {
                0.95
            } else if rel <= 0.05 {
                0.80
            } else if rel <= 0.10 {
                0.50
            } else {
                0.20
            };
            if s > best {
                best = s;
            }
        }
        total += best;
        matched += 1;
    }
    if matched == 0 {
        return 0.0;
    }
    total / matched as f32
}

/// Extract 0x-prefixed hex addresses (40 hex chars) from a string.
fn find_addresses<'a>(text: &'a str, out: &mut [&'a str; 32]) -> usize {
    let bytes = text.as_bytes();
    let mut count = 0;
    let mut i = 0;
    while i + 42 <= bytes.len() && count < 32 {
        if bytes[i] == b'0' && bytes[i + 1] == b'x' {
            let mut ok = true;
            for k in 2..42 {
                if !bytes[i + k].is_ascii_hexdigit() {
                    ok = false;
                    break;
                }
            }
            if ok {
                out[count] = &text[i..i + 42];
                count += 1;
                i += 42;
                continue;
            }
        }
        i += 1;
    }
    count
}

fn eq_case_insensitive(a: &str, b: &str) -> bool {
    if a.len() != b.len() {
        return false;
    }
    for (x, y) in a.bytes().zip(b.bytes()) {
        if x.to_ascii_lowercase() != y.to_ascii_lowercase() {
            return false;
        }
    }
    true
}

/// Score wallet address matches. Addresses are all-or-nothing — a wrong character = 0
/// for that address.
fn address_accuracy(ground_truth: &str, miner_answer: &str) -> f32 {
    let mut gt_addrs: [&str; 32] = [""; 32];
    let mut ma_addrs: [&str; 32] = [""; 32];
    let gt_count = find_addresses(ground_truth, &mut gt_addrs);
    let ma_count = find_addresses(miner_answer, &mut ma_addrs);

    if gt_count == 0 {
        return 0.5; // no addresses in ground truth — neutral
    }

    let mut hits = 0usize;
    for i in 0..gt_count {
        let gt = gt_addrs[i];
        for j in 0..ma_count {
            if eq_case_insensitive(gt, ma_addrs[j]) {
                hits += 1;
                break;
            }
        }
    }
    hits as f32 / gt_count as f32
}

/// Reward miners that include the JSON keys we expect for gambling intelligence
/// responses. Detects on textual presence — a proper JSON parser would blow the WASM
/// budget for a marginal gain.
fn field_completeness(_ground_truth: &str, miner_answer: &str) -> f32 {
    const KEYS: [&str; 10] = [
        "\"deposits_usd\"",
        "\"withdrawals_usd\"",
        "\"net_flow_usd\"",
        "\"unique_depositors\"",
        "\"transaction_count\"",
        "\"casino\"",
        "\"chain\"",
        "\"timestamp\"",
        "\"confidence\"",
        "\"verdict\"",
    ];
    let mut hits = 0u32;
    for k in KEYS.iter() {
        if contains(miner_answer, k) {
            hits += 1;
        }
    }
    (hits as f32 / KEYS.len() as f32).min(1.0)
}

fn contains(haystack: &str, needle: &str) -> bool {
    let hb = haystack.as_bytes();
    let nb = needle.as_bytes();
    if nb.is_empty() || nb.len() > hb.len() {
        return nb.is_empty();
    }
    let last = hb.len() - nb.len();
    for start in 0..=last {
        if &hb[start..start + nb.len()] == nb {
            return true;
        }
    }
    false
}

/// Reward responses that look recent. Detects an ISO-8601 year in the timestamp field —
/// 2026 gets full credit, 2025 partial, older near-zero.
fn recency_score(_question: &str, miner_answer: &str) -> f32 {
    if contains(miner_answer, "\"2026") {
        1.0
    } else if contains(miner_answer, "\"2025") {
        0.6
    } else if contains(miner_answer, "\"2024") {
        0.3
    } else {
        0.5 // no timestamp — neutral, don't punish miners that don't include one
    }
}

// ---- Tests -------------------------------------------------------------------------
//
// Tests run under the host target so we can use std/println/etc. — the WASM build is
// still `#![no_std]`.

#[cfg(test)]
extern crate std;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_answer_scores_zero() {
        let s = score_composite("What is the top casino?", r#"{"deposits_usd": 1000000}"#, "");
        assert_eq!(s, 0.0);
    }

    #[test]
    fn exact_match_scores_one() {
        let gt = r#"{"deposits_usd": 5000000}"#;
        let s = score_composite("q", gt, gt);
        assert_eq!(s, 1.0);
    }

    #[test]
    fn close_number_scores_high() {
        let gt = r#"{"deposits_usd": 5000000, "timestamp": "2026-08-14T00:00:00Z", "confidence": 0.9}"#;
        let ma = r#"{"deposits_usd": 5001200, "timestamp": "2026-08-14T00:00:00Z", "confidence": 0.9}"#;
        let s = score_composite("q", gt, ma);
        assert!(s > 0.65, "expected > 0.65 got {}", s);
    }

    #[test]
    fn wrong_number_scores_low() {
        let gt = r#"{"deposits_usd": 5000000, "timestamp": "2026-08-14T00:00:00Z"}"#;
        let ma = r#"{"deposits_usd": 50, "timestamp": "2026-08-14T00:00:00Z"}"#;
        let s = score_composite("q", gt, ma);
        assert!(s < 0.6, "expected < 0.6 got {}", s);
    }

    #[test]
    fn correct_address_scores_higher_than_wrong() {
        let gt = r#"{"address": "0x974caa59e49682cda0ad2bbe82983419a2ecc400"}"#;
        let right = r#"{"address": "0x974caa59e49682cda0ad2bbe82983419a2ecc400", "timestamp": "2026-08-14T00:00:00Z"}"#;
        let wrong = r#"{"address": "0x0000000000000000000000000000000000000000", "timestamp": "2026-08-14T00:00:00Z"}"#;
        let sr = score_composite("q", gt, right);
        let sw = score_composite("q", gt, wrong);
        assert!(sr > sw, "correct ({}) should beat wrong ({})", sr, sw);
    }

    #[test]
    fn checksum_case_insensitive_address_match() {
        let gt = r#"{"address": "0x974caa59e49682cda0ad2bbe82983419a2ecc400"}"#;
        let ma_upper = r#"{"address": "0x974CAA59E49682CDA0AD2BBE82983419A2ECC400", "timestamp": "2026-08-14T00:00:00Z"}"#;
        let s = score_composite("q", gt, ma_upper);
        assert!(s > 0.4, "expected checksum-insensitive match got {}", s);
    }

    #[test]
    fn old_data_penalized_via_recency() {
        let gt = r#"{"deposits_usd": 1000, "timestamp": "2026-08-14T00:00:00Z"}"#;
        let old = r#"{"deposits_usd": 1000, "timestamp": "2024-01-01T00:00:00Z"}"#;
        let fresh = r#"{"deposits_usd": 1000, "timestamp": "2026-08-14T00:00:00Z"}"#;
        assert!(score_composite("q", gt, fresh) > score_composite("q", gt, old));
    }

    #[test]
    fn completeness_helps_when_numbers_match() {
        let gt = r#"{"deposits_usd": 1000000, "withdrawals_usd": 200000, "net_flow_usd": 800000, "unique_depositors": 500, "transaction_count": 1200, "confidence": 0.9, "verdict": "healthy", "casino": "stake", "chain": "ethereum", "timestamp": "2026-08-14T00:00:00Z"}"#;
        let full = gt;
        let sparse = r#"{"deposits_usd": 1000000, "timestamp": "2026-08-14T00:00:00Z"}"#;
        let sf = score_composite("q", gt, full);
        let ss = score_composite("q", gt, sparse);
        assert!(sf >= ss, "full ({}) should be >= sparse ({})", sf, ss);
    }
}
