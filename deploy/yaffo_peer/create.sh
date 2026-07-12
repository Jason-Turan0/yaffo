#!/usr/bin/env bash
# Create (or update) the yaffo-peer test VM: a full Yaffo instance on GCP
# for real-internet p2p testing against a local instance. Idempotent and
# doubles as the redeploy path — re-running pushes the current local
# working tree and restarts the service. First run also seeds the media
# dir with yaffo_ui_tests/test_data (SKIP_SEED=1 to skip).
#
# Usage (from deploy/yaffo_peer/):
#   ./create.sh
#   ./create.sh --nat-profile=HARD
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

NAT_PROFILE="PUNCHABLE"

usage() {
  cat <<USAGE
Usage:
  ./create.sh [--nat-profile PUNCHABLE|HARD]
  ./create.sh [--nat-profile=PUNCHABLE|HARD]

NAT profiles:
  PUNCHABLE  Plain iptables MASQUERADE. Source ports are preserved when
             available, matching a typical punchable home router. Default.
  HARD       MASQUERADE --random. Simulates endpoint-dependent mappings,
             so UDP hole punching should fail and Yaffo should stay relayed.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --nat-profile)
      if [[ $# -lt 2 ]]; then
        echo "--nat-profile requires PUNCHABLE or HARD." >&2
        usage >&2
        exit 1
      fi
      NAT_PROFILE="${2:-}"
      shift 2
      ;;
    --nat-profile=*)
      NAT_PROFILE="${1#*=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
done

NAT_PROFILE="$(printf '%s' "$NAT_PROFILE" | tr '[:lower:]' '[:upper:]')"
case "$NAT_PROFILE" in
  PUNCHABLE|HARD)
    ;;
  *)
    echo "Invalid --nat-profile '$NAT_PROFILE'; expected PUNCHABLE or HARD." >&2
    exit 1
    ;;
esac

NAT_PROFILE_LOWER="$(printf '%s' "$NAT_PROFILE" | tr '[:upper:]' '[:lower:]')"

exists() {
  gcloud compute "$1" describe "$2" "${@:3}" --project "$PROJECT" >/dev/null 2>&1
}

subnet_exists() {
  gcloud compute networks subnets describe "$SUBNET_NAME" \
    --region "$REGION" --project "$PROJECT" >/dev/null 2>&1
}

add_ssh_key() {
  local vm="$1"
  local keys_file
  keys_file=$(mktemp)
  echo "$SSH_USER:$(cat "$KEY_PUB")" > "$keys_file"
  gcloud compute instances add-metadata "$vm" \
    --zone "$ZONE" --project "$PROJECT" \
    --metadata-from-file ssh-keys="$keys_file" \
    --quiet
  rm -f "$keys_file"
}

ensure_network() {
  if exists networks "$NETWORK_NAME"; then
    echo "  -> network $NETWORK_NAME already exists"
  else
    echo "  -> creating network $NETWORK_NAME..."
    gcloud compute networks create "$NETWORK_NAME" \
      --project "$PROJECT" --subnet-mode=custom --quiet
  fi

  if subnet_exists; then
    echo "  -> subnet $SUBNET_NAME already exists"
  else
    gcloud compute networks subnets create "$SUBNET_NAME" \
      --project "$PROJECT" --network="$NETWORK_NAME" \
      --region "$REGION" --range "$SUBNET_RANGE" --quiet
  fi

  if ! exists firewall-rules "$NETWORK_NAME-allow-iap-ssh"; then
    gcloud compute firewall-rules create "$NETWORK_NAME-allow-iap-ssh" \
      --project "$PROJECT" --network "$NETWORK_NAME" \
      --direction=INGRESS --action=ALLOW \
      --rules=tcp:22 --source-ranges=35.235.240.0/20 --quiet
  fi

  if ! exists firewall-rules "$NETWORK_NAME-allow-internal"; then
    gcloud compute firewall-rules create "$NETWORK_NAME-allow-internal" \
      --project "$PROJECT" --network "$NETWORK_NAME" \
      --direction=INGRESS --action=ALLOW \
      --rules=all --source-ranges="$SUBNET_RANGE" --quiet
  fi
}

