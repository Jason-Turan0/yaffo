#!/usr/bin/env bash
# Deletes everything tier2-setup.sh created (instances, routes, firewall
# rules, subnets, networks). Safe to re-run; skips anything already gone.
# The stack holds no real data — it is meant to be destroyed after every
# test run, including failed ones.
#
# Usage: ./gcp/tier2-teardown.sh

set -euo pipefail

PROJECT="gen-lang-client-0392476874"
REGION="us-central1"
ZONE="us-central1-a"
PREFIX="p2p-tier2"

delete() {
  # $1 may be a multi-word gcloud group ("networks subnets") — expand unquoted.
  local kind="$1" name="$2"
  shift 2
  if gcloud compute $kind describe "$name" "$@" --project="$PROJECT" >/dev/null 2>&1; then
    echo "  -> deleting $kind $name..."
    gcloud compute $kind delete "$name" "$@" --project="$PROJECT" --quiet
  else
    echo "  -> $kind $name already gone"
  fi
}

echo "Deleting instances..."
for vm in "$PREFIX-hub" "$PREFIX-device-a" "$PREFIX-device-b" "$PREFIX-nat-a" "$PREFIX-nat-b"; do
  delete instances "$vm" --zone="$ZONE"
done

echo "Deleting routes..."
for home in a b; do
  delete routes "$PREFIX-$home-via-nat"
  delete routes "$PREFIX-$home-iap-direct"
done

echo "Deleting firewall rules..."
for home in a b; do
  delete firewall-rules "$PREFIX-$home-allow-iap-ssh"
  delete firewall-rules "$PREFIX-$home-allow-internal"
done
delete firewall-rules "$PREFIX-hub-ingress"
delete firewall-rules "$PREFIX-hub-iap-ssh"

echo "Deleting subnets and networks..."
for home in a b; do
  delete "networks subnets" "$PREFIX-$home-subnet" --region="$REGION"
  delete networks "$PREFIX-$home"
done

echo "Tier-2 stack torn down."
