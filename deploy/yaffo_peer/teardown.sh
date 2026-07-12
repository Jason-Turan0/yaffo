#!/usr/bin/env bash
# Delete the yaffo-peer test VM. Everything on it (media, database, p2p
# identity) goes with it — remember to remove/revoke the peer from your
# local instance's Sharing tab afterwards, since it can never come back
# with the same device identity. The automation SSH key stays for next time.
#
# Usage (from deploy/yaffo_peer/): ./teardown.sh
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

exists() {
  gcloud compute "$1" describe "$2" "${@:3}" --project "$PROJECT" >/dev/null 2>&1
}

subnet_exists() {
  gcloud compute networks subnets describe "$SUBNET_NAME" \
    --region "$REGION" --project "$PROJECT" >/dev/null 2>&1
}

DELETED=false

for vm in "$VM_NAME" "$NAT_VM_NAME"; do
  if exists instances "$vm" --zone "$ZONE"; then
    gcloud compute instances delete "$vm" --zone "$ZONE" --project "$PROJECT" --quiet
    DELETED=true
  fi
done

for route in "$NETWORK_NAME-via-nat" "$NETWORK_NAME-iap-direct"; do
  if exists routes "$route"; then
    gcloud compute routes delete "$route" --project "$PROJECT" --quiet
  fi
done

for rule in "$NETWORK_NAME-allow-iap-ssh" "$NETWORK_NAME-allow-internal"; do
  if exists firewall-rules "$rule"; then
    gcloud compute firewall-rules delete "$rule" --project "$PROJECT" --quiet
  fi
done

if subnet_exists; then
  gcloud compute networks subnets delete "$SUBNET_NAME" --region "$REGION" --project "$PROJECT" --quiet
fi

if exists networks "$NETWORK_NAME"; then
  gcloud compute networks delete "$NETWORK_NAME" --project "$PROJECT" --quiet
fi

if [[ "$DELETED" == true ]]; then
  echo "Deleted $VM_NAME NAT test stack. Revoke its entry on your local instance's Sharing tab."
else
  echo "No $VM_NAME instances found — cleaned up any leftover NAT resources."
fi