ensure_nat_vm() {
  if exists instances "$NAT_VM_NAME" --zone "$ZONE"; then
    echo "  -> NAT VM $NAT_VM_NAME already exists"
    return
  fi

  echo "  -> creating NAT VM $NAT_VM_NAME (profile: $NAT_PROFILE)..."
  local startup
  startup=$(mktemp)
  cat > "$startup" <<'STARTUP'
#!/bin/bash
set -e
sysctl -w net.ipv4.ip_forward=1
IFACE=$(ip -o -4 route show to default | awk '{print $5}')
PROFILE=$(curl -s -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/nat-profile || echo punchable)
iptables -t nat -F POSTROUTING
if [ "$PROFILE" = "hard" ]; then
  iptables -t nat -A POSTROUTING -o "$IFACE" -j MASQUERADE --random
else
  iptables -t nat -A POSTROUTING -o "$IFACE" -j MASQUERADE
fi
iptables -P FORWARD ACCEPT
STARTUP
  gcloud compute instances create "$NAT_VM_NAME" \
    --project "$PROJECT" --zone "$ZONE" \
    --machine-type "$NAT_MACHINE_TYPE" \
    --image-family "$IMAGE_FAMILY" --image-project "$IMAGE_PROJECT" \
    --network-interface="network=$NETWORK_NAME,subnet=$SUBNET_NAME" \
    --can-ip-forward \
    --metadata="nat-profile=$NAT_PROFILE_LOWER" \
    --metadata-from-file=startup-script="$startup" \
    --quiet
  rm -f "$startup"
  add_ssh_key "$NAT_VM_NAME"
}

ensure_routes() {
  if ! exists routes "$NETWORK_NAME-via-nat"; then
    gcloud compute routes create "$NETWORK_NAME-via-nat" \
      --project "$PROJECT" --network "$NETWORK_NAME" \
      --destination-range=0.0.0.0/0 --priority=800 \
      --next-hop-instance="$NAT_VM_NAME" --next-hop-instance-zone="$ZONE" \
      --tags="$DEVICE_TAG" --quiet
  fi

  if ! exists routes "$NETWORK_NAME-iap-direct"; then
    gcloud compute routes create "$NETWORK_NAME-iap-direct" \
      --project "$PROJECT" --network "$NETWORK_NAME" \
      --destination-range=35.235.240.0/20 --priority=100 \
      --next-hop-gateway=default-internet-gateway --quiet
  fi
}

apply_nat_profile() {
  echo "  -> applying NAT profile '$NAT_PROFILE' on $NAT_VM_NAME..."
  add_ssh_key "$NAT_VM_NAME"
  gcloud compute instances add-metadata "$NAT_VM_NAME" \
    --zone "$ZONE" --project "$PROJECT" \
    --metadata="nat-profile=$NAT_PROFILE_LOWER" --quiet

  local random_flag=""
  [[ "$NAT_PROFILE" == "HARD" ]] && random_flag="--random"
  gcloud compute ssh "$NAT_VM_NAME" --zone "$ZONE" --project "$PROJECT" \
    "${GCLOUD_SSH_OPTS[@]}" --command="
      set -e
      sudo sysctl -qw net.ipv4.ip_forward=1
      IFACE=\$(ip -o -4 route show to default | awk '{print \$5}')
      sudo iptables -t nat -F POSTROUTING
      sudo iptables -t nat -A POSTROUTING -o \"\$IFACE\" -j MASQUERADE $random_flag
      sudo iptables -P FORWARD ACCEPT
      sudo apt-get install -y -qq conntrack >/dev/null 2>&1 || true
      sudo conntrack -D -p udp >/dev/null 2>&1 || true
      echo \"profile '$NAT_PROFILE_LOWER' active\"
    " --quiet 2>&1 | grep -v "^WARNING:" || true
}

