#!/usr/bin/env python3
"""Preflight check for Telegraph miner registration.

Mirrors what integrate.telegraphprotocol.com does when you paste the YAML:
validates the manifest schema, then sandbox-tests every declared endpoint
against the live base_url.

Run this BEFORE submitting. Registration writes to the registry contract and is
immutable — a failed endpoint or a broken contract cannot be edited afterwards,
only re-registered as a new miner.

    python3 scripts/preflight.py [--yaml config/miner.yaml] [--base-url URL]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("pyyaml is required:  pip install pyyaml")


GREEN, RED, AMBER, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
PASS, FAIL, WARN = f"{GREEN}PASS{RESET}", f"{RED}FAIL{RESET}", f"{AMBER}WARN{RESET}"

VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
VALID_SIGNAL_KEYS = {"confidence_field", "label_field", "reason_field"}
REQUIRED_TOP_LEVEL = {"version", "kind", "id", "slug", "name", "base_url"}

failures: list[str] = []
warnings: list[str] = []


def check(ok: bool, label: str, detail: str = "", *, warn_only: bool = False) -> bool:
    if ok:
        print(f"  {PASS}  {label}")
    elif warn_only:
        print(f"  {WARN}  {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
        warnings.append(f"{label}: {detail}")
    else:
        print(f"  {FAIL}  {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
        failures.append(f"{label}: {detail}")
    return ok


def section(title: str) -> None:
    print(f"\n{title}")
    print("─" * max(len(title), 40))


# ── Manifest validation ──────────────────────────────────────────────────────


def validate_manifest(doc: dict) -> None:
    section("1. Manifest schema")

    missing = REQUIRED_TOP_LEVEL - set(doc)
    check(not missing, "required top-level fields present", f"missing {sorted(missing)}")

    check(str(doc.get("version")) == "1", "version is \"1\"", f"got {doc.get('version')!r}")
    check(doc.get("kind") in {"miner", "validator", "subnet"}, "kind is valid",
          f"got {doc.get('kind')!r}")
    check(
        bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(doc.get("slug", "")))),
        "slug is kebab-case",
        f"got {doc.get('slug')!r}",
    )

    base = str(doc.get("base_url", ""))
    check(base.startswith(("http://", "https://")), "base_url has a scheme", base)
    check(
        not any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0")),
        "base_url is publicly reachable (not localhost)",
        base,
    )
    # A common mistake the Telegraph team calls out explicitly.
    docs = doc.get("docs") or {}
    check(
        base != docs.get("website"),
        "base_url is the API endpoint, not the project website",
        "base_url must be where Telegraph routes requests",
    )

    auth = doc.get("auth")
    if auth is None:
        check(True, "auth omitted (open API)", warn_only=False)
    else:
        check("type" in auth, "auth.type present when auth block exists")
        if auth.get("type") != "none":
            check(
                bool(auth.get("env_var")),
                "auth.env_var set for credentialed API",
                "never inline the key itself",
            )

    sem = doc.get("semantics") or {}
    mapping = sem.get("signal_mapping") or {}
    check(
        set(mapping).issubset(VALID_SIGNAL_KEYS),
        "signal_mapping uses only allowed keys",
        f"illegal: {sorted(set(mapping) - VALID_SIGNAL_KEYS)}",
    )
    intents = sem.get("supported_intents") or []
    check(bool(intents), "at least one supported_intent declared")

    check("on_chain" in doc or True, "on_chain block is optional", warn_only=False)
    if "on_chain" in doc:
        check(
            bool(doc["on_chain"].get("transform")),
            "on_chain.transform present when on_chain block exists",
            "transform is mandatory once the block is declared",
        )

    endpoints = doc.get("endpoints") or []
    check(bool(endpoints), "endpoints declared")
    seen: set[tuple[str, str]] = set()
    for e in endpoints:
        label = f"{e.get('method')} {e.get('path')}"
        ok = {"path", "external_path", "method"} <= set(e)
        check(ok, f"{label}: has path/external_path/method")
        check(e.get("method") in VALID_METHODS, f"{label}: method is valid")
        key = (str(e.get("method")), str(e.get("path")))
        check(key not in seen, f"{label}: not a duplicate declaration")
        seen.add(key)


# ── Live endpoint checks ─────────────────────────────────────────────────────


def sample_body(path: str) -> dict:
    """Representative payload per endpoint, mirroring the YAML examples."""
    if "casino/stats" in path:
        return {"slug": "stake", "hours": 24}
    if "transaction/lookup" in path:
        return {
            "tx_hash": "0x" + "0" * 64,
            "chain": "ethereum",
        }
    if "player/evaluate" in path:
        return {"address": "0x" + "11" * 20, "chain": "ethereum", "hours": 24}
    if "wallet" in path or "anomaly" in path:
        return {"address": "0x" + "11" * 20, "chain": "ethereum", "hours": 24}
    return {}


def call(url: str, method: str, body: dict | None, timeout: int = 90):
    data = json.dumps(body).encode() if body is not None and method != "GET" else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read())
    return r.status, payload, (time.perf_counter() - started) * 1000


def validate_live(doc: dict, base_url: str) -> None:
    section(f"2. Live endpoints — {base_url}")

    mapping = (doc.get("semantics") or {}).get("signal_mapping") or {}
    required_fields = list(mapping.values())

    for e in doc.get("endpoints") or []:
        path, method = e["external_path"], e["method"]
        # Path templates need a concrete value to be callable.
        probe = path.replace("{slug}", "stake")
        url = base_url.rstrip("/") + probe
        label = f"{method:4} {path}"

        try:
            status, payload, ms = call(url, method, sample_body(path))
        except urllib.error.HTTPError as ex:
            check(False, label, f"HTTP {ex.code}")
            continue
        except Exception as ex:  # noqa: BLE001
            check(False, label, f"{type(ex).__name__}: {ex}")
            continue

        if status != 200:
            check(False, label, f"HTTP {status}")
            continue

        missing = [f for f in required_fields if f not in payload]
        if missing:
            check(False, f"{label} ({ms:.0f}ms)", f"missing signal fields {missing}")
            continue

        conf = payload.get(mapping.get("confidence_field", "confidence"))
        if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
            check(False, f"{label} ({ms:.0f}ms)", f"confidence out of range: {conf!r}")
            continue

        slow = ms > 10_000
        check(
            not slow,
            f"{label} ({ms:.0f}ms)",
            "slower than 10s — risks node timeout" if slow else "",
            warn_only=True,
        ) if slow else check(True, f"{label} ({ms:.0f}ms)")


def validate_reliability(base_url: str) -> None:
    section("3. Reliability contract")

    # Garbage input must not 5xx — a throw is a failed answer.
    try:
        status, payload, _ = call(
            base_url.rstrip("/") + "/casino/stats", "POST", {"slug": "___nope___"}
        )
        check(status == 200, "unknown slug returns 200, not 5xx", f"HTTP {status}")
        check(
            payload.get("confidence") == 0.0,
            "unknown slug answers with confidence 0",
            f"got {payload.get('confidence')}",
        )
    except Exception as ex:  # noqa: BLE001
        check(False, "unknown slug handled", f"{type(ex).__name__}")

    for probe in ("/health", "/metrics"):
        try:
            status, payload, _ = call(base_url.rstrip("/") + probe, "GET", None)
            check(status == 200, f"{probe} reachable")
        except Exception as ex:  # noqa: BLE001
            check(False, f"{probe} reachable", f"{type(ex).__name__}", warn_only=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default="config/miner.yaml")
    ap.add_argument("--base-url", default=None, help="override the manifest base_url")
    ap.add_argument("--skip-live", action="store_true")
    args = ap.parse_args()

    with open(args.yaml) as fh:
        doc = yaml.safe_load(fh)

    print(f"Telegraph miner preflight  —  {args.yaml}")
    validate_manifest(doc)

    if not args.skip_live:
        base = args.base_url or doc.get("base_url", "")
        if base:
            validate_live(doc, base)
            validate_reliability(base)

    section("Result")
    if failures:
        print(f"  {RED}{len(failures)} blocking issue(s){RESET} — do NOT register yet:")
        for f in failures:
            print(f"    · {f}")
    if warnings:
        print(f"  {AMBER}{len(warnings)} warning(s){RESET}:")
        for w in warnings:
            print(f"    · {w}")
    if not failures:
        print(f"  {GREEN}Ready to submit at integrate.telegraphprotocol.com{RESET}")
        print(f"  {DIM}Registration is immutable — this is your last check.{RESET}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
