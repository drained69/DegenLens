//! DegenLens WASM scoring module for Telegraph Protocol.
//!
//! Scores on-chain miner answers against ground truth for gambling-intelligence intents:
//!   `ONCHAIN_TX_LOOKUP`, `WALLET_BALANCE_CHECK`, `FRAUD_DETECTION`.
//!
//! Composite score (0..1):
//!   - 40%  numeric precision (relative error on numeric values)
//!   - 25%  textual similarity (token F1 against ground truth)
//!   - 20%  address accuracy (checksum-aware exact match on hex addresses)
//!   - 15%  ground-truth schema completeness
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
    if size > HEAP_SIZE {
        return 0;
    }
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
    if ptr < 0 || len <= 0 {
        return "";
    }
    let slice = core::slice::from_raw_parts(ptr as *const u8, len as usize);
    core::str::from_utf8(slice).unwrap_or("")
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
    let _question = read_str(q_ptr, q_len);
    let ground_truth = read_str(gt_ptr, gt_len);
    let miner_answer = read_str(ma_ptr, ma_len);
    if miner_answer.trim().is_empty() {
        return 0.0;
    }
    score_composite(_question, ground_truth, miner_answer)
}

// ---- Scoring -----------------------------------------------------------------------

/// Public helper so tests can call the scoring logic directly.
pub fn score_composite(_question: &str, ground_truth: &str, miner_answer: &str) -> f32 {
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
    let text = token_f1(ground_truth, miner_answer);

    let score = 0.40 * numeric + 0.25 * text + 0.20 * address + 0.15 * completeness;
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

/// Score numeric values using one-to-one nearest matches. This prevents one
/// repeated value from satisfying every expected value.
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
    let mut used = [false; 64];
    for i in 0..gt_count {
        let gt = gt_nums[i];
        let mut best: f32 = 0.0;
        let mut best_index = 0usize;
        let mut best_distance = f64::MAX;
        for j in 0..ma_count {
            if used[j] {
                continue;
            }
            let ma = ma_nums[j];
            let rel = if gt == 0.0 {
                (ma - gt).abs()
            } else {
                (ma - gt).abs() / gt.abs()
            };
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
            if s > best || (s == best && rel < best_distance) {
                best = s;
                best_index = j;
                best_distance = rel;
            }
        }
        if best > 0.0 {
            used[best_index] = true;
            total += best;
            matched += 1;
        }
    }
    if matched == 0 {
        return 0.0;
    }
    let recall = total / gt_count as f32;
    let precision = total / ma_count.max(1) as f32;
    (2.0 * recall * precision / (recall + precision)).max(0.0)
}

/// Compare answer text without allocating. JSON punctuation and casing do
/// not affect the result, while repeated tokens do not inflate the score.
fn token_f1(ground_truth: &str, miner_answer: &str) -> f32 {
    let mut gt_tokens = [""; 128];
    let mut answer_tokens = [""; 128];
    let gt_count = extract_tokens(ground_truth, &mut gt_tokens);
    let answer_count = extract_tokens(miner_answer, &mut answer_tokens);
    if gt_count == 0 || answer_count == 0 {
        return if gt_count == answer_count { 1.0 } else { 0.0 };
    }

    let mut gt_hits = [false; 128];
    let mut hits = 0usize;
    for i in 0..answer_count {
        for j in 0..gt_count {
            if !gt_hits[j] && eq_case_insensitive(answer_tokens[i], gt_tokens[j]) {
                gt_hits[j] = true;
                hits += 1;
                break;
            }
        }
    }
    let precision = hits as f32 / answer_count as f32;
    let recall = hits as f32 / gt_count as f32;
    if precision + recall == 0.0 {
        0.0
    } else {
        2.0 * precision * recall / (precision + recall)
    }
}

fn extract_tokens<'a>(text: &'a str, out: &mut [&'a str; 128]) -> usize {
    let bytes = text.as_bytes();
    let mut count = 0usize;
    let mut i = 0usize;
    while i < bytes.len() && count < out.len() {
        while i < bytes.len() && !bytes[i].is_ascii_alphanumeric() {
            i += 1;
        }
        let start = i;
        while i < bytes.len() && bytes[i].is_ascii_alphanumeric() {
            i += 1;
        }
        if i > start {
            out[count] = &text[start..i];
            count += 1;
        }
    }
    count
}

