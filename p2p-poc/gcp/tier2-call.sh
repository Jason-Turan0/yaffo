#!/usr/bin/env bash
# Drives one relay-first call across the tier2 stack: device A calls
# device B through the hub. Because coordination now flows over the hub's
# WebSocket, this is ONE command — no more firing two curls in separate
# terminals within seconds of each other (the old /api/punch dance).
#
# Usage: ./gcp/tier2-call.sh   (after ./gcp/tier2-setup.sh)
#
# Reading the report:
#   "relay":  {"ok": true}   the call connected through the hub relay —
#                            expected to ALWAYS work while both are online
#   "punch":  {"ok": ...}    whether the hole punch landed
#   "direct": {"ok": ...}    whether the pinned QUIC ping worked directly
#   "path":   "direct"       upgraded (Tailscale-style)
#             "relay"        stayed relayed — expected when EITHER side's
#                            NAT profile is "hard" (see tier2-setup.sh's
#                            outcome notes: port-restricted x symmetric
#                            is the classic untraversable pairing)

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

echo "Looking up device IDs..."
ID_A=$(run_on "$PREFIX-device-a" "curl -sk https://127.0.0.1:$DEVICE_PORT/api/device-info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["device_id"])')
ID_B=$(run_on "$PREFIX-device-b" "curl -sk https://127.0.0.1:$DEVICE_PORT/api/device-info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["device_id"])')
echo "  device A: $ID_A"
echo "  device B: $ID_B"

echo "Device A calling device B (relay first, then punch upgrade)..."
run_on "$PREFIX-device-a" \
  "curl -sk -X POST https://127.0.0.1:$DEVICE_PORT/api/call -H 'Content-Type: application/json' -d '{\"peer_device_id\":\"$ID_B\"}' --max-time 60" \
  | python3 -m json.tool
