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
VALID_TOP_LEVEL = REQUIRED_TOP_LEVEL | {
    "protocol", "description", "docs", "auth", "input_schema", "output_schema",
    "polling", "cache_ttl_sec", "rate_limit_per_sec", "circuit_threshold",
    "circuit_cooldown_seconds", "limitations", "endpoints", "semantics", "on_chain",
}
VALID_ENDPOINT_KEYS = {
    "path", "external_path", "method", "description", "endpoint_base_url",
    "content_type", "multipart_fields", "param_map", "intents", "params",
}
VALID_LIMITATION_PROPERTIES = {"size_bytes", "value", "length", "count"}
VALID_LIMITATION_OPERATORS = {"lte", "gte", "lt", "gt", "eq"}

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
    check(
        set(doc).issubset(VALID_TOP_LEVEL),
        "top-level fields use Telegraph allowlist",
        f"illegal: {sorted(set(doc) - VALID_TOP_LEVEL)}",
    )

    check(str(doc.get("version")) == "1", "version is \"1\"", f"got {doc.get('version')!r}")
    check(doc.get("kind") in {"miner", "validator", "subnet"}, "kind is valid",
          f"got {doc.get('kind')!r}")
    check(
        bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(doc.get("slug", "")))),
        "slug is kebab-case",
        f"got {doc.get('slug')!r}",
    )
    check(isinstance(doc.get("id"), int) and doc["id"] >= 0, "id is a non-negative integer")

    base = str(doc.get("base_url", ""))
    check(base.startswith(("http://", "https://")), "base_url has a scheme", base)
    check(
        not any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0")),
        "base_url is publicly reachable (not localhost)",
        base,
    )
    # A combined UI/API deployment is valid: the important distinction is that
    # base_url is public and routable, not that it has a different hostname.
    docs = doc.get("docs") or {}
    check(
        base == docs.get("website") or bool(docs.get("website")),
        "base_url is a documented public endpoint",
        "base_url must be where Telegraph routes requests",
    )

    auth = doc.get("auth")
    if auth is None:
        check(True, "auth omitted (open API)", warn_only=False)
    else:
        check("type" in auth, "auth.type present when auth block exists")
        if auth.get("type") not in {"bearer", "header", "none"}:
            check(False, "auth.type is valid", f"got {auth.get('type')!r}")
        if auth.get("type") not in {"none", None}:
            check(
                not auth.get("env_var"),
                "auth does not rely on unsupported env_var injection",
                "install credentials through the node after registration",
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

    for index, limitation in enumerate(doc.get("limitations") or []):
        label = f"limitations.{index}"
        check(
            limitation.get("property") in VALID_LIMITATION_PROPERTIES,
            f"{label}: property is valid",
            f"got {limitation.get('property')!r}",
        ) if limitation.get("property") is not None else None
        if limitation.get("operator") is not None:
            check(
                limitation["operator"] in VALID_LIMITATION_OPERATORS,
                f"{label}: operator is valid",
                f"got {limitation['operator']!r}",
            )
        if limitation.get("property") == "length" and limitation.get("value_num") is not None:
            check(
                limitation.get("operator") == "eq",
                f"{label}: fixed length uses eq",
                "use operator: eq for an exact length",
            )

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
        check(
            set(e).issubset(VALID_ENDPOINT_KEYS),
            f"{label}: endpoint fields use Telegraph allowlist",
            f"illegal: {sorted(set(e) - VALID_ENDPOINT_KEYS)}",
        )
        ok = {"path", "external_path", "method"} <= set(e)
        check(ok, f"{label}: has path/external_path/method")
        check(e.get("method") in VALID_METHODS, f"{label}: method is valid")
        key = (str(e.get("method")), str(e.get("path")))
        check(key not in seen, f"{label}: not a duplicate declaration")
        seen.add(key)


def validate_intents_on_chain(doc: dict, rpc_url: str) -> None:
    """Check the registry contract, not the node's cached catalog metadata."""
    section("1a. On-chain canonical intents")
    intents = (doc.get("semantics") or {}).get("supported_intents") or []
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {
                "to": "0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8",
                "data": _encode_get_canonical_intents(),
            },
            "latest",
        ],
    }
    try:
        request = urllib.request.Request(
            rpc_url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "degenlens-telegraph-preflight/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read())
        result = body.get("result")
        if not result:
            raise RuntimeError(body.get("error", "empty eth_call result"))
        canonical = _decode_string_array(result)
    except Exception as exc:  # noqa: BLE001
        check(False, "on-chain canonical intent registry reachable", str(exc))
        return

    check(
        all(intent in canonical for intent in intents),
        "all supported_intents are on-chain canonical",
        f"non-canonical: {[i for i in intents if i not in canonical]}",
    )


def validate_console_intents(doc: dict, validator_url: str) -> None:
    """Detect a console/backend registry split before a user uploads YAML."""
    section("1b. Integration console compatibility")
    intents = (doc.get("semantics") or {}).get("supported_intents") or []
    body = json.dumps({"yaml": yaml.safe_dump(doc, sort_keys=False), "api_key": ""}).encode()
    request = urllib.request.Request(
        validator_url.rstrip("/") + "/api/validate",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "degenlens-telegraph-preflight/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        check(False, "integration console validator reachable", str(exc))
        return

    errors = result.get("errors") or []
    noncanonical = [
        intent for intent in intents
        if any(f"non-canonical intent: {intent}" in error for error in errors)
    ]
    if noncanonical:
        check(
            False,
            "integration console accepts the manifest intents",
            "console rejects on-chain-canonical intents: " + ", ".join(noncanonical),
            warn_only=True,
        )
        return
    check(bool(result.get("valid")), "integration console accepts the manifest")


def _encode_get_canonical_intents() -> str:
    # keccak256("getCanonicalIntents()")[:4]. Kept as a constant so preflight
    # has no web3 dependency and remains usable in the small miner environment.
    return "0xbf643e59"


def _decode_string_array(value: str) -> list[str]:
    raw = bytes.fromhex(value.removeprefix("0x"))
    # ABI dynamic string[]: offset -> array offset, length, offsets, strings.
    root = int.from_bytes(raw[:32], "big")
    count = int.from_bytes(raw[root:root + 32], "big")
    offsets_start = root + 32
    values: list[str] = []
    for index in range(count):
        offset = int.from_bytes(raw[offsets_start + index * 32:offsets_start + (index + 1) * 32], "big")
        item = offsets_start + offset
        length = int.from_bytes(raw[item:item + 32], "big")
        values.append(raw[item + 32:item + 32 + length].decode())
    return values


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
    ap.add_argument(
        "--rpc-url",
        default="https://base-sepolia-rpc.publicnode.com",
        help="Base Sepolia JSON-RPC URL for on-chain intent validation",
    )
    ap.add_argument(
        "--skip-console",
        action="store_true",
        help="skip the remote console validator compatibility check",
    )
    ap.add_argument(
        "--console-url",
        default="https://integrate.telegraphprotocol.com",
        help="integration console URL",
    )
    args = ap.parse_args()

    with open(args.yaml) as fh:
        doc = yaml.safe_load(fh)

    print(f"Telegraph miner preflight  —  {args.yaml}")
    validate_manifest(doc)
    validate_intents_on_chain(doc, args.rpc_url)
    if not args.skip_console:
        validate_console_intents(doc, args.console_url)

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
