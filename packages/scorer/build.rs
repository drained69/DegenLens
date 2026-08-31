use std::env;

fn main() {
    println!("cargo:rerun-if-env-changed=SCORER_INTENT");
    for cfg in ["intent_otx", "intent_fraud", "intent_research", "intent_text", "intent_web"] {
        println!("cargo:rustc-check-cfg=cfg({cfg})");
    }
    let intent = env::var("SCORER_INTENT").unwrap_or_else(|_| "ONCHAIN_TX_LOOKUP".to_owned());
    let cfg = match intent.as_str() {
        "ONCHAIN_TX_LOOKUP" => "intent_otx",
        "FRAUD_DETECTION" => "intent_fraud",
        "RESEARCH_SYNTHESIS" => "intent_research",
        "TEXT_GENERATION" => "intent_text",
        "WEB_SEARCH" => "intent_web",
        other => panic!("unsupported SCORER_INTENT: {other}"),
    };
    println!("cargo:rustc-cfg={cfg}");
}
