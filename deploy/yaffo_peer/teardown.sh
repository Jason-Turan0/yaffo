#!/usr/bin/env bash
# Delete the yaffo-peer test VM. Everything on it (media, database, p2p
# identity) goes with it — remember to remove/revoke the peer from your
# local instance's Sharing tab afterwards, since it can never come back
# with the same device identity. The automation SSH key stays for next time.
#
# Usage (from deploy/yaffo_peer/): ./teardown.sh
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

if ! vm_exists; then
  echo "VM $VM_NAME does not exist — nothing to tear down."
  exit 0
fi

gcloud compute instances delete "$VM_NAME" --zone "$ZONE" --project "$PROJECT" --quiet
echo "Deleted $VM_NAME. Revoke its entry on your local instance's Sharing tab."
