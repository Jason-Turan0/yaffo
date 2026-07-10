#!/usr/bin/env bash
# Opens a tier2 device's browser UI on your laptop through an IAP TCP
# tunnel — the device VMs have no external IP, so this is the only way to
# reach their HTTPS pages. The tunnel stays open in the foreground; Ctrl+C
# closes it.
#
# Usage:
#   ./gcp/tier2-ui.sh            # device a on https://localhost:8443
#   ./gcp/tier2-ui.sh b 8444     # device b on https://localhost:8444
#
# Open both (two terminals, ports 8443 and 8444) to walk the pairing flow
# entirely in the browser: generate a code in one tab, paste it in the
# other. The self-signed-cert warning is expected — that's the whole
# design (trust is fingerprint pinning, not a CA).
#
# Requires the firewall to allow tcp:8001 from IAP's range — created by
# tier2-setup.sh; this script refreshes the rule in case the stack predates
# the UI port being added.

set -euo pipefail

PROJECT="gen-lang-client-0392476874"
ZONE="us-central1-a"
PREFIX="p2p-tier2"
DEVICE_PORT=8001

SIDE="${1:-a}"
LOCAL_PORT="${2:-8443}"

if [[ "$SIDE" != "a" && "$SIDE" != "b" ]]; then
  echo "usage: $0 [a|b] [local_port]" >&2
  exit 1
fi

# Make sure the UI port is open from IAP's range (idempotent; stacks built
# by older tier2-setup.sh versions only opened tcp:22).
gcloud compute firewall-rules update "$PREFIX-$SIDE-allow-iap-ssh" \
  --project="$PROJECT" --rules="tcp:22,tcp:$DEVICE_PORT" --quiet >/dev/null 2>&1 || true

echo "Tunneling device $SIDE -> https://localhost:$LOCAL_PORT  (Ctrl+C to close)"
echo "Your browser will warn about the self-signed certificate — expected."
exec gcloud compute start-iap-tunnel "$PREFIX-device-$SIDE" "$DEVICE_PORT" \
  --local-host-port="localhost:$LOCAL_PORT" \
  --zone="$ZONE" --project="$PROJECT"
