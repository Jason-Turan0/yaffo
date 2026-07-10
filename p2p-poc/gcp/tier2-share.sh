#!/usr/bin/env bash
# Drives the file-sharing flow across the tier2 stack: device B pulls
# device A's shared text files (5 random .txt seeded at device startup)
# over the hub relay. Requires the devices to be PAIRED first — run
# ./gcp/tier2-pair.sh once beforehand; an unpaired pull is refused by the
# serving device ("not a trusted device"), which you can also try by
# running this before pairing.
#
# Usage: ./gcp/tier2-share.sh   (after tier2-setup.sh and tier2-pair.sh)

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

echo "Device A's own shared files:"
run_on "$PREFIX-device-a" "curl -sk https://127.0.0.1:$DEVICE_PORT/api/shared-files" \
  | python3 -c "import json,sys; [print('  %s (%d bytes)' % (f['name'], f['size'])) for f in json.load(sys.stdin)['files']]"

echo "Looking up device A's ID..."
ID_A=$(run_on "$PREFIX-device-a" "curl -sk https://127.0.0.1:$DEVICE_PORT/api/device-info" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["device_id"])')
echo "  device A: $ID_A"

echo "Device B pulling device A's files via the hub relay..."
run_on "$PREFIX-device-b" \
  "curl -sk https://127.0.0.1:$DEVICE_PORT/api/peers/$ID_A/files --max-time 60" \
  | python3 -m json.tool
