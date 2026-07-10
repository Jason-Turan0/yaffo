#!/usr/bin/env bash
# Drives the full pairing flow across the tier2 stack: device A generates
# a pairing code (the out-of-band trust anchor a human would carry as
# text/QR — here the script plays the human), device B accepts it, and the
# confirm exchange rides the relay-first call flow through the hub. Works
# regardless of NAT profiles — pairing needs no punchable path, just the
# relay. Afterwards both devices list each other as trusted.
#
# Usage: ./gcp/tier2-pair.sh   (after ./gcp/tier2-setup.sh)
#
# Note: a pairing code is single-use and expires after 5 minutes; re-run
# this script to pair again (re-pairing an already-known device just
# refreshes the entry).

set -euo pipefail

PROJECT="gen-lang-client-0392476874"
ZONE="us-central1-a"
PREFIX="p2p-tier2"
DEVICE_PORT=8001

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_OPTS=(--tunnel-through-iap --ssh-key-file="$SCRIPT_DIR/p2p_poc_gce_key")

run_on() {
  local vm="$1" command="$2"
  gcloud compute ssh "$vm" --zone="$ZONE" --project="$PROJECT" "${SSH_OPTS[@]}" \
    --command="$command" --quiet 2>/dev/null
}

echo "Generating a pairing code on device A..."
CODE=$(run_on "$PREFIX-device-a" "curl -sk -X POST https://127.0.0.1:$DEVICE_PORT/api/pairing/generate" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')
echo "  code: ${CODE:0:40}... (base64, expires in 5 min)"

echo "Accepting the code on device B (confirm exchange via hub relay)..."
run_on "$PREFIX-device-b" \
  "curl -sk -X POST https://127.0.0.1:$DEVICE_PORT/api/pairing/accept -H 'Content-Type: application/json' -d '{\"code\":\"$CODE\"}' --max-time 60" \
  | python3 -m json.tool

echo
echo "Known devices on A:"
run_on "$PREFIX-device-a" "curl -sk https://127.0.0.1:$DEVICE_PORT/api/known-devices" | python3 -m json.tool
echo "Known devices on B:"
run_on "$PREFIX-device-b" "curl -sk https://127.0.0.1:$DEVICE_PORT/api/known-devices" | python3 -m json.tool