ensure_key
if vm_exists && vm_has_external_ip; then
  cat >&2 <<ERROR
Existing $VM_NAME has an external IP, which is the old pre-NAT topology.
Run ./teardown.sh first, then re-run ./create.sh --nat-profile=$NAT_PROFILE.
ERROR
  exit 1
fi

ensure_network
ensure_nat_vm
ensure_routes

if vm_exists; then
  echo "VM $VM_NAME already exists — redeploying code to it."
  CREATED_VM=false
else
  echo "Creating $VM_NAME ($MACHINE_TYPE, $IMAGE_FAMILY, $ZONE, no external IP, NAT profile: $NAT_PROFILE)..."
  gcloud compute instances create "$VM_NAME" \
    --project "$PROJECT" --zone "$ZONE" \
    --machine-type "$MACHINE_TYPE" \
    --image-family "$IMAGE_FAMILY" --image-project "$IMAGE_PROJECT" \
    --boot-disk-size "$BOOT_DISK_SIZE" \
    --network-interface="network=$NETWORK_NAME,subnet=$SUBNET_NAME,no-address" \
    --tags="$DEVICE_TAG" \
    --quiet
  CREATED_VM=true

  echo "Adding the automation SSH key to the VM's instance metadata..."
  add_ssh_key "$VM_NAME"
fi

wait_for_gcloud_ssh "$NAT_VM_NAME"
apply_nat_profile

PEER_LABEL="$(peer_label)"
echo "  -> peer access: $PEER_LABEL"
wait_for_gcloud_ssh "$VM_NAME"

echo "Packaging the yaffo source (current working tree, not HEAD)..."
TARBALL=$(mktemp -t yaffo_src.XXXXXX.tar.gz)
trap 'rm -f "$TARBALL"' EXIT
# COPYFILE_DISABLE stops macOS bsdtar from smuggling ._AppleDouble files in.
COPYFILE_DISABLE=1 tar czf "$TARBALL" -C "$REPO_ROOT" \
  --exclude "yaffo/__pycache__" --exclude "*.pyc" \
  pyproject.toml VERSION LICENSE README.md yaffo

echo "Copying source + setup script to the VM..."
peer_scp_to "$TARBALL" "/tmp/yaffo_src.tar.gz"
peer_scp_to "$PEER_SCRIPT_DIR/files/remote-setup.sh" "/tmp/yaffo-remote-setup.sh"

echo "Running remote setup (apt + venv + pip install + systemd)..."
peer_ssh "chmod +x /tmp/yaffo-remote-setup.sh && WEB_PORT=$WEB_PORT /tmp/yaffo-remote-setup.sh"

if [[ "$CREATED_VM" == true && "${SKIP_SEED:-}" != "1" ]]; then
  echo "Seeding the media dir with yaffo_ui_tests/test_data..."
  "$PEER_SCRIPT_DIR/copy-media.sh" "$REPO_ROOT/yaffo_ui_tests/test_data"
fi

cat <<DONE

yaffo-peer is up ($PEER_LABEL, NAT profile: $NAT_PROFILE). Next steps:
  1. ./port-forward.sh                     # then open the printed URL
  2. In the peer's UI: Settings -> add $REMOTE_MEDIA_DIR as a media
     directory (indexing picks up the seeded files), and set a download
     directory on the Sharing page if you'll pull files TO the peer.
  3. Pair it with your local instance from the Sharing tab.
  4. ./teardown.sh when finished — the VM bills while it exists.

Logs on the VM: gcloud compute ssh $VM_NAME --zone $ZONE --project $PROJECT --tunnel-through-iap --ssh-key-file=$KEY --command 'sudo journalctl -u yaffo-peer -f'
(First startup downloads the CLIP/InsightFace models — give it a few minutes.)
DONE
