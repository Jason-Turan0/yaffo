#!/usr/bin/env bash
# Tears down everything nat-punch-setup.sh creates: both VMs, both Cloud NAT
# gateways + their Cloud Routers, and the IAP SSH firewall rule. Does NOT
# touch the separate public-IP device-a VM, the Cloud Run rendezvous
# service, or its Firestore data — those belong to the earlier public-IP
# deployment (see gcp/README.md) and are unrelated to this NAT-punch setup.
#
# Usage: ./gcp/nat-punch-teardown.sh          # asks for confirmation
#        ./gcp/nat-punch-teardown.sh --yes    # skips confirmation

set -euo pipefail

PROJECT="gen-lang-client-0392476874"
FIREWALL_TAG_RULE="p2p-poc-nat-allow-iap-ssh"

DEVICE_A_ZONE="us-central1-a"; DEVICE_A_VM="p2p-poc-nat-device-a"
DEVICE_A_REGION="us-central1"; DEVICE_A_ROUTER="p2p-poc-nat-router"; DEVICE_A_GATEWAY="p2p-poc-nat-gateway"

DEVICE_B_ZONE="us-east1-b"; DEVICE_B_VM="p2p-poc-nat-device-b2"
DEVICE_B_REGION="us-east1"; DEVICE_B_ROUTER="p2p-poc-nat-router-east"; DEVICE_B_GATEWAY="p2p-poc-nat-gateway-east"

if [[ "${1:-}" != "--yes" ]]; then
  echo "This will delete:"
  echo "  - VMs:            $DEVICE_A_VM ($DEVICE_A_ZONE), $DEVICE_B_VM ($DEVICE_B_ZONE)"
  echo "  - NAT gateways:   $DEVICE_A_GATEWAY, $DEVICE_B_GATEWAY"
  echo "  - Cloud Routers:  $DEVICE_A_ROUTER, $DEVICE_B_ROUTER"
  echo "  - Firewall rule:  $FIREWALL_TAG_RULE"
  echo "  (The separate public-IP device-a VM and Cloud Run rendezvous are left untouched.)"
  read -r -p "Proceed? [y/N] " confirm
  if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

delete_if_exists() {
  local kind="$1"; shift
  echo "Deleting $kind: $*"
  gcloud compute "$kind" delete "$@" --project="$PROJECT" --quiet 2>&1 | grep -v "^$" || true
}

if gcloud compute instances describe "$DEVICE_A_VM" --zone="$DEVICE_A_ZONE" --project="$PROJECT" >/dev/null 2>&1; then
  delete_if_exists instances "$DEVICE_A_VM" --zone="$DEVICE_A_ZONE"
fi
if gcloud compute instances describe "$DEVICE_B_VM" --zone="$DEVICE_B_ZONE" --project="$PROJECT" >/dev/null 2>&1; then
  delete_if_exists instances "$DEVICE_B_VM" --zone="$DEVICE_B_ZONE"
fi

if gcloud compute routers nats describe "$DEVICE_A_GATEWAY" --router="$DEVICE_A_ROUTER" --region="$DEVICE_A_REGION" --project="$PROJECT" >/dev/null 2>&1; then
  echo "Deleting NAT gateway $DEVICE_A_GATEWAY..."
  gcloud compute routers nats delete "$DEVICE_A_GATEWAY" --router="$DEVICE_A_ROUTER" --region="$DEVICE_A_REGION" --project="$PROJECT" --quiet
fi
if gcloud compute routers describe "$DEVICE_A_ROUTER" --region="$DEVICE_A_REGION" --project="$PROJECT" >/dev/null 2>&1; then
  delete_if_exists routers "$DEVICE_A_ROUTER" --region="$DEVICE_A_REGION"
fi

if gcloud compute routers nats describe "$DEVICE_B_GATEWAY" --router="$DEVICE_B_ROUTER" --region="$DEVICE_B_REGION" --project="$PROJECT" >/dev/null 2>&1; then
  echo "Deleting NAT gateway $DEVICE_B_GATEWAY..."
  gcloud compute routers nats delete "$DEVICE_B_GATEWAY" --router="$DEVICE_B_ROUTER" --region="$DEVICE_B_REGION" --project="$PROJECT" --quiet
fi
if gcloud compute routers describe "$DEVICE_B_ROUTER" --region="$DEVICE_B_REGION" --project="$PROJECT" >/dev/null 2>&1; then
  delete_if_exists routers "$DEVICE_B_ROUTER" --region="$DEVICE_B_REGION"
fi

if gcloud compute firewall-rules describe "$FIREWALL_TAG_RULE" --project="$PROJECT" >/dev/null 2>&1; then
  delete_if_exists firewall-rules "$FIREWALL_TAG_RULE"
fi

echo "Done. NAT-punch infrastructure removed; public-IP device-a and rendezvous are still live."
