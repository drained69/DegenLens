#!/usr/bin/env bash
# Post-deploy registration helper.
#
# The single most common way a Telegraph registration dies is a hash mismatch:
# the SHA-256 committed on-chain must equal the SHA-256 of the bytes actually
# SERVED at the YAML URL. Registration 127 of this miner was rejected for
# exactly that ("the document at the registered URL is not the one committed on
# chain"), and registration 167 is in the same drifted state right now.
#
# So this hashes what the deployment SERVES, never the local file.
set -euo pipefail

YAML_URL="${YAML_URL:-https://degenlensv1.up.railway.app/miner.yaml}"
NODE="${NODE:-https://devnode.telegraphprotocol.com}"
LOCAL="${LOCAL:-config/miner.yaml}"

say() { printf '%s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*" >&2; exit 1; }

say "1. Fetching served manifest from $YAML_URL"
served="$(mktemp)"; trap 'rm -f "$served"' EXIT
curl -fsS --max-time 30 "$YAML_URL" -o "$served" || fail "URL did not answer — deploy first"

served_hash="$(shasum -a 256 "$served" | awk '{print $1}')"
local_hash="$(shasum -a 256 "$LOCAL" | awk '{print $1}')"

say "   served sha256 : $served_hash"
say "   local  sha256 : $local_hash"
if [ "$served_hash" != "$local_hash" ]; then
  say ""
  say "   The URL is serving a different manifest than $LOCAL."
  say "   Two causes, both worth waiting out before spending gas:"
  say "     - the deploy has not finished yet;"
  say "     - the route sets 'Cache-Control: max-age=300', so a proxy can hold"
  say "       the previous manifest for up to 5 minutes. The node fetches this"
  say "       URL the same way, so the hash must be of what it will actually"
  say "       receive — do NOT cache-bust to force a match."
  fail "re-run in a minute; commit the manifest only once these two hashes agree"
fi
say "   OK: the deployment is serving exactly the manifest in $LOCAL"

say "2. Checking the served manifest parses and declares the intents"
python3 - "$served" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
allowed_ep = {"path","external_path","method","description","endpoint_base_url",
              "content_type","multipart_fields","param_map"}
for i, e in enumerate(d["endpoints"]):
    extra = set(e) - allowed_ep
    assert not extra, f"endpoints.{i} ({e.get('path')}): {sorted(extra)} not allowed"
intents = d["semantics"]["supported_intents"]
for intent in intents:
    assert any((e.get("description") or "").lstrip().startswith(intent)
               for e in d["endpoints"]), f"{intent} leads no endpoint description"
print(f"   OK: {len(d['endpoints'])} endpoints, intents {intents}")
PY

say "3. Confirming every intent is canonical on-chain"
DIAMOND="${DIAMOND:-0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8}"
RPC="${RPC:-https://base-sepolia-rpc.publicnode.com}"
for intent in ONCHAIN_TX_LOOKUP WALLET_BALANCE_CHECK FRAUD_DETECTION; do
  if command -v cast >/dev/null 2>&1; then
    res="$(cast call "$DIAMOND" 'isCanonicalIntent(string)(bool)' "$intent" --rpc-url "$RPC" 2>/dev/null || echo unknown)"
    [ "$res" = "true" ] || fail "$intent is not canonical on-chain (got: $res)"
    say "   OK: $intent canonical"
  fi
done

say
say "4. Send this transaction (it needs YOUR key — run it yourself):"
say
cat <<TX
export DIAMOND=0x5a2324aA18613FAD4e44bDF0d6c73Ec1f6D87ff8
export RPC=https://base-sepolia-rpc.publicnode.com
export MINER_PRIVATE_KEY=0x<the wallet that owns degenlens-onchain>

cast send "\$DIAMOND" \\
  "updateMiner(uint256,string,bytes32,address,uint256,string[])" \\
  167 \\
  "$YAML_URL" \\
  "0x$served_hash" \\
  0xdde7b987a01717eefcca1dc5280c164e2ccd133e \\
  10000 \\
  '["ONCHAIN_TX_LOOKUP","WALLET_BALANCE_CHECK","FRAUD_DETECTION"]' \\
  --rpc-url "\$RPC" --private-key "\$MINER_PRIVATE_KEY"
TX
say
say "5. Then confirm activation with the NEW registrationId from the receipt:"
say "   curl -s $NODE/api/miners/<newRegistrationId> | jq '.miner | {activation_status, rejection_reason}'"
say "   Expect activation_status \"active\". Anything else prints the exact cause."
