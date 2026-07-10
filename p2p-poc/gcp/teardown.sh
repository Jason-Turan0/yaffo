#!/usr/bin/env bash
# Tears down the throwaway GCP test VM + firewall rule created for the
# public-IP pairing test. Does NOT touch the Cloud Run rendezvous service or
# its Firestore data — those are cheap/serverless and worth keeping.
#
# Usage: ./teardown.sh          # asks for confirmation
#        ./teardown.sh --yes    # skips confirmation (for scripting)

set -euo pipefail

PROJECT="gen-lang-client-0392476874"
ZONE="us-central1-a"
VM_NAME="p2p-poc-device-a"
FIREWALL_RULE="p2p-poc-device-allow"

if [[ "${1:-}" != "--yes" ]]; then
  echo "This will delete:"
  echo "  - VM:             $VM_NAME (zone $ZONE)"
  echo "  - Firewall rule:  $FIREWALL_RULE"
  echo "  (Cloud Run rendezvous + Firestore are left untouched.)"
  read -r -p "Proceed? [y/N] " confirm
  if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

echo "Deleting VM $VM_NAME..."
gcloud compute instances delete "$VM_NAME" --zone "$ZONE" --project "$PROJECT" --quiet

echo "Deleting firewall rule $FIREWALL_RULE..."
gcloud compute firewall-rules delete "$FIREWALL_RULE" --project "$PROJECT" --quiet

echo "Done. VM and firewall rule removed; rendezvous service is still live."