/// Extract 0x-prefixed hex addresses (40 hex chars) from a string.
fn find_addresses<'a>(text: &'a str, out: &mut [&'a str; 32]) -> usize {
    let bytes = text.as_bytes();
    let mut count = 0;
    let mut i = 0;
    while i + 42 <= bytes.len() && count < 32 {
        if bytes[i] == b'0' && (bytes[i + 1] == b'x' || bytes[i + 1] == b'X') {
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

/// Score only keys present in the ground truth, preventing fixed-schema
/// keyword stuffing from receiving credit.
fn field_completeness(ground_truth: &str, miner_answer: &str) -> f32 {
    let mut keys = [""; 64];
    let key_count = extract_json_keys(ground_truth, &mut keys);
    if key_count == 0 {
        return 0.5;
    }
    let mut hits = 0u32;
    for k in keys.iter().take(key_count) {
        if contains(miner_answer, k) {
            hits += 1;
        }
    }
    hits as f32 / key_count as f32
}

fn extract_json_keys<'a>(text: &'a str, out: &mut [&'a str; 64]) -> usize {
    let bytes = text.as_bytes();
    let mut count = 0usize;
    let mut i = 0usize;
    while i < bytes.len() && count < out.len() {
        if bytes[i] != b'"' {
            i += 1;
            continue;
        }
        let start = i;
        i += 1;
        while i < bytes.len() {
            if bytes[i] == b'"' && bytes[i - 1] != b'\\' {
                break;
            }
            i += 1;
        }
        if i >= bytes.len() {
            break;
        }
        let end = i + 1;
        i = end;
        while i < bytes.len() && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i < bytes.len() && bytes[i] == b':' {
            out[count] = &text[start..end];
            count += 1;
        }
    }
    count
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
        let s = score_composite(
            "What is the top casino?",
            r#"{"deposits_usd": 1000000}"#,
            "",
        );
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
        let gt =
            r#"{"deposits_usd": 5000000, "timestamp": "2026-08-14T00:00:00Z", "confidence": 0.9}"#;
        let ma =
            r#"{"deposits_usd": 5001200, "timestamp": "2026-08-14T00:00:00Z", "confidence": 0.9}"#;
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

    #[test]
    fn unrelated_timestamp_does_not_score_as_recent() {
        let gt = r#"{"value": 100, "timestamp": "2024-01-01T00:00:00Z"}"#;
        let answer = r#"{"value": 100, "timestamp": "2026-01-01T00:00:00Z"}"#;
        let no_timestamp = r#"{"value": 100}"#;
        assert!(score_composite("q", gt, answer) >= score_composite("q", gt, no_timestamp));
        assert!(score_composite("q", gt, answer) < 1.0);
    }

    #[test]
    fn repeated_number_cannot_satisfy_multiple_fields() {
        let gt = r#"{"in": 100, "out": 200, "net": -100}"#;
        let repeated = r#"{"in": 100, "out": 100, "net": 100}"#;
        let correct = r#"{"in": 100, "out": 200, "net": -100}"#;
        assert!(score_composite("q", gt, correct) > score_composite("q", gt, repeated));
    }

    #[test]
    fn textual_answers_are_scored_without_numbers() {
        let gt = r#"{"verdict": "healthy", "reasoning": "activity is consistent"}"#;
        let good = r#"{"verdict": "HEALTHY", "reasoning": "activity is consistent"}"#;
        let bad = r#"{"verdict": "fraud", "reasoning": "unrelated"}"#;
        assert!(score_composite("q", gt, good) > score_composite("q", gt, bad));
    }

    #[test]
    fn schema_stuffing_without_ground_truth_keys_gets_no_credit() {
        let gt = r#"{"value": 10}"#;
        let stuffed =
            r#"{"value": 10, "confidence": 1, "verdict": "healthy", "timestamp": "2026"}"#;
        let minimal = r#"{"value": 10}"#;
        assert!(score_composite("q", gt, minimal) >= score_composite("q", gt, stuffed));
    }
}
